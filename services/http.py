import time
import random
import requests

from config import MIN_DELAY, MAX_DELAY, REQUEST_TIMEOUT, MAX_RETRIES

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
}


def throttled_get(
    session,
    url,
    min_delay=MIN_DELAY,
    max_delay=MAX_DELAY,
    retries=MAX_RETRIES,
):
    """
    Polite request handler with retry + backoff
    """
    for attempt in range(retries):
        try:
            time.sleep(random.uniform(min_delay, max_delay))
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
