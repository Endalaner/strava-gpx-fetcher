import os
import aiohttp
from datetime import datetime, timedelta
import time

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# Wind arrow helper (shared)
WIND_DIRS = ['↓', '↙', '←', '↖', '↑', '↗', '→', '↘']

def get_wind_arrow(deg):
    """Convert meteorological wind degrees to a directional arrow."""
    if deg is None:
        return ""
    ix = round(deg / 45) % 8
    return WIND_DIRS[ix]


class WeatherService:
    def __init__(self):
        self.cache = {}  # (lat, lon, hour) -> data
        self.geo_cache = {}  # city_name_lower -> (lat, lon)
        self.base_url = "https://api.openweathermap.org/data/2.5/forecast"
        self.geo_url = "https://api.openweathermap.org/geo/1.0/direct"

    # ------------------------------------------------------------------
    # Existing: single-point forecast (used by ride cards)
    # ------------------------------------------------------------------

    async def get_forecast(self, lat: float, lon: float, timestamp: datetime) -> dict:
        """
        Fetches forecast for a specific time using standard 5-day/3-hour forecast.
        """
        ts_hour = timestamp.strftime("%Y-%m-%d-%H")
        key = (round(lat, 2), round(lon, 2), ts_hour)

        if key in self.cache:
            return self.cache[key]

        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
            print(f"DEBUG: Fetching Weather: {url.replace(OPENWEATHER_API_KEY, 'HIDDEN')}")

            async with session.get(url) as resp:
                print(f"DEBUG: Weather API Status: {resp.status}")
                if resp.status != 200:
                    print(f"Weather API Error: {resp.status} - {await resp.text()}")
                    return None

                data = await resp.json()

                target_ts = timestamp.timestamp()
                closest = None
                min_diff = float('inf')

                for item in data.get('list', []):
                    diff = abs(item['dt'] - target_ts)
                    if diff < min_diff:
                        min_diff = diff
                        closest = item

                if closest and min_diff < 43200:  # 12 hours
                    result = {
                        "temp": closest['main'].get('temp'),
                        "feels_like": closest['main'].get('feels_like'),
                        "wind_speed": closest.get('wind', {}).get('speed'),
                        "wind_deg": closest.get('wind', {}).get('deg'),
                        "pop": closest.get('pop', 0) * 100,
                        "desc": closest['weather'][0]['description'] if closest.get('weather') else ""
                    }
                    self.cache[key] = result
                    return result

        return None

    def get_uri_comment(self, w: dict) -> str:
        if not w:
            return ""

        comments = []

        wind_spd = w.get('wind_speed', 0)
        if wind_spd > 5:
            comments.append("Попутный ветер — летим! 🐗💨")

        t = w.get('feels_like', w.get('temp', 20))
        if t < 5:
            comments.append("Холодновато — одевайся теплее! 🧤")
        elif 18 <= t <= 25 and w.get('pop', 0) < 10:
            comments.append("Идеальная погода. Отличный выезд! ✨")

        if w.get('pop', 0) > 50:
            comments.append("Возможен дождь — не забудь крылья! 🌧")

        if not comments:
            comments.append("Погода норм. Крутим! 🚲")

        return " ".join(comments)

    # ------------------------------------------------------------------
    # NEW: Geocoding
    # ------------------------------------------------------------------

    async def geocode_location(self, city_name: str) -> tuple:
        """
        Convert city name -> (lat, lon) using OWM Geocoding API.
        Returns (lat, lon) or None.
        """
        key = city_name.strip().lower()
        if key in self.geo_cache:
            return self.geo_cache[key]

        async with aiohttp.ClientSession() as session:
            url = f"{self.geo_url}?q={city_name}&limit=1&appid={OPENWEATHER_API_KEY}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Geocoding error for '{city_name}': {resp.status}")
                    return None
                data = await resp.json()
                if not data:
                    print(f"Geocoding: no results for '{city_name}'")
                    return None
                lat, lon = data[0]['lat'], data[0]['lon']
                self.geo_cache[key] = (lat, lon)
                return (lat, lon)

    # ------------------------------------------------------------------
    # NEW: Weekend forecast
    # ------------------------------------------------------------------

    async def get_weekend_forecast(self, lat: float, lon: float) -> dict:
        """
        Fetch 5-day forecast and extract morning (06:00-09:00) and midday
        (12:00-15:00) data for the upcoming Saturday and Sunday.

        Returns:
        {
            "saturday": { ... day data ... } or None,
            "sunday":   { ... day data ... } or None,
        }
        """
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
            async with session.get(url) as resp:
                if resp.status != 200:
                    print(f"Weekend forecast error: {resp.status}")
                    return None
                data = await resp.json()

        forecast_list = data.get('list', [])
        if not forecast_list:
            return None

        # Find next Saturday and Sunday dates
        today = datetime.now()
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0:
            days_until_saturday = 7  # next Saturday if today is Saturday
        saturday = (today + timedelta(days=days_until_saturday)).date()
        sunday = saturday + timedelta(days=1)

        result = {}
        for label, target_date in [("saturday", saturday), ("sunday", sunday)]:
            result[label] = self._extract_day_data(forecast_list, target_date)

        return result

    def _extract_day_data(self, forecast_list: list, target_date) -> dict:
        """
        From a forecast list, pick morning (06-09h) and midday (12-15h)
        slots for the given date.
        """
        morning_slot = None
        midday_slot = None

        for item in forecast_list:
            dt = datetime.utcfromtimestamp(item['dt'])
            if dt.date() != target_date:
                continue
            hour = dt.hour
            # Morning: prefer 06 or 09
            if 6 <= hour <= 9:
                if morning_slot is None or abs(hour - 8) < abs(datetime.utcfromtimestamp(morning_slot['dt']).hour - 8):
                    morning_slot = item
            # Midday: prefer 12 or 15
            if 12 <= hour <= 15:
                if midday_slot is None or abs(hour - 12) < abs(datetime.utcfromtimestamp(midday_slot['dt']).hour - 12):
                    midday_slot = item

        if not morning_slot and not midday_slot:
            return None

        def _parse_slot(slot):
            if not slot:
                return None
            return {
                "temp": slot['main'].get('temp'),
                "feels_like": slot['main'].get('feels_like'),
                "wind_speed": slot.get('wind', {}).get('speed'),
                "wind_deg": slot.get('wind', {}).get('deg'),
                "pop": slot.get('pop', 0) * 100,  # percent
                "rain_mm": slot.get('rain', {}).get('3h', 0),
                "snow_mm": slot.get('snow', {}).get('3h', 0),
                "desc": slot['weather'][0]['description'] if slot.get('weather') else "",
            }

        morning = _parse_slot(morning_slot)
        midday = _parse_slot(midday_slot)

        # Aggregate for the day
        day = {
            "date": target_date,
            "morning": morning,
            "midday": midday,
            # Aggregate worst-case values for bad-weather evaluation
            "temp_morning": morning['temp'] if morning else None,
            "feels_like_morning": morning['feels_like'] if morning else None,
            "temp_midday": midday['temp'] if midday else None,
            "pop_morning": morning['pop'] if morning else 0,
            "pop_midday": midday['pop'] if midday else 0,
            "rain_mm": max(
                (morning or {}).get('rain_mm', 0),
                (midday or {}).get('rain_mm', 0),
            ),
            "snow_mm": max(
                (morning or {}).get('snow_mm', 0),
                (midday or {}).get('snow_mm', 0),
            ),
            "wind_speed": max(
                (morning or {}).get('wind_speed', 0),
                (midday or {}).get('wind_speed', 0),
            ),
            "wind_deg": (midday or morning or {}).get('wind_deg'),
        }
        return day

    # ------------------------------------------------------------------
    # NEW: Bad weather evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def is_bad_for_cycling(day_data: dict) -> bool:
        """
        Returns True if weather conditions are bad for cycling.
        Thresholds:
          - morning temp < 3°C
          - midday temp < 5°C
          - rain chance (morning or midday) > 50%
          - expected rain > 2mm
          - wind speed > 11 m/s (~40 km/h)
        """
        if not day_data:
            return True  # no data = assume bad

        if day_data.get('temp_morning') is not None and day_data['temp_morning'] < 3:
            return True
        if day_data.get('temp_midday') is not None and day_data['temp_midday'] < 5:
            return True
        if day_data.get('pop_morning', 0) > 50 or day_data.get('pop_midday', 0) > 50:
            return True
        if day_data.get('rain_mm', 0) > 2:
            return True
        if day_data.get('wind_speed', 0) > 11:
            return True
        return False

    @staticmethod
    def location_score(day_data: dict) -> float:
        """
        Lower is better. Used to rank alternative locations.
        """
        if not day_data:
            return 9999

        score = 0
        # Rain chance penalty
        score += max(day_data.get('pop_morning', 0), day_data.get('pop_midday', 0))
        # Rain amount penalty
        score += day_data.get('rain_mm', 0) * 20
        # Wind penalty
        score += max(0, day_data.get('wind_speed', 0) - 5) * 5
        # Cold penalty
        temp_m = day_data.get('temp_morning')
        if temp_m is not None and temp_m < 5:
            score += (5 - temp_m) * 10
        return score


    # ------------------------------------------------------------------
    # NEW: Tomorrow forecast
    # ------------------------------------------------------------------

    async def get_tomorrow_forecast(self, lat: float, lon: float) -> dict:
        """
        Fetch 5-day forecast and extract morning (06-09h) and midday (12-15h)
        slots for tomorrow's date.

        Returns a day_data dict (same shape as _extract_day_data) or None.
        """
        async with aiohttp.ClientSession() as session:
            url = f"{self.base_url}?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        forecast_list = data.get('list', [])
        if not forecast_list:
            return None

        tomorrow = (datetime.utcnow() + timedelta(days=1)).date()
        return self._extract_day_data(forecast_list, tomorrow)


weather_service = WeatherService()
