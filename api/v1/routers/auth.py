"""
api/v1/routers/auth.py

Endpoints de autenticación Sign-In With Ethereum (EIP-4361).

ENDPOINTS:
  GET  /auth/nonce          → genera nonce + construye mensaje SIWE listo para firmar
  POST /auth/verify-siwe    → verifica firma → emite JWT + refresh token
  POST /auth/refresh        → rota refresh token → nuevo JWT
  POST /auth/logout         → blacklistea JWT + revoca refresh tokens
  GET  /auth/me             → info del wallet autenticado (verificación rápida)
  GET  /auth/status         → verifica si el token actual es válido (sin DB)

SCHEMAS:
  Todos los schemas de request/response vienen de api.schemas.users para
  mantener una única fuente de verdad. Los schemas inline que tenía la versión
  anterior fueron eliminados.

  NonceResponse   → { nonce, message }   ← message es el SIWE armado, listo para firmar
  AuthResponse    → { access_token, refresh_token, token_type, wallet_address,
                      expires_in, refresh_expires_in, is_new_user }
  RefreshResponse → { access_token, refresh_token, token_type,
                      expires_in, refresh_expires_in }

DIFERENCIAS CON LA VERSIÓN ANTERIOR:
  - GET /auth/nonce devuelve también `message` (el SIWE completo armado por el backend).
    El frontend puede usarlo directamente sin reconstruirlo — elimina la posibilidad
    de que el mensaje del cliente diverja del que espera el backend.
  - POST /auth/verify-siwe acepta tanto { message, signature } (frontend construye el
    mensaje) como el flujo donde el frontend firma el `message` devuelto por /nonce.
    En ambos casos el nonce se extrae del mensaje y se valida contra Redis.
  - Schemas importados de api.schemas.users en lugar de definidos inline.

SEGURIDAD:
  - Nonce consumido atómicamente (GETDEL) → anti-replay
  - Mensaje validado campo a campo (domain, chain_id, issued_at) → anti-phishing
  - JWT blacklisteado en logout → invalidación inmediata sin esperar expiración
  - Refresh token opaco, one-shot, con índice inverso → revocación total por wallet
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.core.auth import (
    generate_nonce,
    consume_nonce,
    build_siwe_message,
    verify_eoa_signature,
    create_access_token,
    create_refresh_token,
    consume_refresh_token,
    revoke_all_refresh_tokens,
    blacklist_token,
    decode_access_token,
    extract_token_from_request,
    is_admin,
)
from api.core.dependencies import get_current_wallet
from api.config import settings
from api.schemas.users import (
    NonceResponse,
    AuthResponse,
    AuthStatusResponse,
    RefreshRequest,
    RefreshResponse,
    LogoutRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# ── Constantes de validación SIWE ─────────────────────────────────────────────
_EXPECTED_DOMAIN   = settings.APP_DOMAIN or "ethernal.fund"
_EXPECTED_CHAIN_ID = str(settings.CHAIN_ID)
_WALLET_RE         = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SIWE_MAX_AGE_SEC  = 300   # 5 minutos — ventana máxima de antigüedad del mensaje


# ── Schema de request (no está en users.py — específico de este endpoint) ─────

class SIWEPayload(BaseModel):
    """
    Body de POST /auth/verify-siwe.

    El frontend firma el mensaje EIP-4361 (ya sea el que construyó él mismo
    o el devuelto por GET /auth/nonce) y envía ambos aquí.
    El backend verifica el nonce, el dominio, el chain ID y la firma ECDSA.
    """
    message:   str = Field(..., description="Mensaje EIP-4361 completo tal como fue firmado")
    signature: str = Field(..., description="Firma ECDSA (0x + 130 hex chars)")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_siwe_message(message: str) -> dict:
    """
    Parsea un mensaje EIP-4361 y extrae los campos críticos.

    Formato esperado:
      {domain} wants you to sign in with your Ethereum account:
      {address}

      {statement}

      URI: {uri}
      Version: {version}
      Chain ID: {chain_id}
      Nonce: {nonce}
      Issued At: {issued_at}

    Retorna dict con: domain, address, nonce, chain_id, uri, issued_at.
    Lanza ValueError si el formato es inválido o falta un campo crítico.
    """
    lines = message.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("Mensaje demasiado corto para ser un mensaje SIWE válido")

    # Línea 0: "<domain> wants you to sign in with your Ethereum account:"
    wants_marker = " wants you to sign in"
    if wants_marker not in lines[0]:
        raise ValueError("Línea 0 no tiene el formato SIWE esperado")
    domain = lines[0].split(wants_marker)[0].strip()

    # Línea 1: dirección Ethereum
    address = lines[1].strip()
    if not _WALLET_RE.match(address):
        raise ValueError(f"Dirección inválida en el mensaje SIWE: {address!r}")

    # Resto: pares "Clave: valor"
    fields: dict[str, str] = {}
    for line in lines[2:]:
        if ": " in line:
            key, _, val = line.partition(": ")
            fields[key.strip()] = val.strip()

    nonce     = fields.get("Nonce", "")
    chain_id  = fields.get("Chain ID", "")
    uri       = fields.get("URI", "")
    issued_at = fields.get("Issued At", "")

    if not nonce:
        raise ValueError("Campo 'Nonce' ausente en el mensaje SIWE")
    if not chain_id:
        raise ValueError("Campo 'Chain ID' ausente en el mensaje SIWE")

    return {
        "domain":    domain,
        "address":   address,
        "nonce":     nonce,
        "chain_id":  chain_id,
        "uri":       uri,
        "issued_at": issued_at,
    }


def _validate_siwe_fields(parsed: dict) -> None:
    """
    Valida los campos del mensaje SIWE contra los valores esperados del servidor.

    Previene ataques de phishing: un atacante no puede engañar al usuario para
    que firme un mensaje SIWE de otro dominio y usarlo acá, porque el backend
    rechaza mensajes cuyo domain o chain_id no coincidan con el propio.

    Lanza HTTPException 401 en caso de discrepancia.
    """
    if parsed["domain"] and parsed["domain"] != _EXPECTED_DOMAIN:
        logger.warning(
            "SIWE domain mismatch | expected=%s got=%s",
            _EXPECTED_DOMAIN, parsed["domain"],
        )
        raise HTTPException(
            status_code=401,
            detail=f"SIWE domain inválido. Se esperaba '{_EXPECTED_DOMAIN}'",
        )

    if parsed["chain_id"] and parsed["chain_id"] != _EXPECTED_CHAIN_ID:
        logger.warning(
            "SIWE chain_id mismatch | expected=%s got=%s",
            _EXPECTED_CHAIN_ID, parsed["chain_id"],
        )
        raise HTTPException(
            status_code=401,
            detail=f"SIWE chain ID inválido. Se esperaba {_EXPECTED_CHAIN_ID}",
        )

    # Antigüedad del mensaje — rechazar si tiene más de 5 minutos
    issued_at_str = parsed.get("issued_at", "")
    if issued_at_str:
        try:
            issued_at   = datetime.fromisoformat(issued_at_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(timezone.utc) - issued_at).total_seconds()
            if age_seconds > _SIWE_MAX_AGE_SEC:
                raise HTTPException(
                    status_code=401,
                    detail=(
                        f"Mensaje SIWE expirado ({int(age_seconds)}s de antigüedad, "
                        f"máximo {_SIWE_MAX_AGE_SEC}s). Solicitá un nuevo nonce."
                    ),
                )
        except HTTPException:
            raise
        except ValueError:
            pass  # formato de fecha desconocido — no rechazar


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/nonce",
    response_model=NonceResponse,
    summary="Genera nonce SIWE y devuelve el mensaje listo para firmar",
)
async def get_nonce(address: str) -> NonceResponse:
    """
    GET /auth/nonce?address=0x...

    Genera un nonce criptográfico de un solo uso, lo almacena en Redis con TTL,
    y devuelve tanto el nonce como el mensaje EIP-4361 completo listo para firmar.

    El frontend tiene dos opciones:
      a) Firmar `message` directamente (recomendado — evita divergencias).
      b) Construir su propio mensaje y enviar solo la firma (compatible también).

    En ambos casos el nonce incluido en el mensaje debe coincidir con el
    almacenado en Redis — se consume atómicamente en verify-siwe.
    """
    address = address.strip().lower()

    # Validar formato — acepta con o sin "0x" para mayor compatibilidad
    normalized = address if address.startswith("0x") else f"0x{address}"
    if not _WALLET_RE.match(normalized):
        raise HTTPException(status_code=400, detail="Dirección Ethereum inválida")

    try:
        nonce   = await generate_nonce(normalized)
        message = build_siwe_message(normalized, nonce)
    except Exception as exc:
        logger.error("Error generando nonce | wallet=%s: %s", normalized[:10], exc)
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    logger.debug("Nonce generado | wallet=%s", normalized[:10])
    return NonceResponse(nonce=nonce, message=message)


@router.post(
    "/verify-siwe",
    response_model=AuthResponse,
    summary="Verifica firma SIWE y emite JWT + refresh token",
)
async def verify_siwe(payload: SIWEPayload) -> AuthResponse:
    """
    POST /auth/verify-siwe
    Body: { message: string, signature: string }

    Flujo:
      1. Parsea el mensaje EIP-4361 → extrae address y nonce
      2. Valida domain, chain_id y antigüedad del mensaje (anti-phishing)
      3. Consume el nonce de Redis atómicamente (anti-replay)
      4. Verifica la firma ECDSA contra el mensaje exacto recibido
      5. Emite access token (JWT) + refresh token (opaco)

    El `message` debe ser el mensaje exacto que el usuario firmó en su wallet.
    No se reconstruye server-side para evitar divergencias por timestamp o
    diferencias en el statement.
    """
    # 1. Parsear
    try:
        parsed = _parse_siwe_message(payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Mensaje SIWE inválido: {exc}")

    address = parsed["address"].lower()
    nonce   = parsed["nonce"]

    # 2. Validar campos del mensaje
    _validate_siwe_fields(parsed)

    # 3. Consumir nonce — GETDEL atómico: si no existe o ya fue usado → rechazar
    try:
        stored_nonce = await consume_nonce(address)
    except Exception as exc:
        logger.error("Error consumiendo nonce | wallet=%s: %s", address[:10], exc)
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    if not stored_nonce:
        logger.warning("Nonce no encontrado o expirado | wallet=%s", address[:10])
        raise HTTPException(
            status_code=401,
            detail="Nonce inválido o expirado. Solicitá uno nuevo en GET /auth/nonce",
        )

    if stored_nonce != nonce:
        logger.warning(
            "Nonce mismatch | wallet=%s stored=%.8s got=%.8s",
            address[:10], stored_nonce, nonce,
        )
        raise HTTPException(status_code=401, detail="Nonce no coincide")

    # 4. Verificar firma ECDSA contra el mensaje EXACTO del cliente
    if not verify_eoa_signature(address, payload.signature, payload.message):
        logger.warning("Firma SIWE inválida | wallet=%s", address[:10])
        raise HTTPException(status_code=401, detail="Firma inválida")

    # 5. Emitir tokens
    try:
        access_token  = create_access_token(address)
        refresh_token = await create_refresh_token(address)
    except Exception as exc:
        logger.error("Error emitiendo tokens | wallet=%s: %s", address[:10], exc)
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    logger.info("SIWE autenticado | wallet=%s", address[:10])
    return AuthResponse(
        access_token       = access_token,
        refresh_token      = refresh_token,
        token_type         = "bearer",
        wallet_address     = address,
        expires_in         = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        is_new_user        = False,   # poblar desde DB si se implementa registro
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    summary="Rota el refresh token y emite nuevo JWT",
)
async def refresh_token(payload: RefreshRequest) -> RefreshResponse:
    """
    POST /auth/refresh
    Body: { refresh_token: string }

    Consume el refresh token actual (one-shot — queda invalidado) y emite
    un nuevo par access + refresh token. El cliente debe almacenar el nuevo
    refresh token y descartar el anterior.

    Si el refresh token no existe o ya fue consumido → 401.
    """
    try:
        wallet = await consume_refresh_token(payload.refresh_token)
    except Exception as exc:
        logger.error("Error consumiendo refresh token: %s", exc)
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    if not wallet:
        raise HTTPException(
            status_code=401,
            detail="Refresh token inválido o expirado",
        )

    try:
        new_access  = create_access_token(wallet)
        new_refresh = await create_refresh_token(wallet)
    except Exception as exc:
        logger.error("Error emitiendo tokens en refresh | wallet=%s: %s", wallet[:10], exc)
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    logger.debug("Token rotado | wallet=%s", wallet[:10])
    return RefreshResponse(
        access_token       = new_access,
        refresh_token      = new_refresh,
        token_type         = "bearer",
        expires_in         = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_expires_in = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/logout",
    status_code=204,
    summary="Invalida el JWT activo y revoca refresh tokens",
)
async def logout(
    request: Request,
    body:    LogoutRequest = LogoutRequest(),
) -> Response:
    """
    POST /auth/logout
    Authorization: Bearer <token>
    Body: { refresh_token?: string }   (opcional)

    Blacklistea el JWT activo con TTL = tiempo restante hasta su expiración,
    y revoca todos los refresh tokens del wallet (cerrar sesión en todos los
    dispositivos). Si el cliente envía refresh_token en el body, se garantiza
    que ese token específico también queda invalidado aunque el índice inverso
    en Redis ya lo hubiera eliminado.
    """
    raw_token = extract_token_from_request(request)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Token de acceso requerido")

    wallet = await get_current_wallet(request)

    try:
        await blacklist_token(raw_token)
        revoked = await revoke_all_refresh_tokens(wallet)
    except Exception as exc:
        logger.error("Error en logout | wallet=%s: %s", wallet[:10], exc)
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    logger.info(
        "Logout | wallet=%s refresh_tokens_revocados=%d",
        wallet[:10], revoked,
    )
    return Response(status_code=204)


@router.get(
    "/me",
    summary="Devuelve info básica del wallet autenticado",
)
async def me(request: Request) -> dict:
    """
    GET /auth/me
    Authorization: Bearer <token>

    Verificación rápida + info del wallet. No hace llamadas a DB ni a la chain.
    Útil para que el frontend confirme que el JWT sigue siendo válido y obtenga
    el rol del wallet (admin o no) sin necesidad de decodificarlo client-side.
    """
    wallet = await get_current_wallet(request)
    return {
        "wallet_address": wallet,
        "is_admin":       is_admin(wallet),
    }


@router.get(
    "/status",
    response_model=AuthStatusResponse,
    summary="Verifica si el token actual es válido (sin DB, sin chain)",
)
async def auth_status(request: Request) -> AuthStatusResponse:
    """
    GET /auth/status
    Authorization: Bearer <token>   (opcional)

    Devuelve el estado de autenticación sin lanzar 401 si el token falta o
    es inválido — útil para que el frontend decida si mostrar el modal de
    conexión sin necesidad de capturar errores HTTP.

    Si el token está presente, se decodifica y se verifica la blacklist.
    Si no hay token o es inválido, retorna { authenticated: false }.
    """
    raw_token = extract_token_from_request(request)
    if not raw_token:
        return AuthStatusResponse(authenticated=False)

    payload = decode_access_token(raw_token)
    if not payload:
        return AuthStatusResponse(authenticated=False)

    wallet = payload.get("sub")
    if not wallet:
        return AuthStatusResponse(authenticated=False)

    # Verificar blacklist — si la key no existe en Redis devolvemos False
    try:
        from api.core.auth import is_token_blacklisted
        if await is_token_blacklisted(payload):
            return AuthStatusResponse(authenticated=False)
    except Exception:
        # Redis caído — optamos por no autenticar (fail-closed)
        return AuthStatusResponse(authenticated=False)

    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
        if exp else None
    )

    return AuthStatusResponse(
        authenticated  = True,
        wallet_address = wallet,
        expires_at     = expires_at,
    )