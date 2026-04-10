import os
from dotenv import load_dotenv

load_dotenv()

# --- Run Mode ---
DEBUG_MODE = os.getenv("DEBUG_MODE", "true").lower() == "true"
DEBUG_LIMIT = int(os.getenv("DEBUG_LIMIT", "5"))  # how many venues to process in debug mode

# --- Scraper Behaviour ---
MAX_PAGES = int(os.getenv("MAX_PAGES", "5"))         # max pagination pages per city
MIN_DELAY = float(os.getenv("MIN_DELAY", "1.0"))     # min seconds between requests
MAX_DELAY = float(os.getenv("MAX_DELAY", "3.0"))     # max seconds between requests
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# --- Paths ---
INPUT_CSV = os.getenv("INPUT_CSV", "data/bizly_prod_trending_destinations.csv")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
OUTPUT_FILENAME = os.getenv("OUTPUT_FILENAME", "cvent_venues.csv")

# --- Cvent URLs ---
CVENT_DOMAIN = os.getenv("CVENT_DOMAIN", "https://www.cvent.com")
CVENT_RESULTS_BASE_URL = os.getenv("CVENT_RESULTS_BASE_URL", "https://www.cvent.com/venues/results/")
CVENT_VENUE_PATH_FRAGMENT = os.getenv("CVENT_VENUE_PATH_FRAGMENT", "/venues/en-US")

# --- Bizly API ---
BIZLY_API_URL = os.getenv("BIZLY_API_URL", "https://api-dev.bizly.com/hooks/venues/scraper/batch")
BIZLY_WEBHOOK_KEY = os.getenv("BIZLY_WEBHOOK_KEY", "")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

# --- Future: Database (leave as None for now) ---
DATABASE_URL = os.getenv("DATABASE_URL", None)
