from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import Request

from api.config import settings
from api.core.redis import get_redis

logger = logging.getLogger(__name__)

# Redis key prefixes
_NONCE_PREFIX = "nonce:"          # nonce:<wallet> → JSON {nonce, message, audience}
_REFRESH_PREFIX = "refresh:"      # refresh:<token> → JSON {wallet, audience}
_BLACKLIST_PREFIX = "jwt_bl:"     # jwt_bl:<jti> → "1"
_REFRESH_IDX_PREFIX = "ridx:"     # ridx:<wallet> → set{token, ...}

def extract_token_from_request(request: Request) -> Optional[str]:
    """Extrae el access token desde cookie HttpOnly o header Authorization."""
    cookie_name = getattr(settings, "ACCESS_TOKEN_COOKIE", "access_token")
    token = request.cookies.get(cookie_name)
    if token:
        return token

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):].strip()

    return None

def build_siwe_message(wallet_address: str, nonce: str, audience: str) -> str:
    """
    Construye el mensaje EIP-4361 con campo Audience personalizado.
    """
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    domain = settings.APP_DOMAIN or "ethernal.fund"
    uri = settings.APP_URL or "https://ethernal.fund"

    # Mapeo de audience a texto legible en el mensaje
    audience_display = {
        "retirement": "Retirement Protocol",
        "sale": "Token Sale",
    }.get(audience, audience)
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{wallet_address}\n"
        f"\n"
        f"Sign in to Ethernal Fund ({audience_display})\n"
        f"\n"
        f"URI: {uri}\n"
        f"Version: 1\n"
        f"Chain ID: {settings.SALE_CHAIN_ID}\n"
        f"Nonce: {nonce}\n"
        f"Audience: {audience}\n"
        f"Issued At: {issued_at}"
    )

async def generate_nonce(wallet_address: str, audience: str) -> Tuple[str, str]:
    """
    Genera un nonce criptográfico, construye el mensaje SIWE completo
    y guarda todo en Redis como JSON.

    Devuelve (nonce, message).
    """
    nonce = secrets.token_hex(16)
    message = build_siwe_message(wallet_address, nonce, audience)
    key = _NONCE_PREFIX + wallet_address.lower()

    redis = await get_redis()
    await redis.setex(
        key,
        settings.NONCE_TTL_SECONDS,
        json.dumps({"nonce": nonce, "message": message, "audience": audience}),
    )
    logger.debug("Nonce generado | wallet=%s audience=%s", wallet_address[:10], audience)
    return nonce, message

async def consume_nonce(wallet_address: str) -> Optional[dict]:
    """
    Lee y elimina el nonce atómicamente (GETDEL).
    Devuelve dict con {nonce, message, audience} o None.
    """
    key = _NONCE_PREFIX + wallet_address.lower()
    redis = await get_redis()
    raw = await redis.getdel(key)
    if not raw:
        return None
    logger.debug("Nonce consumido | wallet=%s", wallet_address[:10])
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "nonce" in data:
            return data
        # Formato legacy (string plano) — retornar dict mínimo
        return {"nonce": raw, "message": None, "audience": None}
    except json.JSONDecodeError:
        return {"nonce": raw, "message": None, "audience": None}

def verify_eoa_signature(wallet_address: str, signature: str, message: str) -> bool:
    """Verifica firma ECDSA estándar (EOA)."""
    try:
        msg_hash = encode_defunct(text=message)
        recovered = Account.recover_message(msg_hash, signature=signature)
        return recovered.lower() == wallet_address.lower()
    except Exception as exc:
        logger.debug("EOA signature check failed: %s", exc)
        return False

def create_access_token(wallet_address: str, audience: str) -> str:
    """
    Crea un JWT de corta duración con:
      - sub: wallet en minúsculas
      - aud: audience ('retirement' o 'sale')
      - jti: ID único para blacklist
      - type: "access"
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    jti = secrets.token_hex(16)

    payload = {
        "sub": wallet_address.lower(),
        "aud": audience,
        "jti": jti,
        "type": "access",
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    """Decodifica y valida un JWT. Devuelve payload o None."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("type") != "access":
            logger.debug("Token type mismatch: expected 'access', got '%s'", payload.get("type"))
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Access token expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Invalid access token: %s", exc)
        return None


# Alias para compatibilidad
decode_token = decode_access_token

async def blacklist_token(token: str) -> None:
    """Blacklistea el JWT con TTL = tiempo restante hasta expiración."""
    payload = decode_access_token(token)
    if not payload:
        logger.debug("blacklist_token: token inválido o expirado, ignorado")
        return

    jti = payload.get("jti")
    if not jti:
        logger.warning("blacklist_token: token sin JTI")
        return

    exp = payload.get("exp", 0)
    now = datetime.now(timezone.utc).timestamp()
    ttl = max(int(exp - now), 1)

    redis = await get_redis()
    await redis.setex(_BLACKLIST_PREFIX + jti, ttl, "1")
    logger.info("Access token blacklisted | jti=%s ttl=%ds", jti[:8], ttl)

async def is_token_blacklisted(payload: dict) -> bool:
    """Devuelve True si el JTI está en la blacklist."""
    jti = payload.get("jti")
    if not jti:
        return True
    redis = await get_redis()
    return await redis.exists(_BLACKLIST_PREFIX + jti) == 1

async def create_refresh_token(wallet_address: str, audience: str) -> str:
    """
    Crea un refresh token opaco y lo almacena en Redis con su audience.
    """
    token = secrets.token_urlsafe(48)
    wallet = wallet_address.lower()
    ttl = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60

    redis = await get_redis()
    pipe = redis.pipeline()
    pipe.setex(
        _REFRESH_PREFIX + token,
        ttl,
        json.dumps({"wallet": wallet, "audience": audience}),
    )
    pipe.sadd(_REFRESH_IDX_PREFIX + wallet, token)
    pipe.expire(_REFRESH_IDX_PREFIX + wallet, ttl)
    await pipe.execute()

    logger.debug("Refresh token creado | wallet=%s audience=%s", wallet[:10], audience)
    return token

async def consume_refresh_token(refresh_token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Lee y elimina el refresh token atómicamente.
    Devuelve (wallet, audience) o (None, None) si no existe.
    """
    key = _REFRESH_PREFIX + refresh_token
    redis = await get_redis()
    raw = await redis.getdel(key)
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
        wallet = data.get("wallet")
        audience = data.get("audience")
    except json.JSONDecodeError:
        # Legacy: valor era solo wallet string
        wallet = raw
        audience = None

    if wallet:
        try:
            await redis.srem(_REFRESH_IDX_PREFIX + wallet, refresh_token)
        except Exception:
            pass
        logger.debug("Refresh token consumido | wallet=%s", wallet[:10])

    return wallet, audience

async def revoke_all_refresh_tokens(wallet_address: str) -> int:
    """Revoca todos los refresh tokens activos de un wallet."""
    wallet = wallet_address.lower()
    redis = await get_redis()
    idx_key = _REFRESH_IDX_PREFIX + wallet
    tokens = await redis.smembers(idx_key)
    if not tokens:
        return 0
    pipe = redis.pipeline()
    for token in tokens:
        pipe.delete(_REFRESH_PREFIX + token)
    pipe.delete(idx_key)
    await pipe.execute()
    logger.info(
        "All refresh tokens revoked | wallet=%s count=%d",
        wallet[:10], len(tokens),
    )
    return len(tokens)

def is_admin(wallet: str) -> bool:
    """Verifica si un wallet es administrador."""
    return wallet.lower() in settings.get_admin_wallets()