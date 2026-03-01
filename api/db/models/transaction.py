from sqlalchemy import Column, String, Boolean, DateTime, BigInteger, Numeric, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

from api.db.base import Base

class Transaction(Base):
    """
    Índice off-chain de todos los eventos on-chain relevantes.
    Tipos de evento (del contrato PersonalFund):
    - initialized
    - deposited
    - monthly_deposited
    - extra_deposited
    - invested_in_protocol
    - withdrawn_from_protocol
    - retirement_started
    - auto_withdrawal_executed
    - withdrawn
    - emergency_withdrawal
    - investment_method_updated
    - early_retirement_approved
    - fee_received (Treasury)
    """
    __tablename__ = "transactions"

    id = Column(String(66), primary_key=True)          # tx_hash
    fund_address = Column(String(42), ForeignKey("personal_funds.contract_address"), nullable=True, index=True)
    wallet_address = Column(String(42), ForeignKey("users.wallet_address"), nullable=False, index=True)

    event_type = Column(String(50), nullable=False, index=True)

    gross_amount = Column(Numeric(20, 6), nullable=True)
    fee_amount = Column(Numeric(20, 6), nullable=True)
    net_amount = Column(Numeric(20, 6), nullable=True)

    protocol_address = Column(String(42), nullable=True)

    resulting_balance = Column(Numeric(20, 6), nullable=True)

    block_number = Column(BigInteger, nullable=False, index=True)
    block_timestamp = Column(DateTime, nullable=False)
    log_index = Column(BigInteger, nullable=True)

    extra_data = Column(JSONB, nullable=True)
    indexed_at = Column(DateTime, default=datetime.utcnow)

    fund = relationship("PersonalFund", back_populates="transactions")
    user = relationship("User", back_populates="transactions")

    __table_args__ = (
        Index("ix_transactions_wallet_event", "wallet_address", "event_type"),
        Index("ix_transactions_block", "block_number"),
    )

    def __repr__(self):
        return f"<Transaction {self.event_type} tx={self.id[:10]}...>"