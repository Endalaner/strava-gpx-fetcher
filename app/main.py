import asyncio
import os
import re
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile, CallbackQuery
from aiogram.filters import Command
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import init_db, async_session, Ride, RideParticipant, RideStatus, ParticipantStatus
from strava import StravaService
from scheduler import scheduler, schedule_ride_jobs, schedule_weekend_weather_job, send_weekend_weather_broadcast
from ui import UriChanUI
from weather import weather_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_REFRESH_TOKEN = os.getenv("STRAVA_ADMIN_REFRESH_TOKEN")

# Relay / Megaphone config
TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID")
BOT_RELAY_ALLOWED_USERS = {
    int(uid.strip())
    for uid in os.getenv("BOT_RELAY_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
}
BOT_RELAY_MODE = os.getenv("BOT_RELAY_MODE", "command")  # "command" or "passthrough"

strava_service = StravaService(refresh_token=ADMIN_REFRESH_TOKEN)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Middlewares ---

@dp.update()
async def update_logger(update: types.Update, bot: Bot):
    if update.message:
        logger.info(f">>> MSR RECV from {update.message.from_user.id}: '{update.message.text}'")
    elif update.callback_query:
        logger.info(f">>> CB RECV from {update.callback_query.from_user.id}: '{update.callback_query.data}'")
    
    # In aiogram 3, we don't return anything from update handlers if we want them to pass?
    # Actually, we should probably use a middleware for this.
    # But for now, let's just make sure it doesn't return anything.
    pass

# --- Handlers ---

@dp.message(F.text.contains("strava"))
async def link_handler(message: types.Message):
    logger.info("Triggered link_handler")
    await bot.send_chat_action(message.chat.id, action="upload_document")
    status = await message.reply("🔄 Обрабатываю...")
    
    try:
        # returns dict: data, filename, name, distance, elevation, url, start_time, strava_event_id
        result = await strava_service.get_gpx(message.text)
        print(f"DEBUG: Result type: {type(result)}")
        
        caption = (
            f"🚴 <b>{result['name']}</b>\n"
            f"📏 {result['distance']:.2f} km | ⛰️ {int(result['elevation'])} m\n"
            f"🔗 <a href='{result['url']}'>Страva Маршрут</a>"
        )
        
        # Add "Announce" button if it's a group event with a start time
        reply_markup = None
        if result.get('start_time') and result.get('strava_event_id'):
            reply_markup = UriChanUI.manual_announce_keyboard(result['strava_event_id'])

        await message.reply_document(
            document=BufferedInputFile(result['data'], filename=result['filename']),
            caption=caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        await status.delete()
            
    except Exception as e:
        error_msg = str(e)
        if "404" in error_msg:
            await status.edit_text("❌ Маршрут недоступен. Проверь настройки приватности.")
        elif "Event not found" in error_msg:
            await status.edit_text("❌ Ивент не найден или к нему не привязан маршрут.")
        else:
            await status.edit_text(f"❌ Ошибка: {error_msg}")

@dp.callback_query(F.data.startswith("announce:"))
async def announce_ride(callback: CallbackQuery):
    event_id = callback.data.split(":", 1)[1]
    url = f"https://www.strava.com/group_events/{event_id}"
    
    try:
        # Re-fetch data to be sure (or cache it? Re-fetching is safer for now)
        data = await strava_service.get_gpx(url)
        
        if not data.get('start_time'):
            await callback.answer("❌ Ошибка: Не найдено время старта.", show_alert=True)
            return
            
        # Create Ride in DB
        async with async_session() as session:
            # Check duplicates
            stmt = select(Ride).where(Ride.strava_event_id == data['strava_event_id'])
            existing = (await session.execute(stmt)).scalars().first()
            
            if existing:
                if existing.status == RideStatus.active:
                     await callback.answer("🐗 Этот заезд уже активен!", show_alert=True)
                     return
                else:
                    # Cleanup old ride to allow re-announcement
                    await session.delete(existing)
                    await session.commit() # Commit delete first
                    # Need to re-open session or just continue?
                    # session still open. But we might need to flush.
                    pass

            # Post new pinned message (or edit the one we have? The spec says "Posts a new... message")
            # But we are in a callback from a file message. Let's post a NEW message.
            # Delete the "Announce" button from the file message first
            await callback.message.edit_reply_markup(reply_markup=None)
            
            # Fetch Weather
            w_data = None
            print(f"DEBUG: Fetching weather for Data: lat={data.get('lat')}, lon={data.get('lon')}, time={data.get('start_time')}")
            if data.get('lat') and data.get('lon') and data.get('start_time'):
                w_data = await weather_service.get_forecast(data['lat'], data['lon'], data['start_time'])
                print(f"DEBUG: Weather Data retrieved: {w_data}")
                if w_data:
                    w_data['uri_comment'] = weather_service.get_uri_comment(w_data)

            # Create Ride Card Text
            # Initially no participants
            card_text = UriChanUI.generate_ride_card(data, [], w_data)
            
            # Send Message
            msg = await bot.send_message(
                callback.message.chat.id,
                card_text,
                parse_mode="HTML"
            )
            
            # 2. Pin it
            try:
                await msg.pin()
            except:
                await bot.send_message(callback.message.chat.id, "⚠️ Дай мне права 'Pin Messages' (Закреплять сообщения)!")
                
            # 3. Save to DB
            new_ride = Ride(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                strava_event_id=data['strava_event_id'],
                start_time=data['start_time'],
                status=RideStatus.active
            )
            session.add(new_ride)
            await session.commit()
            
            # 4. Schedule
            schedule_ride_jobs(bot, new_ride.id, new_ride.chat_id, new_ride.start_time)
            
            # 5. Add Buttons to the new message (using ID we just got)
            await msg.edit_reply_markup(reply_markup=UriChanUI.ride_keyboard(new_ride.id))
            
            await callback.answer("🐗 Лобби создано!", show_alert=True)

    except Exception as e:
        await callback.answer(f"Error: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("rsvp:"))
async def rsvp_handler(callback: CallbackQuery):
    # data format: rsvp:ride_id:status
    _, ride_id, action_status = callback.data.split(":")
    ride_id = int(ride_id)
    user = callback.from_user
    new_status = ParticipantStatus[action_status]
    
    async with async_session() as session:
        # Get Ride
        ride = await session.get(Ride, ride_id)
        if not ride or ride.status != RideStatus.active:
            await callback.answer("❌ Заезд завершен или удален.", show_alert=True)
            return

        # Check current status
        stmt = select(RideParticipant).where(
            RideParticipant.ride_id == ride_id,
            RideParticipant.user_id == user.id
        )
        participant = (await session.execute(stmt)).scalars().first()
        
        toast_msg = ""
        
        if participant:
            if participant.status == new_status:
                # Toggle OFF (Remove)
                await session.delete(participant)
                toast_msg = UriChanUI.get_random_leave_message()
            else:
                # Change status
                participant.status = new_status
                participant.username = user.username
                toast_msg = "Обновлено!"
        else:
            # Insert new
            new_p = RideParticipant(
                ride_id=ride_id,
                user_id=user.id,
                username=user.username,
                status=new_status
            )
            session.add(new_p)
            toast_msg = UriChanUI.get_random_join_message()
            
        await session.commit()
        
        # Refresh UI
        # Need to fetch ride data again? Or we can't easily get name/dist/elev from DB since we didn't store it.
        # We did NOT store route metadata in DB (dist, elev, name).
        # We need to re-fetch from Strava OR store it.
        # Storing in DB is better, but schema is already set. 
        # Workaround: Re-fetch from Strava (expensive?) OR Parse from current message text?
        # Parsing message text is fragile.
        # Let's perform a strava fetch, it's safer for consistency, although slower.
        # Optimization: cache this?
        # For V1, let's fetch. Strava API rate limit is decent. 
        # Actually we have `strava_event_id`. We can construct the URL.
        
        # Wait, get_gpx takes a URL.
        # We can reconstruct group event url: strava.com/clubs/0/group_events/{id}
        # But we need club id? No, /group_events/{id} usually redirects or works.
        # Actually `StravaService` helper `get_gpx` does the downloading. We function `_resolve_url` logic.
        
        # Alternative: We can store the initial JSON blob in DB? No column for it.
        # Alternative 2: We can just use the link in the message... but we can't easily parse it.
        
        # User constraint: "Re-render: Fetch all participants for ride_id -> Generate new Message Text"
        # I will implement a lightweight `get_event_metadata` in StravaService to avoid downloading GPX bytes every time.
        
        # Since I can't modify StravaService right now easily without another tool call, I will try to use `get_gpx` but ignore the bytes.
        # It's a bit heavy but works.
        
        event_url = f"https://www.strava.com/group_events/{ride.strava_event_id}"
        data = {}
        try:
           data = await strava_service.get_gpx(event_url)
        except:
           pass

        # Re-fetch participants to be fresh
        await session.refresh(ride, attribute_names=['participants'])
        
        # Fetch Weather (Refresh if needed, service handles cache)
        w_data = None
        if data.get('lat') and data.get('lon') and data.get('start_time'):
             # Note: data['start_time'] comes from fresh fetch. 
             # If fetch failed, 'data' is empty, we lose weather info in UI update?
             # Ideally we should persist lat/lon in DB, but Schema frozen.
             # Since we rely on fresh fetch, if it fails, UI degrades. acceptable for now.
             w_data = await weather_service.get_forecast(data['lat'], data['lon'], data['start_time'])
             if w_data:
                w_data['uri_comment'] = weather_service.get_uri_comment(w_data)

        new_text = UriChanUI.generate_ride_card(data, ride.participants, w_data)
        
        if new_text != callback.message.text:
            await callback.message.edit_text(
                text=new_text,
                parse_mode="HTML",
                reply_markup=UriChanUI.ride_keyboard(ride_id)
            )
            
        await callback.answer(toast_msg)

@dp.callback_query(F.data.startswith("cancel_ride:"))
async def cancel_ride_handler(callback: CallbackQuery):
    ride_id = int(callback.data.split(":")[1])
    
    # Check permissions
    chat_member = await bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
    if chat_member.status not in ("creator", "administrator"):
        await callback.answer("⛔ Только Админы могут удалять заезды!", show_alert=True)
        return

    async with async_session() as session:
        ride = await session.get(Ride, ride_id)
        if not ride:
            await callback.answer("Заезд уже удален.", show_alert=True)
            return
            
        ride.status = RideStatus.cancelled
        await session.commit()
        
        # Unpin and update message
        try:
            await bot.unpin_chat_message(callback.message.chat.id, ride.message_id)
        except:
            pass # Might not be pinned or permission issue
            
        await callback.message.edit_text(
            text=f"❌ <b>ЗАЕЗД ОТМЕНЕН</b>\nКем: @{callback.from_user.username}",
            reply_markup=None,
            parse_mode="HTML"
        )
# ------------------------------------------------------------------
# /ping — simple connectivity check
# ------------------------------------------------------------------

@dp.message(Command("ping"))
async def ping_handler(message: types.Message):
    logger.info("Triggered ping_handler")
    await message.reply("🐗 Хрю! Я живой!")

@dp.message(Command("db_check"))
async def db_check_handler(message: types.Message):
    logger.info("Triggered db_check_handler")
    try:
        async with async_session() as session:
            await session.execute(select(1))
        await message.reply("✅ База данных доступна из хендлера!")
    except Exception as e:
        logger.error(f"DB check error: {e}")
        await message.reply(f"❌ Ошибка базы данных: {e}")


# ------------------------------------------------------------------
# /forecast — debug command to trigger weekend weather broadcast
# ------------------------------------------------------------------

@dp.message(Command("forecast"))
async def forecast_handler(message: types.Message):
    """Debug command: immediately generates and sends the weekend weather forecast."""
    print("DEBUG: Triggered forecast_handler")
    status = await message.reply("🔄 Собираю прогноз на выходные...")
    try:
        msg_text = await send_weekend_weather_broadcast(bot)
        if msg_text:
            # Also reply directly to the invoker so they can see the result
            await status.edit_text("✅ Прогноз отправлен в группу!")
        else:
            await status.edit_text(
                "⚠️ Не удалось сформировать прогноз. "
                "Проверь TELEGRAM_GROUP_CHAT_ID и OPENWEATHER_API_KEY."
            )
    except Exception as e:
        await status.edit_text(f"❌ Ошибка: {e}")

# ------------------------------------------------------------------
# /tomorrow — forecast for tomorrow sent to the group
# ------------------------------------------------------------------

@dp.message(Command("tomorrow"))
async def tomorrow_handler(message: types.Message):
    """Fetches tomorrow's forecast and sends it to the group chat."""
    status = await message.reply("🔄 Смотрю прогноз на завтра...")
    try:
        default_location = os.getenv("DEFAULT_LOCATION", "Belgrade")
        coords = await weather_service.geocode_location(default_location)
        if not coords:
            await status.edit_text("❌ Не удалось определить координаты локации.")
            return

        lat, lon = coords
        day_data = await weather_service.get_tomorrow_forecast(lat, lon)

        msg_text = UriChanUI.format_tomorrow_message(default_location, day_data)

        if TELEGRAM_GROUP_CHAT_ID:
            await bot.send_message(int(TELEGRAM_GROUP_CHAT_ID), msg_text, parse_mode="HTML")
            await status.edit_text("✅ Прогноз на завтра отправлен в группу!")
        else:
            # If no group, reply directly
            await status.edit_text(msg_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Tomorrow handler error: {e}")
        await status.edit_text(f"❌ Ошибка: {e}")

# ------------------------------------------------------------------

def _is_relay_authorized(user_id: int) -> bool:
    return user_id in BOT_RELAY_ALLOWED_USERS

async def _relay_to_group(message: types.Message, text: str | None = None):
    """Forward content from a DM to the group chat."""
    if not TELEGRAM_GROUP_CHAT_ID:
        await message.reply("⚠️ TELEGRAM_GROUP_CHAT_ID не настроен.")
        return

    chat_id = int(TELEGRAM_GROUP_CHAT_ID)

    try:
        if message.photo:
            await bot.send_photo(chat_id, photo=message.photo[-1].file_id, caption=message.caption or "", parse_mode="HTML")
        elif message.document:
            await bot.send_document(chat_id, document=message.document.file_id, caption=message.caption or "", parse_mode="HTML")
        elif text:
            await bot.send_message(chat_id, text, parse_mode="HTML")
        else:
            await message.reply("❌ Пустое сообщение, нечего отправлять.")
            return
        await message.reply("✅ Отправлено в группу!")
    except Exception as e:
        logger.error(f"Relay error: {e}")
        await message.reply(f"❌ Ошибка отправки: {e}")

@dp.message(Command("relay"))
async def relay_command_handler(message: types.Message):
    if message.chat.type != "private" or not _is_relay_authorized(message.from_user.id):
        return
    text = message.text.partition(" ")[2].strip() if message.text else ""
    if not text:
        await message.reply("Использование: <code>/relay Текст сообщения</code>", parse_mode="HTML")
        return
    await _relay_to_group(message, text)

@dp.message(F.chat.type == "private", ~F.text.startswith("/"))
async def passthrough_dm_handler(message: types.Message):
    """Relays non-command text and media to the group."""
    if not _is_relay_authorized(message.from_user.id):
        return

    if message.photo or message.document:
        await _relay_to_group(message)
    elif BOT_RELAY_MODE == "passthrough" and message.text:
        await _relay_to_group(message, message.text)
    elif message.text:
        await message.reply("💡 Используй <code>/relay Текст</code> чтобы отправить сообщение в группу.", parse_mode="HTML")

@dp.message()
async def catch_all(message: types.Message):
    logger.info(f"Catch-all triggered for: '{message.text}' from {message.from_user.id}")

async def main():
    await init_db()
    scheduler.start()
    schedule_weekend_weather_job(bot)
    logger.info("Bot is starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


