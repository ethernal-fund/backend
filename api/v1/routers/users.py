from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.core.auth import (
    build_siwe_message,
    consume_nonce,
    consume_refresh_token,
    create_access_token,
    create_refresh_token,
    generate_nonce,
    is_token_blacklisted,
    revoke_all_refresh_tokens,
    verify_signature,
    blacklist_token,
    decode_access_token,
)
from api.core.dependencies import get_current_wallet
from api.core.exceptions import InvalidSignature, SurveyAlreadyCompleted
from api.core.rate_limit import limiter
from api.db.repositories.user_repo import UserRepository
from api.db.session import get_db
from api.schemas.users import (
    AuthRequest,
    AuthResponse,
    AuthStatusResponse,
    LogoutRequest,
    NonceRequest,
    NonceResponse,
    RefreshRequest,
    RefreshResponse,
    SurveySubmit,
    UserOut,
)

logger   = logging.getLogger(__name__)
router   = APIRouter(prefix="/users", tags=["users"])
security = HTTPBearer(auto_error=False)

# Header RFC 6750 para respuestas 401 en endpoints que requieren auth
_WWW_AUTH_BEARER = {"WWW-Authenticate": 'Bearer realm="ethernal"'}


def _token_from_credentials(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> str:
    """Extrae el token crudo del header Authorization, o lanza 401."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers=_WWW_AUTH_BEARER,
        )
    return credentials.credentials


def _decode_or_401(token: str) -> dict:
    """Decodifica el JWT o lanza 401 con el header correcto."""
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers=_WWW_AUTH_BEARER,
        )
    return payload


@router.post(
    "/nonce",
    response_model=NonceResponse,
    summary="Solicitar nonce SIWE",
    description=(
        "Genera un nonce de un solo uso para el flujo Sign-In With Ethereum. "
        "El nonce expira en 5 minutos. "
        "El campo `message` contiene el string exacto que el frontend debe firmar."
    ),
)
async def request_nonce(
    payload: NonceRequest,
    request: Request,
) -> NonceResponse:
    await limiter(request, max_requests=10, window=60, key_prefix="nonce")

    # generate_nonce ahora devuelve (nonce, message) — ambos se persisten en Redis
    nonce, message = await generate_nonce(payload.wallet_address)

    logger.info("Nonce issued | wallet=%s", payload.wallet_address[:10])
    return NonceResponse(nonce=nonce, message=message)


@router.post(
    "/auth",
    response_model=AuthResponse,
    summary="Autenticar con firma SIWE",
    description=(
        "Verifica la firma del mensaje SIWE y emite un access token (JWT) "
        "y un refresh token. El access token expira en minutos; el refresh "
        "token dura días y permite obtener nuevos access tokens sin re-firmar."
    ),
)
async def authenticate(
    payload: AuthRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
) -> AuthResponse:
    await limiter(request, max_requests=10, window=60, key_prefix="auth")

    # 1. Consumir nonce atómicamente — devuelve {"nonce": str, "message": str} o None.
    #    Si expiró o fue consumido (ej: doble submit), falla aquí.
    stored = await consume_nonce(payload.wallet_address)
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nonce expired or not found. Request a new one via POST /users/nonce.",
            headers=_WWW_AUTH_BEARER,
        )

    # 2. Verificar que el nonce del payload coincide con el almacenado.
    if stored["nonce"] != payload.nonce:
        logger.warning(
            "Nonce mismatch | wallet=%s expected=%s received=%s",
            payload.wallet_address[:10], stored["nonce"][:8], payload.nonce[:8],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nonce mismatch.",
            headers=_WWW_AUTH_BEARER,
        )

    # 3. Obtener el mensaje original — el que el usuario realmente firmó.
    #    NUNCA reconstruir el mensaje aquí: el Issued At sería distinto y la firma fallaría.
    original_message = stored.get("message")
    if not original_message:
        # Nonce en formato legacy (string plano sin mensaje guardado).
        # El usuario debe solicitar un nonce nuevo.
        logger.warning(
            "Nonce legacy sin mensaje guardado | wallet=%s — se rechaza la auth.",
            payload.wallet_address[:10],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nonce expired or not found. Request a new one via POST /users/nonce.",
            headers=_WWW_AUTH_BEARER,
        )

    # 4. Verificar firma ECDSA contra el mensaje original guardado en Redis.
    if not verify_signature(payload.wallet_address, payload.signature, original_message):
        raise InvalidSignature()

    # 5. Crear o actualizar el usuario en la DB.
    repo          = UserRepository(db)
    user, created = await repo.get_or_create(payload.wallet_address)

    # 6. Emitir tokens.
    access_token  = create_access_token(payload.wallet_address)
    refresh_token = await create_refresh_token(payload.wallet_address)

    logger.info(
        "%s authenticated | wallet=%s",
        "New user" if created else "User",
        payload.wallet_address[:10],
    )

    return AuthResponse(
        access_token       = access_token,
        refresh_token      = refresh_token,
        token_type         = "bearer",
        wallet_address     = payload.wallet_address.lower(),
        expires_in         = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        is_new_user        = created,
    )


@router.post(
    "/auth/refresh",
    response_model=RefreshResponse,
    summary="Rotar refresh token",
    description=(
        "Consume el refresh token actual e invalida inmediatamente (Refresh Token Rotation). "
        "Emite un nuevo access token y un nuevo refresh token. "
        "Si el refresh token fue ya usado o expiró, devuelve 401."
    ),
)
async def rotate_refresh_token(
    payload: RefreshRequest,
    request: Request,
) -> RefreshResponse:
    await limiter(request, max_requests=10, window=60, key_prefix="refresh")

    # consume_refresh_token hace GETDEL — atómico, sin race condition.
    wallet = await consume_refresh_token(payload.refresh_token)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalid or expired.",
            headers=_WWW_AUTH_BEARER,
        )

    new_access  = create_access_token(wallet)
    new_refresh = await create_refresh_token(wallet)
    logger.info("Tokens rotated | wallet=%s", wallet[:10])
    return RefreshResponse(
        access_token       = new_access,
        refresh_token      = new_refresh,
        token_type         = "bearer",
        expires_in         = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.delete(
    "/auth/session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión (dispositivo actual)",
    description=(
        "Blacklistea el access token actual e invalida el refresh token enviado. "
        "Siempre devuelve 204 para no revelar si los tokens eran válidos."
    ),
)
async def logout(
    payload:     LogoutRequest,
    request:     Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> None:
    await limiter(request, max_requests=20, window=60, key_prefix="logout")

    wallet_log = "unknown"
    if credentials and credentials.credentials:
        token = credentials.credentials
        p = decode_access_token(token)
        if p:
            wallet_log = p.get("sub", "unknown")[:10]
        await blacklist_token(token)

    if payload.refresh_token:
        await consume_refresh_token(payload.refresh_token)

    logger.info("Session closed | wallet=%s", wallet_log)


@router.post(
    "/auth/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión en todos los dispositivos",
    description=(
        "Revoca todos los refresh tokens del wallet autenticado y blacklistea "
        "el access token actual. Útil ante sospecha de compromiso de cuenta."
    ),
)
async def revoke_all_sessions(
    request:     Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> None:
    await limiter(request, max_requests=5, window=60, key_prefix="revoke-all")

    token   = _token_from_credentials(credentials)
    payload = _decode_or_401(token)
    if await is_token_blacklisted(payload):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked.",
            headers=_WWW_AUTH_BEARER,
        )

    wallet = payload["sub"]

    revoked = await revoke_all_refresh_tokens(wallet)
    await blacklist_token(token)

    logger.info(
        "All sessions revoked | wallet=%s refresh_tokens_revoked=%d",
        wallet[:10], revoked,
    )


@router.get(
    "/auth/status",
    response_model=AuthStatusResponse,
    summary="Verificar estado del access token",
    description=(
        "Verifica si el access token sigue siendo válido (no expirado, no blacklisteado). "
        "No toca la DB — es una validación rápida contra Redis + JWT."
    ),
)
async def auth_status(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> AuthStatusResponse:
    if not credentials or not credentials.credentials:
        return AuthStatusResponse(authenticated=False, wallet_address=None, expires_at=None)

    payload = decode_access_token(credentials.credentials)
    if not payload:
        return AuthStatusResponse(authenticated=False, wallet_address=None, expires_at=None)

    if await is_token_blacklisted(payload):
        return AuthStatusResponse(authenticated=False, wallet_address=None, expires_at=None)

    from datetime import datetime, timezone
    exp = payload.get("exp", 0)
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()

    return AuthStatusResponse(
        authenticated  = True,
        wallet_address = payload["sub"],
        expires_at     = expires_at,
    )


@router.get(
    "/me",
    response_model=UserOut,
    summary="Perfil del usuario autenticado",
)
async def get_me(
    wallet: str          = Depends(get_current_wallet),
    db:     AsyncSession = Depends(get_db),
) -> UserOut:
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post(
    "/survey",
    response_model=UserOut,
    summary="Enviar encuesta de perfil",
    description="Solo puede enviarse una vez por wallet. Devuelve 409 si ya fue completada.",
)
async def submit_survey(
    payload: SurveySubmit,
    wallet:  str          = Depends(get_current_wallet),
    db:      AsyncSession = Depends(get_db),
) -> UserOut:
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.survey_completed:
        raise SurveyAlreadyCompleted()

    updated = await repo.update_survey(wallet, payload.model_dump())
    logger.info("Survey completed | wallet=%s", wallet[:10])
    return updated