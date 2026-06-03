from __future__ import annotations

import logging
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.config import settings
from api.core.auth import (
    decode_access_token,
    extract_token_from_request,
    is_admin,
    is_token_blacklisted,
)
from api.core.rate_limit import limiter

logger = logging.getLogger(__name__)
_bearer_scheme = HTTPBearer(auto_error=False)
_WWW_AUTH = {"WWW-Authenticate": 'Bearer realm="ethernal"'}

async def _validate_token_from_request(request: Request) -> dict:
    """
    Extrae y valida el access token desde cookie o header.
    Lanza HTTP 401 con WWW-Authenticate si algo falla.
    NO valida el audience — eso lo hacen las dependencias específicas.
    Returns:
        payload del JWT decodificado
    """
    raw = extract_token_from_request(request)
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers=_WWW_AUTH,
        )
    payload = decode_access_token(raw)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers=_WWW_AUTH,
        )
    wallet = payload.get("sub")
    if not wallet:
        raise HTTPException(
            status_code=401,
            detail="Malformed token payload",
            headers=_WWW_AUTH,
        )
    if await is_token_blacklisted(payload):
        raise HTTPException(
            status_code=401,
            detail="Token has been revoked",
            headers=_WWW_AUTH,
        )
    return payload

async def _validate_token_with_audience(
    request: Request,
    expected_audience: str,
) -> Tuple[str, str]:
    """
    Valida el token y verifica que el audience coincida con el esperado.
    Returns:
        (wallet_address, audience)
    """
    payload = await _validate_token_from_request(request)

    token_audience = payload.get("aud")
    if token_audience != expected_audience:
        logger.warning(
            "Audience mismatch | expected=%s got=%s wallet=%s",
            expected_audience,
            token_audience,
            payload.get("sub", "unknown")[:10],
        )
        raise HTTPException(
            status_code=403,
            detail=f"Token not authorized for {expected_audience} endpoints",
            headers=_WWW_AUTH,
        )

    return payload["sub"], token_audience

async def get_current_wallet(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    """
    Retorna el wallet address del usuario autenticado.
    Acepta token desde cookie HttpOnly o Authorization: Bearer header.
    NO valida audience — útil para endpoints que aceptan cualquier token válido.
    """
    payload = await _validate_token_from_request(request)
    return payload["sub"]

async def get_current_payload(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> dict:
    """
    Retorna el payload JWT completo (útil cuando el router necesita jti o iat).
    """
    return await _validate_token_from_request(request)

async def get_current_audience(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    """
    Retorna el audience del token actual.
    Útil para endpoints que necesitan saber en qué contexto está el usuario.
    """
    payload = await _validate_token_from_request(request)
    return payload.get("aud", "unknown")


async def get_current_wallet_optional(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> Optional[str]:
    """
    Como get_current_wallet pero retorna None en vez de lanzar 401.
    Útil para endpoints que funcionan con o sin autenticación.
    """
    raw = extract_token_from_request(request)
    if not raw:
        return None
    payload = decode_access_token(raw)
    if not payload:
        return None
    wallet = payload.get("sub")
    if not wallet:
        return None
    try:
        if await is_token_blacklisted(payload):
            return None
    except Exception:
        logger.warning("Redis unavailable in get_current_wallet_optional")
    return wallet

async def get_current_wallet_retirement(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    """
    Requiere JWT con audience = 'retirement'.
    Usado en endpoints del protocolo principal:
      - /funds/*
      - /treasury/*
      - /protocols/*
      - /users/me
      - /users/survey
    """
    wallet, _ = await _validate_token_with_audience(request, "retirement")
    return wallet

async def get_current_wallet_sale(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> str:
    """
    Requiere JWT con audience = 'sale'.
    Usado en endpoints de token sale:
      - /sale/my-purchase
      - /sale/vesting/schedule
      - /sale/vesting/round-info
    """
    wallet, _ = await _validate_token_with_audience(request, "sale")
    return wallet

async def get_current_wallet_optional_with_audience(
    request: Request,
    _: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
) -> Tuple[Optional[str], Optional[str]]:
    """
    Retorna (wallet, audience) o (None, None) si no hay token válido.
    Útil para endpoints que aceptan autenticación opcional pero necesitan
    saber el audience si existe.
    """
    raw = extract_token_from_request(request)
    if not raw:
        return None, None
    payload = decode_access_token(raw)
    if not payload:
        return None, None
    wallet = payload.get("sub")
    audience = payload.get("aud")
    if not wallet or not audience:
        return None, None
    try:
        if await is_token_blacklisted(payload):
            return None, None
    except Exception:
        logger.warning("Redis unavailable in get_current_wallet_optional_with_audience")
    return wallet, audience

async def require_admin(
    request: Request,
    wallet: str = Depends(get_current_wallet),
) -> str:
    """
    Verifica que el wallet autenticado sea admin.
    Aplica rate limiting específico para endpoints de administración.
    NO valida audience porque los endpoints de admin pueden ser llamados
    con tokens de cualquier audience (siempre que el wallet sea admin).
    """
    await limiter(request, max_requests=30, window=60, key_prefix="admin")
    if not is_admin(wallet):
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )
        logger.warning(
            "Unauthorized admin attempt | wallet=%.10s ip=%s path=%s",
            wallet, ip, request.url.path,
        )
        raise HTTPException(status_code=403, detail="Admin access required")
    return wallet

async def require_admin_retirement(
    request: Request,
    wallet: str = Depends(get_current_wallet_retirement),
) -> str:
    """
    Admin específico para endpoints de retirement.
    Requiere token con audience='retirement'.
    """
    return await require_admin(request, wallet)

async def require_admin_sale(
    request: Request,
    wallet: str = Depends(get_current_wallet_sale),
) -> str:
    """
    Admin específico para endpoints de token sale.
    Requiere token con audience='sale'.
    """
    return await require_admin(request, wallet)

async def require_admin_api_key(request: Request) -> bool:
    """
    Verifica el header X-API-Key (segundo factor para endpoints críticos).
    """
    api_key = request.headers.get(settings.API_KEY_HEADER, "")
    if not api_key or api_key != settings.ADMIN_API_KEY:
        logger.warning("Invalid API key | path=%s", request.url.path)
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True

async def require_admin_dual(
    request: Request,
    wallet: str = Depends(require_admin),
    _: bool = Depends(require_admin_api_key),
) -> str:
    """JWT admin + API key — para operaciones destructivas."""
    return wallet