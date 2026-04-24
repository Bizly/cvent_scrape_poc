import json
import logging
import requests
from decimal import Decimal

from config import BIZLY_API_URL, BIZLY_WEBHOOK_KEY

logger = logging.getLogger(__name__)


def _serialize(obj):
    """Recursively convert non-JSON-serializable types."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    return obj


def insert_venue_batch(venues: list[dict]) -> bool:
    """
    POST a batch of venue dicts to the Bizly scraper webhook endpoint.

    Returns True if the request succeeded, False otherwise.
    Intended to be called inside a background thread — does not block the scraper.
    """
    if not venues:
        logger.warning("insert_venue_batch called with empty list — skipping.")
        return True

    if not BIZLY_WEBHOOK_KEY:
        logger.error("BIZLY_WEBHOOK_KEY is not set. Cannot send batch.")
        return False

    serialized_venues = [_serialize(v) for v in venues]
    payload = {"venues": serialized_venues}

    logger.info(f"Full API payload ({len(serialized_venues)} venues):\n{json.dumps(payload, indent=2)}")
    headers = {
        "Content-Type": "application/json",
        "webhook-key": BIZLY_WEBHOOK_KEY,
    }

    try:
        resp = requests.post(BIZLY_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        logger.info(f"Batch of {len(venues)} venues inserted successfully (HTTP {resp.status_code}).")
        return True
    except requests.HTTPError as e:
        logger.exception(f"HTTP error inserting batch: {e} — response: {e.response.text if e.response else 'n/a'}")
    except requests.RequestException as e:
        logger.exception(f"Request error inserting batch: {e}")

    return False
