#!/usr/bin/python3
# -*- coding:utf-8 -*-
import sys
import os
import time
import logging
import threading
import requests
import io
import gc
import socket
import resource
import signal
import json
import subprocess
import math
import calendar
from collections import deque
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
from logging.handlers import RotatingFileHandler

try:
    from icalendar import Calendar as iCalendar
    HAS_ICALENDAR = True
except ImportError:
    HAS_ICALENDAR = False

# --- SYSTEM LIMITS ---
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
except Exception as e:
    print(f"Failed to set rlimit: {e}")

# --- PATHS ---
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(BASE_DIR, 'lib')
FONT_DIR = os.path.join(BASE_DIR, 'fnt')
ICON_DIR = os.path.join(BASE_DIR, 'icons')
LOG_FILE = os.path.join(BASE_DIR, 'dashboard.log')

# ######################
# --- WIDGET TOGGLES ---
# ######################
ENABLE_BAMBU = False
ENABLE_ANTIGRAVITY = False
ENABLE_CALENDAR = True  # Fetches an ICS URL and shows the next upcoming event
ENABLE_GARMIN = True

# --- API ENDPOINTS ---
API_ENDPOINTS = {
    'weather': 'https://api.open-meteo.com/v1/forecast',
    'aqi': 'https://air-quality-api.open-meteo.com/v1/air-quality',
}

# --- CONFIGURATION ---
# Change to your GEO location
LOCATION_LAT = 44.8240855
LOCATION_LON = 20.4934273

PRINTER_CONF = {
    'IP': '192.168....',
    'SERIAL': '',
    'ACCESS_CODE': ''
}

GARMIN_CONF = {
    'EMAIL': '',
    'PASSWORD': '',
}

# ICS calendar URL — paste any public or private ICS link (Google Calendar, iCloud, etc.)
CALENDAR_ICS_URL = 'https://calendar.google.com/calendar/ical/your_calendar_id/basic.ics'

# --- LOCAL CONFIG OVERRIDE ---
# Copy config.example.py to config.py and set your values there.
# config.py is gitignored so git pull will never overwrite it.
try:
    import config as _cfg
    for _k, _v in vars(_cfg).items():
        if not _k.startswith('_'):
            globals()[_k] = _v
except ImportError:
    pass

if os.path.exists(LIB_DIR):
    sys.path.append(LIB_DIR)

try:
    from waveshare_epd import epd10in85
    import bambulabs_api as bl
except ImportError:
    pass

# --- LOGGING ---
logging.getLogger("bambulabs_api").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=1)
file_handler.setFormatter(formatter)

logger.handlers.clear()
logger.addHandler(console_handler)
logger.addHandler(file_handler)


def _ensure_calendar_icon():
    path = os.path.join(ICON_DIR, 'icon_calendar.bmp')
    if os.path.exists(path):
        return
    try:
        S = 60
        img = Image.new('1', (S, S), 1)
        d = ImageDraw.Draw(img)
        d.rectangle([2, 6, S-3, S-3], outline=0, width=2)
        d.rectangle([2, 6, S-3, 18], fill=0)
        for tx in [14, 44]:
            d.rectangle([tx-4, 2, tx+4, 10], fill=0)
        cw = (S - 6) // 3
        ch = (S - 22) // 3
        for row in range(3):
            for col in range(3):
                cx = 3 + col * cw + 2
                cy = 20 + row * ch + 2
                d.rectangle([cx, cy, cx + cw - 4, cy + ch - 3], outline=0, width=1)
        img.save(path)
    except Exception as e:
        logging.warning(f"Could not generate calendar icon: {e}")

_ensure_calendar_icon()


def _ensure_run_icon():
    path = os.path.join(ICON_DIR, 'icon_run.bmp')
    if os.path.exists(path):
        return
    try:
        S = 60
        img = Image.new('1', (S, S), 1)
        d = ImageDraw.Draw(img)
        # Head
        d.ellipse([30, 2, 44, 16], outline=0, width=2)
        # Body (leaning forward)
        d.line([37, 16, 28, 34], fill=0, width=3)
        # Front arm (reaching forward)
        d.line([35, 20, 50, 14], fill=0, width=2)
        # Back arm (swinging back)
        d.line([33, 22, 18, 30], fill=0, width=2)
        # Front leg (extended forward)
        d.line([28, 34, 42, 46], fill=0, width=3)
        d.line([42, 46, 50, 46], fill=0, width=2)
        # Back leg (bent behind)
        d.line([28, 34, 16, 44], fill=0, width=3)
        d.line([16, 44, 20, 56], fill=0, width=2)
        img.save(path)
    except Exception as e:
        logging.warning(f"Could not generate run icon: {e}")

_ensure_run_icon()

icon_cache = {}
global_printer = None


class HardwareTimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise HardwareTimeoutError("Hardware Busy-Wait Timeout")


# --- ROBUST NETWORK MANAGER ---
class NetworkManager:
    def __init__(self):
        self.session = None
        self.create_session()

    def create_session(self):
        if self.session:
            try:
                self.session.close()
            except:
                pass
        gc.collect()
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=10,
            max_retries=requests.adapters.Retry(total=1, backoff_factor=0.5)
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def get_json(self, url, headers=None, data=None, method='GET', timeout=10):
        try:
            if self.session is None: self.create_session()
            if method == 'POST':
                resp = self.session.post(url, headers=headers, data=data, timeout=timeout)
            else:
                resp = self.session.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.create_session()
            return None

    def get_image(self, url, timeout=15):
        try:
            if self.session is None: self.create_session()
            resp = self.session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            self.create_session()
            return None


net = NetworkManager()


