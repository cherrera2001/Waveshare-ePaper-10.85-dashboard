# Copy this file to config.py and fill in your values.
# config.py is gitignored — git pull will never overwrite it.

# --- Location (for weather & air quality) ---
LOCATION_LAT = 44.8240855
LOCATION_LON = 20.4934273

# --- Widget toggles ---
ENABLE_BAMBU = False
ENABLE_ANTIGRAVITY = False
ENABLE_CALENDAR = True
ENABLE_STRAVA = True

# --- ICS calendar ---
# Paste your secret ICS link here (Google Calendar, iCloud, Fastmail, etc.)
CALENDAR_ICS_URL = 'https://calendar.google.com/calendar/ical/your_calendar_id/basic.ics'

# --- Strava (only needed if ENABLE_STRAVA = True) ---
# Get Client ID and Secret from https://www.strava.com/settings/api
STRAVA_CONF = {
    'CLIENT_ID': '',
    'CLIENT_SECRET': '',
    'TOKEN_FILE': 'strava_token.json',
}

# --- Bambu Lab 3D printer (only needed if ENABLE_BAMBU = True) ---
PRINTER_CONF = {
    'IP': '192.168.x.x',
    'SERIAL': '',
    'ACCESS_CODE': '',
}
