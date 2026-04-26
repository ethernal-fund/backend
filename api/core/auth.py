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

def verify_signature(wallet_address: str, signature: str, nonce: str) -> bool:
    try:
        # Usamos el mismo mensaje que se firmó en el frontend
        message = f"{settings.APP_DOMAIN or 'ethernal.fund'} wants you to sign in with your Ethereum account:\n{wallet_address}\n\nNonce: {nonce}\n\nURI: {settings.APP_URL}\nVersion: 1\nChain ID: {settings.CHAIN_ID}\nNonce: {nonce}"
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