# --- GLOBAL DATA STORE ---
class DataStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.weather = {}
        self.aqhi = 0
        self.printer = {'status': 'OFFLINE'}
        self.calendar = {'title': '', 'start': None}  # next upcoming event
        self.garmin = {
            'rides': 0, 'total_distance': 0,
            'rides_curr': 0, 'distance_curr': 0,
            'rides_prev': 0, 'distance_prev': 0,
            'bike_total': 0, 'hike_total': 0, 'run_total': 0,
        }
        self.claude = {'error': False, 'five_hour': {}, 'seven_day': {}}
        self.antigravity = {'error': False, 'models': []}
        self.ping = {'current': 0, 'history': deque(maxlen=50)}
        self.needs_full_refresh = False  # set by data thread when content changes

        self.last_update = {
            'weather': 0, 'printer': 0, 'calendar': 0,
            'garmin': 0, 'ping': 0,
            'claude': 0, 'antigravity': 0
        }


data_store = DataStore()


# --- HELPERS ---
def ping_printer(ip):
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '1', ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result.returncode == 0
    except:
        return False


def get_cached_icon(name, size, is_white=False):
    key = f"{name}_{size[0]}x{size[1]}_{'white' if is_white else 'black'}"
    if key not in icon_cache:
        path = os.path.join(ICON_DIR, f"{name}.bmp")
        if os.path.exists(path):
            try:
                with Image.open(path) as f_img:
                    img = f_img.convert("L").resize(size)
                    img = ImageOps.invert(img)
                    icon_cache[key] = img.convert("1")
            except:
                return None
        else:
            icon_cache[key] = None
    return icon_cache.get(key)


def time_until(iso_str):
    if not iso_str: return "N/A"
    try:
        # Handling the explicit +00:00 timezone format
        target = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        diff = target - now
        if diff.total_seconds() < 0: return "Resetting..."
        hours, rem = divmod(diff.total_seconds(), 3600)
        days, hours = divmod(hours, 24)
        if days > 0:
            return f"{int(days)}d {int(hours)}h"
        else:
            minutes = rem // 60
            return f"{int(hours)}h {int(minutes)}m"
    except Exception:
        return "N/A"


# --- AUTH & FETCH THREADS ---



def _garmin_client():
    try:
        from garminconnect import Garmin
    except ImportError:
        logging.error("garminconnect not installed: pip install garminconnect --break-system-packages")
        return None

    try:
        client = Garmin(GARMIN_CONF['EMAIL'], GARMIN_CONF['PASSWORD'])
        client.login()
        return client
    except Exception as e:
        logging.error("Garmin login failed: %s", e)
        return None


def fetch_garmin_data():
    client = _garmin_client()
    if not client:
        return None

    now_year = datetime.now().year
    bike_types = {'cycling', 'road_biking', 'mountain_biking', 'gravel_cycling',
                  'indoor_cycling', 'virtual_ride', 'e_bike_fitness', 'recumbent_cycling'}
    hike_types = {'hiking', 'walking', 'trail_hiking'}
    run_types  = {'running', 'trail_running', 'treadmill_running', 'track_running',
                  'ultra_run', 'obstacle_run', 'indoor_running'}

    total_rides, total_dist = 0, 0
    rides_curr, dist_curr = 0, 0
    rides_prev, dist_prev = 0, 0
    bike_total, hike_total, run_total = 0, 0, 0

    try:
        # Fetch all activities since start of last year in batches
        start_date = datetime(now_year - 1, 1, 1).strftime('%Y-%m-%d')
        end_date   = datetime.now().strftime('%Y-%m-%d')
        activities = client.get_activities_by_date(start_date, end_date)
    except Exception as e:
        logging.error(f"Garmin activity fetch failed: {e}")
        return None

    for act in activities:
        type_key = act.get('activityType', {}).get('typeKey', '').lower()
        d = act.get('distance', 0) or 0
        start_str = act.get('startTimeLocal', '')
        try:
            act_year = int(start_str[:4])
        except Exception:
            continue

        if type_key in bike_types:
            total_rides += 1
            total_dist += d
            bike_total += d
            if act_year == now_year:
                rides_curr += 1; dist_curr += d
            elif act_year == now_year - 1:
                rides_prev += 1; dist_prev += d
        elif type_key in hike_types:
            hike_total += d
        elif type_key in run_types:
            run_total += d

    return {
        'rides': total_rides, 'total_distance': round(total_dist / 1000, 1),
        'rides_curr': rides_curr, 'distance_curr': round(dist_curr / 1000, 1),
        'rides_prev': rides_prev, 'distance_prev': round(dist_prev / 1000, 1),
        'bike_total': round(bike_total / 1000, 1),
        'hike_total': round(hike_total / 1000, 1),
        'run_total': round(run_total / 1000, 1),
    }


def auth_claude():
    try:
        import claude
        claude.interactive_auth()
    except ImportError:
        pass


def auth_antigravity():
    global ENABLE_ANTIGRAVITY
    if not ENABLE_ANTIGRAVITY: return
    try:
        import antigravity
        success = antigravity.interactive_auth()
        if not success:
            ENABLE_ANTIGRAVITY = False
            print("Antigravity widget is disabled.")
    except ImportError:
        print("antigravity.py not found. Antigravity widget disabled.")
        ENABLE_ANTIGRAVITY = False


