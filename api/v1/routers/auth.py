"""
api/v1/routers/auth.py

Único punto de entrada para autenticación Sign-In With Ethereum (EIP-4361).

RESPONSABILIDAD DE ESTE ARCHIVO:
  Todo lo relacionado con identidad y sesión vive aquí.
  Soporta múltiples audiences: 'retirement' (protocolo principal) y 'sale' (token sale).

ENDPOINTS:
  GET  /auth/nonce              → genera nonce + mensaje SIWE para audience específico
  POST /auth/verify-siwe        → verifica firma SIWE → emite JWT con audience
  POST /auth/refresh            → rota refresh token → nuevo JWT (mismo audience)
  POST /auth/logout             → invalida JWT + revoca refresh tokens del wallet
  POST /auth/revoke-all         → invalida TODOS los refresh tokens del wallet
  GET  /auth/me                 → info del wallet autenticado (sin DB)
  GET  /auth/status             → verifica validez del JWT (sin DB, sin chain)

SEGURIDAD:
  - Nonce consumido atómicamente (GETDEL) → anti-replay
  - Mensaje SIWE incluye Audience → tokens no son intercambiables entre contextos
  - JWT con claim 'aud' estándar → validación en cada endpoint
  - Refresh token rotation (one-shot) + blacklist JWT
  - Rate limiting en todos los endpoints públicos
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import jwt
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.core.auth import (
    blacklist_token,
    build_siwe_message,
    consume_nonce,
    consume_refresh_token,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    extract_token_from_request,
    generate_nonce,
    is_admin,
    is_token_blacklisted,
    revoke_all_refresh_tokens,
    verify_eoa_signature,
)
from api.core.dependencies import get_current_wallet
from api.core.rate_limit import limiter
from api.db.repositories.user_repo import UserRepository
from api.db.session import get_db
from api.schemas.users import (
    AuthResponse,
    AuthStatusResponse,
    NonceResponse,
    RefreshRequest,
    RefreshResponse,
    LogoutRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ─────────────────────────────────────────────────────────────────────────────
# Enums y constantes
# ─────────────────────────────────────────────────────────────────────────────

class TokenAudience(str, Enum):
    """Contextos de autenticación soportados por el sistema."""
    RETIREMENT = "retirement"   # Endpoints de protocolo principal (/funds, /treasury, /protocols)
    SALE       = "sale"         # Endpoints de token sale (/sale/*)


# Constantes de validación SIWE
_EXPECTED_DOMAIN = settings.APP_DOMAIN or "ethernal.fund"
_SIWE_MAX_AGE_SEC = settings.NONCE_TTL_SECONDS
_WWW_AUTH = {"WWW-Authenticate": 'Bearer realm="ethernal"'}
_WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Regex para extraer Audience del mensaje SIWE
# Formato esperado: "Audience: retirement" o "Audience: sale"
_AUDIENCE_RE = re.compile(r"^Audience:\s*(retirement|sale)$", re.MULTILINE)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas específicos de auth
# ─────────────────────────────────────────────────────────────────────────────

class SIWEPayload(BaseModel):
    """
    Body de POST /auth/verify-siwe.
    """
    message: str = Field(..., description="Mensaje EIP-4361 completo tal como fue firmado")
    signature: str = Field(
        ..., min_length=132, max_length=132,
        description="Firma ECDSA: 0x + 130 caracteres hex"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de parseo y validación
# ─────────────────────────────────────────────────────────────────────────────

def _parse_siwe_message(message: str) -> dict:
    """
    Parsea un mensaje EIP-4361 y devuelve sus campos críticos.

    Formato esperado (EIP-4361 + campo Audience personalizado):
      {domain} wants you to sign in with your Ethereum account:
      {address}

      {statement}

      URI: {uri}
      Version: 1
      Chain ID: {chain_id}
      Nonce: {nonce}
      Audience: {audience}
      Issued At: {issued_at}

    Raises:
      ValueError: si el formato es inválido o falta algún campo obligatorio.
    """
    lines = message.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("Mensaje demasiado corto para ser un SIWE válido")

    # Línea 0: "{domain} wants you to sign in with your Ethereum account:"
    marker = " wants you to sign in"
    if marker not in lines[0]:
        raise ValueError("Línea 0 no tiene formato EIP-4361")
    domain = lines[0].split(marker)[0].strip()

    # Línea 1: dirección Ethereum
    address = lines[1].strip()
    if not _WALLET_RE.match(address):
        raise ValueError(f"Dirección inválida en el mensaje SIWE: {address!r}")

    # Extraer campos del resto de líneas
    fields: dict[str, str] = {}
    for line in lines[2:]:
        if ": " in line:
            key, _, val = line.partition(": ")
            fields[key.strip()] = val.strip()

    nonce = fields.get("Nonce", "")
    chain_id = fields.get("Chain ID", "")
    uri = fields.get("URI", "")
    issued_at = fields.get("Issued At", "")
    audience = fields.get("Audience", "")

    if not nonce:
        raise ValueError("Campo 'Nonce' ausente en el mensaje SIWE")
    if not chain_id:
        raise ValueError("Campo 'Chain ID' ausente en el mensaje SIWE")
    if not audience:
        raise ValueError("Campo 'Audience' ausente en el mensaje SIWE")
    if audience not in ["retirement", "sale"]:
        raise ValueError(f"Valor de Audience inválido: {audience}")

    return {
        "domain": domain,
        "address": address.lower(),
        "nonce": nonce,
        "chain_id": chain_id,
        "uri": uri,
        "issued_at": issued_at,
        "audience": audience,
    }


def _validate_siwe_fields(parsed: dict) -> None:
    """
    Valida los campos del mensaje SIWE contra los valores esperados del servidor.

    Protege contra ataques de phishing y asegura que el audience sea válido.
    """
    # Validar dominio
    if parsed["domain"] and parsed["domain"] != _EXPECTED_DOMAIN:
        logger.warning(
            "SIWE domain mismatch | expected=%s got=%s",
            _EXPECTED_DOMAIN, parsed["domain"],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SIWE domain inválido. Esperado: '{_EXPECTED_DOMAIN}'",
            headers=_WWW_AUTH,
        )

    # Validar chain ID — debe coincidir con el chain de la sale
    expected_chain_id = str(settings.SALE_CHAIN_ID)
    if parsed["chain_id"] and parsed["chain_id"] != expected_chain_id:
        logger.warning(
            "SIWE chain_id mismatch | expected=%s got=%s",
            expected_chain_id, parsed["chain_id"],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"SIWE chain ID inválido. Esperado: {expected_chain_id}",
            headers=_WWW_AUTH,
        )

    # Validar antigüedad del mensaje
    issued_at_str = parsed.get("issued_at", "")
    if issued_at_str:
        try:
            issued_at = datetime.fromisoformat(issued_at_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - issued_at).total_seconds()
            if age_seconds > _SIWE_MAX_AGE_SEC:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=(
                        f"Mensaje SIWE expirado ({int(age_seconds)}s de antigüedad, "
                        f"máximo {_SIWE_MAX_AGE_SEC}s). Solicitá un nuevo nonce."
                    ),
                    headers=_WWW_AUTH,
                )
        except HTTPException:
            raise
        except ValueError:
            pass  # formato de fecha desconocido → tolerar


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints públicos (no requieren autenticación)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/nonce",
    response_model=NonceResponse,
    summary="Genera nonce SIWE para un audience específico",
    description=(
        "Genera un nonce criptográfico de un solo uso y devuelve el mensaje "
        "EIP-4361 listo para firmar. El audience determina qué endpoints podrá "
        "acceder el token resultante.\n\n"
        "audience=retirement → para endpoints del protocolo principal\n"
        "audience=sale       → para endpoints de token sale"
    ),
)
async def get_nonce(
    request: Request,
    address: str,
    audience: TokenAudience = TokenAudience.RETIREMENT,
) -> NonceResponse:
    """
    GET /auth/nonce?address=0x...&audience=retirement

    Parámetros:
      - address: Dirección Ethereum (checksum o lowercase)
      - audience: Contexto de autenticación ('retirement' o 'sale')
    """
    await limiter(request, max_requests=10, window=60, key_prefix="nonce")

    address = address.strip().lower()
    normalized = address if address.startswith("0x") else f"0x{address}"
    if not _WALLET_RE.match(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dirección Ethereum inválida",
        )

    try:
        nonce, message = await generate_nonce(normalized, audience.value)
    except Exception as exc:
        logger.error("Error generando nonce | wallet=%s: %s", normalized[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    logger.debug("Nonce generado | wallet=%s audience=%s", normalized[:10], audience.value)
    return NonceResponse(nonce=nonce, message=message)


@router.post(
    "/verify-siwe",
    response_model=AuthResponse,
    summary="Verifica firma SIWE y emite JWT con audience",
    description=(
        "Verifica la firma del mensaje SIWE. El audience extraído del mensaje "
        "determina qué endpoints podrá acceder el token.\n\n"
        "Ejemplo de mensaje SIWE:\n"
        "```\n"
        "ethernal.fund wants you to sign in with your Ethereum account:\n"
        "0x...\n\n"
        "Sign in to Ethernal Fund (retirement)\n\n"
        "URI: https://ethernal.fund\n"
        "Version: 1\n"
        "Chain ID: 11155111\n"
        "Nonce: abc123...\n"
        "Audience: retirement\n"
        "Issued At: 2024-01-01T00:00:00Z\n"
        "```"
    ),
)
async def verify_siwe(
    payload: SIWEPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """
    POST /auth/verify-siwe
    Body: { message: string, signature: string }

    Flujo:
      1. Parsear el mensaje EIP-4361
      2. Validar domain, chain_id, antigüedad y audience
      3. Consumir el nonce (GETDEL atómico)
      4. Verificar firma ECDSA
      5. Crear/actualizar usuario en DB
      6. Emitir JWT + refresh token con el audience del mensaje
    """
    await limiter(request, max_requests=10, window=60, key_prefix="verify-siwe")

    # 1. Parsear mensaje
    try:
        parsed = _parse_siwe_message(payload.message)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mensaje SIWE inválido: {exc}",
        )

    address = parsed["address"]
    nonce = parsed["nonce"]
    audience = parsed["audience"]

    # 2. Validar campos
    _validate_siwe_fields(parsed)

    # 3. Consumir nonce atómicamente
    try:
        stored = await consume_nonce(address)
    except Exception as exc:
        logger.error("Error consumiendo nonce | wallet=%s: %s", address[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    if not stored:
        logger.warning("Nonce no encontrado o expirado | wallet=%s", address[:10])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nonce inválido o expirado. Solicitá uno nuevo en GET /auth/nonce.",
            headers=_WWW_AUTH,
        )

    # stored puede ser string o dict (compatibilidad)
    if isinstance(stored, dict):
        expected_nonce = stored.get("nonce", "")
        stored_audience = stored.get("audience")
    else:
        expected_nonce = stored
        stored_audience = None

    if expected_nonce != nonce:
        logger.warning(
            "Nonce mismatch | wallet=%s stored=%.8s got=%.8s",
            address[:10], expected_nonce, nonce,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nonce no coincide.",
            headers=_WWW_AUTH,
        )

    # Verificar que el audience del nonce coincide con el del mensaje
    if stored_audience and stored_audience != audience:
        logger.warning(
            "Audience mismatch | wallet=%s stored=%s got=%s",
            address[:10], stored_audience, audience,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El audience del mensaje no coincide con el nonce solicitado.",
            headers=_WWW_AUTH,
        )

    # 4. Verificar firma ECDSA
    if not verify_eoa_signature(address, payload.signature, payload.message):
        logger.warning("Firma SIWE inválida | wallet=%s", address[:10])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firma inválida.",
            headers=_WWW_AUTH,
        )

    # 5. Crear o actualizar usuario en DB
    try:
        repo = UserRepository(db)
        user, created = await repo.get_or_create(address)
    except Exception as exc:
        logger.error("Error en get_or_create | wallet=%s: %s", address[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Error al registrar el usuario",
        )

    # 6. Emitir tokens con audience específico
    try:
        access_token = create_access_token(address, audience)
        refresh_token = await create_refresh_token(address, audience)
    except Exception as exc:
        logger.error("Error emitiendo tokens | wallet=%s: %s", address[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    logger.info(
        "%s autenticado | wallet=%s audience=%s",
        "Nuevo usuario" if created else "Usuario",
        address[:10],
        audience,
    )

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        wallet_address=address,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        is_new_user=created,
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Rota el refresh token y emite nuevo JWT",
)
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
) -> RefreshResponse:
    """
    POST /auth/refresh
    Body: { refresh_token: string }

    Consume el refresh token actual (one-shot) y emite un nuevo par
    access + refresh token con el MISMO audience que el token original.
    """
    await limiter(request, max_requests=10, window=60, key_prefix="refresh")

    try:
        wallet, audience = await consume_refresh_token(payload.refresh_token)
    except Exception as exc:
        logger.error("Error consumiendo refresh token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido o expirado.",
            headers=_WWW_AUTH,
        )

    try:
        new_access = create_access_token(wallet, audience)
        new_refresh = await create_refresh_token(wallet, audience)
    except Exception as exc:
        logger.error("Error emitiendo tokens en refresh | wallet=%s: %s", wallet[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    logger.debug("Token rotado | wallet=%s audience=%s", wallet[:10], audience)
    return RefreshResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalida el JWT activo y cierra sesión",
)
async def logout(
    request: Request,
    body: LogoutRequest = LogoutRequest(),
) -> Response:
    """
    POST /auth/logout
    Authorization: Bearer <token>
    Body: { refresh_token?: string } (opcional)

    Blacklistea el JWT activo y revoca TODOS los refresh tokens del wallet.
    """
    await limiter(request, max_requests=20, window=60, key_prefix="logout")

    raw_token = extract_token_from_request(request)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso requerido",
            headers=_WWW_AUTH,
        )

    payload = decode_access_token(raw_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers=_WWW_AUTH,
        )

    wallet = payload.get("sub", "unknown")

    try:
        # Revocar refresh token específico si viene en el body
        if body.refresh_token:
            await consume_refresh_token(body.refresh_token)

        # Blacklist del JWT activo
        await blacklist_token(raw_token)

        # Revocar todos los refresh tokens del wallet
        revoked = await revoke_all_refresh_tokens(wallet)
    except Exception as exc:
        logger.error("Error en logout | wallet=%s: %s", wallet[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    logger.info("Logout | wallet=%s refresh_tokens_revocados=%d", wallet[:10], revoked)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cierra sesión en todos los dispositivos",
)
async def revoke_all(
    request: Request,
) -> Response:
    """
    POST /auth/revoke-all
    Authorization: Bearer <token>

    Revoca TODOS los refresh tokens del wallet autenticado y blacklistea el JWT.
    """
    await limiter(request, max_requests=5, window=60, key_prefix="revoke-all")

    raw_token = extract_token_from_request(request)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso requerido",
            headers=_WWW_AUTH,
        )

    payload = decode_access_token(raw_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers=_WWW_AUTH,
        )

    wallet = payload.get("sub", "unknown")

    try:
        if await is_token_blacklisted(payload):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token ya fue revocado.",
                headers=_WWW_AUTH,
            )
        revoked = await revoke_all_refresh_tokens(wallet)
        await blacklist_token(raw_token)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error en revoke-all | wallet=%s: %s", wallet[:10], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio temporalmente no disponible",
        )

    logger.info(
        "Revoke-all | wallet=%s refresh_tokens_revocados=%d",
        wallet[:10], revoked,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints protegidos (requieren autenticación)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/me",
    summary="Info del wallet autenticado",
)
async def me(
    request: Request,
    wallet: str = Depends(get_current_wallet),
) -> dict:
    """
    GET /auth/me
    Authorization: Bearer <token>

    Devuelve información básica del wallet autenticado.
    """
    # Extraer audience del token actual
    raw_token = extract_token_from_request(request)
    payload = decode_access_token(raw_token) if raw_token else None
    audience = payload.get("aud") if payload else None

    return {
        "wallet_address": wallet,
        "is_admin": is_admin(wallet),
        "audience": audience,
    }


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="Verifica si el JWT es válido",
)
async def auth_status(request: Request) -> AuthStatusResponse:
    """
    GET /auth/status
    Authorization: Bearer <token> (opcional)

    Verifica si el JWT es válido sin lanzar 401.
    Fail-closed: si Redis está caído, devuelve authenticated=False.
    """
    raw_token = extract_token_from_request(request)
    if not raw_token:
        return AuthStatusResponse(authenticated=False)

    payload = decode_access_token(raw_token)
    if not payload:
        return AuthStatusResponse(authenticated=False)

    wallet = payload.get("sub")
    audience = payload.get("aud")
    if not wallet or not audience:
        return AuthStatusResponse(authenticated=False)

    try:
        if await is_token_blacklisted(payload):
            return AuthStatusResponse(authenticated=False)
    except Exception:
        logger.warning("Redis no disponible en /auth/status")
        return AuthStatusResponse(authenticated=False)

    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        if exp else None
    )

    return AuthStatusResponse(
        authenticated=True,
        wallet_address=wallet,
        audience=audience,
        expires_at=expires_at,
    )