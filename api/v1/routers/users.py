from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from api.db.session import get_db
from api.db.repositories.user_repo import UserRepository
from api.core.auth import generate_nonce, get_nonce, verify_signature, create_access_token
from api.core.dependencies import get_current_wallet
from api.core.exceptions import InvalidSignature, SurveyAlreadyCompleted
from api.schemas.users import (          
    NonceRequest, NonceResponse, AuthRequest, AuthResponse,
    SurveySubmit, UserOut
)
from api.config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

@router.post("/nonce", response_model=NonceResponse)
async def request_nonce(payload: NonceRequest):
    nonce = generate_nonce(payload.wallet_address)
    message = settings.AUTH_MESSAGE.format(nonce=nonce)
    return NonceResponse(nonce=nonce, message=message)

@router.post("/auth", response_model=AuthResponse)
async def authenticate(payload: AuthRequest, db: AsyncSession = Depends(get_db)):
    stored_nonce = get_nonce(payload.wallet_address)
    if not stored_nonce or stored_nonce != payload.nonce:
        raise HTTPException(status_code=401, detail="Invalid or expired nonce")
    if not verify_signature(payload.wallet_address, payload.signature, payload.nonce):
        raise InvalidSignature()

    repo = UserRepository(db)
    user, created = await repo.get_or_create(payload.wallet_address)
    token = create_access_token(payload.wallet_address)

    if created:
        logger.info(f"New user: {payload.wallet_address}")
    else:
        logger.info(f"User authenticated: {payload.wallet_address}")

    return AuthResponse(
        access_token=token,
        wallet_address=payload.wallet_address,
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
    )

@router.get("/me", response_model=UserOut)
async def get_me(
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/survey", response_model=UserOut)
async def submit_survey(
    payload: SurveySubmit,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.survey_completed:
        raise SurveyAlreadyCompleted()

    updated = await repo.update_survey(wallet, payload.model_dump())
    logger.info(f"Survey completed: {wallet}")
    return updated