def update_data_thread():
    global global_printer

    if ENABLE_BAMBU:
        try:
            global_printer = bl.Printer(PRINTER_CONF['IP'], PRINTER_CONF['ACCESS_CODE'], PRINTER_CONF['SERIAL'])
        except Exception as e:
            logging.error(f"Bambu init error: {e}")
            global_printer = None

    is_connected = False

    while True:
        now = time.time()

        if now - data_store.last_update['weather'] > 600:
            weather_url = f"{API_ENDPOINTS['weather']}?latitude={LOCATION_LAT}&longitude={LOCATION_LON}&current=temperature_2m,wind_speed_10m,wind_direction_10m,weather_code,is_day,uv_index&hourly=temperature_2m,precipitation_probability,weather_code,cloud_cover&daily=sunrise,sunset&timezone=auto&forecast_days=2"
            aqi_url = f"{API_ENDPOINTS['aqi']}?latitude={LOCATION_LAT}&longitude={LOCATION_LON}&current=ozone,nitrogen_dioxide,pm2_5&timezone=auto"
            w_data = net.get_json(weather_url)
            a_data = net.get_json(aqi_url)
            with data_store.lock:
                if w_data:
                    data_store.weather = w_data
                    data_store.needs_full_refresh = True
                if a_data and 'current' in a_data:
                    cur = a_data['current']
                    # open-meteo gives O3 and NO2 in µg/m³; AQHI formula needs ppb
                    # PM2.5 is already in µg/m³ (no conversion)
                    o3_ppb  = (cur.get('ozone', 0) or 0) / 1.9957
                    no2_ppb = (cur.get('nitrogen_dioxide', 0) or 0) / 1.9125
                    pm25    = cur.get('pm2_5', 0) or 0
                    aqhi = (1000 / 10.4) * (
                        (math.exp(0.000537 * o3_ppb) - 1) +
                        (math.exp(0.000871 * no2_ppb) - 1) +
                        (math.exp(0.000487 * pm25) - 1)
                    )
                    data_store.aqhi = max(1, round(aqhi))
            data_store.last_update['weather'] = now

        if ENABLE_GARMIN and now - data_store.last_update['garmin'] > 14400:
            g_data = fetch_garmin_data()
            if g_data:
                with data_store.lock:
                    data_store.garmin = g_data
                    data_store.needs_full_refresh = True
            data_store.last_update['garmin'] = now

        if ENABLE_BAMBU:
            update_interval = 5 if is_connected else 15
            if now - data_store.last_update['printer'] > update_interval:
                is_alive = ping_printer(PRINTER_CONF['IP'])
                if is_alive:
                    try:
                        if not is_connected and global_printer:
                            global_printer.connect()
                            time.sleep(1)
                            is_connected = True
                        if global_printer:
                            status = global_printer.get_state()
                            if status and status != "UNKNOWN":
                                with data_store.lock:
                                    data_store.printer = {
                                        'status': status,
                                        'percentage': global_printer.get_percentage(),
                                        'remaining_time': global_printer.get_time(),
                                        'layers': f"{global_printer.current_layer_num()}/{global_printer.total_layer_num()}"
                                    }
                    except Exception as e:
                        is_connected = False
                        with data_store.lock:
                            data_store.printer['status'] = 'OFFLINE'
                        try:
                            if global_printer: global_printer.disconnect()
                        except:
                            pass
                else:
                    if is_connected:
                        is_connected = False
                        try:
                            global_printer.disconnect()
                        except:
                            pass
                    with data_store.lock:
                        data_store.printer['status'] = 'OFFLINE'
                data_store.last_update['printer'] = now
        else:
            if now - data_store.last_update['claude'] > 600:
                try:
                    subprocess.run([sys.executable, os.path.join(BASE_DIR, 'claude.py')], capture_output=True, timeout=30)
                    usage_path = os.path.join(BASE_DIR, 'usage.json')
                    if os.path.exists(usage_path):
                        with open(usage_path, 'r') as f:
                            usage_data = json.load(f)
                        with data_store.lock:
                            data_store.claude = usage_data
                            data_store.claude['error'] = 'five_hour' not in usage_data
                            data_store.needs_full_refresh = True
                    else:
                        with data_store.lock:
                            data_store.claude['error'] = True
                except Exception as e:
                    logging.error(f"Claude update error: {e}")
                    with data_store.lock:
                        data_store.claude['error'] = True
                data_store.last_update['claude'] = now

        if not ENABLE_ANTIGRAVITY:
            if now - data_store.last_update['ping'] > 20:
                try:
                    out = subprocess.check_output(['ping', '-c', '1', '-W', '1', '8.8.8.8']).decode('utf-8')
                    ms = float(out.split('time=')[1].split(' ms')[0])
                except:
                    ms = 0
                with data_store.lock:
                    data_store.ping['current'] = int(ms)
                    data_store.ping['history'].append(int(ms))
                data_store.last_update['ping'] = now

        if ENABLE_CALENDAR and HAS_ICALENDAR and now - data_store.last_update['calendar'] > 900:
            try:
                resp = net.session.get(CALENDAR_ICS_URL, timeout=10)
                resp.raise_for_status()
                cal = iCalendar.from_ical(resp.content)
                now_dt = datetime.now(timezone.utc)
                next_event = None
                for component in cal.walk():
                    if component.name != 'VEVENT':
                        continue
                    dtstart = component.get('DTSTART')
                    if dtstart is None:
                        continue
                    start = dtstart.dt
                    # Normalize date-only events to midnight UTC
                    if not hasattr(start, 'tzinfo'):
                        from datetime import date
                        start = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
                    elif start.tzinfo is None:
                        start = start.replace(tzinfo=timezone.utc)
                    if start >= now_dt:
                        if next_event is None or start < next_event[1]:
                            summary = str(component.get('SUMMARY', 'No title'))
                            next_event = (summary, start)
                with data_store.lock:
                    if next_event:
                        data_store.calendar = {'title': next_event[0], 'start': next_event[1]}
                    else:
                        data_store.calendar = {'title': 'No upcoming events', 'start': None}
                    data_store.needs_full_refresh = True
            except Exception as e:
                logging.error(f"Calendar fetch error: {e}")
            data_store.last_update['calendar'] = now

        if ENABLE_ANTIGRAVITY and now - data_store.last_update['antigravity'] > 60:
            try:
                subprocess.run([sys.executable, os.path.join(BASE_DIR, 'antigravity.py')], capture_output=True, timeout=30)
                limits_path = os.path.join(BASE_DIR, 'limits.json')
                if os.path.exists(limits_path):
                    with open(limits_path, 'r', encoding='utf-8') as f:
                        limits_data = json.load(f)
                    with data_store.lock:
                        data_store.antigravity = limits_data
                        if "error" in limits_data:
                            data_store.antigravity['error'] = True
                        else:
                            data_store.antigravity['error'] = False
                else:
                    with data_store.lock:
                        data_store.antigravity['error'] = True
            except Exception as e:
                logging.error(f"Antigravity update error: {e}")
                with data_store.lock:
                    data_store.antigravity['error'] = True
            data_store.last_update['antigravity'] = now

        gc.collect()
        time.sleep(1)


