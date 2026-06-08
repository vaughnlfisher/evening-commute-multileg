"""Constants for evening_commute_multileg."""

DOMAIN = "evening_commute_multileg"

# Darwin token for Huxley2 (Rail Data Marketplace)
DARWIN_TOKEN = "001105bc-e005-48d1-a443-595d23aba5aa"

# CRS codes
LEG1_FROM = "CTK"   # City Thameslink
LEG1_TO   = "ZFD"   # Farringdon
LEG2_FROM = "ZFD"   # Farringdon
LEG2_TO   = "PAD"   # Paddington
LEG3_FROM = "PAD"   # Paddington
LEG3_TO   = "TWY"   # Twyford

# Interchange times (minutes)
FARRINGDON_INTERCHANGE_MINS = 5   # Thameslink platform -> Elizabeth line
PADDINGTON_INTERCHANGE_MINS = 8   # Elizabeth line -> GWR mainline

# Number of leg-1 trains to track
NUM_TRAINS = 3
# Max nested options per subsequent leg
MAX_LEG2 = 3
MAX_LEG3 = 3

# Earliest time of day to show (don't show services before 16:00)
EARLIEST_HOUR = 16

SCAN_INTERVAL_PEAK    = 120
SCAN_INTERVAL_OFFPEAK = 300
SCAN_INTERVAL_NIGHT   = 900

HUXLEY_ROWS = 25

# Northbound termini from City Thameslink calling at Farringdon
# (Thameslink trains heading north: Bedford, Luton, St Albans, Peterborough, Cambridge)
NORTHBOUND_TERMINI = {
    "bedford", "luton", "luton airport parkway", "st albans", "st albans city",
    "peterborough", "cambridge", "cambridge north", "letchworth",
    "letchworth garden city", "stevenage", "hitchin", "welwyn garden city",
    "kentish town", "west hampstead thameslink",
}

# Twyford-bound termini from Paddington (GWR + Elizabeth line westbound)
TWYFORD_TERMINI = {
    "twyford", "reading", "didcot", "didcot parkway", "oxford",
    "swindon", "bristol", "bristol temple meads", "cheltenham",
    "newbury", "bedwyn", "great malvern", "worcester",
    "maidenhead", "slough", "henley", "henley-on-thames",
    "cardiff", "cardiff central", "taunton", "exeter", "plymouth",
    "penzance", "westbury", "frome", "weston-super-mare",
    "hereford", "gloucester", "carmarthen", "swansea",
}

# HSP (Historic Service Performance) — reliability history
HSP_URL = "https://hsp-prod.rockshore.net/api/v1/serviceMetrics"
HSP_USERNAME = "YOUR_NRE_USERNAME"
HSP_PASSWORD = "YOUR_NRE_PASSWORD"
# Per-leg HSP routes for the evening return (16:00-20:00 weekday window)
HSP_LEGS = [
    {"key": "leg1", "from": "CTK", "to": "ZFD", "label": "City Thameslink \u2192 Farringdon"},
    {"key": "leg2", "from": "ZFD", "to": "PAD", "label": "Farringdon \u2192 Paddington"},
    {"key": "leg3", "from": "PAD", "to": "TWY", "label": "Paddington \u2192 Twyford"},
]
HSP_FROM_TIME = "1600"
HSP_TO_TIME   = "2000"
