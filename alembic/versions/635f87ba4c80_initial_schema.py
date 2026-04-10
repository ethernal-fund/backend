"""initial schema — app principal

Revision ID: 635f87ba4c80
Revises:
Create Date: 2026-04-08 16:10:24.876804

Schema 'public' de la app principal (usuarios, fondos, protocolos, etc.).
Esta migración NO toca el schema 'faucet' — ese schema es propiedad exclusiva
del servicio faucet-api y tiene su propia cadena de migraciones.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision:       str                            = "635f87ba4c80"
down_revision:  Union[str, Sequence[str], None] = None
branch_labels:  Union[str, Sequence[str], None] = None
depends_on:     Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── anonymous_surveys ─────────────────────────────────────────────────────
    op.create_table(
        "anonymous_surveys",
        sa.Column("id",                      sa.Integer(),    autoincrement=True, nullable=False),
        sa.Column("age",                     sa.String(10),   nullable=False),
        sa.Column("trust_traditional",       sa.SmallInteger(), nullable=False),
        sa.Column("blockchain_familiarity",  sa.SmallInteger(), nullable=False),
        sa.Column("retirement_concern",      sa.SmallInteger(), nullable=False),
        sa.Column("has_retirement_plan",     sa.SmallInteger(), nullable=False),
        sa.Column("values_in_retirement",    sa.SmallInteger(), nullable=False),
        sa.Column("interested_in_blockchain",sa.SmallInteger(), nullable=False),
        sa.Column("created_at",              sa.DateTime(),   nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anonymous_surveys")),
    )

    # ── contact_messages ──────────────────────────────────────────────────────
    op.create_table(
        "contact_messages",
        sa.Column("id",             sa.Integer(),    autoincrement=True, nullable=False),
        sa.Column("name",           sa.String(100),  nullable=False),
        sa.Column("email",          sa.String(255),  nullable=False),
        sa.Column("subject",        sa.String(200),  nullable=True),
        sa.Column("message",        sa.Text(),       nullable=False),
        sa.Column("wallet_address", sa.String(42),   nullable=True),
        sa.Column("status",         sa.String(20),   nullable=True),
        sa.Column("created_at",     sa.DateTime(),   nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_messages")),
    )
    op.create_index(op.f("ix_contact_messages_email"),  "contact_messages", ["email"],  unique=False)
    op.create_index(op.f("ix_contact_messages_status"), "contact_messages", ["status"], unique=False)

    # ── defi_protocols ────────────────────────────────────────────────────────
    op.create_table(
        "defi_protocols",
        sa.Column("protocol_address",  sa.String(42),                      nullable=False),
        sa.Column("name",              sa.String(100),                     nullable=False),
        sa.Column("apy",               sa.Numeric(precision=10, scale=4),  nullable=True),
        sa.Column("risk_level",        sa.Integer(),                       nullable=False),
        sa.Column("is_active",         sa.Boolean(),                       nullable=True),
        sa.Column("is_verified",       sa.Boolean(),                       nullable=True),
        sa.Column("total_deposited",   sa.Numeric(precision=20, scale=6),  nullable=True),
        sa.Column("added_at",          sa.DateTime(),                      nullable=True),
        sa.Column("last_updated_at",   sa.DateTime(),                      nullable=True),
        sa.Column("synced_at",         sa.DateTime(),                      nullable=True),
        sa.PrimaryKeyConstraint("protocol_address", name=op.f("pk_defi_protocols")),
    )

    # ── survey_followups ──────────────────────────────────────────────────────
    op.create_table(
        "survey_followups",
        sa.Column("id",              sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("survey_id",       sa.Integer(), nullable=True),
        sa.Column("wants_more_info", sa.Boolean(), nullable=False),
        sa.Column("email",           sa.String(255), nullable=True),
        sa.Column("created_at",      sa.DateTime(),  nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_survey_followups")),
    )

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("wallet_address",          sa.String(42),  nullable=False),
        sa.Column("survey_completed",        sa.Boolean(),   nullable=False),
        sa.Column("survey_completed_at",     sa.DateTime(),  nullable=True),
        sa.Column("age_range",               sa.String(20),  nullable=True),
        sa.Column("risk_tolerance",          sa.SmallInteger(), nullable=True),
        sa.Column("crypto_experience",       sa.String(20),  nullable=True),
        sa.Column("retirement_goal",         sa.String(30),  nullable=True),
        sa.Column("investment_horizon_years",sa.Integer(),   nullable=True),
        sa.Column("monthly_income_range",    sa.String(30),  nullable=True),
        sa.Column("country",                 sa.String(3),   nullable=True),
        sa.Column("first_seen_at",           sa.DateTime(),  nullable=False),
        sa.Column("last_active_at",          sa.DateTime(),  nullable=True),
        sa.Column("is_active",               sa.Boolean(),   nullable=True),
        sa.PrimaryKeyConstraint("wallet_address", name=op.f("pk_users")),
    )

    # ── personal_funds ────────────────────────────────────────────────────────
    op.create_table(
        "personal_funds",
        sa.Column("contract_address",               sa.String(42),                     nullable=False),
        sa.Column("owner_wallet",                   sa.String(42),                     nullable=False),
        sa.Column("principal",                      sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("monthly_deposit",                sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("current_age",                    sa.Integer(),                      nullable=False),
        sa.Column("retirement_age",                 sa.Integer(),                      nullable=False),
        sa.Column("desired_monthly",                sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("years_payments",                 sa.Integer(),                      nullable=False),
        sa.Column("interest_rate",                  sa.Integer(),                      nullable=False),
        sa.Column("timelock_years",                 sa.Integer(),                      nullable=False),
        sa.Column("timelock_end",                   sa.DateTime(),                     nullable=False),
        sa.Column("selected_protocol",              sa.String(42),                     nullable=True),
        sa.Column("total_gross_deposited",          sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("total_fees_paid",                sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("total_net_to_fund",              sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("total_balance",                  sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("available_balance",              sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("total_invested",                 sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("total_withdrawn",                sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("monthly_deposit_count",          sa.Integer(),                      nullable=True),
        sa.Column("extra_deposit_count",            sa.Integer(),                      nullable=True),
        sa.Column("withdrawal_count",               sa.Integer(),                      nullable=True),
        sa.Column("is_active",                      sa.Boolean(),                      nullable=True),
        sa.Column("retirement_started",             sa.Boolean(),                      nullable=True),
        sa.Column("retirement_started_at",          sa.DateTime(),                     nullable=True),
        sa.Column("early_retirement_approved",      sa.Boolean(),                      nullable=True),
        sa.Column("auto_withdrawal_enabled",        sa.Boolean(),                      nullable=True),
        sa.Column("auto_withdrawal_amount",         sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("auto_withdrawal_interval_seconds", sa.Integer(),                    nullable=True),
        sa.Column("next_auto_withdrawal_at",        sa.DateTime(),                     nullable=True),
        sa.Column("auto_withdrawal_execution_count", sa.Integer(),                     nullable=True),
        sa.Column("created_at",                     sa.DateTime(),                     nullable=False),
        sa.Column("last_synced_at",                 sa.DateTime(),                     nullable=True),
        sa.Column("created_block",                  sa.BigInteger(),                   nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_wallet"], ["users.wallet_address"],
            name=op.f("fk_personal_funds_owner_wallet_users"),
        ),
        sa.ForeignKeyConstraint(
            ["selected_protocol"], ["defi_protocols.protocol_address"],
            name=op.f("fk_personal_funds_selected_protocol_defi_protocols"),
        ),
        sa.PrimaryKeyConstraint("contract_address", name=op.f("pk_personal_funds")),
    )
    op.create_index(op.f("ix_personal_funds_owner_wallet"), "personal_funds", ["owner_wallet"], unique=False)

    # ── early_retirement_requests ─────────────────────────────────────────────
    op.create_table(
        "early_retirement_requests",
        sa.Column("id",               sa.String(66), nullable=False),
        sa.Column("fund_address",     sa.String(42), nullable=False),
        sa.Column("requester_wallet", sa.String(42), nullable=False),
        sa.Column("reason",           sa.Text(),     nullable=False),
        sa.Column("status",           sa.String(20), nullable=True),
        sa.Column("processed",        sa.Boolean(),  nullable=True),
        sa.Column("approved",         sa.Boolean(),  nullable=True),
        sa.Column("rejected",         sa.Boolean(),  nullable=True),
        sa.Column("processed_at",     sa.DateTime(), nullable=True),
        sa.Column("processed_by",     sa.String(42), nullable=True),
        sa.Column("admin_notes",      sa.Text(),     nullable=True),
        sa.Column("requested_at",     sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["fund_address"], ["personal_funds.contract_address"],
            name=op.f("fk_early_retirement_requests_fund_address_personal_funds"),
        ),
        sa.ForeignKeyConstraint(
            ["requester_wallet"], ["users.wallet_address"],
            name=op.f("fk_early_retirement_requests_requester_wallet_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_early_retirement_requests")),
    )
    op.create_index(op.f("ix_early_retirement_requests_fund_address"),     "early_retirement_requests", ["fund_address"],     unique=False)
    op.create_index(op.f("ix_early_retirement_requests_requester_wallet"), "early_retirement_requests", ["requester_wallet"], unique=False)

    # ── fee_records ───────────────────────────────────────────────────────────
    op.create_table(
        "fee_records",
        sa.Column("fund_address",    sa.String(42),                     nullable=False),
        sa.Column("total_fees_paid", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("fee_count",       sa.Integer(),                      nullable=True),
        sa.Column("last_fee_at",     sa.DateTime(),                     nullable=True),
        sa.Column("updated_at",      sa.DateTime(),                     nullable=True),
        sa.ForeignKeyConstraint(
            ["fund_address"], ["personal_funds.contract_address"],
            name=op.f("fk_fee_records_fund_address_personal_funds"),
        ),
        sa.PrimaryKeyConstraint("fund_address", name=op.f("pk_fee_records")),
    )

    # ── transactions ──────────────────────────────────────────────────────────
    op.create_table(
        "transactions",
        sa.Column("id",                sa.String(66),                     nullable=False),
        sa.Column("fund_address",      sa.String(42),                     nullable=True),
        sa.Column("wallet_address",    sa.String(42),                     nullable=False),
        sa.Column("event_type",        sa.String(50),                     nullable=False),
        sa.Column("gross_amount",      sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("fee_amount",        sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("net_amount",        sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("protocol_address",  sa.String(42),                     nullable=True),
        sa.Column("resulting_balance", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("block_number",      sa.BigInteger(),                   nullable=False),
        sa.Column("block_timestamp",   sa.DateTime(),                     nullable=False),
        sa.Column("log_index",         sa.BigInteger(),                   nullable=True),
        sa.Column("extra_data",        postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("indexed_at",        sa.DateTime(),                     nullable=True),
        sa.ForeignKeyConstraint(
            ["fund_address"],   ["personal_funds.contract_address"],
            name=op.f("fk_transactions_fund_address_personal_funds"),
        ),
        sa.ForeignKeyConstraint(
            ["wallet_address"], ["users.wallet_address"],
            name=op.f("fk_transactions_wallet_address_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index("ix_transactions_block",          "transactions", ["block_number"],                      unique=False)
    op.create_index(op.f("ix_transactions_block_number"),  "transactions", ["block_number"],                 unique=False)
    op.create_index(op.f("ix_transactions_event_type"),    "transactions", ["event_type"],                   unique=False)
    op.create_index(op.f("ix_transactions_fund_address"),  "transactions", ["fund_address"],                 unique=False)
    op.create_index(op.f("ix_transactions_wallet_address"),"transactions", ["wallet_address"],               unique=False)
    op.create_index("ix_transactions_wallet_event",        "transactions", ["wallet_address", "event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transactions_wallet_event",                    table_name="transactions")
    op.drop_index(op.f("ix_transactions_wallet_address"),            table_name="transactions")
    op.drop_index(op.f("ix_transactions_fund_address"),              table_name="transactions")
    op.drop_index(op.f("ix_transactions_event_type"),                table_name="transactions")
    op.drop_index(op.f("ix_transactions_block_number"),              table_name="transactions")
    op.drop_index("ix_transactions_block",                           table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("fee_records")
    op.drop_index(op.f("ix_early_retirement_requests_requester_wallet"), table_name="early_retirement_requests")
    op.drop_index(op.f("ix_early_retirement_requests_fund_address"),     table_name="early_retirement_requests")
    op.drop_table("early_retirement_requests")
    op.drop_index(op.f("ix_personal_funds_owner_wallet"), table_name="personal_funds")
    op.drop_table("personal_funds")
    op.drop_table("users")
    op.drop_table("survey_followups")
    op.drop_table("defi_protocols")
    op.drop_index(op.f("ix_contact_messages_status"), table_name="contact_messages")
    op.drop_index(op.f("ix_contact_messages_email"),  table_name="contact_messages")
    op.drop_table("contact_messages")
    op.drop_table("anonymous_surveys")