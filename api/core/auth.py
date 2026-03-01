from eth_account import Account
from eth_account.messages import encode_defunct
from datetime import datetime, timedelta
from typing import Optional
import jwt
import secrets
import logging

from api.config import settings

logger = logging.getLogger(__name__)
_nonce_cache: dict[str, str] = {}

def generate_nonce(wallet_address: str) -> str:
    """Genera un nonce único para la wallet antes de firmar."""
    nonce = secrets.token_hex(16)
    _nonce_cache[wallet_address.lower()] = nonce
    return nonce

def get_nonce(wallet_address: str) -> Optional[str]:
    return _nonce_cache.get(wallet_address.lower())

def verify_signature(wallet_address: str, signature: str, nonce: str) -> bool:
    try:
        message = settings.AUTH_MESSAGE.format(nonce=nonce)
        message_hash = encode_defunct(text=message)
        recovered = Account.recover_message(message_hash, signature=signature)
        return recovered.lower() == wallet_address.lower()
    except Exception as e:
        logger.warning(f"Signature verification failed: {e}")
        return False

def create_access_token(wallet_address: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": wallet_address.lower(),
        "exp": expire,
        "iat": datetime.utcnow(),
        "type": "access",
    }
    _nonce_cache.pop(wallet_address.lower(), None)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.warning("Expired JWT token")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        return None

def is_admin(wallet_address: str) -> bool:
    return wallet_address.lower() == settings.ADMIN_WALLET.lower()