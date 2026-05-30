"""
Endpoints del token sale ETRF.

ENDPOINTS PÚBLICOS:
  GET  /sale/round                   → ronda activa (cacheada 30s)

ENDPOINTS AUTENTICADOS (JWT requerido):
  GET  /sale/my-purchase             → compra + vesting del wallet conectado
  POST /sale/verify-purchase         → indexa una tx confirmada (fire-and-forget)
  GET  /sale/vesting/schedule        → schedule completo de vesting del wallet
  GET  /sale/vesting/round-info      → tokens reservados/vendidos/no-vendidos por ronda

DIFERENCIAS CON LA VERSIÓN ANTERIOR:
  - Toda la interacción con Web3 fue eliminada — ahora va a través de chain.py
  - Los schemas inline fueron eliminados — ahora vienen de api.schemas.sale
  - _index_purchase persiste en DB usando los modelos reales (SalePurchaseEvent,
    SaleWallet) en lugar de tener el código comentado
  - Se agregaron los endpoints de vesting que estaban definidos en schemas
    pero no expuestos
  - La lógica de ronda ahora usa raw_round_to_response() de chain.py
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func

from api.core.dependencies import get_current_wallet
from api.db.session import get_db_context
from api.db.models.sale import SalePurchaseEvent, SaleWallet
from api.schemas.sale import (
    PurchaseResponse,
    RoundResponse,
    RoundVestingInfoResponse,
    VerifyPurchaseRequest,
    VerifyPurchaseResponse,
    VestingScheduleResponse,
)
from api.services import chain
from api.services import vesting as vesting_service
from api.services.chain import ChainError, ConfigError, raw_round_to_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sale", tags=["sale"])

_round_cache: dict = {"data": None, "expires_at": 0.0}
_ROUND_CACHE_TTL = 30

async def _persist_event(event) -> None:
    now = datetime.now(timezone.utc)

    is_purchase = event.event_type.value == "purchase"
    usdc_delta  = event.usdc_amount  or 0
    token_delta = event.token_amount or 0
    round_bit   = (1 << event.round_id) if (is_purchase and event.round_id is not None) else 0

    try:
        async with get_db_context() as db:

            # 1. Insertar evento (idempotente por unique constraint)
            await db.execute(
                pg_insert(SalePurchaseEvent)
                .values(
                    tx_hash      = event.tx_hash,
                    event_type   = event.event_type.value,
                    wallet       = event.wallet,
                    round_id     = event.round_id,          
                    usdc_amount  = event.usdc_amount,
                    token_amount = event.token_amount,
                    block_number = event.block,
                    tx_timestamp = datetime.fromisoformat(event.timestamp),
                    indexed_at   = now,
                )
                .on_conflict_do_nothing(constraint="uq_purchase_events_tx_type")
            )

            # 2. Upsert en sale_wallets acumulando totales y bitmask de rondas
            await db.execute(
                pg_insert(SaleWallet)
                .values(
                    wallet               = event.wallet,
                    total_usdc_spent     = usdc_delta  if is_purchase else 0,
                    total_tokens_bought  = token_delta if is_purchase else 0,
                    total_tokens_claimed = token_delta if not is_purchase else 0,
                    rounds_participated  = round_bit,
                    purchase_count       = 1 if is_purchase else 0,
                    claim_count          = 1 if not is_purchase else 0,
                    first_purchase_at    = now if is_purchase else None,
                    last_activity_at     = now,
                    updated_at           = now,
                )
                .on_conflict_do_update(
                    index_elements=["wallet"],
                    set_={
                        "rounds_participated":  func.coalesce(
                            SaleWallet.rounds_participated, 0
                        ).op("|")(round_bit),
                        "total_usdc_spent":     SaleWallet.total_usdc_spent     + (usdc_delta  if is_purchase else 0),
                        "total_tokens_bought":  SaleWallet.total_tokens_bought  + (token_delta if is_purchase else 0),
                        "total_tokens_claimed": SaleWallet.total_tokens_claimed + (token_delta if not is_purchase else 0),
                        "purchase_count":       SaleWallet.purchase_count + (1 if is_purchase else 0),
                        "claim_count":          SaleWallet.claim_count    + (1 if not is_purchase else 0),
                        "first_purchase_at":    func.coalesce(
                            SaleWallet.first_purchase_at,
                            now if is_purchase else None,
                        ),
                        "last_activity_at":     now,
                        "updated_at":           now,
                    },
                )
            )

        logger.info(
            "Evento persistido | type=%s tx=%s wallet=%s round=%s",
            event.event_type.value, event.tx_hash[:12],
            event.wallet[:10], event.round_id,
        )

    except Exception as exc:
        logger.error(
            "Error persistiendo evento | tx=%s: %s",
            event.tx_hash[:12], exc, exc_info=True,
        )

async def _index_purchase(tx_hash: str, wallet: str) -> None:
    """
    Background task: verifica la tx on-chain y persiste el resultado.

    El cliente ya recibió 202 antes de que esto empiece.
    Si el RPC falla o la tx no tiene eventos ETRF conocidos, loguea y sale.
    El indexer periódico (cuando exista) cubrirá los casos perdidos.
    """
    logger.info("Indexando tx | tx=%s wallet=%s", tx_hash[:12], wallet[:10])

    try:
        event = await chain.verify_tx(tx_hash, wallet)
    except ConfigError as exc:
        logger.error("Config incompleta, no se puede indexar: %s", exc)
        return

    if event is None:
        logger.warning(
            "Tx no indexada (inválida, revertida o RPC caído) | tx=%s wallet=%s",
            tx_hash[:12], wallet[:10],
        )
        return

    await _persist_event(event)

@router.post(
    "/verify-purchase",
    response_model=VerifyPurchaseResponse,
    status_code=202,
    summary="Indexa una tx de compra o claim (fire-and-forget)",
)
async def verify_purchase(
    payload:    VerifyPurchaseRequest,
    background: BackgroundTasks,
    wallet:     str = Depends(get_current_wallet),
) -> VerifyPurchaseResponse:
    """
    POST /sale/verify-purchase
    Authorization: Bearer <token>
    Body: { txHash: "0x..." }

    El frontend llama esto inmediatamente después de que la tx se confirma
    on-chain (waitForTransactionReceipt resuelve). Responde 202 y procesa
    la verificación on-chain en background para no bloquear al cliente.
    """
    tx_hash = payload.tx_hash
    background.add_task(_index_purchase, tx_hash, wallet)
    logger.info("verify-purchase encolado | tx=%s wallet=%s", tx_hash[:12], wallet[:10])
    return VerifyPurchaseResponse(
        accepted = True,
        tx_hash  = tx_hash,
        message  = "Transacción recibida y en proceso de verificación",
    )

@router.get(
    "/round",
    response_model=RoundResponse,
    summary="Estado de la ronda activa (público, cacheado 30s)",
)
async def get_current_round() -> RoundResponse:
    """
    GET /sale/round — no requiere autenticación.

    Lee la ronda activa de SaleETRF y la cachea 30s en memoria.
    Si el RPC falla pero hay cache (aunque ransia), la devuelve
    en lugar de retornar 503 — el dato es eventualmente consistente.
    """
    now = time.time()
    if _round_cache["data"] and now < _round_cache["expires_at"]:
        return RoundResponse(**_round_cache["data"], cached=True)
    try:
        raw  = await chain.get_current_round()
        data = raw_round_to_response(raw, cached=False)
        _round_cache["data"]       = data
        _round_cache["expires_at"] = now + _ROUND_CACHE_TTL

        return RoundResponse(**data)

    except ConfigError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except ChainError as exc:
        if _round_cache["data"]:
            age = now - (_round_cache["expires_at"] - _ROUND_CACHE_TTL)
            logger.warning("RPC caído, sirviendo cache ransia (%.0fs)", age)
            return RoundResponse(**_round_cache["data"], cached=True)
        logger.error("Error leyendo ronda on-chain: %s", exc)
        raise HTTPException(status_code=503, detail="No se pudo leer la ronda on-chain")

@router.get(
    "/my-purchase",
    response_model=PurchaseResponse,
    summary="Compra y vesting del wallet autenticado",
)
async def get_my_purchase(
    wallet: str = Depends(get_current_wallet),
) -> PurchaseResponse:
    """
    GET /sale/my-purchase
    Authorization: Bearer <token>

    Combina datos de SaleETRF (compra) + VestingETRF (schedule) para el
    wallet autenticado. Útil para el backend propio — el frontend lee
    on-chain directamente via wagmi, pero este endpoint permite al backend
    enviar emails de confirmación o generar reportes sin estado de frontend.
    """
    try:
        current = await chain.get_current_round()
        raw     = await chain.get_user_purchase(wallet, current.id)

    except ConfigError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except ChainError as exc:
        logger.error("Error leyendo purchase | wallet=%s: %s", wallet[:10], exc)
        raise HTTPException(status_code=503, detail="No se pudo leer la compra on-chain")
    cliff_ends_at   = 0
    vesting_ends_at = 0
    if raw.start_time > 0 and raw.has_purchased:
        cliff_s         = current.cliff_months   * 30 * 24 * 3600
        vesting_s       = current.vesting_months * 30 * 24 * 3600
        cliff_ends_at   = raw.start_time + cliff_s
        vesting_ends_at = raw.start_time + vesting_s

    from api.services.chain import format_units
    return PurchaseResponse(
        wallet          = wallet,
        has_purchased   = raw.has_purchased,
        usdc_spent      = format_units(raw.usdc_spent,     6),
        tokens_bought   = format_units(raw.tokens_bought,  18),
        tokens_vested   = format_units(raw.tokens_vested,  18),
        tokens_claimed  = format_units(raw.tokens_claimed, 18),
        claimable       = format_units(raw.claimable,      18),
        start_time      = raw.start_time,
        cliff_ends_at   = cliff_ends_at,
        vesting_ends_at = vesting_ends_at,
    )

@router.get(
    "/vesting/schedule",
    response_model=VestingScheduleResponse,
    summary="Schedule de vesting de un wallet (admin o wallet propio)",
)
async def get_vesting_schedule(
    wallet_param: Optional[str] = Query(None, alias="wallet"),
    current_wallet: str = Depends(get_current_wallet),
) -> VestingScheduleResponse:
    """
    GET /sale/vesting/schedule?wallet=0x...   (admin — cualquier wallet)
    GET /sale/vesting/schedule                (wallet propio)
    Authorization: Bearer <token>

    Sin ?wallet= devuelve el schedule del wallet autenticado.
    Con ?wallet= solo lo puede hacer un admin (verificado contra settings).
    """
    from api.core.auth import is_admin
    target = current_wallet
    if wallet_param:
        if not is_admin(current_wallet):
            raise HTTPException(
                status_code=403,
                detail="Solo admins pueden consultar el schedule de otro wallet",
            )
        target = wallet_param.strip().lower()

    try:
        schedule = await vesting_service.get_schedule_with_claimable(target)
    except ConfigError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except ChainError as exc:
        logger.error("Error leyendo schedule | wallet=%s: %s", target[:10], exc)
        raise HTTPException(status_code=503, detail="No se pudo leer el schedule on-chain")
    if schedule is None:
        raise HTTPException(
            status_code=404,
            detail=f"El wallet {target} no tiene schedule de vesting",
        )
    return schedule

@router.get(
    "/vesting/round-info",
    response_model=RoundVestingInfoResponse,
    summary="Tokens reservados/vendidos y estado de recovery de una ronda",
)
async def get_round_vesting_info(
    round_id: int = Query(..., ge=0, le=2, description="Índice de ronda: 0=Seed, 1=Private, 2=Public"),
    _wallet:  str = Depends(get_current_wallet),
) -> RoundVestingInfoResponse:
    """
    GET /sale/vesting/round-info?round_id=0
    Authorization: Bearer <token>

    Devuelve los tokens reservados, vendidos, no-vendidos y el estado
    del recovery para una ronda específica de VestingETRF.
    Usado en la TreasuryPage para mostrar si recover_unsold() está disponible.
    """
    try:
        return await vesting_service.get_round_vesting_info(round_id)
    except ConfigError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except ChainError as exc:
        logger.error("Error leyendo round_info(%d): %s", round_id, exc)
        raise HTTPException(status_code=503, detail="No se pudo leer la info de la ronda on-chain")