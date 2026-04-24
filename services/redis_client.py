import redis

from config import REDIS_URL

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """
    Returns a process-wide singleton Redis client connected via REDIS_URL.
    decode_responses=True so LIST values come back as str rather than bytes.
    """
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _client
