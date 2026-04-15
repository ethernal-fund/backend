from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from web3 import Web3
from typing import Optional
from datetime import datetime, timezone, timedelta

from api.db.session import get_db
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.transaction_repo import TransactionRepository
from api.db.repositories.protocol_repo import ProtocolRepository
from api.db.models.fund import PersonalFund
from api.core.dependencies import get_current_wallet
from api.core.exceptions import FundNotFound
from api.services.fund_service import FundService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/funds", tags=["funds"])

class RegisterFundRequest(BaseModel):

    contract_address:  str = Field(..., description="Dirección del PersonalFund deployado")
    tx_hash:           str = Field(..., description="Hash de la tx de deploy")

    # Calculator inputs
    principal:              float = Field(..., ge=0)
    monthly_deposit:        float = Field(..., gt=0,  description="result.monthlyGross — lo que paga por mes")
    desired_monthly_income: float = Field(..., gt=0,  description="calculator.desiredMonthlyIncome — meta del usuario")
    current_age:            int   = Field(..., ge=18, le=80)
    retirement_age:         int   = Field(..., ge=55, le=100)
    payment_years:          int   = Field(..., ge=1,  le=50)
    apy_percent:            float = Field(..., ge=0,  le=100, description="Porcentaje, ej: 5.0")

    # Protocol
    protocol_address: str = Field(..., description="Dirección del protocolo seleccionado")

class SyncRequest(BaseModel):
    contract_address: str

@router.post("/register", status_code=201)
async def register_fund(
    payload: RegisterFundRequest,
    wallet: str = Depends(get_current_wallet),
    db: AsyncSession = Depends(get_db),
):
    """
    Registra en la DB el fondo recién deployado on-chain.
    Llamar desde el frontend en Step3Deploy justo después de confirmar la tx.
    """
    # Validar direcciones
    if not Web3.is_address(payload.contract_address):
        raise HTTPException(status_code=400, detail="Invalid contract_address")
    if not Web3.is_address(payload.protocol_address):
        raise HTTPException(status_code=400, detail="Invalid protocol_address")

    contract_address  = Web3.to_checksum_address(payload.contract_address)
    protocol_address  = Web3.to_checksum_address(payload.protocol_address)

    fund_repo     = FundRepository(db)
    protocol_repo = ProtocolRepository(db)

    existing = await fund_repo.get_by_contract(contract_address)
    if existing:
        logger.info(f"Fund already registered: {contract_address}")
        return {"success": True, "contract_address": contract_address, "created": False}

    # Verificar que el protocolo existe en la DB
    protocol = await protocol_repo.get_by_address(protocol_address)
    if not protocol:
        raise HTTPException(status_code=404, detail="Protocol not found in registry")

    # Verificar que el wallet no tenga ya otro fondo registrado
    existing_by_owner = await fund_repo.get_by_owner(wallet)
    if existing_by_owner:
        raise HTTPException(
            status_code=409,
            detail=f"Wallet already has a registered fund: {existing_by_owner.contract_address}",
        )

    # Conversiones
    years_to_retirement = payload.retirement_age - payload.current_age
    timelock_end        = datetime.now(timezone.utc) + timedelta(days=years_to_retirement * 365)
    interest_rate_bps   = int(round(payload.apy_percent * 100))   # 5.0% → 500 bps

    fund = PersonalFund(
        contract_address  = contract_address,
        owner_wallet      = wallet.lower(),

        principal         = payload.principal,
        monthly_deposit   = payload.monthly_deposit,           # monthlyGross (lo real)
        desired_monthly   = payload.desired_monthly_income,    # meta del usuario
        current_age       = payload.current_age,
        retirement_age    = payload.retirement_age,
        years_payments    = payload.payment_years,
        interest_rate     = interest_rate_bps,                 # en bps, no en %
        timelock_years    = years_to_retirement,
        timelock_end      = timelock_end,

        selected_protocol = protocol_address,

        # Balances iniciales
        total_gross_deposited  = payload.principal + payload.monthly_deposit,
        monthly_deposit_count  = 1,
        is_active              = True,
        retirement_started     = False,
    )

    db.add(fund)
    await db.commit()
    await db.refresh(fund)

    logger.info(f"Fund registered: {contract_address} owner={wallet} protocol={protocol_address}")

    return {
        "success":          True,
        "created":          True,
        "contract_address": fund.contract_address,
        "owner_wallet":     fund.owner_wallet,
        "monthly_deposit":  float(fund.monthly_deposit),
        "timelock_end":     fund.timelock_end.isoformat(),
    }

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
                "tx_hash":           tx.id,
                "event_type":        tx.event_type,
                "gross_amount":      float(tx.gross_amount)       if tx.gross_amount       else None,
                "fee_amount":        float(tx.fee_amount)         if tx.fee_amount         else None,
                "net_amount":        float(tx.net_amount)         if tx.net_amount         else None,
                "resulting_balance": float(tx.resulting_balance)  if tx.resulting_balance  else None,
                "protocol":          tx.protocol_address,
                "block_number":      tx.block_number,
                "block_timestamp":   tx.block_timestamp,
                "extra_data":        tx.extra_data,
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
        "contract_address":  fund.contract_address,
        "owner_wallet":      fund.owner_wallet,
        "total_balance":     float(fund.total_balance),
        "retirement_started": fund.retirement_started,
        "is_active":         fund.is_active,
        "created_at":        fund.created_at,
        "last_synced_at":    fund.last_synced_at,
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