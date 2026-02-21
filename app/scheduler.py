import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from database import async_session, Ride, ParticipantStatus, RideStatus
from sqlalchemy import select
from datetime import datetime, timedelta

scheduler = AsyncIOScheduler()

# --- Config for weekend weather broadcast ---
TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID")
DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "Belgrade")
ALTERNATIVE_LOCATIONS = [
    loc.strip()
    for loc in os.getenv("ALTERNATIVE_LOCATIONS", "Valjevo,Novi Sad,Vršac").split(",")
    if loc.strip()
]
WEATHER_BROADCAST_DAY = os.getenv("WEATHER_BROADCAST_DAY", "fri")
WEATHER_BROADCAST_HOUR = int(os.getenv("WEATHER_BROADCAST_HOUR", "18"))


async def send_reminder(bot, chat_id, ride_id):
    """Sends a reminder to participants 1 hour before start."""
    async with async_session() as session:
        result = await session.execute(select(Ride).where(Ride.id == ride_id))
        ride = result.scalars().first()
        if not ride or ride.status != RideStatus.active:
            return

        participants = [p for p in ride.participants if p.status == ParticipantStatus.going]

        if not participants:
            return

        mentions = " ".join([f"@{p.username}" for p in participants if p.username])

        if mentions:
            text = f"⏰ <b>Хрю!</b> Едем через час!\n{mentions}\n\nПроверь давление в шинах! 🚲"
            try:
                await bot.send_message(chat_id, text, parse_mode="HTML")
            except Exception as e:
                print(f"Failed to send reminder: {e}")

async def cleanup_ride(bot, chat_id, ride_id):
    """Marks ride as finished and updates message."""
    async with async_session() as session:
        result = await session.execute(select(Ride).where(Ride.id == ride_id))
        ride = result.scalars().first()

        if not ride: return

        ride.status = RideStatus.finished
        await session.commit()

        # Unpin and Edit
        try:
            await bot.unpin_chat_message(chat_id, ride.message_id)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=ride.message_id,
                text=f"🏁 <b>ФИНИШ: {ride.strava_event_id}</b>\nНадеюсь, хорошо проехались!",
                reply_markup=None
            )
        except Exception as e:
            print(f"Cleanup failed: {e}")

def schedule_ride_jobs(bot, ride_id, chat_id, start_time: datetime):
    # Reminder: 1 hour before
    remind_time = start_time - timedelta(hours=1)
    if remind_time > datetime.now(remind_time.tzinfo):
        scheduler.add_job(
            send_reminder,
            'date',
            run_date=remind_time,
            args=[bot, chat_id, ride_id],
            id=f"remind_{ride_id}"
        )

    # Cleanup: 4 hours after start
    cleanup_time = start_time + timedelta(hours=4)
    scheduler.add_job(
        cleanup_ride,
        'date',
        run_date=cleanup_time,
        args=[bot, chat_id, ride_id],
        id=f"cleanup_{ride_id}"
    )


# ------------------------------------------------------------------
# Weekend Weather Broadcast
# ------------------------------------------------------------------

async def send_weekend_weather_broadcast(bot):
    """
    Fetches weekend forecasts for the default and alternative locations,
    formats the message, and sends it to the configured group chat.
    Returns the formatted message text (useful for /forecast debug).
    """
    from weather import weather_service
    from ui import UriChanUI

    chat_id = TELEGRAM_GROUP_CHAT_ID
    if not chat_id:
        print("WARNING: TELEGRAM_GROUP_CHAT_ID not set, skipping broadcast.")
        return None

    chat_id = int(chat_id)

    # 1. Geocode default location
    default_coords = await weather_service.geocode_location(DEFAULT_LOCATION)
    if not default_coords:
        print(f"ERROR: Could not geocode default location '{DEFAULT_LOCATION}'")
        return None
    default_lat, default_lon = default_coords

    # 2. Fetch default forecast
    default_forecast = await weather_service.get_weekend_forecast(default_lat, default_lon)

    # 3. Geocode + fetch alternative locations
    alternatives = []
    for alt_name in ALTERNATIVE_LOCATIONS:
        coords = await weather_service.geocode_location(alt_name)
        if coords:
            alt_fc = await weather_service.get_weekend_forecast(coords[0], coords[1])
            alternatives.append((alt_name, alt_fc))
        else:
            alternatives.append((alt_name, None))

    # 4. Format message
    message_text = UriChanUI.format_weekend_weather_message(
        DEFAULT_LOCATION, default_forecast, alternatives
    )

    # 5. Send to group
    try:
        await bot.send_message(chat_id, message_text, parse_mode="HTML")
        print(f"Weekend weather broadcast sent to chat {chat_id}")
    except Exception as e:
        print(f"Failed to send weekend weather broadcast: {e}")

    return message_text


def schedule_weekend_weather_job(bot):
    """Register a cron job that fires every Friday (configurable) at the given hour."""
    scheduler.add_job(
        send_weekend_weather_broadcast,
        'cron',
        day_of_week=WEATHER_BROADCAST_DAY,
        hour=WEATHER_BROADCAST_HOUR,
        args=[bot],
        id="weekend_weather_broadcast",
        replace_existing=True,
    )
    print(
        f"Scheduled weekend weather broadcast: "
        f"every {WEATHER_BROADCAST_DAY} at {WEATHER_BROADCAST_HOUR}:00"
    )

