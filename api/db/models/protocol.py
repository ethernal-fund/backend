from sqlalchemy import Column, String, Boolean, DateTime, Integer, Numeric, SmallInteger
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from api.db.base import Base

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class DeFiProtocol(Base):
    __tablename__ = "defi_protocols"

    protocol_address = Column(String(42), primary_key=True)
    name             = Column(String(100), nullable=False)
    apy              = Column(Numeric(10, 4), default=0)
    risk_level       = Column(Integer, nullable=False)          # 1=LOW 2=MEDIUM 3=HIGH

    category         = Column(SmallInteger, nullable=True)      # 1=DEFI 2=RWA 3=EQUITY 4=COMMODITY 5=BOND
    is_active        = Column(Boolean, default=True)
    is_verified      = Column(Boolean, default=False)
    total_deposited  = Column(Numeric(20, 6), default=0)
    added_at         = Column(DateTime(timezone=True), default=_utcnow)
    last_updated_at  = Column(DateTime(timezone=True), nullable=True)
    synced_at        = Column(DateTime(timezone=True), nullable=True)

    funds = relationship("PersonalFund", back_populates="protocol")

    def __repr__(self):
        return f"<DeFiProtocol {self.name} addr={self.protocol_address} category={self.category}>"