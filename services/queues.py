import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from services.redis_client import get_redis
from storage.bizly_api.insert_batch_venues import insert_venue_batch

logger = logging.getLogger(__name__)

# Redis key layout
QUEUE_URLS = "cvent:queue:urls"
SEEN_URLS = "cvent:seen:urls"             # SET — dedup guard for the work queue
SEEN_DESTINATIONS = "cvent:seen:destinations"  # SET — destinations successfully explored
DLQ_DESTINATIONS = "cvent:dlq:destinations"
DLQ_URLS = "cvent:dlq:urls"
CACHE_RESULTS = "cvent:cache:results"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj: Any):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


# ---------------------------------------------------------------------------
# URL queue
# ---------------------------------------------------------------------------

def enqueue_url(link: str, country: Optional[str], city: Optional[str], trace_id: str) -> bool:
    """
    RPUSH a venue URL job onto the Phase 2 work queue, but only if the URL
    has not been enqueued before (checked via the SEEN_URLS SET).

    Uses SADD for an atomic O(1) membership test-and-mark:
      - SADD returns 1  → new URL: also push the job onto QUEUE_URLS.
      - SADD returns 0  → already seen: skip silently.

    Returns True if the URL was newly enqueued, False if it was a duplicate.
    """
    r = get_redis()
    added = r.sadd(SEEN_URLS, link)   # 1 = new member, 0 = already present
    if not added:
        logger.debug("SKIP_DUPLICATE link=%s", link)
        return False

    payload = {
        "link": link,
        "country": country,
        "city": city,
        "trace_id": trace_id,
        "enqueued_at": _utcnow_iso(),
    }
    r.rpush(QUEUE_URLS, json.dumps(payload))
    return True


def pop_url() -> Optional[dict]:
    """
    LPOP the next URL job. Returns None when the queue is drained.
    FIFO: paired with RPUSH in enqueue_url.
    """
    raw = get_redis().lpop(QUEUE_URLS)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("Failed to decode queued URL payload; dropping: %r", raw)
        return None


def queue_len() -> int:
    return get_redis().llen(QUEUE_URLS)


# ---------------------------------------------------------------------------
# Destination tracking (Phase 1)
# ---------------------------------------------------------------------------

def is_destination_processed(country: str, city: str) -> bool:
    """Check if we have already finished link discovery for this city."""
    key = f"{city}:{country}".lower()
    return bool(get_redis().sismember(SEEN_DESTINATIONS, key))


def mark_destination_processed(country: str, city: str) -> None:
    """Mark a city as successfully explored so we skip it on restart."""
    key = f"{city}:{country}".lower()
    get_redis().sadd(SEEN_DESTINATIONS, key)


# ---------------------------------------------------------------------------
# Dead-letter queues
# ---------------------------------------------------------------------------

def push_dlq_destination(country: Optional[str], city: Optional[str], trace_id: str, error: str) -> None:
    payload = {
        "country": country,
        "city": city,
        "trace_id": trace_id,
        "error": error,
        "failed_at": _utcnow_iso(),
    }
    get_redis().rpush(DLQ_DESTINATIONS, json.dumps(payload))


def push_dlq_url(link: str, country: Optional[str], city: Optional[str], trace_id: str, error: str) -> None:
    payload = {
        "link": link,
        "country": country,
        "city": city,
        "trace_id": trace_id,
        "error": error,
        "failed_at": _utcnow_iso(),
    }
    get_redis().rpush(DLQ_URLS, json.dumps(payload))


def dlq_counts() -> dict:
    r = get_redis()
    return {
        "dlq_destinations": r.llen(DLQ_DESTINATIONS),
        "dlq_urls": r.llen(DLQ_URLS),
    }


# ---------------------------------------------------------------------------
# Result cache (flushes to Bizly API in batches of BATCH_SIZE)
# ---------------------------------------------------------------------------

def cache_push_result(venue: dict) -> int:
    """RPUSH a scraped venue onto the result cache. Returns new length."""
    return get_redis().rpush(CACHE_RESULTS, json.dumps(venue, default=_json_default))


def _atomic_drain_cache() -> list[dict]:
    """
    Atomically read + delete the current contents of CACHE_RESULTS via a
    MULTI/EXEC pipeline, and return the parsed venue dicts. If the flush
    fails downstream, callers are responsible for re-pushing.
    """
    r = get_redis()
    pipe = r.pipeline(transaction=True)
    pipe.lrange(CACHE_RESULTS, 0, -1)
    pipe.delete(CACHE_RESULTS)
    raw_items, _ = pipe.execute()

    venues: list[dict] = []
    for raw in raw_items:
        try:
            venues.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.exception("Skipping un-decodable cache entry: %r", raw)
    return venues


def _rehydrate_cache(venues: list[dict]) -> None:
    """Re-RPUSH a batch back onto CACHE_RESULTS after a failed API flush."""
    if not venues:
        return
    r = get_redis()
    pipe = r.pipeline(transaction=False)
    for v in venues:
        pipe.rpush(CACHE_RESULTS, json.dumps(v, default=_json_default))
    pipe.execute()


def _flush(venues: list[dict]) -> bool:
    """Send a batch to the Bizly API. On failure, rehydrate the cache."""
    if not venues:
        return True

    logger.info("BATCH_FLUSH_START size=%d", len(venues))
    try:
        ok = insert_venue_batch(venues)
    except Exception as e:
        logger.exception("BATCH_FLUSH_FAIL size=%d error=%s (rehydrating cache)", len(venues), e)
        _rehydrate_cache(venues)
        return False

    if ok:
        logger.info("BATCH_FLUSH_SUCCESS size=%d", len(venues))
        return True

    logger.error("BATCH_FLUSH_FAIL size=%d error=insert_venue_batch_returned_false (rehydrating cache)", len(venues))
    _rehydrate_cache(venues)
    return False


def cache_flush_if_ready(batch_size: int) -> int:
    """
    If the cache has >= batch_size entries, drain & flush it.
    Returns the number of venues flushed (0 if not ready or flush failed).
    """
    r = get_redis()
    if r.llen(CACHE_RESULTS) < batch_size:
        return 0

    venues = _atomic_drain_cache()
    if not venues:
        return 0

    ok = _flush(venues)
    return len(venues) if ok else 0


def cache_final_drain() -> int:
    """
    Unconditionally flush whatever is left in the cache. Returns count flushed.
    """
    r = get_redis()
    size = r.llen(CACHE_RESULTS)
    if size == 0:
        logger.info("FINAL_DRAIN size=0 (nothing to flush)")
        return 0

    logger.info("FINAL_DRAIN size=%d", size)
    venues = _atomic_drain_cache()
    ok = _flush(venues)
    return len(venues) if ok else 0
