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

NONCE_TTL_SECONDS = 300
NONCE_KEY_PREFIX = "nonce:"

async def generate_nonce(wallet_address: str) -> str:
    nonce = secrets.token_hex(16)
    key = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    await redis.setex(key, NONCE_TTL_SECONDS, nonce)
    logger.debug("Nonce generated for %s", wallet_address[:10])
    return nonce

async def get_nonce(wallet_address: str) -> Optional[str]:
    key = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    return await redis.get(key)

async def consume_nonce(wallet_address: str) -> None:
    key = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    await redis.delete(key)

def build_auth_message(wallet_address: str, nonce: str) -> str:
    return settings.AUTH_MESSAGE.format(
        domain=settings.APP_DOMAIN or "ethernal.fund",
        wallet=wallet_address,
        nonce=nonce,
        uri=settings.APP_URL,
        chain_id=settings.CHAIN_ID,
    )

def verify_signature(wallet_address: str, signature: str, nonce: str) -> bool:
    try:
        message = build_auth_message(wallet_address, nonce)
        message_hash = encode_defunct(text=message)
        recovered = Account.recover_message(message_hash, signature=signature)
        return recovered.lower() == wallet_address.lower()
    except Exception as exc:
        logger.warning("Signature verification failed: %s", exc)
        return False

def create_access_token(wallet_address: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": wallet_address.lower(),
        "exp": expire,
        "iat": now,
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid token: %s", exc)
        return None

def is_admin(wallet: str) -> bool:
    return wallet.lower() == settings.ADMIN_WALLET.lower()  # Retorn true if the wallet matches the admin wallet 