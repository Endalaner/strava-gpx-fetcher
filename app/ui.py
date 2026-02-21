from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import Ride, RideStatus, ParticipantStatus
from weather import get_wind_arrow, WeatherService
from datetime import datetime
import random

# Russian day names
DAY_NAMES = {
    "saturday": "Суббота",
    "sunday": "Воскресенье",
}

# Dynamic headers for /tomorrow based on forecast quality
TOMORROW_HEADERS = {
    "great": [
        "🐗💨 Отличный день для катания! Выезжаем?",
        "☀️🚲 Погода зовёт: выходи и крути! Завтра всё будет отлично.",
        "🐗 Кабанья даёт зелёный свет. Завтра — GAS! 🔥",
    ],
    "rainy": [
        "🌧🐗 Дождь — это просто вода на фоне. Крилья помогут!",
        "💧 Немного мокро. Подготовь защиту от грязи и вперёд!",
        "🌧️ Даждь?😉 Настоящие кабаны не боятся грязи… А ты?",
    ],
    "cold": [
        "🥶🧤 Холодновато. Натяни зимние перчатки — это поможет!",
        "❄️🐗 Прохладно. Но холодный воздух будит веселее!",
        "🧣 Похолоднее — значит быстрее заразъешься. Закутайся теплееее!",
    ],
    "windy": [
        "💨🐗 Ветрено! Хвостовой — летим, встречный — растём. Игра !",
        "🌬️ Ветрено! Спрячься за колесо и наслаждайся.",
        "💨 Ветер — это естественный резистанс. Стая едет вперёд!",
    ],
    "bad": [
        "🐗🛑 Завтра — хороший день для отдыха. Позаботься о велосипеде дома.",
        "🙏 Погода не с нами завтра. Зато послезавтра — готовься к выезду в двойном размере!",
        "☁️ Не лучший день. Но все идеальные дни ещё впереди!",
    ],
}


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
        date_str = dt.strftime("%A, %d %b %H:%M")

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

    # ------------------------------------------------------------------
    # NEW: Weekend Weather Broadcast message
    # ------------------------------------------------------------------

    @staticmethod
    def _format_day_block(label: str, day_data: dict) -> list:
        """Format a single day's forecast into message lines."""
        if not day_data:
            return [f"📅 <b>{label}</b>", "  <i>нет данных</i>"]

        lines = [f"📅 <b>{label}</b>"]

        m = day_data.get('morning')
        d = day_data.get('midday')

        # Temperature line
        temp_parts = []
        if m:
            fl = f" (ощущ. {int(m['feels_like'])}°C)" if m.get('feels_like') is not None else ""
            temp_parts.append(f"Утро: {int(m['temp'])}°C{fl}")
        if d:
            temp_parts.append(f"Полдень: {int(d['temp'])}°C")
        if temp_parts:
            lines.append(f"  🌡 {' | '.join(temp_parts)}")

        # Precipitation line
        pop_max = max(day_data.get('pop_morning', 0), day_data.get('pop_midday', 0))
        precip_str = f"  🌧 Осадки: {int(pop_max)}%"
        rain = day_data.get('rain_mm', 0)
        snow = day_data.get('snow_mm', 0)
        if rain > 0:
            precip_str += f" | ~{rain:.1f}мм дождя"
        if snow > 0:
            precip_str += f" | ~{snow:.1f}мм снега"
        lines.append(precip_str)

        # Wind line
        ws = day_data.get('wind_speed', 0)
        wd = day_data.get('wind_deg')
        arrow = get_wind_arrow(wd)
        lines.append(f"  💨 Ветер: {int(ws)} м/с {arrow}")

        return lines

    @staticmethod
    def _format_alt_short(name: str, day_data: dict, day_label: str) -> str:
        """One-liner summary for an alternative location day."""
        if not day_data:
            return f"  {day_label}: нет данных"

        m = day_data.get('morning')
        d = day_data.get('midday')
        t_m = f"утро {int(m['temp'])}°C" if m and m.get('temp') is not None else ""
        t_d = f"полдень {int(d['temp'])}°C" if d and d.get('temp') is not None else ""
        pop = int(max(day_data.get('pop_morning', 0), day_data.get('pop_midday', 0)))
        ws = int(day_data.get('wind_speed', 0))
        arrow = get_wind_arrow(day_data.get('wind_deg'))

        parts = [p for p in [t_m, t_d] if p]
        return f"  {day_label}: {', '.join(parts)}, осадки {pop}%, ветер {ws} м/с {arrow}"

    @staticmethod
    def format_weekend_weather_message(
        default_name: str,
        default_forecast: dict,
        alternatives: list,  # [(name, forecast_dict), ...]
    ) -> str:
        """
        Build the full HTML weekend weather broadcast message.

        default_forecast / alt forecast shape:
            {"saturday": {day_data}, "sunday": {day_data}}
        """
        lines = [f"🌤 <b>Прогноз на выходные</b> — <b>{default_name}</b>", ""]

        sat = default_forecast.get('saturday') if default_forecast else None
        sun = default_forecast.get('sunday') if default_forecast else None

        # Main location forecast
        lines.extend(UriChanUI._format_day_block(DAY_NAMES['saturday'], sat))
        lines.append("")
        lines.extend(UriChanUI._format_day_block(DAY_NAMES['sunday'], sun))
        lines.append("")

        # Evaluate bad weather
        sat_bad = WeatherService.is_bad_for_cycling(sat)
        sun_bad = WeatherService.is_bad_for_cycling(sun)

        if sat_bad or sun_bad:
            bad_days = []
            if sat_bad:
                bad_days.append(DAY_NAMES['saturday'])
            if sun_bad:
                bad_days.append(DAY_NAMES['sunday'])
            lines.append(f"⚠️ <b>{', '.join(bad_days)} — сложно для катания.</b>")
            lines.append("Может, рванём в другое место?")
            lines.append("")

            if alternatives:
                lines.append("📍 <b>Альтернативы:</b>")
                lines.append("")

                # Score each alternative (combine sat+sun scores)
                scored = []
                for alt_name, alt_fc in alternatives:
                    a_sat = alt_fc.get('saturday') if alt_fc else None
                    a_sun = alt_fc.get('sunday') if alt_fc else None
                    score = WeatherService.location_score(a_sat) + WeatherService.location_score(a_sun)
                    scored.append((alt_name, alt_fc, score))

                scored.sort(key=lambda x: x[2])

                # Color indicators
                indicators = ['🟢', '🟡', '🔴']
                for i, (alt_name, alt_fc, _score) in enumerate(scored):
                    indicator = indicators[min(i, len(indicators) - 1)]
                    suffix = " — лучший вариант!" if i == 0 else ""
                    lines.append(f"{indicator} <b>{alt_name}</b>{suffix}")

                    a_sat = alt_fc.get('saturday') if alt_fc else None
                    a_sun = alt_fc.get('sunday') if alt_fc else None
                    lines.append(UriChanUI._format_alt_short(alt_name, a_sat, "Сб"))
                    lines.append(UriChanUI._format_alt_short(alt_name, a_sun, "Вс"))
                    lines.append("")

                # Kabanya recommendation
                best_name = scored[0][0] if scored else None
                if best_name:
                    lines.append(f"🐗 Кабанья рекомендует: <b>{best_name}</b> — едем!")
        else:
            # Good weather
            lines.append("✨ <b>Погода отличная! Никаких отмазок — крутим!</b> 🐗🚲")

        return "\n".join(lines)

    @staticmethod
    def _pick_tomorrow_header(day_data: dict) -> str:
        """Pick a random header variant based on forecast conditions."""
        if not day_data:
            return random.choice(TOMORROW_HEADERS["bad"])

        is_bad = WeatherService.is_bad_for_cycling(day_data)
        if is_bad:
            # Determine dominant bad condition for a specific header
            if day_data.get('pop_morning', 0) > 50 or day_data.get('pop_midday', 0) > 50 or day_data.get('rain_mm', 0) > 2:
                return random.choice(TOMORROW_HEADERS["rainy"])
            if day_data.get('temp_morning') is not None and day_data['temp_morning'] < 3:
                return random.choice(TOMORROW_HEADERS["cold"])
            if day_data.get('wind_speed', 0) > 11:
                return random.choice(TOMORROW_HEADERS["windy"])
            return random.choice(TOMORROW_HEADERS["bad"])

        # Good weather — check for marginal conditions
        if day_data.get('wind_speed', 0) > 7:
            return random.choice(TOMORROW_HEADERS["windy"])
        if day_data.get('temp_morning') is not None and day_data['temp_morning'] < 5:
            return random.choice(TOMORROW_HEADERS["cold"])
        return random.choice(TOMORROW_HEADERS["great"])

    @staticmethod
    def format_tomorrow_message(location_name: str, day_data: dict) -> str:
        """Format the /tomorrow forecast broadcast message."""
        from datetime import datetime, timedelta
        tomorrow = (datetime.utcnow() + timedelta(days=1))
        day_str = tomorrow.strftime("%A, %d.%m")

        header = UriChanUI._pick_tomorrow_header(day_data)

        lines = [
            f"🗓 <b>Прогноз на завтра ({day_str})</b>",
            f"📍 {location_name}",
            "",
            f"<i>{header}</i>",
            "",
        ]

        # Day block
        lines.extend(UriChanUI._format_day_block("", day_data))

        # Bad weather alternatives hint
        if WeatherService.is_bad_for_cycling(day_data):
            lines.append("")
            lines.append("💡 Используй /forecast для сравнения альтернативных маршрутов.")

        return "\n".join(line for line in lines if line is not None)

    @staticmethod
    def get_random_join_message():
        msgs = [
            "🐗 +1 кабан! Добро пожаловать в стаю!",
            "👏 Отлично! Нас становится больше!",
            "✅ Записал. Будем ждать!",
            "🚀 Ещё один боец в пак! Отлично!",
        ]
        return random.choice(msgs)

    @staticmethod
    def get_random_leave_message():
        msgs = [
            "🙏 Жаль, что не выйдет. Удачи в следующий раз!",
            "👍 Бывает!️ Будем ждать тебя на следующем заезде.",
            "🐗 Не вопросс. Иногда жизнь мешает. До следующего!",
        ]
        return random.choice(msgs)
