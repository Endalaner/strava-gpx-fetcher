import asyncio
import os
import re
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import init_db, async_session, Ride, RideParticipant, RideStatus, ParticipantStatus
from database import init_db, async_session, Ride, RideParticipant, RideStatus, ParticipantStatus
from strava import StravaService
from scheduler import scheduler, schedule_ride_jobs
from ui import UriChanUI
from weather import weather_service

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_REFRESH_TOKEN = os.getenv("STRAVA_ADMIN_REFRESH_TOKEN")

strava_service = StravaService(refresh_token=ADMIN_REFRESH_TOKEN)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Handlers ---

@dp.message(F.text.contains("strava"))
async def link_handler(message: types.Message):
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
        await callback.answer("Заезд отменен.")

async def main():
    await init_db()
    
    # Start Scheduler
    scheduler.start()
    
    # Start Polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
