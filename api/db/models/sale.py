"""
Modelos SQLAlchemy para persistencia off-chain del protocolo de venta ETRF.

FILOSOFÍA:
  La chain es la fuente de verdad. Los modelos aquí son un índice secundario
  que acelera consultas frecuentes (historial de compras, panel de admin) sin
  necesidad de paginación de eventos on-chain en cada request.
  Nada en estos modelos puede contradecir el estado del contrato. Si hay
  divergencia, la chain gana. El indexer (verify-purchase background task)
  es el único que escribe en estas tablas.

TABLAS:
  sale_rounds          Snapshot de cada ronda, sincronizado periódicamente.
  sale_purchase_events Eventos TokensPurchased y TokensClaimed indexados.
  sale_wallets         Resumen acumulado por wallet (denormalización para perf).

CONVENCIONES:
  - Direcciones Ethereum: VARCHAR(42) en lowercase ("0x" + 40 hex).
  - Cantidades on-chain:  NUMERIC(78, 0) — caben uint256 sin pérdida de precisión.
  - Timestamps on-chain:  BIGINT (Unix epoch). Columnas de auditoría: TIMESTAMP WITH TIME ZONE.
  - Hashes de TX:         VARCHAR(66) en lowercase.
  - round_id:             SMALLINT (0=Seed, 1=Private, 2=Public).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────────────────────
# sale_rounds
# ─────────────────────────────────────────────────────────────────────────────
class SaleRound(Base):
    """
    Snapshot de una ronda de venta de SaleETRF.

    Se actualiza en cada sync del indexer (o al recibir un evento RoundActivated /
    RoundFinalized). No reemplaza la lectura on-chain — se usa para:
      - Dashboard de admin (sin RPC en cada request)
      - Validación de round_id en verify-purchase
      - Cálculo de progress_pct en /sale/round con caché

    Campos de cadena (price, hard_cap, …): almacenados como NUMERIC(78,0)
    en unidades on-chain (6 decimales para USDC, 18 para ETRF).
    El formateo a string decimal se hace en la capa de servicio.
    """
    __tablename__ = "sale_rounds"

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        comment="Índice de ronda on-chain: 0=Seed, 1=Private, 2=Public",
    )
    name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Nombre legible. Ej: 'Seed Round'",
    )
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="upcoming",
        comment="upcoming | active | ended",
    )

    # Precios y caps en unidades on-chain (USDC, 6 decimales)
    price      : Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, comment="USDC por 1 ETRF (6 dec)")
    hard_cap   : Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, comment="USDC máximo (6 dec)")
    raised     : Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0, comment="USDC recaudado (6 dec)")
    wallet_cap : Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, comment="USDC máx/wallet (6 dec)")

    # Vesting config (meses enteros del contrato)
    cliff_months   : Mapped[int] = mapped_column(SmallInteger, nullable=False)
    vesting_months : Mapped[int] = mapped_column(SmallInteger, nullable=False)

    # Timestamps on-chain (Unix epoch, 0 = no ocurrió aún)
    start_time : Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    end_time   : Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Contadores
    buyers         : Mapped[int]  = mapped_column(BigInteger,  nullable=False, default=0)
    is_finalized   : Mapped[bool] = mapped_column(Boolean,     nullable=False, default=False)

    # Auditoría
    synced_at : Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now_utc,
        onupdate=_now_utc,
        comment="Última vez que se sincronizó desde chain",
    )

    __table_args__ = (
        CheckConstraint("status IN ('upcoming','active','ended')", name="ck_sale_rounds_status"),
        CheckConstraint("id BETWEEN 0 AND 2",                      name="ck_sale_rounds_id"),
    )

    def __repr__(self) -> str:
        return f"<SaleRound id={self.id} name={self.name!r} status={self.status}>"

# ─────────────────────────────────────────────────────────────────────────────
# sale_purchase_events
# ─────────────────────────────────────────────────────────────────────────────
class SalePurchaseEvent(Base):
    """
    Evento indexado de compra (TokensPurchased) o claim (TokensClaimed).

    La clave única es (tx_hash, event_type): una misma tx puede contener
    en teoría ambos eventos si el contrato los emitiera en la misma tx,
    aunque en la práctica no ocurre — buy() y claim() son funciones separadas.

    Campos opcionales:
      usdc_amount   — solo en compras (event_type='purchase')
      token_amount  — en compras y claims
      round_id      — solo en compras (el evento TokensPurchased incluye round_id)
    """
    __tablename__ = "sale_purchase_events"

    # ── PK autoincremental + unique constraint funcional ──────────────────────
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    tx_hash: Mapped[str] = mapped_column(
        String(66),
        nullable=False,
        comment="0x + 64 hex chars, lowercase",
    )
    event_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="purchase | claim",
    )

    # Wallet del comprador / claimer (lowercase)
    wallet: Mapped[str] = mapped_column(
        String(42),
        nullable=False,
        index=True,
        comment="Ethereum address, lowercase",
    )

    # Ronda (None para claims si no se puede determinar — raro)
    round_id: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
        comment="0=Seed, 1=Private, 2=Public. NULL si no aplica",
    )

    # Cantidades en unidades on-chain
    usdc_amount : Mapped[int | None] = mapped_column(Numeric(78, 0), nullable=True,  comment="USDC (6 dec), solo en compras")
    token_amount: Mapped[int | None] = mapped_column(Numeric(78, 0), nullable=True,  comment="ETRF (18 dec)")

    # Datos de la tx
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp ISO 8601 UTC parseado del receipt",
    )

    # Auditoría
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now_utc,
        comment="Cuando se insertó en la DB",
    )

    __table_args__ = (
        # Una TX solo puede ser del mismo event_type una vez
        UniqueConstraint("tx_hash", "event_type", name="uq_purchase_events_tx_type"),
        CheckConstraint("event_type IN ('purchase','claim')",    name="ck_purchase_events_type"),
        CheckConstraint("round_id IS NULL OR round_id BETWEEN 0 AND 2", name="ck_purchase_events_round"),
        Index("ix_purchase_events_wallet_round", "wallet", "round_id"),
        Index("ix_purchase_events_block",        "block_number"),
    )

    def __repr__(self) -> str:
        return (
            f"<SalePurchaseEvent id={self.id} type={self.event_type} "
            f"wallet={self.wallet[:10]}… tx={self.tx_hash[:12]}…>"
        )

# ─────────────────────────────────────────────────────────────────────────────
# sale_wallets
# ─────────────────────────────────────────────────────────────────────────────
class SaleWallet(Base):
    """
    Resumen acumulado de participación de un wallet en la sale.

    Denormalización deliberada para acelerar consultas del tipo:
      "Dame el historial de participación de 0xABC en todas las rondas"
    sin tener que agregar sale_purchase_events cada vez.

    Se mantiene actualizado en el mismo background task que inserta eventos.
    Si hay una discrepancia entre esta tabla y sale_purchase_events,
    sale_purchase_events es la fuente correcta — esta tabla se puede reconstruir.

    rounds_participated: bitmask de rondas. Bit 0 = Seed, Bit 1 = Private, Bit 2 = Public.
    Ej: 0b101 (=5) → participó en Seed y Public pero no en Private.
    """
    __tablename__ = "sale_wallets"

    wallet: Mapped[str] = mapped_column(
        String(42),
        primary_key=True,
        comment="Ethereum address, lowercase",
    )

    # Totales acumulados en todas las rondas
    total_usdc_spent   : Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0, comment="USDC total (6 dec)")
    total_tokens_bought: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0, comment="ETRF total (18 dec)")
    total_tokens_claimed: Mapped[int]= mapped_column(Numeric(78, 0), nullable=False, default=0, comment="ETRF reclamado (18 dec)")

    # Participación por ronda
    rounds_participated: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="Bitmask: bit0=Seed, bit1=Private, bit2=Public",
    )

    # Contadores
    purchase_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="Nº de txs de compra indexadas"
    )
    claim_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="Nº de txs de claim indexadas"
    )

    # Primera y última actividad
    first_purchase_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at : Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Auditoría
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now_utc,
        onupdate=_now_utc,
    )

    __table_args__ = (
        CheckConstraint("rounds_participated BETWEEN 0 AND 7", name="ck_sale_wallets_rounds_bitmask"),
    )

    def __repr__(self) -> str:
        return (
            f"<SaleWallet wallet={self.wallet[:10]}… "
            f"usdc={self.total_usdc_spent} rounds={bin(self.rounds_participated)}>"
        )