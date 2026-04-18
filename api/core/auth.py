from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import secrets
import logging

from eth_account import Account
from eth_account.messages import encode_defunct

from api.config import settings
from api.core.redis import get_redis

logger = logging.getLogger(__name__)

NONCE_TTL_SECONDS = 300          # 5 min para completar el flujo de auth
NONCE_KEY_PREFIX  = "nonce:"     # Redis key: "nonce:{wallet_lower}"

async def generate_nonce(wallet_address: str) -> str:
    nonce = secrets.token_hex(16)
    key   = NONCE_KEY_PREFIX + wallet_address.lower()

    redis = await get_redis()
    await redis.setex(key, NONCE_TTL_SECONDS, nonce)
    logger.debug("Nonce generated for %s (TTL=%ss)", wallet_address[:10], NONCE_TTL_SECONDS)
    return nonce

async def get_nonce(wallet_address: str) -> Optional[str]:
    """Recupera el nonce activo para una wallet. Retorna None si expiró o no existe."""
    key   = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    value = await redis.get(key)
    return value  

async def consume_nonce(wallet_address: str) -> None:
    key   = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    deleted = await redis.delete(key)
    if deleted:
        logger.debug("Nonce consumed for %s", wallet_address[:10])
    else:
        # El nonce ya expiró entre la verificación y el consume — no es un error
        logger.warning("Nonce already expired at consume for %s", wallet_address[:10])

def verify_signature(wallet_address: str, signature: str, nonce: str) -> bool:
    try:
        message      = settings.AUTH_MESSAGE.format(nonce=nonce)
        message_hash = encode_defunct(text=message)
        recovered    = Account.recover_message(message_hash, signature=signature)
        match        = recovered.lower() == wallet_address.lower()

        if not match:
            logger.warning(
                "Signature mismatch: expected=%s recovered=%s",
                wallet_address[:10],
                recovered[:10],
            )
        return match

    except Exception as exc:
        logger.warning("Signature verification error for %s: %s", wallet_address[:10], exc)
        return False

def create_access_token(wallet_address: str) -> str:
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)

    payload = {
        "sub":  wallet_address.lower(),
        "exp":  expire,
        "iat":  now,
        "type": "access",
    }

    token = jwt.encode(
        payload,
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )

    logger.info("JWT issued for %s (expires=%s)", wallet_address[:10], expire.isoformat())
    return token

def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )

        if payload.get("type") != "access":
            logger.warning("JWT with unexpected type: %s", payload.get("type"))
            return None

        return payload

    except jwt.ExpiredSignatureError:
        logger.info("Expired JWT token")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT token: %s", exc)
        return None

def is_admin(wallet_address: str) -> bool:
    return wallet_address.lower() == settings.ADMIN_WALLET.lower()