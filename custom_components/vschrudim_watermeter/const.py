"""Constants for VSChrudim watermeter."""
from datetime import timedelta

DOMAIN = "vschrudim_watermeter"
PLATFORMS = ["sensor"]
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PLACE = "place"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_PRICE_PER_M3 = "price_per_m3"
CONF_NOTIFY_UNAVAILABLE = "notify_unavailable"
CONF_NOTIFY_MISSING = "notify_missing"
CONF_FAILURE_THRESHOLD = "failure_threshold"
CONF_MISSING_RETRY_ATTEMPTS = "missing_retry_attempts"
CONF_RETRY_DELAY = "retry_delay"
DEFAULT_PRICE_PER_M3 = 0.0
DEFAULT_NOTIFY_UNAVAILABLE = True
DEFAULT_NOTIFY_MISSING = True
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_MISSING_RETRY_ATTEMPTS = 2
DEFAULT_RETRY_DELAY = 30
DEFAULT_SCAN_INTERVAL = timedelta(hours=4)
MIN_SCAN_INTERVAL = timedelta(minutes=15)
BASE_URL = "https://zakaznik.vschrudim.cz/"
PLACES_URL = "https://zakaznik.vschrudim.cz/ConsumptionPlaceList.aspx"
READINGS_URL = "https://zakaznik.vschrudim.cz/Userdata/ProfileData.aspx"
