from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from database import async_session, Ride, ParticipantStatus, RideStatus
from sqlalchemy import select
from datetime import datetime, timedelta

scheduler = AsyncIOScheduler()

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
        # Fallback for users without username not implemented to avoid spamming IDs, assumption is usernames exist
        
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
