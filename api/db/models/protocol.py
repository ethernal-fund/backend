from sqlalchemy import Column, String, Boolean, DateTime, Integer, Numeric
from sqlalchemy.orm import relationship
from datetime import datetime

from api.db.base import Base


class DeFiProtocol(Base):
    __tablename__ = "defi_protocols"

    protocol_address = Column(String(42), primary_key=True)
    name             = Column(String(100), nullable=False)
    apy              = Column(Numeric(10, 4), default=0)       
    risk_level       = Column(Integer, nullable=False)          # 1=LOW 2=MEDIUM 3=HIGH
    is_active        = Column(Boolean, default=True)
    is_verified      = Column(Boolean, default=False)
    total_deposited  = Column(Numeric(20, 6), default=0)

    added_at         = Column(DateTime, default=datetime.utcnow)
    last_updated_at  = Column(DateTime, nullable=True)
    synced_at        = Column(DateTime, nullable=True)

    funds = relationship("PersonalFund", back_populates="protocol")

    def __repr__(self):
        return f"<DeFiProtocol {self.name} addr={self.protocol_address}>"