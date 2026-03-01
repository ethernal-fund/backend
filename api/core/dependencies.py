from fastapi import Depends, HTTPException, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging

from api.core.auth import decode_token, is_admin
from api.db.session import get_db
from api.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

async def get_current_wallet(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> str:
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")

    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    wallet = payload.get("sub")
    if not wallet:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return wallet

async def get_current_wallet_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> Optional[str]:
    if not credentials:
        return None
    payload = decode_token(credentials.credentials)
    if not payload:
        return None
    return payload.get("sub")

async def require_admin(
    wallet: str = Depends(get_current_wallet),
) -> str:
    if not is_admin(wallet):
        raise HTTPException(status_code=403, detail="Admin access required")
    return wallet

async def require_admin_api_key(request: Request) -> bool:
    api_key = request.headers.get(settings.API_KEY_HEADER)
    if api_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True