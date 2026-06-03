from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.db.repositories.user_repo import UserRepository
from api.core.dependencies import get_current_wallet_retirement  # ← audience retirement
from api.core.exceptions import SurveyAlreadyCompleted
from api.core.rate_limit import limiter
from api.schemas.users import SurveySubmit, UserOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

@router.get("/me", response_model=UserOut)
async def get_me(
    wallet: str = Depends(get_current_wallet_retirement),  # ← retirement audience
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """
    Perfil del usuario autenticado.
    Requiere autenticación con audience='retirement'.
    """
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/survey", response_model=UserOut)
async def submit_survey(
    payload: SurveySubmit,
    request: Request,
    wallet: str = Depends(get_current_wallet_retirement),  # ← retirement audience
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    await limiter(request, max_requests=5, window=300, key_prefix="user-survey")
    repo = UserRepository(db)
    user = await repo.get_by_wallet(wallet)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.survey_completed:
        raise SurveyAlreadyCompleted()
    updated = await repo.update_survey(wallet, payload.model_dump())
    logger.info("Survey completed | wallet=%s", wallet[:10])
    return updated