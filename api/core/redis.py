from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import RedisError

from api.config import settings

logger = logging.getLogger(__name__)

_redis: Optional[Redis] = None

async def get_redis() -> Redis:
    global _redis

    if _redis is None:
        if not settings.REDIS_URL:
            raise ConnectionError(
                "REDIS_URL not configured. "
                "Redis is required for nonce storage and rate limiting."
            )

        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,          # los valores vuelven como str, no bytes
            socket_connect_timeout=5,       # falla rápido si Redis no está disponible
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,       # keep-alive para Render free tier
        )

        try:
            await _redis.ping()
            logger.info("Redis connected: %s", _sanitize_url(settings.REDIS_URL))
        except RedisError as exc:
            _redis = None
            raise ConnectionError(f"Redis connection failed: {exc}") from exc

    return _redis

async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection closed")

async def ping_redis() -> bool:
    try:
        redis = await get_redis()
        return await redis.ping()
    except Exception:
        return False

def _sanitize_url(url: str) -> str:
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        if parsed.password:
            netloc = f"{parsed.username}:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass
    return url