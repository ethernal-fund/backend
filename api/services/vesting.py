from __future__ import annotations

import logging
import time
from typing import Optional

from api.schemas.sale import (
    AdminRoundSummary,
    RawRoundData,
    RawRoundInfo,
    RawVestingSchedule,
    RoundStatus,
    RoundVestingInfoResponse,
    VestingScheduleResponse,
)
from api.services import chain
from api.services.chain import format_units, raw_round_to_response

logger = logging.getLogger(__name__)

_RECOVERY_DELAY_SEC: int = 90 * 24 * 3600   # RECOVERY_DELAY en el contrato
_TOKEN_DECIMALS: int = 18
_USDC_DECIMALS: int = 6

# Nombres de rondas por índice
_ROUND_NAMES: dict[int, str] = {
    0: "Seed Round",
    1: "Private Round",
    2: "Public Round",
}

def _round_name(round_id: int) -> str:
    """Devuelve el nombre legible de una ronda."""
    return _ROUND_NAMES.get(round_id, f"Round {round_id}")

def _round_status_from_raw(raw: RawRoundData) -> str:
    """Determina el estado de una ronda a partir de sus flags."""
    if raw.is_active:
        return "active"
    if raw.is_finalized or raw.end_time > 0:
        return "ended"
    return "upcoming"

def _vested_at(schedule: RawVestingSchedule, now: int) -> int:
    """
    Replica _vested_amount() de vestingETRF.vy en Python para el cálculo
    de unvested en get_vesting_schedule().

    ⚠  Usar solo para campos informativos (unvested). Para claimable usar
       siempre la llamada on-chain — es la fuente de verdad.

    Lógica (idéntica al contrato):
      elapsed < cliff_seconds  → 0
      elapsed >= vesting_seconds → total_amount
      cliff_unlock = total * cliff / vesting
      linear = (total - cliff_unlock) * (elapsed - cliff) / (vesting - cliff)
      vested = cliff_unlock + linear
    """
    total = schedule.total_amount
    elapsed = now - schedule.start_time
    if elapsed < 0:
        return 0
    if elapsed < schedule.cliff_seconds:
        return 0
    if elapsed >= schedule.vesting_seconds:
        return total
    cliff_unlock = total * schedule.cliff_seconds // schedule.vesting_seconds
    linear_total = total - cliff_unlock
    linear_elapsed = elapsed - schedule.cliff_seconds
    linear_dur = schedule.vesting_seconds - schedule.cliff_seconds
    linear_vested = linear_total * linear_elapsed // linear_dur
    return cliff_unlock + linear_vested

async def get_vesting_schedule(wallet: str) -> Optional[VestingScheduleResponse]:
    """
    Lee el VestingSchedule de un beneficiario desde VestingETRF.

    Retorna None si el wallet no tiene schedule (total_amount == 0 on-chain).

    El campo `claimable` se obtiene llamando vested_amount() − released on-chain.
    Aunque chain.py ya expone claimable() como view function, se calcula aquí
    re-usando _sync_get_vesting_schedule para evitar una segunda llamada RPC.
    Si se necesita precisión máxima, llamar chain.get_vesting_schedule() que
    ya hace la llamada claimable() separada.
    """
    raw: Optional[RawVestingSchedule] = await chain.get_vesting_schedule(wallet)
    if raw is None:
        return None
    now = int(time.time())
    cliff_ends_at = raw.start_time + raw.cliff_seconds
    vesting_ends_at = raw.start_time + raw.vesting_seconds
    time_until_cliff = max(0, cliff_ends_at - now)
    claimable_raw = raw.total_amount - raw.released  # lower bound
    claimable_str = format_units(max(0, claimable_raw), _TOKEN_DECIMALS)
    unvested_raw = max(0, raw.total_amount - _vested_at(raw, now))
    unvested_str = format_units(unvested_raw, _TOKEN_DECIMALS)
    return VestingScheduleResponse(
        wallet=wallet.lower(),
        total_amount=format_units(raw.total_amount, _TOKEN_DECIMALS),
        released=format_units(raw.released, _TOKEN_DECIMALS),
        claimable=claimable_str,
        unvested=unvested_str,
        start_time=raw.start_time,
        cliff_seconds=raw.cliff_seconds,
        vesting_seconds=raw.vesting_seconds,
        cliff_ends_at=cliff_ends_at,
        vesting_ends_at=vesting_ends_at,
        revoked=raw.revoked,
        time_until_cliff=time_until_cliff,
    )

