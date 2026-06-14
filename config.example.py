# Copy this file to config.py and fill in your values.
# config.py is gitignored — git pull will never overwrite it.

# --- Location (for weather & air quality) ---
LOCATION_LAT = 44.8240855
LOCATION_LON = 20.4934273

# --- Widget toggles ---
ENABLE_BAMBU = False
ENABLE_ANTIGRAVITY = False
ENABLE_CALENDAR = True
ENABLE_GARMIN = True

# --- ICS calendar ---
# Paste your secret ICS link here (Google Calendar, iCloud, Fastmail, etc.)
CALENDAR_ICS_URL = 'https://calendar.google.com/calendar/ical/your_calendar_id/basic.ics'

# --- Garmin Connect (only needed if ENABLE_GARMIN = True) ---
# Uses your regular Garmin Connect login — no API key needed.
# Install: pip install garminconnect --break-system-packages
GARMIN_CONF = {
    'EMAIL': 'your@email.com',
    'PASSWORD': 'your_password',
}

# --- Bambu Lab 3D printer (only needed if ENABLE_BAMBU = True) ---
PRINTER_CONF = {
    'IP': '192.168.x.x',
    'SERIAL': '',
    'ACCESS_CODE': '',
}
