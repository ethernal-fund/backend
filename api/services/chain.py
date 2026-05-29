"""
Capa de acceso a blockchain para el protocolo Ethernal.fund.
Toda interacción con Web3 pasa por aquí — los routers y otros servicios
nunca instancian Web3 directamente.

DISEÑO:
  - Una sola función _get_w3() cachea la conexión HTTP al RPC.
  - Las funciones de lectura son síncronas (Web3.py es síncrono) y se
    ejecutan en un thread pool vía run_in_executor() para no bloquear
    el event loop de FastAPI.
  - Las funciones async públicas son el único punto de entrada para los routers.
  - Los ABIs mínimos se definen aquí como constantes — se mantienen
    sincronizados con los contratos Vyper en saleETRF.vy y vestingETRF.vy.

CONTRATOS CUBIERTOS:
  - SaleETRF   (SALE_CONTRACT_ADDRESS)   → lectura de rondas y compras
  - VestingETRF (VESTING_CONTRACT_ADDRESS) → lectura de schedules y round_info

VARIABLES DE ENTORNO (en api/config.py → Settings):
  SALE_RPC_URL              RPC endpoint (Alchemy, Infura, etc.)
  SALE_CONTRACT_ADDRESS     Dirección de SaleETRF
  VESTING_CONTRACT_ADDRESS  Dirección de VestingETRF
  SALE_CHAIN_ID             Chain ID para validación (ej: 11155111 para Sepolia)

MANEJO DE ERRORES:
  - RuntimeError  → config incompleta (settings missing)  → HTTP 501
  - ChainError    → fallo de conectividad o dato inesperado → HTTP 503
  Nunca se propagan excepciones crudas de Web3 fuera de este módulo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from web3 import Web3
from web3.contract import Contract

from api.config import settings
from api.schemas.sale import (
    EventType,
    IndexedSaleEvent,
    RawPurchaseData,
    RawRoundData,
    RawRoundInfo,
    RawVestingSchedule,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Excepciones propias
# ─────────────────────────────────────────────────────────────────────────────

class ChainError(Exception):
    """Error de conectividad o dato inesperado desde la chain."""


class ConfigError(RuntimeError):
    """Variable de entorno obligatoria no configurada."""


# ─────────────────────────────────────────────────────────────────────────────
# ABIs mínimos
# Sincronizar con saleETRF.vy y vestingETRF.vy si se agregan funciones.
# ─────────────────────────────────────────────────────────────────────────────

#
# SaleETRF — funciones de lectura y eventos
#
SALE_ABI: list[dict] = [
    # ── Eventos ──────────────────────────────────────────────────────────────
    {
        "name":   "TokensPurchased",
        "type":   "event",
        "inputs": [
            {"name": "buyer",        "type": "address", "indexed": True},
            {"name": "round_id",     "type": "uint8",   "indexed": True},
            {"name": "usdc_amount",  "type": "uint256", "indexed": False},
            {"name": "token_amount", "type": "uint256", "indexed": False},
        ],
    },
    {
        "name":   "RoundActivated",
        "type":   "event",
        "inputs": [
            {"name": "round_id",        "type": "uint8",   "indexed": True},
            {"name": "start_time",      "type": "uint256", "indexed": False},
            {"name": "tokens_reserved", "type": "uint256", "indexed": False},
        ],
    },
    {
        "name":   "RoundFinalized",
        "type":   "event",
        "inputs": [
            {"name": "round_id",     "type": "uint8",   "indexed": True},
            {"name": "total_raised", "type": "uint256", "indexed": False},
            {"name": "total_buyers", "type": "uint256", "indexed": False},
            {"name": "end_time",     "type": "uint256", "indexed": False},
        ],
    },
    # ── Lectura ───────────────────────────────────────────────────────────────
    {
        "name":            "getCurrentRound",
        "type":            "function",
        "stateMutability": "view",
        "inputs":          [],
        "outputs": [
            {"name": "id",             "type": "uint8"},
            {"name": "price",          "type": "uint256"},
            {"name": "hard_cap",       "type": "uint256"},
            {"name": "raised",         "type": "uint256"},
            {"name": "wallet_cap",     "type": "uint256"},
            {"name": "start_time",     "type": "uint256"},
            {"name": "end_time",       "type": "uint256"},
            {"name": "is_active",      "type": "bool"},
            {"name": "cliff_months",   "type": "uint8"},
            {"name": "vesting_months", "type": "uint8"},
        ],
    },
    {
        "name":            "getRound",
        "type":            "function",
        "stateMutability": "view",
        "inputs":          [{"name": "round_id", "type": "uint8"}],
        "outputs": [
            {"name": "id",             "type": "uint8"},
            {"name": "price",          "type": "uint256"},
            {"name": "hard_cap",       "type": "uint256"},
            {"name": "raised",         "type": "uint256"},
            {"name": "wallet_cap",     "type": "uint256"},
            {"name": "start_time",     "type": "uint256"},
            {"name": "end_time",       "type": "uint256"},
            {"name": "is_active",      "type": "bool"},
            {"name": "is_finalized",   "type": "bool"},
            {"name": "buyers",         "type": "uint256"},
            {"name": "cliff_months",   "type": "uint8"},
            {"name": "vesting_months", "type": "uint8"},
        ],
    },
    {
        "name":            "getUserPurchase",
        "type":            "function",
        "stateMutability": "view",
        # Toma (user, round_id) — usamos el round_id de la ronda activa
        "inputs":  [
            {"name": "user",     "type": "address"},
            {"name": "round_id", "type": "uint8"},
        ],
        "outputs": [
            {"name": "usdc_spent",    "type": "uint256"},
            {"name": "tokens_bought", "type": "uint256"},
            {"name": "has_purchased", "type": "bool"},
        ],
    },
    {
        "name":            "getUserTotals",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "total_usdc_spent",   "type": "uint256"},
            {"name": "total_tokens_bought","type": "uint256"},
            {"name": "rounds_participated","type": "uint8"},
        ],
    },
    {
        "name":            "active_round",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name":            "round_count",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name":            "totalRaised",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "remainingCap",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "walletRemaining",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "user", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "tokensForUsdc",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "usdc_amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

#
# VestingETRF — funciones de lectura
#
VESTING_ABI: list[dict] = [
    # ── Eventos ──────────────────────────────────────────────────────────────
    {
        "name":   "TokensClaimed",
        "type":   "event",
        "inputs": [
            {"name": "beneficiary",    "type": "address", "indexed": True},
            {"name": "amount",         "type": "uint256", "indexed": False},
            {"name": "total_released", "type": "uint256", "indexed": False},
        ],
    },
    # ── Lectura ───────────────────────────────────────────────────────────────
    {
        "name":            "schedules",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "arg0", "type": "address"}],
        "outputs": [
            {"name": "total_amount",    "type": "uint256"},
            {"name": "released",        "type": "uint256"},
            {"name": "start_time",      "type": "uint256"},
            {"name": "cliff_seconds",   "type": "uint256"},
            {"name": "vesting_seconds", "type": "uint256"},
            {"name": "revoked",         "type": "bool"},
        ],
    },
    {
        "name":            "round_info",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "arg0", "type": "uint8"}],
        "outputs": [
            {"name": "tokens_reserved", "type": "uint256"},
            {"name": "tokens_sold",     "type": "uint256"},
            {"name": "end_time",        "type": "uint256"},
            {"name": "recovered",       "type": "bool"},
            {"name": "is_active",       "type": "bool"},
        ],
    },
    {
        "name":            "claimable",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "beneficiary", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "vested_amount",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "beneficiary", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "unvested_amount",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "beneficiary", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "time_until_cliff",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "beneficiary", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "available_balance",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "total_allocated",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "beneficiary_count",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "recovery_available_at",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "round_id", "type": "uint8"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name":            "unsold_amount",
        "type":            "function",
        "stateMutability": "view",
        "inputs":  [{"name": "round_id", "type": "uint8"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# Sentinel del contrato: active_round = 255 → ninguna ronda activa
_INACTIVE: int = 255

# Nombres de rondas por índice (coinciden con el orden de create_round())
_ROUND_NAMES: dict[int, str] = {
    0: "Seed Round",
    1: "Private Round",
    2: "Public Round",
}


# ─────────────────────────────────────────────────────────────────────────────
# Conexión y contratos
# ─────────────────────────────────────────────────────────────────────────────

def _get_rpc_url() -> str:
    url = getattr(settings, "SALE_RPC_URL", None)
    if not url:
        raise ConfigError("SALE_RPC_URL no configurada en settings")
    return url


def _get_w3() -> Web3:
    """
    Instancia de Web3 con reconexión lazy.
    No cacheamos a nivel de módulo porque el RPC puede rotar (Alchemy failover).
    El overhead de HTTPProvider es mínimo — es solo configuración, no un socket.
    """
    w3 = Web3(Web3.HTTPProvider(_get_rpc_url(), request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        raise ChainError(f"No se pudo conectar al RPC: {_get_rpc_url()[:40]}...")
    return w3


def _get_sale_contract(w3: Web3) -> Contract:
    addr = getattr(settings, "SALE_CONTRACT_ADDRESS", None)
    if not addr:
        raise ConfigError("SALE_CONTRACT_ADDRESS no configurada en settings")
    return w3.eth.contract(
        address=Web3.to_checksum_address(addr),
        abi=SALE_ABI,
    )


def _get_vesting_contract(w3: Web3) -> Contract:
    addr = getattr(settings, "VESTING_CONTRACT_ADDRESS", None)
    if not addr:
        raise ConfigError("VESTING_CONTRACT_ADDRESS no configurada en settings")
    return w3.eth.contract(
        address=Web3.to_checksum_address(addr),
        abi=VESTING_ABI,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de formato
# ─────────────────────────────────────────────────────────────────────────────

def format_units(value: int, decimals: int) -> str:
    """
    Convierte un entero on-chain a string decimal legible.
    Ej: format_units(10_000, 6) → "0.01"
        format_units(50_000_000_000_000_000_000, 18) → "50.0"

    No usa floats para evitar errores de precisión con montos grandes.
    """
    if value < 0:
        raise ValueError(f"format_units: valor negativo ({value})")
    divisor      = 10 ** decimals
    integer_part = value // divisor
    frac_part    = value % divisor
    frac_str     = str(frac_part).zfill(decimals).rstrip("0") or "0"
    return f"{integer_part}.{frac_str}"


def _round_status(raw: RawRoundData) -> str:
    """Determina el estado de una ronda a partir de sus flags."""
    if raw.is_active:
        return "active"
    if raw.is_finalized or raw.end_time > 0:
        return "ended"
    return "upcoming"


def _round_name(round_id: int) -> str:
    return _ROUND_NAMES.get(round_id, f"Round {round_id}")


# ─────────────────────────────────────────────────────────────────────────────
# Funciones síncronas de lectura (se ejecutan en thread pool)
# ─────────────────────────────────────────────────────────────────────────────

def _sync_get_current_round() -> RawRoundData:
    """
    Lee la ronda activa (o la última creada si no hay activa) desde SaleETRF.
    Mapea la tupla del ABI a RawRoundData.
    """
    w3       = _get_w3()
    contract = _get_sale_contract(w3)

    try:
        raw = contract.functions.getCurrentRound().call()
    except Exception as exc:
        raise ChainError(f"getCurrentRound() falló: {exc}") from exc

    # raw = (id, price, hard_cap, raised, wallet_cap, start_time, end_time,
    #        is_active, cliff_months, vesting_months)
    return RawRoundData(
        id             = int(raw[0]),
        price          = int(raw[1]),
        hard_cap       = int(raw[2]),
        raised         = int(raw[3]),
        wallet_cap     = int(raw[4]),
        start_time     = int(raw[5]),
        end_time       = int(raw[6]),
        is_active      = bool(raw[7]),
        cliff_months   = int(raw[8]),
        vesting_months = int(raw[9]),
    )


def _sync_get_round(round_id: int) -> RawRoundData:
    """Lee una ronda específica por índice (0–2) desde SaleETRF.getRound()."""
    w3       = _get_w3()
    contract = _get_sale_contract(w3)

    try:
        raw = contract.functions.getRound(round_id).call()
    except Exception as exc:
        raise ChainError(f"getRound({round_id}) falló: {exc}") from exc

    # raw = (id, price, hard_cap, raised, wallet_cap, start_time, end_time,
    #        is_active, is_finalized, buyers, cliff_months, vesting_months)
    return RawRoundData(
        id             = int(raw[0]),
        price          = int(raw[1]),
        hard_cap       = int(raw[2]),
        raised         = int(raw[3]),
        wallet_cap     = int(raw[4]),
        start_time     = int(raw[5]),
        end_time       = int(raw[6]),
        is_active      = bool(raw[7]),
        is_finalized   = bool(raw[8]),
        buyers         = int(raw[9]),
        cliff_months   = int(raw[10]),
        vesting_months = int(raw[11]),
    )


def _sync_get_all_rounds() -> list[RawRoundData]:
    """
    Lee todas las rondas creadas hasta ahora.
    Llama round_count() y luego getRound() por cada índice.
    """
    w3       = _get_w3()
    contract = _get_sale_contract(w3)

    try:
        count = int(contract.functions.round_count().call())
    except Exception as exc:
        raise ChainError(f"round_count() falló: {exc}") from exc

    rounds = []
    for i in range(count):
        try:
            raw = contract.functions.getRound(i).call()
            rounds.append(RawRoundData(
                id             = int(raw[0]),
                price          = int(raw[1]),
                hard_cap       = int(raw[2]),
                raised         = int(raw[3]),
                wallet_cap     = int(raw[4]),
                start_time     = int(raw[5]),
                end_time       = int(raw[6]),
                is_active      = bool(raw[7]),
                is_finalized   = bool(raw[8]),
                buyers         = int(raw[9]),
                cliff_months   = int(raw[10]),
                vesting_months = int(raw[11]),
            ))
        except Exception as exc:
            logger.warning("getRound(%d) falló: %s", i, exc)

    return rounds


def _sync_get_user_purchase(wallet: str, round_id: int) -> RawPurchaseData:
    """
    Lee la compra de un usuario en una ronda específica desde SaleETRF.
    Enriquece con datos de VestingETRF (vested, claimed, claimable, start_time).
    """
    w3            = _get_w3()
    sale_contract = _get_sale_contract(w3)
    vest_contract = _get_vesting_contract(w3)
    checksum      = Web3.to_checksum_address(wallet)

    try:
        purchase_raw = sale_contract.functions.getUserPurchase(checksum, round_id).call()
        # (usdc_spent, tokens_bought, has_purchased)
        usdc_spent    = int(purchase_raw[0])
        tokens_bought = int(purchase_raw[1])
        has_purchased = bool(purchase_raw[2])
    except Exception as exc:
        raise ChainError(f"getUserPurchase() falló: {exc}") from exc

    # Leer schedule de VestingETRF para complementar
    tokens_vested  = 0
    tokens_claimed = 0
    claimable      = 0
    start_time     = 0

    if has_purchased:
        try:
            schedule_raw = vest_contract.functions.schedules(checksum).call()
            # (total_amount, released, start_time, cliff_seconds, vesting_seconds, revoked)
            tokens_claimed = int(schedule_raw[1])
            start_time     = int(schedule_raw[2])
        except Exception as exc:
            logger.warning("schedules(%s) falló: %s", wallet[:10], exc)

        try:
            tokens_vested = int(vest_contract.functions.vested_amount(checksum).call())
        except Exception as exc:
            logger.warning("vested_amount(%s) falló: %s", wallet[:10], exc)

        try:
            claimable = int(vest_contract.functions.claimable(checksum).call())
        except Exception as exc:
            logger.warning("claimable(%s) falló: %s", wallet[:10], exc)

    return RawPurchaseData(
        usdc_spent     = usdc_spent,
        tokens_bought  = tokens_bought,
        has_purchased  = has_purchased,
        tokens_vested  = tokens_vested,
        tokens_claimed = tokens_claimed,
        claimable      = claimable,
        start_time     = start_time,
    )


def _sync_get_vesting_schedule(wallet: str) -> Optional[RawVestingSchedule]:
    """
    Lee el VestingSchedule completo de un beneficiario desde VestingETRF.
    Retorna None si el usuario no es beneficiario (total_amount == 0).
    """
    w3            = _get_w3()
    vest_contract = _get_vesting_contract(w3)
    checksum      = Web3.to_checksum_address(wallet)

    try:
        raw = vest_contract.functions.schedules(checksum).call()
        # (total_amount, released, start_time, cliff_seconds, vesting_seconds, revoked)
    except Exception as exc:
        raise ChainError(f"schedules({wallet[:10]}) falló: {exc}") from exc

    if int(raw[0]) == 0:
        return None

    return RawVestingSchedule(
        total_amount    = int(raw[0]),
        released        = int(raw[1]),
        start_time      = int(raw[2]),
        cliff_seconds   = int(raw[3]),
        vesting_seconds = int(raw[4]),
        revoked         = bool(raw[5]),
    )


def _sync_get_round_info(round_id: int) -> RawRoundInfo:
    """
    Lee RoundInfo de VestingETRF para un round_id dado.
    Usado en TreasuryPage para mostrar tokens no vendidos y estado del recovery.
    """
    w3            = _get_w3()
    vest_contract = _get_vesting_contract(w3)

    try:
        raw = vest_contract.functions.round_info(round_id).call()
        # (tokens_reserved, tokens_sold, end_time, recovered, is_active)
    except Exception as exc:
        raise ChainError(f"round_info({round_id}) falló: {exc}") from exc

    return RawRoundInfo(
        tokens_reserved = int(raw[0]),
        tokens_sold     = int(raw[1]),
        end_time        = int(raw[2]),
        recovered       = bool(raw[3]),
        is_active       = bool(raw[4]),
    )


def _sync_verify_tx(tx_hash: str, expected_wallet: str) -> Optional[IndexedSaleEvent]:
    """
    Verifica una transacción on-chain y parsea el evento ETRF correspondiente.

    Orden de búsqueda:
      1. TokensPurchased en SaleETRF
      2. TokensClaimed   en VestingETRF

    Retorna None si:
      - La tx no existe en el RPC
      - La tx revirtió (status != 1)
      - El buyer/claimer no coincide con expected_wallet
      - No se encontró ningún evento ETRF conocido

    No lanza excepciones — cualquier error se loguea y retorna None para
    que el background task no crashee silenciosamente.
    """
    try:
        w3            = _get_w3()
        sale_contract = _get_sale_contract(w3)
        vest_contract = _get_vesting_contract(w3)
        receipt       = w3.eth.get_transaction_receipt(tx_hash)

        if receipt is None:
            logger.warning("TX no encontrada on-chain | tx=%s", tx_hash[:12])
            return None

        if receipt.status != 1:
            logger.warning("TX revertida | tx=%s status=%s", tx_hash[:12], receipt.status)
            return None

        timestamp = datetime.now(timezone.utc).isoformat()
        block     = receipt.blockNumber
        wallet    = expected_wallet.lower()

        # 1. Intentar TokensPurchased (SaleETRF)
        try:
            events = sale_contract.events.TokensPurchased().process_receipt(receipt)
            if events:
                evt   = events[0]
                buyer = evt["args"]["buyer"].lower()
                if buyer != wallet:
                    logger.warning(
                        "Buyer mismatch en TX | tx=%s expected=%s got=%s",
                        tx_hash[:12], wallet[:10], buyer[:10],
                    )
                    return None
                return IndexedSaleEvent(
                    event_type   = EventType.purchase,
                    tx_hash      = tx_hash,
                    wallet       = buyer,
                    block        = block,
                    usdc_amount  = int(evt["args"]["usdc_amount"]),
                    token_amount = int(evt["args"]["token_amount"]),
                    timestamp    = timestamp,
                )
        except Exception as exc:
            logger.debug("No se pudo parsear TokensPurchased: %s", exc)

        # 2. Intentar TokensClaimed (VestingETRF)
        try:
            events = vest_contract.events.TokensClaimed().process_receipt(receipt)
            if events:
                evt     = events[0]
                claimer = evt["args"]["beneficiary"].lower()
                if claimer != wallet:
                    logger.warning(
                        "Claimer mismatch en TX | tx=%s expected=%s got=%s",
                        tx_hash[:12], wallet[:10], claimer[:10],
                    )
                    return None
                return IndexedSaleEvent(
                    event_type   = EventType.claim,
                    tx_hash      = tx_hash,
                    wallet       = claimer,
                    block        = block,
                    token_amount = int(evt["args"]["amount"]),
                    timestamp    = timestamp,
                )
        except Exception as exc:
            logger.debug("No se pudo parsear TokensClaimed: %s", exc)

        logger.warning("TX válida sin eventos ETRF conocidos | tx=%s", tx_hash[:12])
        return None

    except ConfigError:
        raise   # re-raise para que el router la maneje como 501
    except Exception as exc:
        logger.error("Error verificando tx %s: %s", tx_hash[:12], exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# API pública async — para usar desde routers y otros services
# ─────────────────────────────────────────────────────────────────────────────

async def _in_thread(fn, *args):
    """Ejecuta una función síncrona en el thread pool del event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


