from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional
from api.db.session import get_db
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.treasury_repo import TreasuryRepository
from api.core.dependencies import get_current_wallet, require_admin
from api.services.blockchain_service import BlockchainService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/treasury", tags=["treasury"])

class EarlyRetirementRequestPayload(BaseModel):
    fund_address: str
    reason: str = Field(..., min_length=20, max_length=512)

class ProcessRequestPayload(BaseModel):
    tx_hash: str
    approve: bool
    admin_notes: Optional[str] = None

@router.get("/stats")
async def get_treasury_stats():
    try:
        blockchain = BlockchainService()
        return await blockchain.get_treasury_stats()
    except Exception as e:
        logger.error(f"Treasury stats failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to fetch treasury stats")


@router.get("/fees/me")
async def get_my_fees(
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    fund_repo     = FundRepository(db)
    treasury_repo = TreasuryRepository(db)

    fund = await fund_repo.get_by_owner(wallet)
    if not fund:
        raise HTTPException(status_code=404, detail="No fund found for this wallet")

    record = await treasury_repo.get_fee_record(fund.contract_address)
    if not record:
        return {"total_fees_paid": 0, "fee_count": 0, "last_fee_at": None}

    return {
        "fund_address":    record.fund_address,
        "total_fees_paid": float(record.total_fees_paid),
        "fee_count":       record.fee_count,
        "last_fee_at":     record.last_fee_at,
    }

@router.post("/early-retirement/request")
async def request_early_retirement(
    payload: EarlyRetirementRequestPayload,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    fund_repo     = FundRepository(db)
    treasury_repo = TreasuryRepository(db)

    fund = await fund_repo.get_by_owner(wallet)
    if not fund:
        raise HTTPException(status_code=404, detail="No fund found for this wallet")
    if fund.contract_address.lower() != payload.fund_address.lower():
        raise HTTPException(status_code=403, detail="Not your fund")
    if fund.retirement_started:
        raise HTTPException(status_code=400, detail="Retirement already started")
    existing = await treasury_repo.get_request(fund.contract_address)
    if existing and existing.status == "pending":
        raise HTTPException(status_code=409, detail="You already have a pending request")

    return {
        "success":          True,
        "message":          "Call Treasury.requestEarlyRetirement() on-chain with this data",
        "fund_address":     fund.contract_address,
        "treasury_address": BlockchainService().w3.to_checksum_address(
            __import__("api.config", fromlist=["settings"]).settings.TREASURY_ADDRESS
        ),
        "reason":           payload.reason,
    }

@router.get("/early-retirement/me")
async def get_my_early_retirement_request(
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    fund_repo     = FundRepository(db)
    treasury_repo = TreasuryRepository(db)

    fund = await fund_repo.get_by_owner(wallet)
    if not fund:
        return {"has_request": False}

    requests = await treasury_repo.get_by_wallet(wallet)
    if not requests:
        return {"has_request": False}

    latest = requests[0]
    return {
        "has_request":  True,
        "status":       latest.status,
        "reason":       latest.reason,
        "requested_at": latest.requested_at,
        "processed_at": latest.processed_at,
        "admin_notes":  latest.admin_notes,
    }

@router.get("/early-retirement/pending")
async def get_pending_requests(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    treasury_repo = TreasuryRepository(db)
    requests      = await treasury_repo.get_pending()

    return {
        "pending_requests": [
            {
                "id":             r.id,
                "fund_address":   r.fund_address,
                "requester":      r.requester_wallet,
                "reason":         r.reason,
                "requested_at":   r.requested_at,
            }
            for r in requests
        ],
        "count": len(requests),
    }

@router.post("/early-retirement/process")
async def process_early_retirement(
    payload: ProcessRequestPayload,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    treasury_repo = TreasuryRepository(db)
    result = await treasury_repo.process_request(
        tx_hash=payload.tx_hash,
        approved=payload.approve,
        processed_by=admin,
        admin_notes=payload.admin_notes,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Request not found")
    return {
        "success":   True,
        "status":    result.status,
        "processed_at": result.processed_at,
    }