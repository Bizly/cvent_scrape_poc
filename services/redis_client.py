import time
import logging
import redis

from config import REDIS_URL

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None

_MAX_ATTEMPTS = 10
_RETRY_DELAY_S = 3


def get_redis() -> redis.Redis:
    """
    Returns a process-wide singleton Redis client connected via REDIS_URL.
    decode_responses=True so LIST values come back as str rather than bytes.

    On first call, retries the connection up to _MAX_ATTEMPTS times with a
    fixed delay between attempts so the scraper survives a brief Redis
    startup race even when the healthcheck condition fires slightly early.
    """
    global _client
    if _client is not None:
        return _client

    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            client.ping()
            _client = client
            logger.info("REDIS_CONNECTED url=%s attempt=%d", REDIS_URL, attempt)
            return _client
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            last_error = exc
            logger.warning(
                "REDIS_CONNECT_RETRY attempt=%d/%d url=%s error=%s",
                attempt,
                _MAX_ATTEMPTS,
                REDIS_URL,
                exc,
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_DELAY_S)

    raise RuntimeError(
        f"Could not connect to Redis at {REDIS_URL} after {_MAX_ATTEMPTS} attempts. "
        f"Last error: {last_error}"
    ) from last_error
