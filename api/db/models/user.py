from sqlalchemy import Column, String, Boolean, DateTime, Integer, SmallInteger
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from api.db.base import Base

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    wallet_address           = Column(String(42), primary_key=True)
    survey_completed         = Column(Boolean, default=False, nullable=False)
    survey_completed_at      = Column(DateTime(timezone=True), nullable=True)
    age_range                = Column(String(20), nullable=True)
    risk_tolerance           = Column(SmallInteger, nullable=True)
    crypto_experience        = Column(String(20), nullable=True)
    retirement_goal          = Column(String(30), nullable=True)
    investment_horizon_years = Column(Integer, nullable=True)
    monthly_income_range     = Column(String(30), nullable=True)
    country                  = Column(String(3), nullable=True)   # ISO 3166-1 alpha-2

    first_seen_at  = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_active_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    is_active      = Column(Boolean, default=True)

    fund                      = relationship("PersonalFund", back_populates="user", uselist=False)
    transactions              = relationship("Transaction", back_populates="user")
    early_retirement_requests = relationship("EarlyRetirementRequest", back_populates="user")

    def __repr__(self) -> str:
        return f"<User wallet={self.wallet_address}>"