from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from web3 import Web3
from typing import Optional

from api.db.session import get_db
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.transaction_repo import TransactionRepository
from api.core.dependencies import get_current_wallet
from api.core.exceptions import FundNotFound
from api.services.fund_service import FundService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funds", tags=["funds"])

class SyncRequest(BaseModel):
    contract_address: str

@router.get("/me")
async def get_my_fund(
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    service = FundService(db)
    return await service.get_fund_dashboard(wallet)

@router.get("/me/transactions")
async def get_my_transactions(
    event_type: Optional[str] = Query(None, description="Filtrar por tipo de evento"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    repo = TransactionRepository(db)
    txs  = await repo.get_by_wallet(wallet, event_type=event_type, skip=skip, limit=limit)
    return {
        "wallet":       wallet,
        "transactions": [
            {
                "tx_hash":         tx.id,
                "event_type":      tx.event_type,
                "gross_amount":    float(tx.gross_amount)      if tx.gross_amount      else None,
                "fee_amount":      float(tx.fee_amount)        if tx.fee_amount        else None,
                "net_amount":      float(tx.net_amount)        if tx.net_amount        else None,
                "resulting_balance": float(tx.resulting_balance) if tx.resulting_balance else None,
                "protocol":        tx.protocol_address,
                "block_number":    tx.block_number,
                "block_timestamp": tx.block_timestamp,
                "extra_data":      tx.extra_data,
            }
            for tx in txs
        ],
        "count": len(txs),
    }

@router.get("/{contract_address}")
async def get_fund_by_address(
    contract_address: str,
    db: AsyncSession = Depends(get_db),
):
    if not Web3.is_address(contract_address):
        raise HTTPException(status_code=400, detail="Invalid contract address")

    repo = FundRepository(db)
    fund = await repo.get_by_contract(contract_address)
    if not fund:
        raise FundNotFound(contract_address)

    return {
        "contract_address": fund.contract_address,
        "owner_wallet":     fund.owner_wallet,
        "total_balance":    float(fund.total_balance),
        "retirement_started": fund.retirement_started,
        "is_active":        fund.is_active,
        "created_at":       fund.created_at,
        "last_synced_at":   fund.last_synced_at,
    }

@router.post("/sync")
async def sync_fund(
    payload: SyncRequest,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    repo = FundRepository(db)
    fund = await repo.get_by_owner(wallet)

    if not fund:
        raise FundNotFound(wallet)
    if fund.contract_address.lower() != payload.contract_address.lower():
        raise HTTPException(status_code=403, detail="Not your fund")
    service = FundService(db)
    result  = await service.sync_from_blockchain(fund.contract_address)
    return result