import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from api.db.session import get_db
from api.db.models.user import User
from api.db.models.fund import PersonalFund
from api.db.models.transaction import Transaction
from api.db.repositories.contact_repo import ContactRepository
from api.db.repositories.survey_repo import SurveyRepository
from api.core.dependencies import require_admin_retirement, require_admin_sale
from api.services.user_service import UserService
from api.services.fund_service import FundService
from api.services.blockchain_service import BlockchainService
from api.services.vesting import get_admin_round_summaries  # ← importar función de vesting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


# ─────────────────────────────────────────────────────────────────────────────
# Retirement Admin Endpoints (requieren token con audience='retirement')
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    user_service = UserService(db)
    fund_service = FundService(db)
    survey_repo = SurveyRepository(db)
    contact_repo = ContactRepository(db)

    async def _treasury_stats():
        try:
            blockchain = BlockchainService()
            return await blockchain.get_treasury_stats()
        except Exception as e:
            logger.error("Treasury stats failed: %s", e)
            return {"error": str(e)}

    (
        user_stats,
        fund_stats,
        treasury_stats,
        survey_total,
        survey_wanting_info,
        survey_averages,
        survey_by_age,
        contact_total,
        contact_new,
    ) = await asyncio.gather(
        user_service.get_admin_user_stats(),
        fund_service.get_admin_fund_stats(),
        _treasury_stats(),
        survey_repo.count_total(),
        survey_repo.count_followups_wanting_info(),
        survey_repo.get_averages(),
        survey_repo.count_by_age(),
        contact_repo.count(),
        contact_repo.count(status="new"),
    )

    return {
        "users": user_stats,
        "funds": fund_stats,
        "treasury": treasury_stats,
        "surveys": {
            "total": survey_total,
            "wanting_more_info": survey_wanting_info,
            "averages": survey_averages,
            "by_age": survey_by_age,
        },
        "contacts": {
            "total": contact_total,
            "new": contact_new,
        },
    }