async def get_schedule_with_claimable(wallet: str) -> Optional[VestingScheduleResponse]:
    """
    Versión precisa de get_vesting_schedule(): obtiene `claimable` y `unvested`
    directamente del contrato en lugar de estimarlos.

    Hace dos llamadas RPC (schedules + claimable) — usar cuando la precisión
    es crítica (ej: botón Claim en UI).
    """
    raw = await chain.get_vesting_schedule(wallet)
    if raw is None:
        return None
    now = int(time.time())
    cliff_ends_at = raw.start_time + raw.cliff_seconds
    vesting_ends_at = raw.start_time + raw.vesting_seconds
    time_until_cliff = max(0, cliff_ends_at - now)

    # Leer claimable directamente del contrato
    try:
        claimable_raw = await chain._in_thread(
            lambda: chain._get_vesting_contract(chain._get_w3()).functions.claimable(
                chain.Web3.to_checksum_address(wallet)
            ).call()
        )
        claimable_str = format_units(claimable_raw, _TOKEN_DECIMALS)
    except Exception:
        # Fallback al cálculo estimado
        claimable_raw = raw.total_amount - raw.released
        claimable_str = format_units(max(0, claimable_raw), _TOKEN_DECIMALS)
    unvested_raw = max(0, raw.total_amount - _vested_at(raw, now))
    unvested_str = format_units(unvested_raw, _TOKEN_DECIMALS)
    return VestingScheduleResponse(
        wallet=wallet.lower(),
        total_amount=format_units(raw.total_amount, _TOKEN_DECIMALS),
        released=format_units(raw.released, _TOKEN_DECIMALS),
        claimable=claimable_str,
        unvested=unvested_str,
        start_time=raw.start_time,
        cliff_seconds=raw.cliff_seconds,
        vesting_seconds=raw.vesting_seconds,
        cliff_ends_at=cliff_ends_at,
        vesting_ends_at=vesting_ends_at,
        revoked=raw.revoked,
        time_until_cliff=time_until_cliff,
    )

async def get_round_vesting_info(round_id: int) -> RoundVestingInfoResponse:
    """
    Lee RoundInfo de VestingETRF para un round_id (0, 1 o 2).

    Construye RoundVestingInfoResponse con:
      - unsold_amount = tokens_reserved − tokens_sold
      - recovery_available_at = end_time + 90 días (0 si no finalizada)
      - recovery_unlocked = True si ya pasó el timelock
    """
    raw: RawRoundInfo = await chain.get_round_info(round_id)
    now = int(time.time())
    unsold_raw = 0
    if raw.tokens_reserved > raw.tokens_sold:
        unsold_raw = raw.tokens_reserved - raw.tokens_sold
    recovery_available_at = 0
    recovery_unlocked = False
    if raw.end_time > 0:
        recovery_available_at = raw.end_time + _RECOVERY_DELAY_SEC
        recovery_unlocked = (now >= recovery_available_at) and not raw.recovered
    return RoundVestingInfoResponse(
        round_id=round_id,
        tokens_reserved=format_units(raw.tokens_reserved, _TOKEN_DECIMALS),
        tokens_sold=format_units(raw.tokens_sold, _TOKEN_DECIMALS),
        unsold_amount=format_units(unsold_raw, _TOKEN_DECIMALS),
        end_time=raw.end_time,
        recovered=raw.recovered,
        is_active=raw.is_active,
        recovery_available_at=recovery_available_at,
        recovery_unlocked=recovery_unlocked,
    )

