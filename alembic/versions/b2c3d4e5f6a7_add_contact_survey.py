"""add contact and survey tables

Revision ID: b2c3d4e5f6a7
Revises: afbb68d171ca
Create Date: 2026-03-01 20:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'afbb68d171ca'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'contact_messages',
        sa.Column('id',             sa.Integer(),    nullable=False, autoincrement=True),
        sa.Column('name',           sa.String(100),    nullable=False),
        sa.Column('email',          sa.String(255),    nullable=False),
        sa.Column('subject',        sa.String(200),    nullable=True),
        sa.Column('message',        sa.Text(),         nullable=False),
        sa.Column('wallet_address', sa.String(42),     nullable=True),
        sa.Column('status',         sa.String(20),     nullable=True, server_default='new'),
        sa.Column('created_at',     sa.DateTime(),     nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_contact_messages')),
    )
    op.create_index(op.f('ix_contact_messages_email'), 'contact_messages', ['email'], unique=False)
    op.create_index(op.f('ix_contact_messages_status'), 'contact_messages', ['status'], unique=False)

    op.create_table(
        'anonymous_surveys',
        sa.Column('id',                       sa.Integer(),      primary_key=True, autoincrement=True),
        sa.Column('age',                      sa.String(10),     nullable=False),
        sa.Column('trust_traditional',        sa.SmallInteger(), nullable=False),
        sa.Column('blockchain_familiarity',   sa.SmallInteger(), nullable=False),
        sa.Column('retirement_concern',       sa.SmallInteger(), nullable=False),
        sa.Column('has_retirement_plan',      sa.SmallInteger(), nullable=False),
        sa.Column('values_in_retirement',     sa.SmallInteger(), nullable=False),
        sa.Column('interested_in_blockchain', sa.SmallInteger(), nullable=False),
        sa.Column('created_at',               sa.DateTime(),     nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_anonymous_surveys')),
    )

    op.create_table(
        'survey_followups',
        sa.Column('id',              sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column('survey_id',       sa.Integer(),     nullable=True),
        sa.Column('wants_more_info', sa.Boolean(),     nullable=False),
        sa.Column('email',           sa.String(255),   nullable=True),
        sa.Column('created_at',      sa.DateTime(),    nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_survey_followups')),
    )

def downgrade() -> None:
    op.drop_table('survey_followups')
    op.drop_table('anonymous_surveys')
    op.drop_index(op.f('ix_contact_messages_status'), table_name='contact_messages')
    op.drop_index(op.f('ix_contact_messages_email'), table_name='contact_messages')
    op.drop_table('contact_messages')