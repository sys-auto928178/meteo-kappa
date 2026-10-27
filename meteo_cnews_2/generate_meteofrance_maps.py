import os
import re
import json
import urllib.request
import urllib.parse
import subprocess
import time
import http.server
import socketserver
import threading
import base64
import csv
import shutil
import argparse
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python 3.8 fallback

PARIS_TZ = ZoneInfo("Europe/Paris")

def utc_to_paris_local(iso_str):
    """Convert an ISO datetime string (UTC, no 'Z' suffix) to Europe/Paris local time ISO string."""
    if not iso_str:
        return iso_str
    try:
        dt_utc = datetime.fromisoformat(iso_str).replace(tzinfo=ZoneInfo("UTC"))
        dt_local = dt_utc.astimezone(PARIS_TZ)
        return dt_local.strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return iso_str

# 1. Configuration of paths
CHROME_PATH = os.environ.get("CHROME_PATH") or (
    "/usr/bin/google-chrome" if os.name != 'nt' else r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
DEST_DIR = r"C:\Users\grego\Desktop\cartes_alertes"
if not os.path.exists(DEST_DIR):
    DEST_DIR = os.path.abspath(os.path.join(PROJECT_DIR, "..", "cartes_alertes"))

# Fallback master cities list for france_pictos (in case of index.html parsing issue)
FALLBACK_CITIES = [
    { "name": "Brest", "lat": 48.39, "lon": -4.48 },
    { "name": "Rennes", "lat": 48.11, "lon": -1.67 },
    { "name": "Cherbourg", "lat": 49.63, "lon": -1.62 },
    { "name": "Rouen", "lat": 49.44, "lon": 1.10 },
    { "name": "Paris", "lat": 48.85, "lon": 2.35 },
    { "name": "Lille", "lat": 50.62, "lon": 3.05 },
    { "name": "Boulogne-sur-Mer", "lat": 50.726, "lon": 1.614 },
    { "name": "Reims", "lat": 49.25, "lon": 4.03 },
    { "name": "Metz", "lat": 49.11, "lon": 6.17 },
    { "name": "Nantes", "lat": 47.21, "lon": -1.55 },
    { "name": "Tours", "lat": 47.39, "lon": 0.68 },
    { "name": "Auxerre", "lat": 47.79, "lon": 3.57 },
    { "name": "Chaumont", "lat": 48.11, "lon": 5.14 },
    { "name": "Strasbourg", "lat": 48.57, "lon": 7.75 },
    { "name": "Bourges", "lat": 47.08, "lon": 2.39 },
    { "name": "Belfort", "lat": 47.63, "lon": 6.86 },
    { "name": "Limoges", "lat": 45.83, "lon": 1.26 },
    { "name": "Vichy", "lat": 46.12, "lon": 3.42 },
    { "name": "Lyon", "lat": 45.76, "lon": 4.83 },
    { "name": "Pontarlier", "lat": 46.90, "lon": 6.35 },
    { "name": "La Rochelle", "lat": 46.16, "lon": -1.15 },
    { "name": "Bordeaux", "lat": 44.83, "lon": -0.57 },
    { "name": "Biarritz", "lat": 43.48, "lon": -1.56 },
    { "name": "Tarbes", "lat": 43.23, "lon": 0.07 },
    { "name": "Toulouse", "lat": 43.60, "lon": 1.44 },
    { "name": "Aurillac", "lat": 44.92, "lon": 2.44 },
    { "name": "Montélimar", "lat": 44.55, "lon": 4.75 },
    { "name": "Gap", "lat": 44.55, "lon": 6.07 },
    { "name": "Perpignan", "lat": 42.69, "lon": 2.89 },
    { "name": "Montpellier", "lat": 43.61, "lon": 3.87 },
    { "name": "Marseille", "lat": 43.296, "lon": 5.381 },
    { "name": "Amiens", "lat": 49.894, "lon": 2.295 },
    { "name": "Nice", "lat": 43.71, "lon": 7.26 },
    { "name": "Ajaccio", "lat": 41.92, "lon": 8.73 },
    { "name": "Bastia", "lat": 42.69, "lon": 9.45 },
    { "name": "Alençon", "lat": 48.43, "lon": 0.09 },
    { "name": "Bourg-St-Maurice", "lat": 45.62, "lon": 6.77 },
    { "name": "Chalon/Saône", "lat": 46.78, "lon": 4.85 },
    { "name": "Agen", "lat": 44.20, "lon": 0.61 }
]

def get_zone_cities(zone_key):
    """Dynamically parses index.html to extract the cities list for a given zone."""
    if zone_key == "grandest":
        zone_key = "grand-est"
    index_path = os.path.join(PROJECT_DIR, "index.html")
    if not os.path.exists(index_path):
        return None
    try:
        with open(index_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        # Locate the start of the zone configuration
        start_pos = content.find(f"{zone_key}: {{")
        if start_pos == -1:
            start_pos = content.find(f"'{zone_key}': {{")
        if start_pos == -1:
            start_pos = content.find(f'"{zone_key}": {{')
            
        if start_pos == -1:
            return None
            
        cities_start = content.find("cities: [", start_pos)
        if cities_start == -1:
            return None
        
        # Match closing bracket of cities array
        bracket_count = 1
        pos = cities_start + len("cities: [")
        while pos < len(content) and bracket_count > 0:
            if content[pos] == '[':
                bracket_count += 1
            elif content[pos] == ']':
                bracket_count -= 1
            pos += 1
            
        cities_str = content[cities_start + len("cities: [") : pos - 1]
        
        # Regex to parse objects like { name: "...", lat: ..., lon: ... }
        pattern = r'\{\s*name:\s*["\']([^"\']+)["\']\s*,\s*lat:\s*([0-9.-]+)\s*,\s*lon:\s*([0-9.-]+)'
        matches = re.findall(pattern, cities_str)
        
        cities = []
        for name, lat, lon in matches:
            cities.append({
                "name": name,
                "lat": float(lat),
                "lon": float(lon)
            })
        return cities if cities else None
    except Exception as e:
        print(f"Error dynamically parsing zone cities: {e}")
        return None

def rot13(s):
    res = []
    for c in s:
        if 'a' <= c <= 'z':
            res.append(chr(97 + (ord(c) - 97 + 13) % 26))
        elif 'A' <= c <= 'Z':
            res.append(chr(65 + (ord(c) - 65 + 13) % 26))
        else:
            res.append(c)
    return "".join(res)

def get_session_token():
    print("Connecting to Météo-France to retrieve session token...")
    url = "https://vigilance.meteofrance.fr/fr"
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    
    mfsession = None
    try:
        with urllib.request.urlopen(req) as response:
            headers = response.getheaders()
            for header, value in headers:
                if header.lower() == 'set-cookie' and 'mfsession=' in value:
                    m = re.search(r'mfsession=([^;]+)', value)
                    if m:
                        mfsession = m.group(1)
                        break
    except Exception as e:
        print("Error fetching main page:", e)
        return None
        
    if not mfsession:
        print("Could not find mfsession cookie.")
        return None
        
    return rot13(urllib.parse.unquote(mfsession))

def map_mf_icon(icon):
    if not icon:
        return 0
    icon = icon.lower().strip()
    if icon.endswith('j') or icon.endswith('n'):
        icon_prefix = icon[:-1]
    else:
        icon_prefix = icon
        
    mapping = {
        'p1': 0,    # Soleil -> P1 Soleil
        'p1bis': 1, # Peu nuageux -> P2
        'p2': 2,    # Éclaircies -> P8 Nuageux
        'p3': 2,    # Variable ou Nuageux -> P8 Nuageux
        'p4': 5,    # Ciel voilé -> P6 Soleil voilé
        'p5': 3,    # Très Nuageux, Courtes Éclaircies -> P4 Très nuageux
        'p6': 4,    # Couvert -> P5 Couvert
        'p6bis': 12, # Bancs de brouillard
        'p7': 6,    # Variable avec Averses -> P9 Averses
        'p8': 7,    # Couvert, Bruines ou Pluies -> P10 Pluie faible
        'p9': 8,    # Couvert, Pluies Modérées/fortes -> P11 Pluie forte
        'p10': 7,   # Couvert, Bruine / Pluie faible -> P10 Pluie faible
        'p11': 6,   # Variable, Averses -> P9 Averses
        'p12': 6,   # Averses -> P9 Averses
        'p12bis': 6, # Averses faibles -> P9 Averses
        'p13': 6,   # Pluies éparses -> P9 Averses
        'p14': 7,   # Pluie -> P10 Pluie faible
        'p14bis': 6, # Averses -> P9 Averses
        'p15': 12,  # Brumes ou Brouillards -> brouillards
        'p16': 12,  # Brouillards Givrants -> brouillards
        'p16bis': 10, # Averses orageuses -> Orages (Corrected from 12 to 10)
        'p17': 12,  # Verglas -> brouillards
        'p18': 9,   # Neige faible -> P12 Neige
        'p19': 9,   # Neige modérée -> P12 Neige
        'p20': 9,   # Neige forte -> P12 Neige
        'p21': 9,   # Averses de neige -> P12 Neige
        'p22': 9,   # Pluie et neige mêlées -> P12 Neige
        'p23': 9,   # Averses de neige mêlée -> P12 Neige
        'p26': 10,  # Orages -> orages
        'p27': 10,  # Orages -> orages
        'p28': 10,  # Orages -> orages
        'p29': 10,  # Orages -> orages
        'p30': 10,  # Orages -> orages
    }
    
    if icon_prefix in mapping:
        return mapping[icon_prefix]
        
    fallback_prefix = icon_prefix.replace('bis', '')
    return mapping.get(fallback_prefix, 0)

def fetch_city_forecast(token, lat, lon):
    import time
    cb = int(time.time() * 1000)
    url = f"https://rwg.meteofrance.com/internet2018client/2.0/forecast?lat={lat}&lon={lon}&token={token}&_={cb}"
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching forecast for lat={lat}, lon={lon}: {e}")
        return None

def fetch_openmeteo_gusts(lat, lon, start_tomorrow=False):
    """Fetch wind_gusts_10m hourly from Open-Meteo (free, no auth, 16-day forecast).
    Returns a list of 192 floats (km/h) aligned to the same hourly grid as build_openmeteo_mock.
    Falls back to None on any error so the caller can keep the MF value.
    """
    import time
    now = datetime.now()
    start_dt = datetime(now.year, now.month, now.day) + timedelta(days=(1 if start_tomorrow else 0))
    end_dt = start_dt + timedelta(hours=191)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=wind_gusts_10m"
        f"&wind_speed_unit=kmh"
        f"&timezone=Europe%2FParis"
        f"&start_date={start_str}&end_date={end_str}"
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        gusts = data.get('hourly', {}).get('wind_gusts_10m', [])
        # Pad or truncate to exactly 192 entries
        if len(gusts) >= 192:
            return [float(v or 0) for v in gusts[:192]]
        return [float(v or 0) for v in gusts] + [0.0] * (192 - len(gusts))
    except Exception as e:
        print(f"  [open-meteo gusts] fetch failed for lat={lat},lon={lon}: {e}")
        return None


def fetch_all_openmeteo_gusts(cities_list, start_tomorrow=False):
    """Fetch wind_gusts_10m hourly for all cities in a single batch request to Open-Meteo."""
    if not cities_list:
        return {}
    
    import json
    import urllib.request
    from datetime import datetime, timedelta
    
    now = datetime.now()
    start_dt = datetime(now.year, now.month, now.day) + timedelta(days=(1 if start_tomorrow else 0))
    end_dt = start_dt + timedelta(hours=191)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    
    lats = ",".join(str(c['lat']) for c in cities_list)
    lons = ",".join(str(c['lon']) for c in cities_list)
    
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lats}&longitude={lons}"
        f"&hourly=wind_gusts_10m"
        f"&wind_speed_unit=kmh"
        f"&timezone=Europe%2FParis"
        f"&start_date={start_str}&end_date={end_str}"
    )
    
    gusts_map = {}
    print(f"  [open-meteo gusts] Batch fetching gusts for {len(cities_list)} locations...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        
        results = data if isinstance(data, list) else [data]
        for idx, r in enumerate(results):
            city = cities_list[idx]
            gusts = r.get('hourly', {}).get('wind_gusts_10m', [])
            
            if len(gusts) >= 192:
                final_gusts = [float(v or 0) for v in gusts[:192]]
            else:
                final_gusts = [float(v or 0) for v in gusts] + [0.0] * (192 - len(gusts))
            
            key = f"{round(float(city['lat']), 2)}_{round(float(city['lon']), 2)}"
            gusts_map[key] = final_gusts
        print(f"  [open-meteo gusts] Batch fetch successful for {len(results)} locations.")
            
    except Exception as e:
        print(f"  [open-meteo gusts] Batch fetch failed: {e}")
        
    return gusts_map


def build_openmeteo_mock(mf_data, start_tomorrow=False, om_gusts=None):
    if not mf_data or 'properties' not in mf_data:
        return None
        
    prop = mf_data['properties']
    forecasts = prop.get('forecast', [])
    daily_forecasts = prop.get('daily_forecast', [])
    
    now = datetime.now()
    start_of_today = datetime(now.year, now.month, now.day, 0, 0, 0)
    if start_tomorrow:
        start_of_today += timedelta(days=1)
    
    hourly_times = []
    hourly_temp = []
    hourly_wc = []
    hourly_ws = []
    hourly_wg = []
    hourly_precip = []
    hourly_clouds = []
    
    for h in range(192):
        target_dt = start_of_today + timedelta(hours=h)
        target_iso = target_dt.strftime("%Y-%m-%dT%H:00")
        hourly_times.append(target_iso)
        
        match_item = None
        min_diff = timedelta(hours=2)
        for item in forecasts:
            item_time_str = item['time'].replace('Z', '')
            try:
                item_time_str = item_time_str.split('.')[0]
                item_dt = datetime.fromisoformat(item_time_str)
                diff = abs(target_dt - item_dt)
                if diff < min_diff:
                    min_diff = diff
                    match_item = item
            except Exception:
                pass
                
        if match_item:
            hourly_temp.append(match_item.get('T', 15))
            hourly_wc.append(map_mf_icon(match_item.get('weather_icon', 'p1j')))
            hourly_ws.append(match_item.get('wind_speed', 0))
            # ponytail: gust overridden by Open-Meteo below if available
            hourly_wg.append(match_item.get('wind_speed_gust', 0))
            hourly_precip.append(match_item.get('rain_1h', 0) or 0)
            hourly_clouds.append(match_item.get('total_cloud_cover', 0) or 0)
        else:
            day_idx = h // 24
            hour_of_day = h % 24
            
            lookup_idx = day_idx + (1 if start_tomorrow else 0)
            daily_item = None
            if lookup_idx < len(daily_forecasts):
                daily_item = daily_forecasts[lookup_idx]
                
            t_min = daily_item.get('T_min', 12) if daily_item else 12
            t_max = daily_item.get('T_max', 22) if daily_item else 22
            daily_icon = daily_item.get('daily_weather_icon', 'p1j') if daily_item else 'p1j'
            
            if hour_of_day <= 6:
                est_temp = t_min
            elif hour_of_day <= 15:
                pct = (hour_of_day - 6) / 9
                est_temp = t_min + (t_max - t_min) * pct
            else:
                pct = (hour_of_day - 15) / 9
                est_temp = t_max - (t_max - t_min) * pct
                
            hourly_temp.append(round(est_temp, 1))
            hourly_wc.append(map_mf_icon(daily_icon))
            hourly_ws.append(5)
            hourly_wg.append(0)  # will be replaced by Open-Meteo below
            hourly_precip.append(0)
            hourly_clouds.append(0)
            
    daily_times = []
    daily_min_temp = []
    daily_max_temp = []
    daily_sunrise = []
    daily_sunset = []
    
    for d in range(8):
        target_date = start_of_today + timedelta(days=d)
        date_str = target_date.strftime("%Y-%m-%d")
        daily_times.append(date_str)
        
        match_daily = None
        for item in daily_forecasts:
            item_time_str = item['time'].split('T')[0]
            if item_time_str == date_str:
                match_daily = item
                break
                
        if match_daily:
            daily_min_temp.append(match_daily.get('T_min', 10))
            daily_max_temp.append(match_daily.get('T_max', 20))
            
            sunrise_str = match_daily.get('sunrise_time')
            if sunrise_str:
                daily_sunrise.append(utc_to_paris_local(sunrise_str.replace('Z', '')))
            else:
                daily_sunrise.append(f"{date_str}T06:00:00")
                
            sunset_str = match_daily.get('sunset_time')
            if sunset_str:
                daily_sunset.append(utc_to_paris_local(sunset_str.replace('Z', '')))
            else:
                daily_sunset.append(f"{date_str}T21:00:00")
        else:
            day_slice = hourly_temp[d*24 : (d+1)*24]
            daily_min_temp.append(min(day_slice) if day_slice else 10)
            daily_max_temp.append(max(day_slice) if day_slice else 20)
            daily_sunrise.append(f"{date_str}T06:00:00")
            daily_sunset.append(f"{date_str}T21:00:00")
            
    coords = mf_data.get("geometry", {}).get("coordinates", [2.35, 48.85])
    mock_obj = {
        "latitude": coords[1],
        "longitude": coords[0],
        "timezone": "Europe/Paris",
        "hourly": {
            "time": hourly_times,
            "temperature_2m": hourly_temp,
            "weathercode": hourly_wc,
            "windspeed_10m": hourly_ws,
            "wind_gusts_10m": om_gusts if om_gusts else hourly_wg,
            "precipitation": hourly_precip,
            "relativehumidity_2m": [50] * 192,
            "pressure_msl": [1013] * 192,
            "winddirection_10m": [180] * 192,
            "cloud_cover": hourly_clouds,
            "cloud_cover_low": [0] * 192,
            "cloud_cover_mid": [0] * 192,
            "cloud_cover_high": [0] * 192
        },
        "daily": {
            "time": daily_times,
            "temperature_2m_min": daily_min_temp,
            "temperature_2m_max": daily_max_temp,
            "sunrise": daily_sunrise,
            "sunset": daily_sunset
        }
    }
    return mock_obj

save_done_event = threading.Event()
saved_image_data = {}

class DualHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
        
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        global saved_image_data
        if self.path == '/save_map':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                payload = json.loads(post_data.decode('utf-8'))
                img_b64 = payload['image'].split(',')[1]
                day = payload['day']
                period = payload['period']
                
                saved_image_data = {
                    'data': base64.b64decode(img_b64),
                    'day': day,
                    'period': period
                }
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                
                save_done_event.set()
            except Exception as e:
                print("Error receiving image on server:", e)
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run_local_server():
    os.chdir(PROJECT_DIR)
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("127.0.0.1", 8001), DualHandler) as httpd:
            httpd.serve_forever()
    except Exception as e:
        pass

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Générateur de cartes météo pour CNews")
    parser.add_argument("--zone", type=str, default="france_pictos", help="Code de la zone (ex: france_pictos, hdf, paca...)")
    parser.add_argument("--days", type=int, default=None, help="Nombre de jours à générer (ex: 3 pour J+2, 8 pour J+7)")
    parser.add_argument("--orientation", type=str, default="landscape", choices=["landscape", "portrait", "square"], help="Orientation de capture de la carte (landscape, portrait ou square)")
    
    now = datetime.now()
    default_start_tomorrow = True
    parser.add_argument("--start-tomorrow", action="store_true", default=default_start_tomorrow, help="Commencer les prévisions à partir de demain au lieu d'aujourd'hui")
    parser.add_argument("--temp-highlight", action="store_true", help="Mise en avant min/max des températures (bleu min, rouge max, noir pour le reste)")
    parser.add_argument("--patrick", action="store_true", help="Générer les cartes spécifiques pour le Bulletin Patrick")
    args = parser.parse_args()

    zone_key = args.zone
    if zone_key == "grandest":
        zone_key = "grand-est"
    days_to_capture = args.days
    start_tomorrow = args.start_tomorrow
    orientation = args.orientation
    temp_highlight = args.temp_highlight
    patrick_mode = args.patrick
    if patrick_mode:
        temp_highlight = True
    
    # By default: 8 days for france_pictos (national), 3 days (J0, J1, J2) for regional maps
    if days_to_capture is None:
        if patrick_mode:
            days_to_capture = 5
        else:
            days_to_capture = 8 if zone_key == "france_pictos" else 3
    elif patrick_mode:
        days_to_capture = max(days_to_capture, 5)

    json_filename = f"meteofrance_data_{zone_key}.json" if zone_key != "france_pictos" else "meteofrance_data.json"
    JSON_OUT_PATH = os.path.join(PROJECT_DIR, json_filename)

    print(f"Target Zone: {zone_key}")
    print(f"Days to capture: {days_to_capture} (J+{(days_to_capture-1)})")

    # Extract cities for this zone from index.html
    cities_list = get_zone_cities(zone_key)
    if not cities_list:
        print(f"Warning: Could not parse cities for zone '{zone_key}' from index.html. Falling back to default.")
        cities_list = FALLBACK_CITIES

    if not os.path.exists(DEST_DIR):
        os.makedirs(DEST_DIR)

    chrome_profile_dir = os.path.join(PROJECT_DIR, f".chrome_profile_{os.getpid()}")

    token = get_session_token()
    if not token:
        print("Aborting: Could not fetch active Météo-France session token.")
        return

    # Ephemeris city: national = Paris, regional = chef-lieu of the region
    ZONE_EPHEMERIS_CITY = {
        "france_pictos": {"name": "Paris",      "lat": 48.8566, "lon":  2.3522},
        "hdf":           {"name": "Lille",       "lat": 50.6292, "lon":  3.0573},
        "normandie":     {"name": "Rouen",       "lat": 49.4432, "lon":  1.0993},
        "idf":           {"name": "Paris",       "lat": 48.8566, "lon":  2.3522},
        "grandest":      {"name": "Strasbourg",  "lat": 48.5734, "lon":  7.7521},
        "ara":           {"name": "Lyon",        "lat": 45.7640, "lon":  4.8357},
        "naq":           {"name": "Bordeaux",    "lat": 44.8378, "lon": -0.5792},
        "occitanie":     {"name": "Toulouse",    "lat": 43.6047, "lon":  1.4442},
        "paca":          {"name": "Marseille",   "lat": 43.2965, "lon":  5.3698},
        "bfc":           {"name": "Dijon",       "lat": 47.3220, "lon":  5.0415},
        "bretagne":      {"name": "Rennes",      "lat": 48.1173, "lon": -1.6778},
        "pdl":           {"name": "Nantes",      "lat": 47.2184, "lon": -1.5536},
        "cvl":           {"name": "Orléans",     "lat": 47.9029, "lon":  1.9092},
        "corse":         {"name": "Ajaccio",     "lat": 41.9192, "lon":  8.7386},
    }
    eph_city = ZONE_EPHEMERIS_CITY.get(zone_key, {"name": "Paris", "lat": 48.8566, "lon": 2.3522})
    eph_in_list = any(abs(c.get('lat', 0) - eph_city['lat']) < 0.05 and abs(c.get('lon', 0) - eph_city['lon']) < 0.05 for c in cities_list)

    # Pre-fetch all gusts in a single batch request
    all_cities_for_gusts = list(cities_list)
    if not eph_in_list:
        all_cities_for_gusts.append(eph_city)
    all_gusts = fetch_all_openmeteo_gusts(all_cities_for_gusts, start_tomorrow=start_tomorrow)

    # Fetch forecasts for all zone cities
    print(f"Fetching Météo-France forecasts for {len(cities_list)} cities...")
    weather_data_list = []
    
    for i, city in enumerate(cities_list):
        print(f" [{i+1}/{len(cities_list)}] {city['name']}...")
        mf_json = fetch_city_forecast(token, city['lat'], city['lon'])
        
        # Read pre-fetched gusts from batch map
        key = f"{round(float(city['lat']), 2)}_{round(float(city['lon']), 2)}"
        om_gusts = all_gusts.get(key)
        
        if mf_json:
            mock = build_openmeteo_mock(mf_json, start_tomorrow=start_tomorrow, om_gusts=om_gusts)
            if mock:
                weather_data_list.append(mock)
            else:
                print(f"Warning: Failed to format forecast for {city['name']}.")
                weather_data_list.append(None)
        else:
            print(f"Warning: Failed to fetch forecast for {city['name']}.")
            weather_data_list.append(None)
            
    if not eph_in_list:
        print(f" [0/{len(cities_list)}] {eph_city['name']} (éphéméride)...")
        eph_mf = fetch_city_forecast(token, eph_city['lat'], eph_city['lon'])
        
        key = f"{round(float(eph_city['lat']), 2)}_{round(float(eph_city['lon']), 2)}"
        eph_gusts = all_gusts.get(key)
        
        eph_mock = build_openmeteo_mock(eph_mf, start_tomorrow=start_tomorrow, om_gusts=eph_gusts) if eph_mf else None
        if eph_mock:
            eph_mock['ephemeris_city'] = eph_city['name']
            weather_data_list.insert(0, eph_mock)
    else:
        # City already in list — move it to position 0 and tag it
        for idx, w in enumerate(weather_data_list):
            if w and abs(w.get('latitude', 0) - eph_city['lat']) < 0.05 and abs(w.get('longitude', 0) - eph_city['lon']) < 0.05:
                w['ephemeris_city'] = eph_city['name']
                weather_data_list.insert(0, weather_data_list.pop(idx))
                break

    # Write to local file for index.html to read
    with open(JSON_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(weather_data_list, f, indent=2)
    print(f"Successfully generated weather data file: {JSON_OUT_PATH}")

    # Generate daily CSV
    daily_csv_path = os.path.join(PROJECT_DIR, f"meteofrance_daily_forecast_{zone_key}.csv" if zone_key != "france_pictos" else "meteofrance_daily_forecast.csv")
    daily_csv_dest = os.path.join(DEST_DIR, f"meteofrance_daily_forecast_{zone_key}.csv" if zone_key != "france_pictos" else "meteofrance_daily_forecast.csv")
    print(f"Generating daily CSV: {daily_csv_path}")
    
    cat_labels = {
        0: 'SOLEIL', 1: 'PEU NUAGEUX', 2: 'NUAGEUX', 3: 'TRÈS NUAGEUX',
        4: 'COUVERT', 5: 'SOLEIL VOILÉ', 6: 'AVERSES', 7: 'PLUIE FAIBLE',
        8: 'PLUIE FORTE', 9: 'NEIGE', 10: 'ORAGEUX', 11: 'ORAGES & GRÊLE', 12: 'BROUILLARD'
    }
    
    try:
        with open(daily_csv_path, "w", newline="", encoding="utf-8-sig") as f_csv:
            writer = csv.writer(f_csv, delimiter=";")
            writer.writerow(["Ville", "Latitude", "Longitude", "Date", "weathercode", "Temps_Label", "temperature_2m_max", "temperature_2m_min"])
            for idx, city in enumerate(cities_list):
                mock = weather_data_list[idx]
                if not mock:
                    continue
                csv_days = min(len(mock["daily"]["time"]), max(7, days_to_capture))
                for d in range(csv_days):
                    date_str = mock["daily"]["time"][d]
                    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    date_fmt = dt_obj.strftime("%d/%m/%Y")
                    code = mock["daily"].get("weathercode", [0]*8)[d] if "weathercode" in mock["daily"] else mock["hourly"]["weathercode"][d*24 + 12]
                    writer.writerow([
                        city["name"],
                        city["lat"],
                        city["lon"],
                        date_fmt,
                        code,
                        cat_labels.get(code, "SOLEIL"),
                        mock["daily"]["temperature_2m_max"][d],
                        mock["daily"]["temperature_2m_min"][d]
                    ])
        try:
            shutil.copy(daily_csv_path, daily_csv_dest)
        except PermissionError:
            print(f"Warning: {daily_csv_dest} is open. Saving copy suffix _NEW.csv")
            shutil.copy(daily_csv_path, daily_csv_dest.replace(".csv", "_NEW.csv"))
    except Exception as e:
        print("Failed to write daily CSV:", e)

    # Generate hourly CSV
    hourly_csv_path = os.path.join(PROJECT_DIR, f"meteofrance_hourly_forecast_{zone_key}.csv" if zone_key != "france_pictos" else "meteofrance_hourly_forecast.csv")
    hourly_csv_dest = os.path.join(DEST_DIR, f"meteofrance_hourly_forecast_{zone_key}.csv" if zone_key != "france_pictos" else "meteofrance_hourly_forecast.csv")
    print(f"Generating hourly CSV: {hourly_csv_path}")
    
    try:
        with open(hourly_csv_path, "w", newline="", encoding="utf-8-sig") as f_csv:
            writer = csv.writer(f_csv, delimiter=";")
            writer.writerow(["Ville", "Latitude", "Longitude", "Date", "Heure", "temperature_2m", "weathercode", "Temps_Label", "windspeed_10m", "wind_gusts_10m", "precipitation"])
            for idx, city in enumerate(cities_list):
                mock = weather_data_list[idx]
                if not mock:
                    continue
                for h in range(days_to_capture * 24):
                    dt_str = mock["hourly"]["time"][h]
                    dt_obj = datetime.fromisoformat(dt_str)
                    date_fmt = dt_obj.strftime("%d/%m/%Y")
                    time_fmt = dt_obj.strftime("%H:%M")
                    code = mock["hourly"]["weathercode"][h]
                    writer.writerow([
                        city["name"],
                        city["lat"],
                        city["lon"],
                        date_fmt,
                        time_fmt,
                        mock["hourly"]["temperature_2m"][h],
                        code,
                        cat_labels.get(code, "SOLEIL"),
                        mock["hourly"]["windspeed_10m"][h],
                        mock["hourly"]["wind_gusts_10m"][h],
                        mock["hourly"]["precipitation"][h]
                    ])
        try:
            shutil.copy(hourly_csv_path, hourly_csv_dest)
        except PermissionError:
            print(f"Warning: {hourly_csv_dest} is open. Saving copy suffix _NEW.csv")
            shutil.copy(hourly_csv_path, hourly_csv_dest.replace(".csv", "_NEW.csv"))
    except Exception as e:
        print("Failed to write hourly CSV:", e)
        
    print("CSV files generated successfully.")

    # 2. Start local HTTP server
    server_thread = threading.Thread(target=run_local_server, daemon=True)
    server_thread.start()
    print("Local Dual File/Capture server started on port 8001.")
    time.sleep(2.0)

    # 3. Render maps
    if patrick_mode:
        renders = [
            (0, 'morning', 'matin', 'weather_temp'),
            (0, 'afternoon', 'apresmidi', 'weather_temp'),
            (0, 'day', 'precip', 'precip'),
            (0, 'day', 'gusts', 'gusts'),
            (1, 'afternoon', 'apresmidi', 'weather_temp'),
            (2, 'afternoon', 'apresmidi', 'weather_temp'),
            (3, 'afternoon', 'apresmidi', 'weather_temp'),
            (4, 'afternoon', 'apresmidi', 'weather_temp')
        ]
    else:
        periods = {
            'morning': 'matin',
            'afternoon': 'apresmidi'
        }
        renders = []
        for day in range(days_to_capture):
            for period_key, period_name in periods.items():
                renders.append((day, period_key, period_name, 'weather_temp'))
                
    total_renders = len(renders)
    current_render = 0
    
    print("\nStarting automated map rendering...")
    use_playwright_turbo = False
    try:
        from playwright.sync_api import sync_playwright
        use_playwright_turbo = True
    except ImportError:
        pass

    if use_playwright_turbo:
        try:
            print("⚡ [Mode Turbo] Démarrage session Playwright réutilisable (zéro cold-start)...")
            with sync_playwright() as p:
                w_val = 1080 if orientation in ["portrait", "square"] else 1920
                h_val = 1920 if orientation == "portrait" else 1080
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--mute-audio",
                        "--hide-scrollbars",
                        "--disable-background-networking"
                    ]
                )
                context = browser.new_context(viewport={"width": w_val, "height": h_val})
                page = context.new_page()

                for day, period_key, period_name, param in renders:
                    actual_day = day + 1 if start_tomorrow else day
                    current_render += 1
                    print(f"[{current_render}/{total_renders}] Rendering J{actual_day} - {period_name.upper()} ({param})...")
                    
                    target_url = f"http://127.0.0.1:8001/index.html?headless=true&use_mf=true&day={day}&period={period_key}&zone={zone_key}&param={param}&auto_save=true"
                    if temp_highlight:
                        target_url += "&highlight=true"
                    if orientation != 'landscape':
                        target_url += f"&orientation={orientation}"
                        
                    save_done_event.clear()
                    try:
                        page.goto(target_url, wait_until="commit", timeout=15000)
                    except Exception as ge:
                        pass
                    success = save_done_event.wait(timeout=30)
                    
                    if success and saved_image_data:
                        suffix = f"_{orientation}" if orientation != "landscape" else ""
                        filename = f"carte_{zone_key}_J{actual_day}_{period_name}{suffix}.jpg" if zone_key != "france_pictos" else f"carte_J{actual_day}_{period_name}{suffix}.jpg"
                        filepath = os.path.join(DEST_DIR, filename)
                        with open(filepath, 'wb') as f_img:
                            f_img.write(saved_image_data['data'])
                        print(f"   -> Saved: {filepath}")
                    else:
                        print(f"   -> Error: Timeout waiting for render of J{actual_day} {period_name}.")

                # Ephemeride
                print(f"\n[{total_renders+1}/{total_renders+1}] Rendering J0 - EPHEMERIDE...")
                target_url = f"http://127.0.0.1:8001/index.html?headless=true&use_mf=true&day=0&period=ephemeride&zone={zone_key}&auto_save=true"
                if orientation != 'landscape':
                    target_url += f"&orientation={orientation}"
                save_done_event.clear()
                try:
                    page.goto(target_url, wait_until="commit", timeout=15000)
                except Exception as ge:
                    pass
                success = save_done_event.wait(timeout=30)
                if success and saved_image_data:
                    suffix = f"_{orientation}" if orientation != "landscape" else ""
                    filename = f"carte_{zone_key}_ephemeride{suffix}.jpg" if zone_key != "france_pictos" else f"carte_ephemeride{suffix}.jpg"
                    filepath = os.path.join(DEST_DIR, filename)
                    with open(filepath, 'wb') as f_img:
                        f_img.write(saved_image_data['data'])
                    print(f"   -> Saved: {filepath}")
                else:
                    print("   -> Error: Timeout waiting for render of ephemeride.")
                browser.close()
        except Exception as e:
            print(f"Playwright pool exception: {e}, falling back to standard Chrome launcher...")
            use_playwright_turbo = False

    if not use_playwright_turbo:
        for day, period_key, period_name, param in renders:
            actual_day = day + 1 if start_tomorrow else day
            current_render += 1
            print(f"[{current_render}/{total_renders}] Rendering J{actual_day} - {period_name.upper()} ({param})...")
            
            target_url = f"http://127.0.0.1:8001/index.html?headless=true&use_mf=true&day={day}&period={period_key}&zone={zone_key}&param={param}&auto_save=true"
            if temp_highlight:
                target_url += "&highlight=true"
            if orientation != 'landscape':
                target_url += f"&orientation={orientation}"
                
            save_done_event.clear()
            
            w_size = "1080,1920" if orientation == "portrait" else ("1080,1080" if orientation == "square" else "1920,1080")
            args = [
                CHROME_PATH,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--window-size={w_size}",
                "--force-device-scale-factor=1",
                "--mute-audio",
                "--hide-scrollbars",
                "--enable-logging",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-first-run",
                "--disable-extensions",
                f"--user-data-dir={chrome_profile_dir}_{current_render}",
                target_url
            ]
            
            creationflags = 0x08000000 if os.name == 'nt' else 0
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
            
            def log_chrome(p):
                try:
                    for line in p.stderr:
                        print("  [Chrome Err]", line.decode('utf-8', errors='ignore').strip())
                except:
                    pass
            threading.Thread(target=log_chrome, args=(proc,), daemon=True).start()
            
            success = save_done_event.wait(timeout=45)
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                
            if success and saved_image_data:
                suffix = f"_{orientation}" if orientation != "landscape" else ""
                filename = f"carte_{zone_key}_J{actual_day}_{period_name}{suffix}.jpg" if zone_key != "france_pictos" else f"carte_J{actual_day}_{period_name}{suffix}.jpg"
                filepath = os.path.join(DEST_DIR, filename)
                with open(filepath, 'wb') as f_img:
                    f_img.write(saved_image_data['data'])
                print(f"   -> Saved: {filepath}")
            else:
                print(f"   -> Error: Timeout waiting for render of J{actual_day} {period_name}.")
                
            time.sleep(0.5)

        # 4. Render ephemeris card (J0) at the end of the bulletin
        print(f"\n[{total_renders+1}/{total_renders+1}] Rendering J0 - EPHEMERIDE...")
        target_url = f"http://127.0.0.1:8001/index.html?headless=true&use_mf=true&day=0&period=ephemeride&zone={zone_key}&auto_save=true"
        if orientation != 'landscape':
            target_url += f"&orientation={orientation}"
        
        save_done_event.clear()
        
        w_size = "1080,1920" if orientation == "portrait" else ("1080,1080" if orientation == "square" else "1920,1080")
        args = [
            CHROME_PATH,
            "--headless=new",
            "--disable-gpu",
            f"--window-size={w_size}",
            "--force-device-scale-factor=1",
            "--mute-audio",
            "--hide-scrollbars",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-sync",
            "--no-first-run",
            "--disable-extensions",
            f"--user-data-dir={chrome_profile_dir}_eph",
            target_url
        ]
        
        creationflags = 0x08000000 if os.name == 'nt' else 0
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
        success = save_done_event.wait(timeout=45)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            
        if success and saved_image_data:
            suffix = f"_{orientation}" if orientation != "landscape" else ""
            filename = f"carte_{zone_key}_ephemeride{suffix}.jpg" if zone_key != "france_pictos" else f"carte_ephemeride{suffix}.jpg"
            filepath = os.path.join(DEST_DIR, filename)
            with open(filepath, 'wb') as f_img:
                f_img.write(saved_image_data['data'])
            print(f"   -> Saved: {filepath}")
        else:
            print("   -> Error: Timeout waiting for render of ephemeride.")

    # 5. Capture vigilance map at the end
    print(f"\nRendering vigilance map for {zone_key}...")
    try:
        import sys
        sys.path.append(PROJECT_DIR)
        from generate_video_bulletin import capture_and_compose_vigilance
        suffix = f"_{orientation}" if orientation != "landscape" else ""
        vigilance_file = f"carte_vigilance_{zone_key}{suffix}.jpg"
        vigilance_path = os.path.join(DEST_DIR, vigilance_file)
        res = capture_and_compose_vigilance(zone_key, orientation, vigilance_path)
        if res:
            print(f"   -> Saved: {vigilance_path}")
        else:
            print("   -> Error capturing vigilance.")
    except Exception as e:
        print(f"   -> Error calling capture_and_compose_vigilance: {e}")

    # 6. Capture carte Risque Feux de Forêt
    print(f"\nRendering forest fire risk map for {zone_key}...")
    try:
        from generate_video_bulletin import capture_and_compose_forets
        suffix = f"_{orientation}" if orientation != "landscape" else ""
        forets_file = f"carte_forets_{zone_key}{suffix}.jpg"
        forets_path = os.path.join(DEST_DIR, forets_file)
        res = capture_and_compose_forets(zone_key, orientation, forets_path)
        if res:
            print(f"   -> Saved: {forets_path}")
        else:
            print("   -> Error capturing forest fire map.")
    except Exception as e:
        print(f"   -> Error calling capture_and_compose_forets: {e}")

    # Cleanup unique chrome profiles
    import glob
    for d in glob.glob(f"{chrome_profile_dir}*"):
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
            except Exception:
                pass

    print("\nSuccess! All weather map images, ephemeris card, and CSV reports have been generated.")
    print(f"You can find them in: {DEST_DIR}")

if __name__ == "__main__":
    main()