async def get_current_round() -> RawRoundData:
    """Lee la ronda activa (o última creada) desde SaleETRF."""
    return await _in_thread(_sync_get_current_round)


async def get_round(round_id: int) -> RawRoundData:
    """Lee una ronda específica por índice."""
    return await _in_thread(_sync_get_round, round_id)


async def get_all_rounds() -> list[RawRoundData]:
    """Lee todas las rondas creadas."""
    return await _in_thread(_sync_get_all_rounds)


async def get_user_purchase(wallet: str, round_id: int) -> RawPurchaseData:
    """
    Lee la compra del wallet en una ronda específica.
    Incluye datos de vesting de VestingETRF.
    """
    return await _in_thread(_sync_get_user_purchase, wallet, round_id)


async def get_vesting_schedule(wallet: str) -> Optional[RawVestingSchedule]:
    """
    Lee el VestingSchedule de un beneficiario.
    Retorna None si el wallet no tiene schedule.
    """
    return await _in_thread(_sync_get_vesting_schedule, wallet)


async def get_round_info(round_id: int) -> RawRoundInfo:
    """Lee el RoundInfo de VestingETRF para un round_id."""
    return await _in_thread(_sync_get_round_info, round_id)


async def verify_tx(tx_hash: str, wallet: str) -> Optional[IndexedSaleEvent]:
    """
    Verifica una transacción on-chain.
    Retorna el evento indexado o None si la tx es inválida.
    Diseñado para ser llamado desde background tasks.
    """
    return await _in_thread(_sync_verify_tx, tx_hash, wallet)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de conversión: Raw → Response schemas
# (se usan también desde services/vesting.py)
# ─────────────────────────────────────────────────────────────────────────────

def raw_round_to_response(raw: RawRoundData, *, cached: bool = False) -> dict:
    """
    Convierte RawRoundData al dict que construye RoundResponse.
    Centralizado aquí para que sale.py y vesting.py usen la misma lógica.
    """
    progress = (raw.raised / raw.hard_cap * 100) if raw.hard_cap > 0 else 0.0
    return {
        "id":             raw.id,
        "name":           _round_name(raw.id),
        "status":         _round_status(raw),
        "price":          format_units(raw.price,      6),
        "hard_cap":       format_units(raw.hard_cap,   6),
        "raised":         format_units(raw.raised,     6),
        "wallet_cap":     format_units(raw.wallet_cap, 6),
        "start_time":     raw.start_time,
        "end_time":       raw.end_time,
        "cliff_months":   raw.cliff_months,
        "vesting_months": raw.vesting_months,
        "progress_pct":   round(min(progress, 100.0), 2),
        "buyers":         raw.buyers,
        "cached":         cached,
    }