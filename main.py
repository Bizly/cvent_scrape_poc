import logging
import threading

import pandas as pd

from services.scraper import CventScraper
from storage.bizly_api.insert_batch_venues import insert_venue_batch
from config import DEBUG_MODE, DEBUG_LIMIT, INPUT_CSV, BATCH_SIZE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def run():
    scraper = CventScraper()

    # --- Phase 1: Link Discovery ---
    logger.info("Phase 1: Discovering venue links...")
    df = pd.read_csv(INPUT_CSV)
    destinations = df[['country', 'city_name']].drop_duplicates()

    raw_links = []
    for _, row in destinations.iterrows():
        country, city = row['country'], row['city_name']
        logger.info(f"Collecting links for {city}, {country}")
        try:
            links = scraper.scrape_venue_links(country, city)
            for url in links:
                raw_links.append({"link": url, "country": country, "city": city})
        except Exception as e:
            logger.error(f"Link discovery failed for {city}, {country}: {e}")

    # Deduplicate
    seen = set()
    all_links = []
    for item in raw_links:
        if item["link"] not in seen:
            all_links.append(item)
            seen.add(item["link"])

    logger.info(f"Phase 1 complete. {len(all_links)} unique venue URLs discovered.")

    # --- Phase 2: Venue Scraping ---
    logger.info("Phase 2: Scraping venue details...")

    # TODO: Replace this loop with a queue (RQ or Celery) when scaling.
    # Each item in all_links becomes a job pushed to Redis.
    # Workers call extract_venue_details() independently.
    # 1 venue = 1 job. Max 2-3 concurrent workers to respect Cvent rate limits.
    links_to_process = all_links[:DEBUG_LIMIT] if DEBUG_MODE else all_links
    logger.info(f"Processing {len(links_to_process)} venues (DEBUG_MODE={DEBUG_MODE})")

    results = []
    threads = []

    def _dispatch(batch: list[dict]):
        """Fire insert_venue_batch in a background thread so scraping is not blocked."""
        t = threading.Thread(target=insert_venue_batch, args=(batch,), daemon=True)
        t.start()
        threads.append(t)
        logger.info(f"Batch of {len(batch)} venues dispatched to background thread.")

    for i, item in enumerate(links_to_process):
        logger.info(f"[{i+1}/{len(links_to_process)}] Scraping: {item['link']}")
        try:
            details = scraper.scrape_venue_details(item["link"], item["country"], item["city"])
            results.append(details)
        except Exception as e:
            logger.error(f"Failed to scrape {item['link']}: {e}")

        if len(results) >= BATCH_SIZE:
            _dispatch(results.copy())
            results = []

    # --- Flush remaining venues ---
    if results:
        _dispatch(results.copy())
        results = []

    # --- Wait for all background inserts to finish ---
    for t in threads:
        t.join()

    logger.info(f"Done. {len(threads)} batch(es) sent to API.")


if __name__ == "__main__":
    run()
