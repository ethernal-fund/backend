"""sale token tables

Revision ID: c4e8f2a1d9b7
Revises: d13ba5c1f2d8
Create Date: 2026-05-28 00:00:00.000000

Crea las tres tablas del protocolo de token sale ETRF en el schema 'public'.

TABLAS:
  sale_rounds          Snapshot de cada ronda (0=Seed, 1=Private, 2=Public),
                       sincronizado periódicamente desde SaleETRF.

  sale_purchase_events Eventos TokensPurchased y TokensClaimed indexados.
                       Clave única: (tx_hash, event_type) — idempotente.

  sale_wallets         Resumen acumulado por wallet (denormalización para perf).
                       Actualizado en el mismo background task que inserta eventos.

RELACIÓN CON CHAIN:
  La chain es la fuente de verdad. Estas tablas son un índice secundario que
  acelera consultas sin RPC. Si hay divergencia, la chain gana.

NOTAS:
  - NUMERIC(78, 0) para cantidades on-chain: caben uint256 sin pérdida.
  - BIGINT para timestamps Unix y block numbers.
  - SMALLINT para round_id (0–2) y contadores de bajo cardinality.
  - Los CHECK CONSTRAINTS replican las reglas de negocio del contrato:
      round_id ∈ {0,1,2}, status ∈ {upcoming,active,ended}, etc.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision:       str                            = "c4e8f2a1d9b7"
down_revision:  Union[str, Sequence[str], None] = "d13ba5c1f2d8"
branch_labels:  Union[str, Sequence[str], None] = None
depends_on:     Union[str, Sequence[str], None] = None

def upgrade() -> None:

    # ── sale_rounds ───────────────────────────────────────────────────────────
    op.create_table(
        "sale_rounds",

        sa.Column("id",             sa.SmallInteger(),              primary_key=True,
                  comment="Índice de ronda on-chain: 0=Seed, 1=Private, 2=Public"),
        sa.Column("name",           sa.String(64),                  nullable=False,
                  comment="Nombre legible. Ej: 'Seed Round'"),
        sa.Column("status",         sa.String(10),                  nullable=False,  server_default="upcoming",
                  comment="upcoming | active | ended"),

        # Cantidades on-chain (USDC, 6 decimales) — NUMERIC(78,0) cabe uint256
        sa.Column("price",          sa.Numeric(78, 0),              nullable=False,
                  comment="USDC por 1 ETRF (6 dec)"),
        sa.Column("hard_cap",       sa.Numeric(78, 0),              nullable=False,
                  comment="USDC máximo a recaudar (6 dec)"),
        sa.Column("raised",         sa.Numeric(78, 0),              nullable=False,  server_default="0",
                  comment="USDC recaudado hasta ahora (6 dec)"),
        sa.Column("wallet_cap",     sa.Numeric(78, 0),              nullable=False,
                  comment="USDC máximo por wallet (6 dec)"),

        # Vesting config (meses enteros del contrato)
        sa.Column("cliff_months",   sa.SmallInteger(),              nullable=False),
        sa.Column("vesting_months", sa.SmallInteger(),              nullable=False),

        # Timestamps on-chain (Unix epoch; 0 = no ocurrió aún)
        sa.Column("start_time",     sa.BigInteger(),                nullable=False,  server_default="0"),
        sa.Column("end_time",       sa.BigInteger(),                nullable=False,  server_default="0"),

        # Contadores
        sa.Column("buyers",         sa.BigInteger(),                nullable=False,  server_default="0"),
        sa.Column("is_finalized",   sa.Boolean(),                   nullable=False,  server_default=sa.false()),

        # Auditoría
        sa.Column("synced_at",      sa.DateTime(timezone=True),     nullable=False,
                  server_default=sa.text("now()"),
                  comment="Última vez que se sincronizó desde chain"),

        # Constraints
        sa.CheckConstraint("status IN ('upcoming','active','ended')", name="ck_sale_rounds_status"),
        sa.CheckConstraint("id BETWEEN 0 AND 2",                      name="ck_sale_rounds_id"),
        sa.PrimaryKeyConstraint("id",                                 name=op.f("pk_sale_rounds")),
    )

    # ── sale_purchase_events ──────────────────────────────────────────────────
    op.create_table(
        "sale_purchase_events",

        sa.Column("id",           sa.BigInteger(),            primary_key=True, autoincrement=True),
        sa.Column("tx_hash",      sa.String(66),              nullable=False,
                  comment="0x + 64 hex chars, lowercase"),
        sa.Column("event_type",   sa.String(10),              nullable=False,
                  comment="purchase | claim"),
        sa.Column("wallet",       sa.String(42),              nullable=False,
                  comment="Ethereum address, lowercase"),
        sa.Column("round_id",     sa.SmallInteger(),          nullable=True,
                  comment="0=Seed, 1=Private, 2=Public. NULL si no aplica (claims)"),

        # Cantidades on-chain
        sa.Column("usdc_amount",  sa.Numeric(78, 0),          nullable=True,
                  comment="USDC (6 dec). Solo en compras"),
        sa.Column("token_amount", sa.Numeric(78, 0),          nullable=True,
                  comment="ETRF (18 dec). Compras y claims"),

        # Datos de la tx
        sa.Column("block_number", sa.BigInteger(),            nullable=False),
        sa.Column("tx_timestamp", sa.DateTime(timezone=True), nullable=False,
                  comment="Timestamp ISO 8601 UTC del bloque"),

        # Auditoría
        sa.Column("indexed_at",   sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()"),
                  comment="Cuándo se insertó en la DB"),

        # Constraints
        sa.UniqueConstraint("tx_hash", "event_type",          name="uq_purchase_events_tx_type"),
        sa.CheckConstraint("event_type IN ('purchase','claim')",              name="ck_purchase_events_type"),
        sa.CheckConstraint("round_id IS NULL OR round_id BETWEEN 0 AND 2",   name="ck_purchase_events_round"),
        sa.PrimaryKeyConstraint("id",                                         name=op.f("pk_sale_purchase_events")),
    )

    # Índices de sale_purchase_events
    op.create_index("ix_purchase_events_wallet",       "sale_purchase_events", ["wallet"])
    op.create_index("ix_purchase_events_wallet_round", "sale_purchase_events", ["wallet", "round_id"])
    op.create_index("ix_purchase_events_block",        "sale_purchase_events", ["block_number"])
    op.create_index("ix_purchase_events_tx_hash",      "sale_purchase_events", ["tx_hash"])

    # ── sale_wallets ──────────────────────────────────────────────────────────
    op.create_table(
        "sale_wallets",

        sa.Column("wallet",                sa.String(42),              primary_key=True,
                  comment="Ethereum address, lowercase"),

        # Totales acumulados en todas las rondas
        sa.Column("total_usdc_spent",      sa.Numeric(78, 0),          nullable=False, server_default="0",
                  comment="USDC total (6 dec)"),
        sa.Column("total_tokens_bought",   sa.Numeric(78, 0),          nullable=False, server_default="0",
                  comment="ETRF total comprado (18 dec)"),
        sa.Column("total_tokens_claimed",  sa.Numeric(78, 0),          nullable=False, server_default="0",
                  comment="ETRF total reclamado (18 dec)"),

        # Participación por ronda — bitmask: bit0=Seed, bit1=Private, bit2=Public
        # Ej: 0b101 (=5) → participó en Seed y Public
        sa.Column("rounds_participated",   sa.SmallInteger(),           nullable=False, server_default="0",
                  comment="Bitmask: bit0=Seed, bit1=Private, bit2=Public"),

        # Contadores
        sa.Column("purchase_count",        sa.SmallInteger(),           nullable=False, server_default="0",
                  comment="Nº de txs de compra indexadas"),
        sa.Column("claim_count",           sa.SmallInteger(),           nullable=False, server_default="0",
                  comment="Nº de txs de claim indexadas"),

        # Primera y última actividad
        sa.Column("first_purchase_at",     sa.DateTime(timezone=True),  nullable=True),
        sa.Column("last_activity_at",      sa.DateTime(timezone=True),  nullable=True),

        # Auditoría
        sa.Column("updated_at",            sa.DateTime(timezone=True),  nullable=False,
                  server_default=sa.text("now()")),

        # Constraints
        sa.CheckConstraint("rounds_participated BETWEEN 0 AND 7", name="ck_sale_wallets_rounds_bitmask"),
        sa.PrimaryKeyConstraint("wallet",                          name=op.f("pk_sale_wallets")),
    )

def downgrade() -> None:
    op.drop_table("sale_wallets")
    op.drop_index("ix_purchase_events_tx_hash",      table_name="sale_purchase_events")
    op.drop_index("ix_purchase_events_block",        table_name="sale_purchase_events")
    op.drop_index("ix_purchase_events_wallet_round", table_name="sale_purchase_events")
    op.drop_index("ix_purchase_events_wallet",       table_name="sale_purchase_events")
    op.drop_table("sale_purchase_events")
    op.drop_table("sale_rounds")