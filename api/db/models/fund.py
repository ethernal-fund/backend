from sqlalchemy import Column, String, Boolean, DateTime, Integer, BigInteger, Numeric, ForeignKey, SmallInteger
from sqlalchemy.orm import relationship
from datetime import datetime

from api.db.base import Base

class PersonalFund(Base):
    __tablename__ = "personal_funds"

    contract_address = Column(String(42), primary_key=True)
    owner_wallet = Column(String(42), ForeignKey("users.wallet_address"), nullable=False, index=True)

    principal = Column(Numeric(20, 6), nullable=False)          # USDC con 6 decimales
    monthly_deposit = Column(Numeric(20, 6), nullable=False)
    current_age = Column(Integer, nullable=False)
    retirement_age = Column(Integer, nullable=False)
    desired_monthly = Column(Numeric(20, 6), nullable=False)
    years_payments = Column(Integer, nullable=False)
    interest_rate = Column(Integer, nullable=False)             # basis points
    timelock_years = Column(Integer, nullable=False)
    timelock_end = Column(DateTime, nullable=False)

    selected_protocol = Column(String(42), ForeignKey("defi_protocols.protocol_address"), nullable=True)

    total_gross_deposited = Column(Numeric(20, 6), default=0)
    total_fees_paid = Column(Numeric(20, 6), default=0)
    total_net_to_fund = Column(Numeric(20, 6), default=0)
    total_balance = Column(Numeric(20, 6), default=0)
    available_balance = Column(Numeric(20, 6), default=0)
    total_invested = Column(Numeric(20, 6), default=0)
    total_withdrawn = Column(Numeric(20, 6), default=0)

    monthly_deposit_count = Column(Integer, default=1)
    extra_deposit_count = Column(Integer, default=0)
    withdrawal_count = Column(Integer, default=0)

    is_active = Column(Boolean, default=True)
    retirement_started = Column(Boolean, default=False)
    retirement_started_at = Column(DateTime, nullable=True)
    early_retirement_approved = Column(Boolean, default=False)

    auto_withdrawal_enabled = Column(Boolean, default=False)
    auto_withdrawal_amount = Column(Numeric(20, 6), nullable=True)
    auto_withdrawal_interval_seconds = Column(Integer, nullable=True)
    next_auto_withdrawal_at = Column(DateTime, nullable=True)
    auto_withdrawal_execution_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_synced_at = Column(DateTime, default=datetime.utcnow)
    created_block = Column(BigInteger, nullable=True)

    user = relationship("User", back_populates="fund")
    transactions = relationship("Transaction", back_populates="fund")
    protocol = relationship("DeFiProtocol", back_populates="funds")

    def __repr__(self):
        return f"<PersonalFund contract={self.contract_address} owner={self.owner_wallet}>"