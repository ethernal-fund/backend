from sqlalchemy import Column, String, Boolean, DateTime, Integer, Numeric, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from api.db.base import Base


class FeeRecord(Base):
    __tablename__ = "fee_records"

    fund_address    = Column(String(42), ForeignKey("personal_funds.contract_address"), primary_key=True)
    total_fees_paid = Column(Numeric(20, 6), default=0)
    fee_count       = Column(Integer, default=0)
    last_fee_at     = Column(DateTime, nullable=True)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<FeeRecord fund={self.fund_address} total={self.total_fees_paid}>"

class EarlyRetirementRequest(Base):
    __tablename__ = "early_retirement_requests"

    id               = Column(String(66), primary_key=True, default=lambda: str(uuid.uuid4()))
    fund_address     = Column(String(42), ForeignKey("personal_funds.contract_address"), nullable=False, index=True)
    requester_wallet = Column(String(42), ForeignKey("users.wallet_address"), nullable=False, index=True)
    reason           = Column(Text, nullable=False)
    status           = Column(String(20), default="pending")    # pending | approved | rejected

    processed        = Column(Boolean, default=False)
    approved         = Column(Boolean, nullable=True)
    rejected         = Column(Boolean, nullable=True)
    processed_at     = Column(DateTime, nullable=True)
    processed_by     = Column(String(42), nullable=True)        # admin wallet
    admin_notes      = Column(Text, nullable=True)

    requested_at     = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="early_retirement_requests")

    def __repr__(self):
        return f"<EarlyRetirementRequest fund={self.fund_address} status={self.status}>"