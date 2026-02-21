import os
import time
import re
import aiohttp
from typing import Optional, Dict, Any
from transliterate import translit
from datetime import datetime
from zoneinfo import ZoneInfo

CLIENT_ID = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")

def sanitize_and_translit(text):
    # 1. Transliterate (Cyrillic -> Latin)
    try:
        text = translit(text, 'ru', reversed=True)
    except:
        pass 

    # 2. Sanitize
    text = text.replace(" ", "_")
    return re.sub(r'(?u)[^-\w.]', '', text)

class StravaService:
    def __init__(self, refresh_token):
        self.refresh_token = refresh_token
        self.access_token = None
        self.expires_at = 0
        self.base_url = "https://www.strava.com/api/v3"

    async def _ensure_token(self):
        """Checks if token is valid, if not, refreshes it using OAuth."""
        if time.time() < self.expires_at:
            return

        async with aiohttp.ClientSession() as session:
            url = "https://www.strava.com/oauth/token"
            data = {
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token
            }
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    raise Exception(f"Auth Failed: {await resp.text()}")
                
                token_data = await resp.json()
                self.access_token = token_data['access_token']
                self.expires_at = token_data['expires_at']

    async def _resolve_url(self, url: str) -> str:
        """Resolves short links like strava.app.link."""
        if "strava.app.link" in url:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, allow_redirects=True) as resp:
                    return str(resp.url)
        return url

    async def get_gpx(self, url: str) -> Dict[str, Any]:
        """
        Returns a dict with keys: 
        'data' (bytes), 'filename' (str), 'name' (str), 'distance' (float), 'elevation' (float), 'url' (str)
        """
        await self._ensure_token()
        url = await self._resolve_url(url)
        
        headers = {"Authorization": f"Bearer {self.access_token}"}
        
        async with aiohttp.ClientSession(headers=headers) as session:
            route_id = None
            
            # 1. Resolve ID
            if "group_events" in url:
                event_id = re.search(r'group_events/(\d+)', url).group(1)
                async with session.get(f"{self.base_url}/group_events/{event_id}") as resp:
                    if resp.status != 200: raise Exception("Event not found")
                    data = await resp.json()
                    route_id = data.get('route', {}).get('id')
            elif "routes" in url:
                match = re.search(r'routes/(\d+)', url)
                if match: route_id = match.group(1)
            elif "activities" in url:
                 # Bonus: Handle activities too if needed, but for now stick to routes
                 pass
            
            if not route_id:
                raise Exception("Could not find Route ID in link")

            # 2. Get Route Details (for Name, Distance, Elevation)
            route_name = f"route_{route_id}"
            distance = 0.0
            elevation = 0.0
            start_time = None
            
            # Additional fetch if it was a group event (to get time)
            if "group_events" in url:
                 event_id = re.search(r'group_events/(\d+)', url).group(1)
                 async with session.get(f"{self.base_url}/group_events/{event_id}") as resp:
                    if resp.status == 200:
                        evt_data = await resp.json()
                        # Parse time. Format usually: "2023-05-15T08:00:00Z" or similar
                        # But actually Strava returns 'upcoming_occurrences' for club events sometimes
                        # For simplicity, assuming the event object has 'upcoming_occurrences' or we use 'start_date_local' check
                        # In v3 API standard Group Event object has 'upcoming_occurrences': [{'start_date': '...'}]
                        # Or simple club event 'start_date'
                        
                        # Let's try to find a start date
                        occurrences = evt_data.get('upcoming_occurrences')
                        print(f"DEBUG: Occurrences: {occurrences}")
                        if occurrences:
                            first_occ = occurrences[0]
                            if isinstance(first_occ, str):
                                s_date = first_occ
                            elif hasattr(first_occ, 'get'):
                                s_date = first_occ.get('start_date')
                        else:
                            s_date = evt_data.get('start_date')
                            
                        # s_date = evt_data.get('upcoming_occurrences', [{}])[0].get('start_date') or evt_data.get('start_date')
                        if s_date:
                            start_time = datetime.fromisoformat(s_date.replace("Z", "+00:00"))

            start_lat, start_lon = None, None
            
            async with session.get(f"{self.base_url}/routes/{route_id}") as resp:
                if resp.status == 200:
                    r_data = await resp.json()
                    route_name = r_data.get('name', route_name)
                    distance = r_data.get('distance', 0) / 1000 # to km
                    elevation = r_data.get('elevation_gain', 0)
                    
                    # Geocoding
                    map_obj = r_data.get('map')
                    if map_obj and map_obj.get('summary_polyline'):
                        try:
                            import polyline
                            decoded = polyline.decode(map_obj['summary_polyline'])
                            if decoded:
                                start_lat, start_lon = decoded[0]
                        except Exception as e:
                            print(f"Polyline Error: {e}")
                    
                elif resp.status == 403:
                    raise Exception("Route is private (403)")
                elif resp.status == 404:
                    raise Exception("Route not found (404)")
                else:
                    raise Exception(f"Route fetch failed ({resp.status})")

            # 3. Download GPX
            # Also extract Lat/Lon from Polyline if available (needed for Weather)
            lat, lon = None, None
            try:
                import polyline
                # Try getting summary polyline from route data ('map' object)
                # We need to re-read 'r_data' ... not available here directly.
                # Refactoring logic: We capture start_lat/start_lon in step 2.
                print(f"DEBUG: Start Lat/Lon from Step 2: {start_lat}, {start_lon}")
            except ImportError:
                 print("DEBUG: Polyline module not found!")
            except Exception as e:
                print(f"DEBUG: Geocoding Error: {e}")

            filename = f"{sanitize_and_translit(route_name)}.gpx"
            export_url = f"{self.base_url}/routes/{route_id}/export_gpx"
            
            async with session.get(export_url) as resp:
                 if resp.status == 200:
                    return {
                        "data": await resp.read(),
                        "filename": filename,
                        "name": route_name,
                        "distance": distance,
                        "elevation": elevation,
                        "url": f"https://www.strava.com/routes/{route_id}",
                        "start_time": start_time,
                        "strava_event_id": event_id if "group_events" in url else None,
                        "lat": start_lat,
                        "lon": start_lon
                    }
                 elif resp.status in (403, 404):
                    raise Exception(f"GPX download blocked ({resp.status})")
                 else:
                    raise Exception(f"GPX Download Failed: {resp.status}")

