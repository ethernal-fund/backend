from __future__ import annotations

import logging

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Security
from typing import Optional
from api.db.session import get_db
from api.db.repositories.user_repo import UserRepository
from api.core.auth import (
    generate_nonce,
    get_nonce,
    consume_nonce,
    verify_signature,
    create_access_token,
    create_refresh_token,
    consume_refresh_token,
    blacklist_token,
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
    RefreshRequest,
    RefreshResponse,
    LogoutRequest,
    SurveySubmit,
    UserOut,
)
from api.config import settings

logger   = logging.getLogger(__name__)
router   = APIRouter(prefix="/users", tags=["users"])
security = HTTPBearer(auto_error=False)

@router.post("/nonce", response_model=NonceResponse)
async def request_nonce(
    payload: NonceRequest,
    request: Request,
) -> NonceResponse:
    await limiter(request, max_requests=10, window=60, key_prefix="nonce")
    nonce   = await generate_nonce(payload.wallet_address)
    message = build_auth_message(payload.wallet_address, nonce)
    logger.info("Nonce generated | wallet=%s", payload.wallet_address[:10])
    return NonceResponse(nonce=nonce, message=message)

@router.post("/auth", response_model=AuthResponse)
async def authenticate(
    payload: AuthRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
) -> AuthResponse:
    await limiter(request, max_requests=10, window=60, key_prefix="auth")
    stored_nonce = await get_nonce(payload.wallet_address)
    if not stored_nonce or stored_nonce != payload.nonce:
        if stored_nonce:
            await consume_nonce(payload.wallet_address)
        raise HTTPException(status_code=401, detail="Invalid or expired nonce")
    await consume_nonce(payload.wallet_address)
    if not verify_signature(payload.wallet_address, payload.signature, payload.nonce):
        raise InvalidSignature()
    repo              = UserRepository(db)
    user, created     = await repo.get_or_create(payload.wallet_address)
    access_token      = create_access_token(payload.wallet_address)
    refresh_token     = await create_refresh_token(payload.wallet_address)
    if created:
        logger.info("New user authenticated | wallet=%s", payload.wallet_address[:10])
    else:
        logger.info("User authenticated | wallet=%s", payload.wallet_address[:10])
    return AuthResponse(
        access_token          = access_token,
        refresh_token         = refresh_token,
        token_type            = "bearer",
        wallet_address        = payload.wallet_address,
        expires_in            = settings.JWT_EXPIRE_MINUTES * 60,
        refresh_expires_in    = settings.JWT_REFRESH_EXPIRE_MINUTES * 60,
    )

@router.post("/auth/refresh", response_model=RefreshResponse)
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
) -> RefreshResponse:
    await limiter(request, max_requests=10, window=60, key_prefix="refresh")
    wallet = await consume_refresh_token(payload.refresh_token)
    if not wallet:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    new_access_token  = create_access_token(wallet)
    new_refresh_token = await create_refresh_token(wallet)
    logger.info("Tokens refreshed | wallet=%s", wallet[:10])
    return RefreshResponse(
        access_token       = new_access_token,
        refresh_token      = new_refresh_token,
        token_type         = "bearer",
        expires_in         = settings.JWT_EXPIRE_MINUTES * 60,
        refresh_expires_in = settings.JWT_REFRESH_EXPIRE_MINUTES * 60,
    )

@router.post("/logout", status_code=204)
async def logout(
    payload:     LogoutRequest,
    request:     Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> None:
    await limiter(request, max_requests=20, window=60, key_prefix="logout")
    if credentials and credentials.credentials:
        await blacklist_token(credentials.credentials)
    if payload.refresh_token:
        await consume_refresh_token(payload.refresh_token)

    logger.info(
        "User logged out | wallet=%s",
        credentials.credentials[:10] if credentials else "unknown",
    )

@router.get("/me", response_model=UserOut)
async def get_me(
    wallet: str           = Depends(get_current_wallet),
    db:     AsyncSession  = Depends(get_db),
) -> UserOut:
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/survey", response_model=UserOut)
async def submit_survey(
    payload: SurveySubmit,
    wallet:  str          = Depends(get_current_wallet),
    db:      AsyncSession = Depends(get_db),
) -> UserOut:
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.survey_completed:
        raise SurveyAlreadyCompleted()

    updated = await repo.update_survey(wallet, payload.model_dump())
    logger.info("Survey completed | wallet=%s", wallet[:10])
    return updated