@router.get("/users")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    survey_completed: bool = None,
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    query = select(User).order_by(User.first_seen_at.desc()).offset(skip).limit(limit)
    if survey_completed is not None:
        query = query.where(User.survey_completed == survey_completed)
    result = await db.execute(query)
    users = result.scalars().all()
    return {
        "users": [
            {
                "wallet_address": u.wallet_address,
                "survey_completed": u.survey_completed,
                "age_range": u.age_range,
                "risk_tolerance": u.risk_tolerance,
                "crypto_experience": u.crypto_experience,
                "retirement_goal": u.retirement_goal,
                "investment_horizon_years": u.investment_horizon_years,
                "monthly_income_range": u.monthly_income_range,
                "country": u.country,
                "first_seen_at": u.first_seen_at,
                "last_active_at": u.last_active_at,
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
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    query = select(PersonalFund).order_by(PersonalFund.created_at.desc()).offset(skip).limit(limit)
    if retirement_started is not None:
        query = query.where(PersonalFund.retirement_started == retirement_started)
    result = await db.execute(query)
    funds = result.scalars().all()
    return {
        "funds": [
            {
                "contract_address": f.contract_address,
                "owner_wallet": f.owner_wallet,
                "total_balance": float(f.total_balance),
                "total_fees_paid": float(f.total_fees_paid),
                "total_invested": float(f.total_invested),
                "retirement_started": f.retirement_started,
                "early_retirement_approved": f.early_retirement_approved,
                "is_active": f.is_active,
                "created_at": f.created_at,
                "last_synced_at": f.last_synced_at,
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
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    query = select(Transaction).order_by(desc(Transaction.block_timestamp)).offset(skip).limit(limit)
    if event_type:
        query = query.where(Transaction.event_type == event_type)
    result = await db.execute(query)
    txs = result.scalars().all()
    return {
        "transactions": [
            {
                "tx_hash": tx.id,
                "fund_address": tx.fund_address,
                "wallet_address": tx.wallet_address,
                "event_type": tx.event_type,
                "gross_amount": float(tx.gross_amount) if tx.gross_amount else None,
                "fee_amount": float(tx.fee_amount) if tx.fee_amount else None,
                "net_amount": float(tx.net_amount) if tx.net_amount else None,
                "block_number": tx.block_number,
                "block_timestamp": tx.block_timestamp,
            }
            for tx in txs
        ],
        "count": len(txs),
    }


@router.get("/contacts")
async def list_contacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = Query(None, description="new | read | replied"),
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    repo = ContactRepository(db)
    messages, total = await asyncio.gather(
        repo.get_all(skip=skip, limit=limit, status=status),
        repo.count(status=status),
    )
    return {
        "messages": [
            {
                "id": m.id,
                "name": m.name,
                "email": m.email,
                "subject": m.subject,
                "message": m.message,
                "wallet_address": m.wallet_address,
                "status": m.status,
                "created_at": m.created_at,
            }
            for m in messages
        ],
        "count": total,
    }


@router.patch("/contacts/{msg_id}/read")
async def mark_contact_read(
    msg_id: int,
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    repo = ContactRepository(db)
    msg = await repo.mark_read(msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"success": True, "status": msg.status}


@router.get("/surveys")
async def list_surveys(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    repo = SurveyRepository(db)
    surveys, total, averages, by_age = await asyncio.gather(
        repo.get_all(skip=skip, limit=limit),
        repo.count_total(),
        repo.get_averages(),
        repo.count_by_age(),
    )
    return {
        "surveys": [
            {
                "id": s.id,
                "age": s.age,
                "trust_traditional": s.trust_traditional,
                "blockchain_familiarity": s.blockchain_familiarity,
                "retirement_concern": s.retirement_concern,
                "has_retirement_plan": s.has_retirement_plan,
                "values_in_retirement": s.values_in_retirement,
                "interested_in_blockchain": s.interested_in_blockchain,
                "created_at": s.created_at,
            }
            for s in surveys
        ],
        "total": total,
        "averages": averages,
        "by_age": by_age,
    }


@router.post("/indexer/run")
async def trigger_indexer(
    admin: str = Depends(require_admin_retirement),
    db: AsyncSession = Depends(get_db),
):
    from api.services.indexer_service import IndexerService
    from api.main import _indexer_lock

    try:
        async with _indexer_lock():
            indexer = IndexerService(db)
            result = await indexer.run_cycle()
    except Exception as exc:
        logger.error("Manual indexer trigger failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Indexer cycle failed: {exc}")
    logger.info(
        "Manual indexer run by admin=%s — indexed=%d",
        admin[:10],
        result.get("indexed", 0),
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Sale Admin Endpoints (requieren token con audience='sale')
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sale/rounds")
async def admin_sale_rounds(
    admin: str = Depends(require_admin_sale),  # ← requiere admin + audience sale
):
    """
    GET /admin/sale/rounds
    Retorna el resumen completo de todas las rondas de token sale.
    Requiere autenticación con audience='sale' y wallet admin.
    """
    try:
        summaries = await get_admin_round_summaries()
        return {
            "rounds": [
                {
                    "round_id": s.round_id,
                    "name": s.name,
                    "status": s.status.value,
                    "price": s.price,
                    "hard_cap": s.hard_cap,
                    "raised": s.raised,
                    "progress_pct": s.progress_pct,
                    "buyers": s.buyers,
                    "tokens_reserved": s.tokens_reserved,
                    "tokens_sold": s.tokens_sold,
                    "unsold_amount": s.unsold_amount,
                    "end_time": s.end_time,
                    "recovery_available_at": s.recovery_available_at,
                    "recovery_unlocked": s.recovery_unlocked,
                    "recovered": s.recovered,
                }
                for s in summaries
            ],
            "count": len(summaries),
        }
    except Exception as exc:
        logger.error("Error fetching sale rounds: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch sale rounds: {exc}"
        )


@router.get("/sale/participants")
async def admin_sale_participants(
    round_id: Optional[int] = Query(None, ge=0, le=2, description="Filtrar por ronda específica"),
    admin: str = Depends(require_admin_sale),
    db: AsyncSession = Depends(get_db),
):
    """
    GET /admin/sale/participants?round_id=0
    Lista todos los participantes de la token sale con sus compras.
    Requiere autenticación con audience='sale' y wallet admin.
    """
    from api.db.models.sale import SalePurchaseEvent, SaleWallet

    query = (
        select(SalePurchaseEvent)
        .where(SalePurchaseEvent.event_type == "purchase")
        .order_by(desc(SalePurchaseEvent.tx_timestamp))
    )

    if round_id is not None:
        query = query.where(SalePurchaseEvent.round_id == round_id)

    result = await db.execute(query)
    events = result.scalars().all()

    # Agrupar por wallet
    participants = {}
    for event in events:
        wallet = event.wallet
        if wallet not in participants:
            participants[wallet] = {
                "wallet": wallet,
                "total_usdc_spent": 0,
                "total_tokens_bought": 0,
                "rounds": [],
                "first_purchase_at": None,
                "last_purchase_at": None,
            }

        participants[wallet]["total_usdc_spent"] += float(event.usdc_amount or 0) / 1e6
        participants[wallet]["total_tokens_bought"] += float(event.token_amount or 0) / 1e18

        if not participants[wallet]["first_purchase_at"] or event.tx_timestamp < participants[wallet]["first_purchase_at"]:
            participants[wallet]["first_purchase_at"] = event.tx_timestamp
        if not participants[wallet]["last_purchase_at"] or event.tx_timestamp > participants[wallet]["last_purchase_at"]:
            participants[wallet]["last_purchase_at"] = event.tx_timestamp

        participants[wallet]["rounds"].append({
            "round_id": event.round_id,
            "usdc_amount": float(event.usdc_amount or 0) / 1e6,
            "token_amount": float(event.token_amount or 0) / 1e18,
            "tx_hash": event.tx_hash,
            "timestamp": event.tx_timestamp.isoformat(),
        })

    return {
        "participants": list(participants.values()),
        "total_participants": len(participants),
        "total_usdc_raised": sum(p["total_usdc_spent"] for p in participants.values()),
        "total_tokens_sold": sum(p["total_tokens_bought"] for p in participants.values()),
    }


@router.get("/sale/stats")
async def admin_sale_stats(
    admin: str = Depends(require_admin_sale),
    db: AsyncSession = Depends(get_db),
):
    """
    GET /admin/sale/stats
    Estadísticas agregadas de la token sale.
    Requiere autenticación con audience='sale' y wallet admin.
    """
    from api.db.models.sale import SalePurchaseEvent, SaleWallet
    from sqlalchemy import func

    # Estadísticas de compras
    purchase_stats = await db.execute(
        select(
            func.count(SalePurchaseEvent.id).label("total_transactions"),
            func.sum(SalePurchaseEvent.usdc_amount).label("total_usdc"),
            func.sum(SalePurchaseEvent.token_amount).label("total_tokens"),
            func.count(SalePurchaseEvent.wallet.distinct()).label("unique_buyers"),
        ).where(SalePurchaseEvent.event_type == "purchase")
    )
    purchase_row = purchase_stats.one()

    # Estadísticas por ronda
    round_stats = await db.execute(
        select(
            SalePurchaseEvent.round_id,
            func.count(SalePurchaseEvent.id).label("tx_count"),
            func.sum(SalePurchaseEvent.usdc_amount).label("usdc_raised"),
            func.sum(SalePurchaseEvent.token_amount).label("tokens_sold"),
            func.count(SalePurchaseEvent.wallet.distinct()).label("buyers"),
        )
        .where(SalePurchaseEvent.event_type == "purchase")
        .group_by(SalePurchaseEvent.round_id)
        .order_by(SalePurchaseEvent.round_id)
    )

    rounds = []
    for row in round_stats:
        rounds.append({
            "round_id": row.round_id,
            "tx_count": row.tx_count,
            "usdc_raised": float(row.usdc_raised or 0) / 1e6,
            "tokens_sold": float(row.tokens_sold or 0) / 1e18,
            "buyers": row.buyers,
        })

    # Estadísticas de claims
    claim_stats = await db.execute(
        select(
            func.count(SalePurchaseEvent.id).label("total_claims"),
            func.sum(SalePurchaseEvent.token_amount).label("total_tokens_claimed"),
        ).where(SalePurchaseEvent.event_type == "claim")
    )
    claim_row = claim_stats.one()

    # Wallets activos
    active_wallets = await db.execute(
        select(func.count(SaleWallet.wallet)).where(
            SaleWallet.total_usdc_spent > 0
        )
    )

    return {
        "overview": {
            "total_transactions": purchase_row.total_transactions or 0,
            "total_usdc_raised": float(purchase_row.total_usdc or 0) / 1e6,
            "total_tokens_sold": float(purchase_row.total_tokens or 0) / 1e18,
            "unique_buyers": purchase_row.unique_buyers or 0,
            "total_claims": claim_row.total_claims or 0,
            "total_tokens_claimed": float(claim_row.total_tokens_claimed or 0) / 1e18,
            "active_wallets": active_wallets.scalar() or 0,
        },
        "by_round": rounds,
    }