from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import Request

from api.config import settings
from api.core.redis import get_redis

logger = logging.getLogger(__name__)

# ── Redis key prefixes ────────────────────────────────────────────────────────
_NONCE_PREFIX       = "nonce:"        # nonce:<wallet_lower>      → JSON {nonce, message}
_REFRESH_PREFIX     = "refresh:"      # refresh:<token>           → wallet (str)
_BLACKLIST_PREFIX   = "jwt_bl:"       # jwt_bl:<jti>              → "1"
_REFRESH_IDX_PREFIX = "ridx:"         # ridx:<wallet_lower>       → set{token, ...}

def extract_token_from_request(request: Request) -> Optional[str]:
    """
    Extrae el access token desde la request en el siguiente orden de precedencia:

      1. Cookie HttpOnly  (nombre: settings.ACCESS_TOKEN_COOKIE, default "access_token")
         — preferida porque es invisible a JavaScript y resiste XSS.
      2. Header  Authorization: Bearer <token>
         — necesario para clientes que no soportan cookies (mobile, CLI, otros servicios).

    Devuelve el token crudo como str, o None si no se encuentra en ninguna fuente.
    No valida ni decodifica el token — esa responsabilidad recae en decode_access_token.
    """
    # 1. Cookie HttpOnly
    cookie_name: str = getattr(settings, "ACCESS_TOKEN_COOKIE", "access_token")
    token = request.cookies.get(cookie_name)
    if token:
        return token

    # 2. Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        raw = auth_header[len("Bearer "):].strip()
        return raw or None

    return None

async def generate_nonce(wallet_address: str) -> tuple[str, str]:
    """
    Genera un nonce criptográficamente seguro, construye el mensaje SIWE completo
    (con el Issued At fijado en este momento) y guarda AMBOS en Redis como JSON.

    Devuelve (nonce, message) para que el router los incluya en la respuesta.

    ⚠  El mensaje se construye UNA SOLA VEZ aquí y se persiste en Redis.
       verify_signature() lo leerá tal cual — nunca lo reconstruye, porque
       cualquier diferencia de timestamp haría que la firma no matcheara.
    """
    nonce   = secrets.token_hex(16)
    message = build_siwe_message(wallet_address, nonce)
    key     = _NONCE_PREFIX + wallet_address.lower()

    redis = await get_redis()
    await redis.setex(
        key,
        settings.NONCE_TTL_SECONDS,
        json.dumps({"nonce": nonce, "message": message}),
    )
    logger.debug("Nonce generated | wallet=%s", wallet_address[:10])
    return nonce, message

async def consume_nonce(wallet_address: str) -> Optional[dict]:
    """
    Lee y elimina el nonce en una sola operación atómica (GETDEL).

    Devuelve un dict {"nonce": str, "message": str} si existía,
    o None si ya expiró o fue consumido.

    Compatibilidad hacia atrás: si el valor almacenado es un string plano
    (nonces generados antes del cambio a JSON), lo trata como nonce sin mensaje.
    En ese caso la verificación de firma fallará — el usuario deberá solicitar
    un nonce nuevo.
    """
    key   = _NONCE_PREFIX + wallet_address.lower()
    redis = await get_redis()
    raw   = await redis.getdel(key)

    if not raw:
        return None
    logger.debug("Nonce consumed | wallet=%s", wallet_address[:10])
    try:
        data = json.loads(raw)
        # Asegurarse de que tenga las claves esperadas
        if isinstance(data, dict) and "nonce" in data and "message" in data:
            return data
        # JSON válido pero con forma inesperada — tratar como nonce plano
        raise ValueError("Unexpected JSON shape")
    except (json.JSONDecodeError, ValueError):
        # Compatibilidad: valor almacenado como string plano (nonce anterior)
        logger.warning(
            "Nonce en formato legacy (string plano) | wallet=%s — el usuario "
            "debe solicitar un nonce nuevo para completar la autenticación.",
            wallet_address[:10],
        )
        return {"nonce": raw, "message": None}

def build_siwe_message(wallet_address: str, nonce: str) -> str:
    """
    Construye el mensaje EIP-4361 (Sign-In With Ethereum).

    Este método se llama exclusivamente desde generate_nonce() para fijar
    el Issued At en el momento de emisión del nonce.  NO debe llamarse desde
    verify_signature() — la verificación usa el mensaje guardado en Redis.

    ⚠  Si cambiás este formato, el frontend debe cambiar exactamente igual.
    """
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    domain    = settings.APP_DOMAIN or "ethernal.fund"
    uri       = settings.APP_URL    or "https://ethernal.fund"

    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{wallet_address}\n"
        f"\n"
        f"Sign in to Ethernal Fund\n"
        f"\n"
        f"URI: {uri}\n"
        f"Version: 1\n"
        f"Chain ID: {settings.CHAIN_ID}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )
build_auth_message = build_siwe_message

def verify_eoa_signature(wallet_address: str, signature: str, message: str) -> bool:
    """
    Verifica firma ECDSA estándar (EOA).
    Soporta tanto el mensaje completo como el hash EIP-191 (eth_sign).
    """
    try:
        msg_hash  = encode_defunct(text=message)
        recovered = Account.recover_message(msg_hash, signature=signature)
        return recovered.lower() == wallet_address.lower()
    except Exception as exc:
        logger.debug("EOA signature check failed: %s", exc)
        return False

