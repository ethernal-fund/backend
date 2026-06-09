from __future__ import annotations
from enum       import Enum
from typing     import Optional
from pydantic   import BaseModel, Field, field_validator

class RoundStatus(str, Enum):
    upcoming   = "upcoming"
    active     = "active"
    ended      = "ended"

class EventType(str, Enum):
    purchase = "purchase"
    claim    = "claim"

class RawRoundData(BaseModel):
    """
    Tupla cruda de getCurrentRound() / getRound() en SaleETRF.
    Todos los valores enteros tal como vienen del ABI (sin formatear).

    getCurrentRound() retorna:
      (id, price, hard_cap, raised, wallet_cap,
       start_time, end_time, is_active, cliff_months, vesting_months)

    getRound() retorna lo mismo + is_finalized + buyers.
    """
    id:             int
    price:          int   # USDC/token, 6 decimales. Ej: 10_000 = $0.01
    hard_cap:       int   # USDC total, 6 decimales
    raised:         int   # USDC recaudado, 6 decimales
    wallet_cap:     int   # USDC máx por wallet, 6 decimales
    start_time:     int   # Unix timestamp, 0 si no activada
    end_time:       int   # Unix timestamp, 0 si no finalizada
    is_active:      bool
    is_finalized:   bool  = False
    buyers:         int   = 0
    cliff_months:   int   # uint8 en el contrato
    vesting_months: int   # uint8 en el contrato

class RawPurchaseData(BaseModel):
    """
    Tupla cruda de getUserPurchase() en SaleETRF.
    El contrato devuelve (usdc_spent, tokens_bought, has_purchased).
    Los campos de vesting (vested / claimed / claimable) vienen de VestingETRF.
    """
    usdc_spent:     int
    tokens_bought:  int
    has_purchased:  bool
    tokens_vested:  int = 0
    tokens_claimed: int = 0
    claimable:      int = 0
    start_time:     int = 0   # schedule.start_time en VestingETRF

class RawVestingSchedule(BaseModel):
    """
    VestingSchedule struct de VestingETRF.schedules[beneficiary].
    """
    total_amount:    int
    released:        int
    start_time:      int
    cliff_seconds:   int
    vesting_seconds: int
    revoked:         bool

class RawRoundInfo(BaseModel):
    """
    RoundInfo struct de VestingETRF.round_info[round_id].
    """
    tokens_reserved: int
    tokens_sold:     int
    end_time:        int  
    recovered:       bool
    is_active:       bool

class RoundResponse(BaseModel):
    """
    Respuesta de GET /sale/round.
    Las cantidades USDC/ETRF se entregan como strings para evitar pérdida
    de precisión en JSON (JS no maneja uint256 nativamente).
    """
    id:             int
    name:           str   # "Seed Round", "Private Round", "Public Round"
    status:         RoundStatus
    price:          str   = Field(description="USDC por 1 ETRF, formateado. Ej: '0.01'")
    hard_cap:       str   = Field(description="USDC máximo a recaudar. Ej: '1000000.00'")
    raised:         str   = Field(description="USDC recaudado hasta ahora")
    wallet_cap:     str   = Field(description="USDC máximo por wallet")
    start_time:     int
    end_time:       int
    cliff_months:   int
    vesting_months: int
    progress_pct:   float = Field(ge=0.0, le=100.0)
    buyers:         int   = 0
    cached:         bool  = False

class PurchaseResponse(BaseModel):
    """
    Respuesta de GET /sale/my-purchase.
    Combina datos de SaleETRF (compra) y VestingETRF (schedule).
    """
    wallet:         str
    has_purchased:  bool
    usdc_spent:     str   = Field(description="USDC total invertido. Ej: '500.00'")
    tokens_bought:  str   = Field(description="ETRF comprado. Ej: '50000.00'")
    tokens_vested:  str   = Field(description="ETRF vested (incluyendo ya reclamados)")
    tokens_claimed: str   = Field(description="ETRF ya reclamados")
    claimable:      str   = Field(description="ETRF disponibles para reclamar ahora")
    start_time:     int   = Field(description="Timestamp de inicio del vesting")
    cliff_ends_at:  int   = Field(0, description="Timestamp en que termina el cliff")
    vesting_ends_at:int   = Field(0, description="Timestamp en que termina el vesting")