# --- GRAPHICS FUNCTIONS ---
def draw_icon(draw, x, y, name, size=(40, 40), is_white=False):
    icon = get_cached_icon(name, size, is_white)
    if icon:
        draw.bitmap((x, y), icon, fill=255 if is_white else 0)
    else:
        draw.rectangle((x, y, x + size[0], y + size[1]), outline=255 if is_white else 0)


def draw_sparkline(draw, x, y, data, max_items=50, width=400, height=60, color=0, style="bar"):
    if not data: return
    max_val = max(data) if max(data) > 0 else 1
    step = width / max(max_items - 1, 1)

    if style == "line":
        points = []
        for i, val in enumerate(data):
            px = x + i * step
            py = y + height - (val / max_val) * height
            points.append((px, py))
        if len(points) > 1: draw.line(points, fill=color, width=2)
    elif style == "bar":
        bar_w = max(int(step) - 1, 1)
        for i, val in enumerate(data):
            bh = int((val / max_val) * height)
            bx = x + i * step
            by = y + height - bh
            draw.rectangle((bx, by, bx + bar_w, y + height), fill=color)


def get_weather_icon(code, is_day=1):
    if code == 0:
        return "icon_sun" if is_day else "icon_moon"
    elif code in [1, 2]:
        return "icon_partly-cloudy-day"
    elif code == 3:
        return "icon_clouds"
    elif code in [45, 48]:
        return "icon_wind"
    elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
        return "icon_rain"
    elif code in [71, 73, 75, 85, 86]:
        return "icon_snow"
    elif code in [95, 96, 99]:
        return "icon_lightning"
    return "icon_sun"


