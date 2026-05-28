from fastapi import APIRouter, HTTPException
from eth_account.messages import encode_defunct
from eth_account import Account
from pydantic import BaseModel

router = APIRouter()

class SIWEPayload(BaseModel):
    message: str   # el mensaje EIP-4361 completo
    signature: str

@router.post("/auth/verify-siwe")
async def verify_siwe(payload: SIWEPayload):
    msg = encode_defunct(text=payload.message)
    try:
        address = Account.recover_message(msg, signature=payload.signature)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Generar JWT con PyJWT
    import jwt, os
    token = jwt.encode(
        {"address": address.lower(), "exp": ...},
        os.getenv("JWT_SECRET"),
        algorithm="HS256"
    )
    return {"token": token, "address": address.lower()}