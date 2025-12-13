import os
import aiohttp
from datetime import datetime
import time

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

class WeatherService:
    def __init__(self):
        self.cache = {} # (lat, lon, hour) -> data
        self.base_url = "https://api.openweathermap.org/data/2.5/forecast" 
        
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
                # 2.5/forecast returns "list": [...]
                
                target_ts = timestamp.timestamp()
                closest = None
                min_diff = float('inf')
                
                for item in data.get('list', []):
                    diff = abs(item['dt'] - target_ts)
                    if diff < min_diff:
                        min_diff = diff
                        closest = item
                
                # Check if closest is reasonable (e.g. within 3 hours? or just take best match)
                # If event is 7 days away, closest will be 5 days away. That's WRONG.
                # Threshold: 12 hours?
                if closest and min_diff < 43200: # 12 hours
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

    def get_wind_arrow(self, deg):
        if deg is None: return ""
        dirs = ['↓', '↙', '←', '↖', '↑', '↗', '→', '↘']
        # 0 deg is North (wind coming FROM North? No, Meteorologic: 0=N, 90=E. Arrow usually points WHERE it blows TO)
        # OWM: "Wind direction, degrees (meteorological)" -> 0 is Wind from North.
        # So it blows TO South (180).
        # Visualizing "Wind is blowing North-to-South": Arrow ↓
        # Let's map directly.
        ix = round(deg / 45) % 8
        return dirs[ix]

    def get_uri_comment(self, w: dict) -> str:
        if not w: return ""
        
        comments = []
        
        # Wind
        wind_spd = w.get('wind_speed', 0)
        if wind_spd > 5:
            comments.append("Ветер в спину! Летим! 🐗💨") 
        
        # Temp
        t = w.get('feels_like', w.get('temp', 20))
        if t < 5:
            comments.append("Брр! Надевай зимние перчатки! 🧤")
        elif 18 <= t <= 25 and w.get('pop', 0) < 10:
             comments.append("Идеально. Никаких отмазок! ✨")
             
        # Rain
        if w.get('pop', 0) > 50:
            comments.append("Не забудь крылья (и гряземесы)! 🌧")
            
        if not comments:
             comments.append("Погода норм. Крутим! 🚲")
             
        return " ".join(comments)

weather_service = WeatherService()