def render_screen(epd, fonts, volatile):
    # `volatile` holds a frozen snapshot (ping + the time used for the slow
    # progress bars) captured at the last full refresh.  Rendering these from the
    # snapshot — instead of live data — keeps their pixels byte-identical between
    # partials, so they never widen the changed-rectangle.  Only the clock and the
    # calendar countdown (both column 3) update live during partials.
    Himage = Image.new('1', (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(Himage)

    if not data_store.lock.acquire(timeout=2.0): return Himage
    try:
        weather = data_store.weather.copy()
        aqhi = data_store.aqhi
        printer = data_store.printer.copy()
        cal_event = data_store.calendar.copy()
        claude = data_store.claude.copy()
        antigravity = data_store.antigravity.copy()
        garmin = data_store.garmin.copy()
    finally:
        data_store.lock.release()

    ping = volatile['ping']

    col_w = epd.width // 3

    # --- COLUMN 1 (Widgets) ---
    col1_x = 20

    # Widget 1: Strava
    y1 = 20
    now_y = datetime.now().year
    draw_icon(draw, col1_x, y1, "icon_rocket", (60, 60))
    draw.text((col1_x + 70, y1), "GARMIN STATS", font=fonts['28'], fill=0)
    draw.text((col1_x + 70, y1 + 35),
              f"{now_y}: {garmin['distance_curr']} km  |  {now_y - 1}: {garmin['distance_prev']} km",
              font=fonts['20'], fill=0)
    draw.text((col1_x + 70, y1 + 60),
              f"Total: {garmin['total_distance']} km  |  {garmin['rides']} rides",
              font=fonts['20'], fill=0)
    draw_icon(draw, col1_x + 70, y1 + 85, "icon_bike", (28, 28))
    draw.text((col1_x + 100, y1 + 90), f"{garmin['bike_total']}km", font=fonts['20'], fill=0)
    draw_icon(draw, col1_x + 190, y1 + 85, "icon_hike", (28, 28))
    draw.text((col1_x + 220, y1 + 90), f"{garmin['hike_total']}km", font=fonts['20'], fill=0)
    draw_icon(draw, col1_x + 310, y1 + 85, "icon_run", (28, 28))
    draw.text((col1_x + 340, y1 + 90), f"{garmin['run_total']}km", font=fonts['20'], fill=0)

    draw.line((col1_x, 150, col_w - 20, 150), fill=0, width=2)

    # Widget 2: Bambu or Crypto
    y2 = 170
    if ENABLE_BAMBU:
        p_status = str(printer.get('status', 'OFFLINE')).upper()
        draw_icon(draw, col1_x, y2, "icon_3d", (60, 60))
        draw.text((col1_x + 70, y2), f"PRINTER: {p_status}", font=fonts['28'], fill=0)
        if p_status not in ["OFFLINE", "UNKNOWN", "FINISH"]:
            percent = printer.get('percentage', 0)
            draw.rectangle((col1_x + 70, y2 + 40, col1_x + 400, y2 + 60), outline=0)
            draw.rectangle((col1_x + 70, y2 + 40, col1_x + 70 + int(330 * (percent / 100)), y2 + 60), fill=0)
            draw.text((col1_x + 70, y2 + 70),
                      f"{percent}% | Rem: {printer.get('remaining_time', '0')}m | {printer.get('layers', '0/0')} L",
                      font=fonts['20'], fill=0)
    else:
        draw_icon(draw, col1_x, y2, "icon_cpu", (50, 50))
        draw.text((col1_x + 60, y2), "CLAUDE USAGE", font=fonts['28'], fill=0)
        if claude.get('error'):
            draw.text((col1_x + 60, y2 + 40), "No data — auth required", font=fonts['20'], fill=0)
        else:
            pct_5h = claude.get('five_hour', {}).get('utilization', 0)
            rem_5h = time_until(claude.get('five_hour', {}).get('resets_at'))
            draw.text((col1_x + 60, y2 + 38), f"5h:  {pct_5h}%  (resets {rem_5h})", font=fonts['20'], fill=0)
            bx, bw, bh = col1_x + 60, 340, 14
            draw.rectangle((bx, y2 + 60, bx + bw, y2 + 60 + bh), outline=0, width=2)
            fw = int((bw - 4) * min(pct_5h / 100.0, 1.0))
            if fw > 0: draw.rectangle((bx + 2, y2 + 62, bx + 2 + fw, y2 + 60 + bh - 2), fill=0)

            pct_7d = claude.get('seven_day', {}).get('utilization', 0)
            rem_7d = time_until(claude.get('seven_day', {}).get('resets_at'))
            draw.text((col1_x + 60, y2 + 85), f"7d:  {pct_7d}%  (resets {rem_7d})", font=fonts['20'], fill=0)
            draw.rectangle((bx, y2 + 107, bx + bw, y2 + 107 + bh), outline=0, width=2)
            fw = int((bw - 4) * min(pct_7d / 100.0, 1.0))
            if fw > 0: draw.rectangle((bx + 2, y2 + 109, bx + 2 + fw, y2 + 107 + bh - 2), fill=0)

    draw.line((col1_x, 320, col_w - 20, 320), fill=0, width=2)

    # Widget 3: Antigravity or Ping
    y3 = 340
    if ENABLE_ANTIGRAVITY:
        draw_icon(draw, col1_x, y3, "icon_cpu", (50, 50))
        draw.text((col1_x + 60, y3), "ANTIGRAVITY USAGE", font=fonts['28'], fill=0)
        
        if antigravity.get('error'):
            draw.text((col1_x + 60, y3 + 35), "Error loading data", font=fonts['20'], fill=0)
        else:
            models = antigravity.get('models', [])
            opus = next((m for m in models if m.get('modelId') == 'claude-opus-4-6-thinking'), None)
            gemini = next((m for m in models if m.get('modelId') == 'gemini-3-pro-high'), None)
            
            y_off = y3 + 35
            for m_data in (opus, gemini):
                if m_data:
                    label = "Opus 4.6" if m_data.get('modelId') == 'claude-opus-4-6-thinking' else "Gemini 3Pro"
                    pct = m_data.get('usedPercentage', 0)
                    rem_time = time_until(m_data.get('resetDate'))
                    
                    draw.text((col1_x + 60, y_off), f"{label} {pct}% | In {rem_time}", font=fonts['20'], fill=0)
                    
                    bx, bw, bh = col1_x + 60, 330, 15
                    draw.rectangle((bx, y_off + 25, bx + bw, y_off + 25 + bh), outline=0, width=2)
                    fill_w = int((bw - 4) * min(pct / 100.0, 1.0))
                    if fill_w > 0: draw.rectangle((bx + 2, y_off + 27, bx + 2 + fill_w, y_off + 25 + bh - 2), fill=0)
                    
                    y_off += 50
    else:
        draw_icon(draw, col1_x, y3, "icon_wifi", (50, 50))
        draw.text((col1_x + 60, y3), f"Internet Quality: {ping['current']} ms", font=fonts['28'], fill=0)
        draw_sparkline(draw, col1_x, y3 + 60, list(ping['history']), max_items=50, width=400, height=40, style="bar")

    draw.line((col_w, 10, col_w, 470), fill=0, width=2)

    # --- COLUMN 2 (Weather) ---
    col2_x = col_w + 20

    if 'current' in weather:
        cur = weather['current']
        temp = cur.get('temperature_2m', 0)
        daily = weather.get('daily', {})
        sunrise = daily.get('sunrise', [''])[0][11:16] if daily.get('sunrise') else '--:--'
        sunset  = daily.get('sunset',  [''])[0][11:16] if daily.get('sunset')  else '--:--'
        w_code = cur.get('weather_code', 0)
        wind_dir = cur.get('wind_direction_10m', 0)
        wind_spd = cur.get('wind_speed_10m', 0)
        is_day = cur.get('is_day', 1)
        uv_index = cur.get('uv_index', 0.0)

        temp_rounded = math.floor(temp + 0.5)

        draw_icon(draw, col2_x, 20, get_weather_icon(w_code, is_day), (90, 90))
        draw.text((col2_x + 100, 10), f"{temp_rounded}°C", font=fonts['80'], fill=0)

        uv_x, uv_y = col2_x + 320, 25
        uv_rounded = math.floor(uv_index + 0.5)
        draw.text((uv_x, uv_y), "UV", font=fonts['28'], fill=0)
        uv_val_str = str(uv_rounded)
        try:
            bbox = draw.textbbox((0, 0), uv_val_str, font=fonts['60'])
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(uv_val_str, font=fonts['60'])

        uv_val_x, uv_val_y = uv_x + 45, 5
        if uv_rounded >= 6:
            pad = 5
            draw.rectangle((uv_val_x - pad, uv_val_y - pad + 10, uv_val_x + tw + pad, uv_val_y + th + pad), fill=0)
            draw.text((uv_val_x, uv_val_y), uv_val_str, font=fonts['60'], fill=255)
        else:
            draw.text((uv_val_x, uv_val_y), uv_val_str, font=fonts['60'], fill=0)

        draw_icon(draw, col2_x + 100, 93, "icon_sun", (22, 22))
        draw.text((col2_x + 126, 95), f"Rise  {sunrise}", font=fonts['20'], fill=0)
        draw_icon(draw, col2_x + 100, 118, "icon_moon", (22, 22))
        draw.text((col2_x + 126, 120), f"Set    {sunset}", font=fonts['20'], fill=0)

        draw.line((col2_x, 140, col2_x + col_w - 40, 140), fill=0, width=2)

        y_c2 = 160
        draw_icon(draw, col2_x + 5, y_c2, "icon_wind", (30, 30))

        cx, cy, r = col2_x + 80, y_c2 + 80, 60
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=0, width=2)

        for angle in range(0, 360, 45):
            rad_tick = math.radians(angle)
            inner_r = r - 8 if angle % 90 == 0 else r - 4
            tx1, ty1 = cx + inner_r * math.cos(rad_tick), cy + inner_r * math.sin(rad_tick)
            tx2, ty2 = cx + r * math.cos(rad_tick), cy + r * math.sin(rad_tick)
            draw.line((tx1, ty1, tx2, ty2), fill=0, width=2)

        draw.text((cx - 8, cy - r - 22), "N", font=fonts['20'], fill=0)
        draw.text((cx - 8, cy + r + 4), "S", font=fonts['20'], fill=0)
        draw.text((cx + r + 6, cy - 10), "E", font=fonts['20'], fill=0)
        draw.text((cx - r - 24, cy - 10), "W", font=fonts['20'], fill=0)

        rad_arrow = math.radians(wind_dir - 90)
        tip_x = cx + (r - 12) * math.cos(rad_arrow)
        tip_y = cy + (r - 12) * math.sin(rad_arrow)
        base_angle = math.radians(150)
        left_x = cx + 20 * math.cos(rad_arrow + base_angle)
        left_y = cy + 20 * math.sin(rad_arrow + base_angle)
        right_x = cx + 20 * math.cos(rad_arrow - base_angle)
        right_y = cy + 20 * math.sin(rad_arrow - base_angle)
        draw.polygon([(tip_x, tip_y), (left_x, left_y), (right_x, right_y)], fill=0)
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=0)

        spd_text = f"{wind_spd} km/h"
        try:
            bbox = draw.textbbox((0, 0), spd_text, font=fonts['20'])
            tw = bbox[2] - bbox[0]
        except AttributeError:
            tw = draw.textsize(spd_text, font=fonts['20'])[0]

        draw.text((cx - tw / 2, cy + 25), spd_text, font=fonts['20'], fill=0)

        aqi_x = col2_x + 180
        draw.text((aqi_x, y_c2 + 10), "AIR QUALITY", font=fonts['20'], fill=0)
        draw.text((aqi_x, y_c2 + 55), "AQHI:", font=fonts['28'], fill=0)

        aqhi_str = str(aqhi)
        try:
            bbox = draw.textbbox((0, 0), aqhi_str, font=fonts['80'])
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = draw.textsize(aqhi_str, font=fonts['80'])

        val_x, val_y = aqi_x + 80, y_c2 + 66

        # AQHI 7+ is high risk (vs AQI 50+)
        if aqhi >= 7:
            pad = 20
            draw.rectangle((val_x - pad, val_y - pad + 15, val_x + tw + pad, val_y + th + pad - 5), fill=0)
            draw.text((val_x, val_y), aqhi_str, font=fonts['80'], fill=255)
        else:
            draw.text((val_x, val_y), aqhi_str, font=fonts['80'], fill=0)

        draw.line((col2_x, 320, col2_x + col_w - 40, 320), fill=0, width=2)

        hourly = weather.get('hourly', {})
        times = hourly.get('time', [])
        temps = hourly.get('temperature_2m', [])
        codes = hourly.get('weather_code', [])

        cur_iso = datetime.now().strftime("%Y-%m-%dT%H:00")
        try:
            start_idx = times.index(cur_iso) + 1
        except:
            start_idx = 0

        for i in range(4):
            idx = start_idx + i
            if idx < len(times):
                off_x = col2_x + (i * 105)
                draw.text((off_x + 10, 340), f"{times[idx].split('T')[1][:5]}", font=fonts['24'], fill=0)
                hour = int(times[idx].split('T')[1][:2])
                draw_icon(draw, off_x + 15, 375, get_weather_icon(codes[idx], 6 <= hour <= 21), (60, 60))
                f_temp = math.floor(temps[idx] + 0.5)
                draw.text((off_x + 15, 440), f"{f_temp}°C", font=fonts['24'], fill=0)

    draw.line((col_w * 2, 10, col_w * 2, 470), fill=0, width=2)

    # --- COLUMN 3 (Time, Claude/Spotify/Progress, Gmail) ---
    col3_x = col_w * 2 + 30
    dt = datetime.now()

    # 1. Time & Date
    draw.text((col3_x, 10), dt.strftime("%H:%M"), font=fonts['clock'], fill=0)

    date_str = dt.strftime("%d %B %Y")
    day_str = dt.strftime("%a").upper()

    draw.text((col3_x, 170), date_str, font=fonts['32'], fill=0)
    draw.text((col3_x + 340, 170), day_str, font=fonts['32'], fill=0)

    draw.line((col3_x, 220, epd.width - 20, 220), fill=0, width=2)

    # 2. Time Progress
    sp_y = 240
    draw.rectangle((col3_x, sp_y, col3_x + 420, sp_y + 130), fill=255)
    tp_y = sp_y
    draw.text((col3_x, tp_y), "TIME PROGRESS", font=fonts['28'], fill=0)

    # Progress bars use the frozen snapshot time so they only move on a full
    # refresh — they change too slowly to be worth a partial, and rendering them
    # live would dirty this region every cycle.
    pdt = volatile['prog_dt']
    day_pct = (pdt.hour * 3600 + pdt.minute * 60 + pdt.second) / 86400.0
    days_in_m = calendar.monthrange(pdt.year, pdt.month)[1]
    month_pct = (pdt.day - 1 + (pdt.hour / 24.0)) / days_in_m
    days_in_y = 366 if calendar.isleap(pdt.year) else 365
    year_pct = (pdt.timetuple().tm_yday - 1 + (pdt.hour / 24.0)) / days_in_y

    def draw_prog(y_offset, label, pct):
        draw.text((col3_x, tp_y + y_offset), label, font=fonts['24'], fill=0)
        bx = col3_x + 110
        bw = 200
        bh = 20
        draw.rectangle((bx, tp_y + y_offset + 2, bx + bw, tp_y + y_offset + bh + 2), outline=0, width=2)
        if pct > 0:
            fill_w = int((bw - 4) * min(pct, 1.0))
            if fill_w > 0:
                draw.rectangle((bx + 2, tp_y + y_offset + 4, bx + 2 + fill_w, tp_y + y_offset + bh), fill=0)
        draw.text((bx + bw + 15, tp_y + y_offset), f"{int(pct * 100)}%", font=fonts['24'], fill=0)

    draw_prog(40, "DAY", day_pct)
    draw_prog(75, "MONTH", month_pct)
    draw_prog(110, "YEAR", year_pct)

    draw.line((col3_x, 380, epd.width - 20, 380), fill=0, width=2)

    # 3. Calendar
    cal_y = 395
    draw_icon(draw, col3_x, cal_y, "icon_calendar", (50, 50))
    draw.text((col3_x + 60, cal_y), "NEXT EVENT", font=fonts['24'], fill=0)
    if ENABLE_CALENDAR and cal_event.get('title'):
        title = cal_event['title']
        start = cal_event.get('start')
        # Truncate long titles to fit the column
        max_chars = 22
        display_title = title if len(title) <= max_chars else title[:max_chars - 1] + '…'
        draw.text((col3_x + 60, cal_y + 28), display_title, font=fonts['28'], fill=0)
        if start:
            local_start = start.astimezone().replace(tzinfo=None)
            # Use the frozen snapshot time so the countdown only changes on a full
            # refresh — otherwise it would add another set of changing rows to the
            # partial band every minute.
            now_local = volatile['prog_dt']
            diff = local_start - now_local
            days = diff.days
            hours = diff.seconds // 3600
            if days > 0:
                when = f"In {days}d {hours}h  —  {local_start.strftime('%a %d %b')}"
            elif diff.total_seconds() > 0:
                mins = (diff.seconds % 3600) // 60
                when = f"In {hours}h {mins}m  —  {local_start.strftime('%H:%M')}"
            else:
                when = local_start.strftime('%a %d %b  %H:%M')
            draw.text((col3_x + 60, cal_y + 58), when, font=fonts['20'], fill=0)
    else:
        draw.text((col3_x + 60, cal_y + 28), "No events / disabled", font=fonts['24'], fill=0)

    return Himage


