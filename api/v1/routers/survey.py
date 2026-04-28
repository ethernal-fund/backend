from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr, Field

from api.db.session import get_db
from api.db.repositories.survey_repo import SurveyRepository
from api.db.repositories.user_repo import UserRepository
from api.core.dependencies import get_current_wallet_optional
from api.core.rate_limit import limiter
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/surveys", tags=["survey"])

class SurveyCreate(BaseModel):
    age:                      str
    trust_traditional:        int = Field(..., ge=-2, le=2)
    blockchain_familiarity:   int = Field(..., ge=-2, le=2)
    retirement_concern:       int = Field(..., ge=-2, le=2)
    has_retirement_plan:      int = Field(..., ge=-2, le=2)
    values_in_retirement:     int = Field(..., ge=-2, le=2)
    interested_in_blockchain: int = Field(..., ge=-2, le=2)

class FollowUpCreate(BaseModel):
    wants_more_info: bool
    email:           Optional[str] = Field(None, max_length=255)
    survey_id:       Optional[int] = None

@router.post("", status_code=201)
async def submit_survey(
    payload: SurveyCreate,
    request: Request,
    db:      AsyncSession         = Depends(get_db),
    wallet:  Optional[str]        = Depends(get_current_wallet_optional),
):
    await limiter(request, max_requests=5, window=300, key_prefix="survey")
    survey_repo = SurveyRepository(db)
    survey      = await survey_repo.create_survey(payload.model_dump())
    if wallet:
        try:
            user_repo = UserRepository(db)
            user      = await user_repo.get_by_wallet(wallet)
            if user and not user.survey_completed:
                await user_repo.update_survey(
                    wallet,
                    {
                        "age_range":            payload.age,
                        "survey_completed":     True,
                        "survey_completed_at":  datetime.now(timezone.utc),
                    },
                )
                logger.info(
                    "users.survey_completed updated | wallet=%s survey_id=%d",
                    wallet[:10],
                    survey.id,
                )
            elif user and user.survey_completed:
                logger.debug(
                    "Survey already completed for wallet=%s — skipping users update",
                    wallet[:10],
                )
        except Exception:
            logger.exception(
                "Failed to update users table after survey | wallet=%s survey_id=%d",
                wallet[:10] if wallet else "?",
                survey.id,
            )

    logger.info(
        "Anonymous survey submitted | id=%d wallet=%s",
        survey.id,
        wallet[:10] if wallet else "anon",
    )
    return {"success": True, "survey_id": survey.id}

@router.post("/follow-up", status_code=201)
async def submit_followup(
    payload: FollowUpCreate,
    request: Request,
    db:      AsyncSession = Depends(get_db),
):
    await limiter(request, max_requests=5, window=300, key_prefix="survey-followup")
    repo = SurveyRepository(db)
    await repo.create_followup(
        {
            "survey_id":       payload.survey_id,
            "wants_more_info": payload.wants_more_info,
            "email":           payload.email.strip().lower() if payload.email else None,
        }
    )

    logger.info("Follow-up recorded | wants_more_info=%s", payload.wants_more_info)
    return {"success": True}