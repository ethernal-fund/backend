from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from api.db.session import get_db
from api.db.models.user import User
from api.db.models.fund import PersonalFund
from api.db.models.transaction import Transaction
from api.core.dependencies import require_admin
from api.services.user_service import UserService
from api.services.fund_service import FundService
from api.services.blockchain_service import BlockchainService
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/stats")
async def admin_stats(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user_service = UserService(db)
    fund_service = FundService(db)

    user_stats = await user_service.get_admin_user_stats()
    fund_stats = await fund_service.get_admin_fund_stats()

    try:
        blockchain     = BlockchainService()
        treasury_stats = await blockchain.get_treasury_stats()
    except Exception as e:
        logger.error(f"Treasury stats failed: {e}")
        treasury_stats = {"error": str(e)}

    return {
        "users":    user_stats,
        "funds":    fund_stats,
        "treasury": treasury_stats,
    }

@router.get("/users")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    survey_completed: bool = None,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(User).order_by(User.first_seen_at.desc()).offset(skip).limit(limit)
    if survey_completed is not None:
        query = query.where(User.survey_completed == survey_completed)
    result = await db.execute(query)
    users  = result.scalars().all()

    return {
        "users": [
            {
                "wallet_address":           u.wallet_address,
                "survey_completed":         u.survey_completed,
                "age_range":                u.age_range,
                "risk_tolerance":           u.risk_tolerance,
                "crypto_experience":        u.crypto_experience,
                "retirement_goal":          u.retirement_goal,
                "investment_horizon_years": u.investment_horizon_years,
                "monthly_income_range":     u.monthly_income_range,
                "country":                  u.country,
                "first_seen_at":            u.first_seen_at,
                "last_active_at":           u.last_active_at,
            }
            for u in users
        ],
        "count": len(users),
    }

@router.get("/funds")
async def list_funds(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    retirement_started: bool = None,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(PersonalFund).order_by(PersonalFund.created_at.desc()).offset(skip).limit(limit)
    if retirement_started is not None:
        query = query.where(PersonalFund.retirement_started == retirement_started)
    result = await db.execute(query)
    funds  = result.scalars().all()

    return {
        "funds": [
            {
                "contract_address":          f.contract_address,
                "owner_wallet":              f.owner_wallet,
                "total_balance":             float(f.total_balance),
                "total_fees_paid":           float(f.total_fees_paid),
                "total_invested":            float(f.total_invested),
                "retirement_started":        f.retirement_started,
                "early_retirement_approved": f.early_retirement_approved,
                "is_active":                 f.is_active,
                "created_at":                f.created_at,
                "last_synced_at":            f.last_synced_at,
            }
            for f in funds
        ],
        "count": len(funds),
    }

@router.get("/transactions")
async def list_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    event_type: str = None,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Transaction).order_by(desc(Transaction.block_timestamp)).offset(skip).limit(limit)
    if event_type:
        query = query.where(Transaction.event_type == event_type)
    result = await db.execute(query)
    txs    = result.scalars().all()

    return {
        "transactions": [
            {
                "tx_hash":         tx.id,
                "fund_address":    tx.fund_address,
                "wallet_address":  tx.wallet_address,
                "event_type":      tx.event_type,
                "gross_amount":    float(tx.gross_amount) if tx.gross_amount else None,
                "fee_amount":      float(tx.fee_amount)   if tx.fee_amount   else None,
                "net_amount":      float(tx.net_amount)   if tx.net_amount   else None,
                "block_number":    tx.block_number,
                "block_timestamp": tx.block_timestamp,
            }
            for tx in txs
        ],
        "count": len(txs),
    }

@router.post("/indexer/run")
async def trigger_indexer(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from api.services.indexer_service import IndexerService
    indexer = IndexerService(db)
    return await indexer.run_cycle()