# --- MAIN LOOP ---
def _sync_dtm1(epd, buf):
    """Write content to DTM1 (Old Data) without triggering a display refresh.

    epd.display() always sets DTM1=0xFF (white) so the full waveform sees
    every pixel as changed and drives them all.  After init_Part() we need
    DTM1 to reflect what is actually on screen, otherwise the very first
    partial refresh treats every pixel as changed (white→content) and applies
    the partial waveform to the whole panel — causing widespread drift.
    With DTM1 synced to content, partial refreshes only drive pixels that
    genuinely changed (the clock digits), leaving static content untouched.
    """
    half = epd.width // 16          # bytes per half-row  (85)
    stride = half * 2               # bytes per full row  (170)
    master = bytearray(half * epd.height)
    slave  = bytearray(half * epd.height)
    for row in range(epd.height):
        base = row * stride
        master[row * half:(row + 1) * half] = buf[base       : base + half]
        slave [row * half:(row + 1) * half] = buf[base + half: base + stride]
    epd.send_command_M(0x10)
    epd.send_data2_M(master)
    epd.send_command_S(0x10)
    epd.send_data2_S(slave)


def _changed_rect(buf, last, width, height):
    """Byte-aligned bounding box (in pixels) of the bytes differing between two
    full-frame buffers.  Returns (x0, y0, x1, y1) or None if nothing changed.
    A full row is width/8 bytes (170); byte column c covers pixels [c*8, c*8+8).
    """
    stride = width // 8                     # 170 bytes per row
    c0, c1, r0, r1 = stride, -1, height, -1
    for r in range(height):
        base = r * stride
        for c in range(stride):
            if buf[base + c] != last[base + c]:
                if c < c0: c0 = c
                if c > c1: c1 = c
                if r < r0: r0 = r
                if r > r1: r1 = r
    if c1 < 0:
        return None
    return c0 * 8, r0, (c1 + 1) * 8, r1 + 1


