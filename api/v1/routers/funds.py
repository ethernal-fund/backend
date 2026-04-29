from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from api.db.session import get_db
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.user_repo import UserRepository
from api.core.dependencies import get_current_wallet
from api.core.exceptions import FundNotFound
from api.schemas.funds import FundOut, FundSyncRequest, RegisterFundRequest
from api.services.blockchain_service import BlockchainService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funds", tags=["funds"])

@router.post("/register", status_code=201)
async def register_fund(
    payload: RegisterFundRequest,
    wallet:  str           = Depends(get_current_wallet),
    db:      AsyncSession  = Depends(get_db),
):
    if not Web3.is_address(payload.contract_address):
        raise HTTPException(status_code=400, detail="Invalid contract_address")

    contract_address = Web3.to_checksum_address(payload.contract_address)
    fund_repo        = FundRepository(db)

    existing = await fund_repo.get_by_contract(contract_address)
    if existing:
        logger.info("Fund already registered: %s", contract_address)
        return {
            "success":          True,
            "created":          False,
            "contract_address": contract_address,
        }

    fund = await fund_repo.create_from_event({
        "contract_address":  contract_address.lower(),
        "owner_wallet":      wallet.lower(),
        "principal":         payload.principal,
        "monthly_deposit":   payload.monthly_deposit,
        "desired_monthly":   payload.desired_monthly_income,
        "current_age":       payload.current_age,
        "retirement_age":    payload.retirement_age,
        "years_payments":    payload.payment_years,
        "interest_rate":     payload.apy_percent,
        "timelock_years":    payload.timelock_years,
        "timelock_end":      payload.timelock_end,
        "selected_protocol": payload.protocol_address,
    })

    try:
        user_repo = UserRepository(db)
        await user_repo.touch(wallet)
        logger.debug("users.last_active_at refreshed | wallet=%s", wallet[:10])
    except Exception:
        logger.exception(
            "Failed to touch users.last_active_at after fund registration | wallet=%s",
            wallet[:10],
        )

    logger.info("Fund registered: %s  owner: %s", contract_address, wallet)
    return {
        "success":          True,
        "created":          True,
        "contract_address": fund.contract_address,
        "owner_wallet":     fund.owner_wallet,
    }

@router.post("/sync")
async def sync_fund(
    payload: FundSyncRequest,
    wallet:  str           = Depends(get_current_wallet),
    db:      AsyncSession  = Depends(get_db),
):
    if not Web3.is_address(payload.contract_address):
        raise HTTPException(status_code=400, detail="Invalid contract_address")

    fund_repo = FundRepository(db)
    fund      = await fund_repo.get_by_owner(wallet)
    if not fund:
        raise FundNotFound(wallet)
    if fund.contract_address.lower() != payload.contract_address.lower():
        raise HTTPException(status_code=403, detail="Not your fund")
    try:
        blockchain    = BlockchainService()
        on_chain_data = await blockchain.get_fund_info(fund.contract_address)
        await fund_repo.update_balances(fund.contract_address, on_chain_data)
        logger.info("Fund synced: %s", fund.contract_address)
        return {"success": True, "message": "Fund synced from blockchain"}
    except Exception as exc:
        logger.error("Fund sync failed for %s: %s", fund.contract_address, exc)
        raise HTTPException(status_code=502, detail=f"Blockchain sync failed: {exc}")

@router.get("/me", response_model=FundOut)
async def get_my_fund(
    wallet: str           = Depends(get_current_wallet),
    db:     AsyncSession  = Depends(get_db),
):
    fund_repo = FundRepository(db)
    fund      = await fund_repo.get_by_owner(wallet)
    if not fund:
        raise FundNotFound(wallet)
    return fund

@router.get("/{contract_address}", response_model=FundOut)
async def get_fund(
    contract_address: str,
    db: AsyncSession = Depends(get_db),
):
    if not Web3.is_address(contract_address):
        raise HTTPException(status_code=400, detail="Invalid contract address")
    fund_repo = FundRepository(db)
    fund      = await fund_repo.get_by_contract(contract_address)
    if not fund:
        raise FundNotFound(contract_address)
    return fund