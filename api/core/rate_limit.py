from __future__ import annotations

import time
import logging
from typing import Optional
from fastapi import HTTPException, Request

from api.config import settings
from api.core.redis import get_redis

logger = logging.getLogger(__name__)

async def limiter(
    request: Request,
    max_requests: int = 60,
    window: int = 60,
    key_prefix: Optional[str] = None,
) -> None:
    """
    Sliding window rate limiter.

    Args:
        request:      FastAPI Request (para extraer IP y path).
        max_requests: Máximo de requests permitidos en la ventana.
        window:       Tamaño de la ventana en segundos.
        key_prefix:   Prefijo Redis personalizado. Por defecto usa IP + path.

    Raises:
        HTTPException(429) si se excede el límite.
        No lanza nada si Redis no está disponible (degradación graceful).
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    ip   = _get_client_ip(request)
    path = request.url.path
    key  = f"rl:{key_prefix or path}:{ip}"

    try:
        redis = await get_redis()
        now   = time.time()
        cutoff = now - window
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", cutoff)   # purgar fuera de ventana
        pipe.zadd(key, {str(now): now})              # registrar request actual
        pipe.zcard(key)                              # contar en ventana
        pipe.expire(key, window + 1)                 # TTL ligeramente mayor que ventana
        results = await pipe.execute()

        count = results[2]

        if count > max_requests:
            logger.warning(
                "Rate limit exceeded: ip=%s path=%s count=%d limit=%d",
                ip, path, count, max_requests,
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Limit: {max_requests} per {window}s.",
                headers={
                    "Retry-After": str(window),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(now + window)),
                },
            )

        remaining = max(0, max_requests - count)
        request.state.rate_limit_remaining = remaining

    except HTTPException:
        raise  # propagar el 429

    except Exception as exc:
        logger.warning("Rate limiting unavailable (Redis error): %s", exc)

def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"