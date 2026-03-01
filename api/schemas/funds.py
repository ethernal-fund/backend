from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from api.db.session import get_db
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.transaction_repo import TransactionRepository
from api.core.dependencies import get_current_wallet
from api.core.exceptions import FundNotFound
from api.schemas.funds import FundOut, FundSyncRequest
from api.services.blockchain_service import BlockchainService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funds", tags=["funds"])

@router.get("/me", response_model=FundOut)
async def get_my_fund(
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    repo = FundRepository(db)
    fund = await repo.get_by_owner(wallet)
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

    repo = FundRepository(db)
    fund = await repo.get_by_contract(contract_address)
    if not fund:
        raise FundNotFound(contract_address)
    return fund

@router.post("/sync")
async def sync_fund(
    payload: FundSyncRequest,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    fund_repo = FundRepository(db)
    fund = await fund_repo.get_by_owner(wallet)
    if not fund:
        raise FundNotFound(wallet)

    if fund.contract_address.lower() != payload.contract_address.lower():
        raise HTTPException(status_code=403, detail="Not your fund")

    try:
        blockchain = BlockchainService()
        on_chain_data = await blockchain.get_fund_info(fund.contract_address)
        await fund_repo.update_balances(fund.contract_address, on_chain_data)
        logger.info(f"Fund synced: {fund.contract_address}")
        return {"success": True, "message": "Fund synced from blockchain"}
    except Exception as e:
        logger.error(f"Fund sync failed: {e}")
        raise HTTPException(status_code=502, detail=f"Blockchain sync failed: {str(e)}")

@router.get("/me/transactions")
async def get_my_transactions(
    event_type: str = None,
    skip: int = 0,
    limit: int = 50,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    """Historial de transacciones del usuario autenticado."""
    repo = TransactionRepository(db)
    txs = await repo.get_by_wallet(wallet, event_type=event_type, skip=skip, limit=limit)
    return {
        "wallet": wallet,
        "transactions": txs,
        "count": len(txs),
    }