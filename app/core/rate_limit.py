"""Small fixed-window rate limiter for unauthenticated endpoints.

Counts live in Redis so every API process (and every replica) shares them. If
Redis is unreachable the limiter falls back to per-process memory rather than
failing open: a weaker limit beats none, and it never blocks a legitimate
request just because the broker is down.
"""

import logging
import time

import redis
from fastapi import Request

from app.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None
# key -> (window_started_at, count); only used when Redis is unavailable.
_memory: dict[str, tuple[float, int]] = {}


def _get_client() -> redis.Redis | None:
    global _client
    if _client is None:
        try:
            _client = redis.from_url(settings.REDIS_URL, socket_timeout=2)
        except Exception as exc:
            logger.warning("Rate limiter could not create a Redis client: %s", exc)
            return None
    return _client


def hit(key: str, limit: int, window_seconds: int) -> bool:
    """Record one attempt against key. Returns False once limit is exceeded."""
    client = _get_client()
    if client is not None:
        try:
            redis_key = f"ratelimit:{key}"
            pipe = client.pipeline()
            # nx: the window starts at the first attempt and is not extended by
            # later ones. SET NX EX rather than EXPIRE NX, which needs Redis 7.
            pipe.set(redis_key, 0, ex=window_seconds, nx=True)
            pipe.incr(redis_key)
            _, count = pipe.execute()
            return int(count) <= limit
        except Exception as exc:
            logger.warning("Rate limiter falling back to memory, Redis failed: %s", exc)

    now = time.time()
    started, count = _memory.get(key, (now, 0))
    if now - started >= window_seconds:
        started, count = now, 0
    count += 1
    _memory[key] = (started, count)
    return count <= limit


def client_ip(request: Request) -> str:
    """The caller's IP as seen by the proxy closest to us.

    Every deployment sits behind one reverse proxy (Caddy, Render, Railway),
    which appends the address it received the connection from to
    X-Forwarded-For. Taking the last entry means a client cannot dodge the
    limit by sending its own X-Forwarded-For: anything it supplies ends up to
    the left of the value the proxy adds.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"
