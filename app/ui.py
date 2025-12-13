from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import Ride, RideStatus, ParticipantStatus
from datetime import datetime

class UriChanUI:
    @staticmethod
    def manual_announce_keyboard(event_id: str):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Анонс", callback_data=f"announce:{event_id}")]
        ])

    @staticmethod
    def ride_keyboard(ride_id: int):
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🐗 Я В ДЕЛЕ!", callback_data=f"rsvp:{ride_id}:going"),
                InlineKeyboardButton(text="🐌 МОЖЕТ БЫТЬ", callback_data=f"rsvp:{ride_id}:maybe")
            ],
            [
                InlineKeyboardButton(text="❌ ОТМЕНА (Админ)", callback_data=f"cancel_ride:{ride_id}")
            ]
        ])

    @staticmethod
    def generate_ride_card(ride_data: dict, participants: list, weather: dict = None) -> str:
        """
        ride_data: needs 'name', 'start_time', 'distance', 'elevation', 'url'
        participants: list of RideParticipant objects
        weather: dict with 'temp', 'feels_like', 'wind_speed', 'wind_deg', 'pop', 'desc', 'uri_comment'
        """
        # Format Date
        dt = ride_data['start_time']
        date_str = dt.strftime("%A, %d %b %H:%M") # Consider translating month names? Simple for now.
        
        # Group participants
        going = [p for p in participants if p.status == ParticipantStatus.going]
        maybe = [p for p in participants if p.status == ParticipantStatus.maybe]
        
        # Build Text
        text = [
            f"🐗 <b>СБОР СТАИ: \"{ride_data['name']}\"</b>",
            "",
            f"📅 <b>Когда:</b> {date_str} (UTC)",
            f"📍 <b>Маршрут:</b> {ride_data['distance']:.1f} км | ⛰️ {int(ride_data['elevation'])} м",
            f"🔗 <a href='{ride_data['url']}'>Ивент в Strava</a>",
            ""
        ]
        
        # Weather Section
        if weather:
            # Arrows helpers
            def get_wind_arrow(deg):
                if deg is None: return ""
                dirs = ['↓', '↙', '←', '↖', '↑', '↗', '→', '↘']
                ix = round(deg / 45) % 8
                return dirs[ix]
            
            w_arrow = get_wind_arrow(weather.get('wind_deg', 0))
            
            text.append(f"🌤 <b>Погода (на старте):</b>")
            text.append(f"🌡 {int(weather.get('temp',0))}°C (Ощущается {int(weather.get('feels_like',0))}°C)")
            text.append(f"💨 {int(weather.get('wind_speed',0))} м/с {w_arrow} • ☔️ {int(weather.get('pop',0))}%")
            if weather.get('uri_comment'):
                text.append(f"<i>\"{weather['uri_comment']}\"</i>")
            text.append("")

        text.append(f"💪 <b>БОЕВЫЕ КАБАНЧИКИ (Едут: {len(going)}):</b>")
        
        if going:
            for p in going:
                name = f"@{p.username}" if p.username else f"User {p.user_id}"
                text.append(f"• {name}")
        else:
            text.append("<i>...тишина... будь первым!</i>")
            
        text.append("")
        text.append(f"🤔 <b>ДУМАЮТ ({len(maybe)}):</b>")
        if maybe:
            for p in maybe:
                name = f"@{p.username}" if p.username else f"User {p.user_id}"
                text.append(f"• {name}")
                
        text.append("")
        text.append(f"<i>Обновлено: {datetime.now().strftime('%H:%M')}</i>")
        
        return "\n".join(text)

    @staticmethod
    def get_random_join_message():
         import random
         msgs = ["Опа! +1 в стае! 🐗", "Добро пожаловать в боль! 😈", "Готовь икры!", "Допуск разрешен!"]
         return random.choice(msgs)

    @staticmethod
    def get_random_leave_message():
         import random
         msgs = ["Эх... жаль терять бойца 😿", "Нам больше попутного ветра достанется!", "Предатель! (Шутка)"]
         return random.choice(msgs)
