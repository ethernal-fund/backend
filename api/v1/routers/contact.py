from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional

from api.db.session import get_db
from api.db.repositories.contact_repo import ContactRepository
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contact", tags=["contact"])

class ContactCreate(BaseModel):
    name:          str            = Field(..., min_length=2, max_length=100)
    email:         str            = Field(..., min_length=5, max_length=255)
    subject:       Optional[str]  = Field(None, max_length=200)
    message:       str            = Field(..., min_length=10, max_length=5000)
    walletAddress: Optional[str]  = None

@router.post("")
async def submit_contact(
    payload: ContactCreate,
    db: AsyncSession = Depends(get_db),
):
    repo = ContactRepository(db)
    await repo.create({
        "name":           payload.name.strip(),
        "email":          payload.email.strip().lower(),
        "subject":        payload.subject.strip() if payload.subject else None,
        "message":        payload.message.strip(),
        "wallet_address": payload.walletAddress.lower() if payload.walletAddress else None,
    })
    logger.info(f"New contact message from {payload.email}")
    return {"success": True, "message": "Message received. We'll get back to you soon."}