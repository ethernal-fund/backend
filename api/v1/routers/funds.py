from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from api.db.session import get_db
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.transaction_repo import TransactionRepository
from api.core.dependencies import get_current_wallet
from api.core.exceptions import FundNotFound
from api.schemas.funds import FundOut, FundSyncRequest, RegisterFundRequest
from api.services.blockchain_service import BlockchainService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funds", tags=["funds"])

@router.post("/register", status_code=201)
async def register_fund(
    payload: RegisterFundRequest,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    if not Web3.is_address(payload.contract_address):
        raise HTTPException(status_code=400, detail="Invalid contract_address")

    contract_address = Web3.to_checksum_address(payload.contract_address)
    repo = FundRepository(db)

    # Evitar duplicados
    existing = await repo.get_by_contract(contract_address)
    if existing:
        logger.info(f"Fund already registered: {contract_address}")
        return {"success": True, "contract_address": contract_address, "created": False}

    fund = await repo.create_from_deployment(
        contract_address=contract_address,
        owner_wallet=wallet,
        principal=payload.principal,
        monthly_deposit=payload.monthly_deposit,
        desired_monthly=payload.desired_monthly_income,
        current_age=payload.current_age,
        retirement_age=payload.retirement_age,
        payment_years=payload.payment_years,
        apy_percent=payload.apy_percent,
        protocol_address=payload.protocol_address,
    )

    logger.info(f"Fund registered successfully: {contract_address} by {wallet}")
    return {
        "success": True,
        "created": True,
        "contract_address": fund.contract_address,
        "owner_wallet": fund.owner_wallet,
    }

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
    repo = FundRepository(db)
    fund = await repo.get_by_owner(wallet)
    if not fund:
        raise FundNotFound(wallet)

    if fund.contract_address.lower() != payload.contract_address.lower():
        raise HTTPException(status_code=403, detail="Not your fund")

    try:
        blockchain = BlockchainService()
        on_chain_data = await blockchain.get_fund_info(fund.contract_address)
        await repo.update_balances(fund.contract_address, on_chain_data)
        logger.info(f"Fund synced: {fund.contract_address}")
        return {"success": True, "message": "Fund synced from blockchain"}
    except Exception as e:
        logger.error(f"Fund sync failed: {e}")
        raise HTTPException(status_code=502, detail=f"Blockchain sync failed: {str(e)}")