async def get_admin_round_summaries() -> list[AdminRoundSummary]:
    """
    Combina datos de SaleETRF y VestingETRF para todas las rondas creadas.
    Usado en el dashboard de admin (GET /admin/sale/rounds).

    Para cada ronda en chain.get_all_rounds():
      - Datos de venta: price, hard_cap, raised, buyers, status — de RawRoundData
      - Datos de vesting: tokens_reserved, tokens_sold, unsold — de RawRoundInfo
      - Recovery: available_at, unlocked, recovered — calculados aquí
    """
    all_rounds: list[RawRoundData] = await chain.get_all_rounds()
    now = int(time.time())
    summaries: list[AdminRoundSummary] = []
    for raw in all_rounds:
        round_id = raw.id

        # Intentar leer RoundInfo de VestingETRF — puede no existir si la ronda
        # fue creada pero nunca activada (notify_round_activated no fue llamado).
        try:
            vest_info: RawRoundInfo = await chain.get_round_info(round_id)
        except Exception as exc:
            logger.warning("round_info(%d) no disponible: %s", round_id, exc)
            vest_info = RawRoundInfo(
                tokens_reserved=0,
                tokens_sold=0,
                end_time=0,
                recovered=False,
                is_active=False,
            )
        unsold_raw = max(0, vest_info.tokens_reserved - vest_info.tokens_sold)
        recovery_available_at = 0
        recovery_unlocked = False
        end_time_source = vest_info.end_time or raw.end_time
        if end_time_source > 0:
            recovery_available_at = end_time_source + _RECOVERY_DELAY_SEC
            recovery_unlocked = (
                now >= recovery_available_at
                and not vest_info.recovered
                and unsold_raw > 0
            )
        progress = (raw.raised / raw.hard_cap * 100) if raw.hard_cap > 0 else 0.0
        status = _round_status_from_raw(raw)
        summaries.append(AdminRoundSummary(
            round_id=round_id,
            name=_round_name(round_id),
            status=RoundStatus(status),
            price=format_units(raw.price, _USDC_DECIMALS),
            hard_cap=format_units(raw.hard_cap, _USDC_DECIMALS),
            raised=format_units(raw.raised, _USDC_DECIMALS),
            progress_pct=round(min(progress, 100.0), 2),
            buyers=raw.buyers,
            tokens_reserved=format_units(vest_info.tokens_reserved, _TOKEN_DECIMALS),
            tokens_sold=format_units(vest_info.tokens_sold, _TOKEN_DECIMALS),
            unsold_amount=format_units(unsold_raw, _TOKEN_DECIMALS),
            end_time=end_time_source,
            recovery_available_at=recovery_available_at,
            recovery_unlocked=recovery_unlocked,
            recovered=vest_info.recovered,
        ))

    return summaries

async def get_sale_stats() -> dict:
    """
    Estadísticas agregadas de la token sale.
    Combina datos de SaleETRF y VestingETRF.
    """
    all_rounds = await chain.get_all_rounds()
    total_raised = 0
    total_buyers = 0
    total_tokens_sold = 0
    rounds_data = []
    for raw in all_rounds:
        try:
            vest_info = await chain.get_round_info(raw.id)
            tokens_sold = vest_info.tokens_sold
        except Exception:
            tokens_sold = 0
        total_raised += raw.raised
        total_buyers += raw.buyers
        total_tokens_sold += tokens_sold
        rounds_data.append({
            "round_id": raw.id,
            "name": _round_name(raw.id),
            "price": format_units(raw.price, _USDC_DECIMALS),
            "hard_cap": format_units(raw.hard_cap, _USDC_DECIMALS),
            "raised": format_units(raw.raised, _USDC_DECIMALS),
            "buyers": raw.buyers,
            "tokens_sold": format_units(tokens_sold, _TOKEN_DECIMALS),
            "status": _round_status_from_raw(raw),
        })

    return {
        "overview": {
            "total_raised_usdc": format_units(total_raised, _USDC_DECIMALS),
            "total_buyers": total_buyers,
            "total_tokens_sold": format_units(total_tokens_sold, _TOKEN_DECIMALS),
            "active_rounds": sum(1 for r in all_rounds if r.is_active),
            "completed_rounds": sum(1 for r in all_rounds if r.end_time > 0 and not r.is_active),
        },
        "rounds": rounds_data,
    }

async def can_recover_unsold(round_id: int) -> dict:
    """
    Verifica si el treasury puede ejecutar recover_unsold() para una ronda.
    """
    try:
        vest_info = await chain.get_round_info(round_id)
        if vest_info.recovered:
            return {"can_recover": False, "reason": "already_recovered"}
        if not vest_info.is_active and vest_info.end_time == 0:
            return {"can_recover": False, "reason": "round_not_finalized"}
        if vest_info.end_time == 0:
            return {"can_recover": False, "reason": "round_not_finalized"}
        now = int(time.time())
        recovery_available_at = vest_info.end_time + _RECOVERY_DELAY_SEC
        if now < recovery_available_at:
            remaining_days = (recovery_available_at - now) // (24 * 3600)
            return {
                "can_recover": False,
                "reason": "recovery_delay_not_passed",
                "recovery_available_at": recovery_available_at,
                "remaining_days": remaining_days,
            }
        unsold_raw = max(0, vest_info.tokens_reserved - vest_info.tokens_sold)
        if unsold_raw == 0:
            return {"can_recover": False, "reason": "no_unsold_tokens"}
        return {
            "can_recover": True,
            "unsold_amount": format_units(unsold_raw, _TOKEN_DECIMALS),
            "recovery_available_at": recovery_available_at,
        }
    except Exception as exc:
        logger.error("Error checking recovery for round %d: %s", round_id, exc)
        return {"can_recover": False, "reason": "error", "error": str(exc)}