def verify_signature(wallet_address: str, signature: str, message: str) -> bool:
    """
    Verifica que la firma corresponda al mensaje SIWE original.

    IMPORTANTE: recibe el `message` completo (leído desde Redis por consume_nonce),
    NO el nonce. Esto garantiza que se verifica contra el string exacto que
    el usuario firmó, incluyendo el Issued At original.

    Flujo completo:
      1. Frontend llama POST /users/nonce  → recibe {nonce, message}
      2. Usuario firma `message` con su wallet
      3. Frontend llama POST /users/auth   → envía {wallet_address, nonce, signature}
      4. Backend llama consume_nonce()     → obtiene {nonce, message} de Redis
      5. Backend llama verify_signature()  → pasa message original, no lo reconstruye

    Nota sobre EIP-1271 (smart-contract wallets como Safe/Gnosis):
      La verificación EIP-1271 requiere una llamada RPC al contrato del wallet.
      Para habilitarla, inyectá un w3 instance y llamá a:
          contract.functions.isValidSignature(msg_hash, sig).call()
      Esto se omite aquí para no acoplar auth.py a BlockchainService.
    """
    return verify_eoa_signature(wallet_address, signature, message)

def create_access_token(wallet_address: str) -> str:
    """
    Crea un JWT de corta duración con:
      - sub: wallet en minúsculas
      - jti: ID único para blacklist selectiva
      - type: "access" para distinguirlo de otros tokens
      - iat / exp: estándar JWT
    """
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    jti    = secrets.token_hex(16)

    payload = {
        "sub":  wallet_address.lower(),
        "jti":  jti,
        "type": "access",
        "iat":  now,
        "exp":  expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    """
    Decodifica y valida un JWT.  Devuelve el payload si es válido, None si no.
    No lanza excepciones — los errores se loguean a nivel DEBUG.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        # Rechazar tokens que no sean de tipo "access"
        if payload.get("type") != "access":
            logger.debug(
                "Token type mismatch: expected 'access', got '%s'",
                payload.get("type"),
            )
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Access token expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Invalid access token: %s", exc)
        return None
decode_token = decode_access_token

async def blacklist_token(token: str) -> None:
    """
    Agrega el JTI del token a la blacklist con TTL = tiempo restante hasta expiración.
    Si el token ya expiró, no hace nada (no tiene sentido blacklistear algo vencido).
    """
    payload = decode_access_token(token)
    if not payload:
        logger.debug("blacklist_token: token inválido o expirado, ignorado")
        return

    jti = payload.get("jti")
    if not jti:
        logger.warning("blacklist_token: token sin JTI, no se puede blacklistear")
        return

    exp = payload.get("exp", 0)
    now = datetime.now(timezone.utc).timestamp()
    ttl = max(int(exp - now), 1)
    redis = await get_redis()
    await redis.setex(_BLACKLIST_PREFIX + jti, ttl, "1")
    logger.info(
        "Access token blacklisted | jti=%s wallet=%s ttl=%ds",
        jti[:8], payload.get("sub", "?")[:10], ttl,
    )

async def is_token_blacklisted(payload: dict) -> bool:
    """Devuelve True si el JTI del token está en la blacklist."""
    jti = payload.get("jti")
    if not jti:
        # Sin JTI no podemos verificar — rechazamos por seguridad.
        return True
    redis = await get_redis()
    return await redis.exists(_BLACKLIST_PREFIX + jti) == 1

async def create_refresh_token(wallet_address: str) -> str:
    """
    Crea un refresh token opaco y lo almacena en Redis.

    Almacenamiento:
      - Clave principal: refresh:<token>  → wallet (para consume)
      - Índice inverso:  ridx:<wallet>    → set{token, ...} (para revocar todos)
    """
    token  = secrets.token_urlsafe(48)
    wallet = wallet_address.lower()
    ttl    = settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60

    redis = await get_redis()
    pipe  = redis.pipeline()
    pipe.setex(_REFRESH_PREFIX + token, ttl, wallet)
    pipe.sadd(_REFRESH_IDX_PREFIX + wallet, token)
    pipe.expire(_REFRESH_IDX_PREFIX + wallet, ttl)
    await pipe.execute()
    logger.debug("Refresh token created | wallet=%s", wallet[:10])
    return token

async def consume_refresh_token(refresh_token: str) -> Optional[str]:
    """
    Lee y elimina el refresh token atómicamente.  Devuelve el wallet si era válido.
    También limpia el token del índice inverso.
    """
    key    = _REFRESH_PREFIX + refresh_token
    redis  = await get_redis()
    wallet = await redis.getdel(key)

    if wallet:
        try:
            await redis.srem(_REFRESH_IDX_PREFIX + wallet, refresh_token)
        except Exception:
            pass
        logger.debug("Refresh token consumed | wallet=%s", wallet[:10])

    return wallet

async def revoke_all_refresh_tokens(wallet_address: str) -> int:
    """
    Revoca todos los refresh tokens activos de un wallet.
    Devuelve la cantidad de tokens revocados.
    """
    wallet  = wallet_address.lower()
    redis   = await get_redis()
    idx_key = _REFRESH_IDX_PREFIX + wallet
    tokens  = await redis.smembers(idx_key)
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
    return wallet.lower() in settings.get_admin_wallets()