class VestingScheduleResponse(BaseModel):
    """
    Detalle del schedule de vesting de un beneficiario.
    Respuesta de GET /sale/vesting/schedule?wallet=0x...
    """
    wallet:          str
    total_amount:    str   = Field(description="ETRF total asignado")
    released:        str   = Field(description="ETRF ya reclamados")
    claimable:       str   = Field(description="ETRF disponibles ahora")
    unvested:        str   = Field(description="ETRF todavía bloqueados")
    start_time:      int
    cliff_seconds:   int
    vesting_seconds: int
    cliff_ends_at:   int   = Field(description="start_time + cliff_seconds")
    vesting_ends_at: int   = Field(description="start_time + vesting_seconds")
    revoked:         bool
    time_until_cliff:int   = Field(description="Segundos hasta el cliff. 0 si ya pasó")

class RoundVestingInfoResponse(BaseModel):
    """
    Información de vesting de una ronda específica.
    Respuesta de GET /sale/vesting/round-info?round_id=0
    Útil para la TreasuryPage (recovery de tokens no vendidos).
    """
    round_id:          int
    tokens_reserved:   str   = Field(description="ETRF reservados al activar la ronda")
    tokens_sold:       str   = Field(description="ETRF efectivamente asignados a compradores")
    unsold_amount:     str   = Field(description="ETRF no vendidos = reservados - vendidos")
    end_time:          int
    recovered:         bool
    is_active:         bool
    recovery_available_at: int = Field(
        description="Timestamp en que el treasury puede ejecutar recover_unsold(). 0 si la ronda no finalizó"
    )
    recovery_unlocked: bool = Field(
        description="True si el timelock de 90 días ya pasó y se puede ejecutar recover_unsold()"
    )

class VerifyPurchaseRequest(BaseModel):
    """
    Body de POST /sale/verify-purchase.
    txHash validado con regex — el endpoint rechaza hashes malformados con 400.
    """
    tx_hash: str = Field(
        alias="txHash",
        description="Hash de la tx confirmada (0x + 64 hex chars)",
        examples=["0xabc123def456..."],
    )

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        import re
        v = v.strip()
        if not re.match(r'^0x[0-9a-fA-F]{64}$', v):
            raise ValueError("txHash debe ser 0x seguido de exactamente 64 caracteres hex")
        return v

    model_config = {"populate_by_name": True}

class VerifyPurchaseResponse(BaseModel):
    """Respuesta de POST /sale/verify-purchase (202 Accepted)."""
    accepted:  bool
    tx_hash:   str
    message:   str

# ─────────────────────────────────────────────────────────────────────────────
# Schema interno: evento indexado de compra/claim
# ─────────────────────────────────────────────────────────────────────────────
class IndexedSaleEvent(BaseModel):
    """
    Evento parseado por _verify_tx_on_chain() en services/chain.py.
    Se persiste en la tabla sale_purchase_events.
    No se expone directamente en la API.
    """
    event_type:   EventType
    tx_hash:      str
    wallet:       str
    block:        int
    round_id:     Optional[int] = None
    usdc_amount:  Optional[int] = None   # Solo en compras (6 decimales)
    token_amount: Optional[int] = None   # ETRF (18 decimales) — compra o claim
    timestamp:    str                    # ISO 8601 UTC

# ─────────────────────────────────────────────────────────────────────────────
# Schema de admin — /admin/sale/rounds
# ─────────────────────────────────────────────────────────────────────────────
class AdminRoundSummary(BaseModel):
    """
    Vista completa de todas las rondas para el dashboard de admin.
    Combina datos de SaleETRF + VestingETRF.
    """
    round_id:        int
    name:            str
    status:          RoundStatus
    price:           str
    hard_cap:        str
    raised:          str
    progress_pct:    float
    buyers:          int
    tokens_reserved: str
    tokens_sold:     str
    unsold_amount:   str
    end_time:        int
    recovery_available_at: int
    recovery_unlocked:     bool
    recovered:             bool