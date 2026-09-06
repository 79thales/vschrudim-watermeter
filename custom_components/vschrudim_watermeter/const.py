"""Constants for VSChrudim watermeter."""
from datetime import timedelta

DOMAIN = "vschrudim_watermeter"
PLATFORMS = ["sensor"]
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PLACE = "place"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = timedelta(hours=4)
MIN_SCAN_INTERVAL = timedelta(minutes=15)
BASE_URL = "https://zakaznik.vschrudim.cz/"
PLACES_URL = "https://zakaznik.vschrudim.cz/ConsumptionPlaceList.aspx"
