from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional

import secrets
import logging
import jwt

from eth_account import Account
from eth_account.messages import encode_defunct
from api.config import settings
from api.core.redis import get_redis

logger = logging.getLogger(__name__)

NONCE_KEY_PREFIX    = "nonce:"
BLACKLIST_KEY_PREFIX = "jwt_blacklist:"
REFRESH_KEY_PREFIX  = "refresh:"

NONCE_TTL_SECONDS = 300   # 5 min — tiempo razonable para firmar

async def generate_nonce(wallet_address: str) -> str:
    nonce = secrets.token_hex(16)
    key   = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    await redis.setex(key, NONCE_TTL_SECONDS, nonce)
    logger.debug("Nonce generated for %s", wallet_address[:10])
    return nonce

async def get_nonce(wallet_address: str) -> Optional[str]:
    key   = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    return await redis.get(key)

async def consume_nonce(wallet_address: str) -> None:
    key   = NONCE_KEY_PREFIX + wallet_address.lower()
    redis = await get_redis()
    await redis.delete(key)

def build_auth_message(wallet_address: str, nonce: str) -> str:
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return settings.AUTH_MESSAGE.format(
        domain    = settings.APP_DOMAIN or "ethernal.fund",
        wallet    = wallet_address,
        nonce     = nonce,
        uri       = settings.APP_URL,
        chain_id  = settings.CHAIN_ID,
        issued_at = issued_at,
    )

def verify_signature(wallet_address: str, signature: str, nonce: str) -> bool:
    try:
        message      = build_auth_message(wallet_address, nonce)
        message_hash = encode_defunct(text=message)
        recovered    = Account.recover_message(message_hash, signature=signature)
        return recovered.lower() == wallet_address.lower()
    except Exception as exc:
        logger.warning("Signature verification failed: %s", exc)
        return False

def create_access_token(wallet_address: str) -> str:
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    jti    = secrets.token_hex(16)   # JWT ID — necesario para blacklist
    payload = {
        "sub":  wallet_address.lower(),
        "exp":  expire,
        "iat":  now,
        "jti":  jti,
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
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Invalid token: %s", exc)
        return None

async def create_refresh_token(wallet_address: str) -> str:
    token = secrets.token_urlsafe(48)
    key   = REFRESH_KEY_PREFIX + token
    ttl   = settings.JWT_REFRESH_EXPIRE_MINUTES * 60
    redis = await get_redis()
    await redis.setex(key, ttl, wallet_address.lower())
    logger.debug("Refresh token created for %s", wallet_address[:10])
    return token

async def consume_refresh_token(refresh_token: str) -> Optional[str]:
    key   = REFRESH_KEY_PREFIX + refresh_token
    redis = await get_redis()
    wallet = await redis.getdel(key)
    if wallet:
        logger.debug("Refresh token consumed for %s", wallet[:10])
    return wallet

async def blacklist_token(token: str) -> None:
    payload = decode_token(token)
    if not payload:
        return  

    jti = payload.get("jti")
    if not jti:
        sub = payload.get("sub", "unknown")
        iat = payload.get("iat", 0)
        jti = f"legacy_{sub}_{iat}"

    exp = payload.get("exp", 0)
    now = datetime.now(timezone.utc).timestamp()
    ttl = max(int(exp - now), 1)   

    key   = BLACKLIST_KEY_PREFIX + jti
    redis = await get_redis()
    await redis.setex(key, ttl, "1")
    logger.info("Token blacklisted | jti=%s wallet=%s", jti, payload.get("sub", "?")[:10])


async def is_token_blacklisted(payload: dict) -> bool:
    jti = payload.get("jti")
    if not jti:
        sub = payload.get("sub", "unknown")
        iat = payload.get("iat", 0)
        jti = f"legacy_{sub}_{iat}"
    key   = BLACKLIST_KEY_PREFIX + jti
    redis = await get_redis()
    return await redis.exists(key) == 1

def is_admin(wallet: str) -> bool:
    return wallet.lower() in settings.get_admin_wallets() 