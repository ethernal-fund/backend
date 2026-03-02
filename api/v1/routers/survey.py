from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional
from api.db.session import get_db
from api.db.repositories.survey_repo import SurveyRepository
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["survey"])

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

@router.post("")
async def submit_survey(
    payload: SurveyCreate,
    db: AsyncSession = Depends(get_db),
):
    repo = SurveyRepository(db)
    survey = await repo.create_survey(payload.model_dump())
    logger.info(f"New anonymous survey submitted id={survey.id}")
    return {"success": True, "survey_id": survey.id}

@router.post("/follow-up")
async def submit_followup(
    payload: FollowUpCreate,
    db: AsyncSession = Depends(get_db),
):
    repo = SurveyRepository(db)
    await repo.create_followup({
        "survey_id":       payload.survey_id,
        "wants_more_info": payload.wants_more_info,
        "email":           payload.email.strip().lower() if payload.email else None,
    })
    logger.info(f"New follow-up wants_more_info={payload.wants_more_info}")
    return {"success": True}