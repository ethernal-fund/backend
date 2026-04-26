from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.db.repositories.user_repo import UserRepository
from api.core.auth import (
    generate_nonce,
    get_nonce,
    consume_nonce,
    verify_signature,
    create_access_token,
    build_auth_message,
)
from api.core.dependencies import get_current_wallet
from api.core.exceptions import InvalidSignature, SurveyAlreadyCompleted
from api.core.rate_limit import limiter
from api.schemas.users import (
    NonceRequest,
    NonceResponse,
    AuthRequest,
    AuthResponse,
    SurveySubmit,
    UserOut,
)
from api.config import settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

@router.post("/nonce", response_model=NonceResponse)
async def request_nonce(
    payload: NonceRequest,
    request: Request,
):
    await limiter(request, max_requests=10, window=60, key_prefix="nonce")
    nonce = await generate_nonce(payload.wallet_address)
    message = build_auth_message(payload.wallet_address, nonce)
    logger.info("Nonce generated for wallet: %s", payload.wallet_address[:10])

    return NonceResponse(nonce=nonce, message=message)

@router.post("/auth", response_model=AuthResponse)
async def authenticate(
    payload: AuthRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await limiter(request, max_requests=10, window=60, key_prefix="auth")

    stored_nonce = await get_nonce(payload.wallet_address)
    if not stored_nonce or stored_nonce != payload.nonce:
        if stored_nonce:
            await consume_nonce(payload.wallet_address)
        raise HTTPException(status_code=401, detail="Invalid or expired nonce")

    await consume_nonce(payload.wallet_address)
    if not verify_signature(payload.wallet_address, payload.signature, payload.nonce):
        raise InvalidSignature()

    repo = UserRepository(db)
    user, created = await repo.get_or_create(payload.wallet_address)
    token = create_access_token(payload.wallet_address)

    if created:
        logger.info("New user authenticated: %s", payload.wallet_address[:10])
    else:
        logger.info("User authenticated: %s", payload.wallet_address[:10])

    return AuthResponse(
        access_token=token,
        token_type="bearer",
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
    logger.info("Survey completed: %s", wallet[:10])
    return updated