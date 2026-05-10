from __future__ import annotations

import logging

from typing import Optional
from fastapi import Depends, HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from api.core.auth import decode_token, is_admin, is_token_blacklisted
from api.core.rate_limit import limiter
from api.db.session import get_db
from api.config import settings

logger    = logging.getLogger(__name__)
security  = HTTPBearer(auto_error=False)

async def _extract_and_validate_token(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    wallet = payload.get("sub")
    if not wallet:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    if await is_token_blacklisted(payload):
        raise HTTPException(status_code=401, detail="Token has been revoked")
    return payload

async def get_current_wallet(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> str:
    payload = await _extract_and_validate_token(credentials)
    return payload["sub"]

async def get_current_wallet_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[str]:
    if not credentials:
        return None
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    wallet = payload.get("sub")
    if not wallet:
        return None
    try:
        if await is_token_blacklisted(payload):
            return None
    except Exception:
        pass
    return wallet

async def require_admin(
    request:     Request,
    wallet:      str = Depends(get_current_wallet),
) -> str:
    await limiter(request, max_requests=30, window=60, key_prefix="admin")

    if not is_admin(wallet):
        # Log de intento no autorizado con IP para auditoría.
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )
        logger.warning(
            "Unauthorized admin access attempt | wallet=%s ip=%s path=%s",
            wallet[:10], ip, request.url.path,
        )
        raise HTTPException(status_code=403, detail="Admin access required")
    return wallet

async def require_admin_api_key(request: Request) -> bool:
    api_key = request.headers.get(settings.API_KEY_HEADER)
    if not api_key or api_key != settings.ADMIN_API_KEY:
        logger.warning(
            "Invalid API key attempt | path=%s", request.url.path
        )
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True

async def require_admin_dual(
    request: Request,
    wallet:  str  = Depends(require_admin),
    _:       bool = Depends(require_admin_api_key),
) -> str:
    return wallet