def main():
    auth_claude()
    auth_antigravity()

    signal.signal(signal.SIGALRM, timeout_handler)
    epd = None

    try:
        epd = epd10in85.EPD()
        epd.init()
        epd.Clear()
        time.sleep(1)
        _startup_full_refresh_pending = True

        def load_font(name, size):
            return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

        fonts = {
            '20': load_font('Aldrich-Regular.ttc', 20),
            '24': load_font('Aldrich-Regular.ttc', 24),
            '28': load_font('Aldrich-Regular.ttc', 28),
            '32': load_font('Aldrich-Regular.ttc', 32),
            '35': load_font('Aldrich-Regular.ttc', 35),
            '40': load_font('Aldrich-Regular.ttc', 40),
            '60': load_font('Aldrich-Regular.ttc', 60),
            '80': load_font('Aldrich-Regular.ttc', 80),
            'clock': load_font('advanced_led_board-7.ttc', 180),
        }

        t_data = threading.Thread(target=update_data_thread)
        t_data.daemon = True
        t_data.start()

        # --- REFRESH STRATEGY ---
        # Partial refreshes use a *differential* waveform: no black/white flashing,
        # and the panel only physically drives pixels that differ between the old
        # frame (controller reg 0x10, kept in sync by _sync_dtm1) and the new frame
        # (0x13).  Static content is therefore never re-flashed, and ghosting only
        # builds up on the pixels that actually change (mainly the clock digits).
        # A periodic FULL refresh (normal flashing waveform) clears that residue.
        #
        # Tune these two if you still see ghosting (lower them) or want fewer
        # flashes (raise them):
        FULL_REFRESH_INTERVAL = 600     # secs: force a clean full refresh at least this often
        MAX_PARTIALS_BEFORE_FULL = 3    # also force a full refresh after this many partials
        PARTIAL_PASSES = 1              # repeat each partial pulse N times (vendor uses 1)

        last_full_refresh_day = -1
        last_full_refresh_ts = 0
        partial_count = 0
        last_buf = None
        in_partial_mode = False
        volatile = None  # frozen snapshot of ping + progress-bar time (see render_screen)

        while True:
            start_time = time.time()
            try:
                signal.alarm(60)
                now_dt = datetime.now()
                now_ts = time.time()

                # A widget's data changed (weather, garmin, calendar, claude…).
                # Those updates redraw large regions, which ghosts badly under a
                # partial waveform — so force a clean full refresh.  The clock
                # ticking only flips a few digits, so that stays a partial.
                with data_store.lock:
                    data_changed = data_store.needs_full_refresh
                    data_store.needs_full_refresh = False

                do_full = (
                    _startup_full_refresh_pending
                    or data_changed
                    or (now_dt.hour == 3 and now_dt.day != last_full_refresh_day)
                    or partial_count >= MAX_PARTIALS_BEFORE_FULL
                    or (now_ts - last_full_refresh_ts) >= FULL_REFRESH_INTERVAL
                )

                # Re-snapshot the volatile widgets only on a full refresh (and the
                # first pass).  Partials reuse this snapshot, so the ping sparkline
                # and progress bars stay byte-identical and never dirty their region.
                if do_full or volatile is None:
                    with data_store.lock:
                        volatile = {
                            'ping': {
                                'current': data_store.ping['current'],
                                'history': list(data_store.ping['history']),
                            },
                            'prog_dt': now_dt,
                        }

                image = render_screen(epd, fonts, volatile)
                buf = epd.getbuffer(image)

                if buf == last_buf and not do_full:
                    # Nothing changed on screen and no full refresh due — skip the
                    # panel entirely (no flash, no partial drive).
                    signal.alarm(0)
                    del image, buf
                    gc.collect()
                    time.sleep(max(2, 30 - (time.time() - start_time)))
                    continue

                if do_full:
                    logging.info("Full Refresh")
                    epd.init()
                    epd.display(buf)
                    # Re-enter partial mode and sync the controller's "old data"
                    # (0x10) to what is now on screen, so the next partial drives
                    # only genuinely-changed pixels.
                    epd.init_Part()
                    _sync_dtm1(epd, buf)
                    in_partial_mode = True
                    partial_count = 0
                    last_full_refresh_ts = now_ts
                    last_full_refresh_day = now_dt.day
                    _startup_full_refresh_pending = False
                else:
                    logging.info("Partial Refresh")
                    if not in_partial_mode:
                        epd.init_Part()
                        _sync_dtm1(epd, last_buf if last_buf is not None else buf)
                        in_partial_mode = True
                    # Unified partial: refresh only the changed rectangle (the
                    # clock), driving both controllers together so the unchanged
                    # half gets a tiny no-op window instead of corrupting.
                    rect = _changed_rect(buf, last_buf, epd.width, epd.height)
                    if rect is not None:
                        epd.display_Partial_Unified(buf, *rect, passes=PARTIAL_PASSES)
                    partial_count += 1

                last_buf = buf

                signal.alarm(0)
                del image
                gc.collect()

            except HardwareTimeoutError:
                logging.critical("HARDWARE HANG DETECTED!")
                signal.alarm(0)
                logging.shutdown()
                os.execv(sys.executable, ['python'] + sys.argv)
            except OSError as e:
                signal.alarm(0)
                if e.errno == 24:
                    os.execv(sys.executable, ['python'] + sys.argv)
            except Exception as e:
                signal.alarm(0)
                logging.error(f"Unexpected error in main: {e}")

            elapsed = time.time() - start_time
            sleep_time = max(2, 30 - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        try:
            signal.alarm(0)
            epd10in85.epdconfig.module_exit(cleanup=True)
        except:
            pass
        exit()


if __name__ == '__main__':
    main()
