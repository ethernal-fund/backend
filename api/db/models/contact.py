from sqlalchemy import Column, String, Text, DateTime, Integer
from datetime import datetime, timezone
from api.db.base import Base

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    name           = Column(String(100), nullable=False)
    email          = Column(String(255), nullable=False, index=True)
    subject        = Column(String(200), nullable=True)
    message        = Column(Text, nullable=False)
    wallet_address = Column(String(42), nullable=True)
    status         = Column(String(20), default="new", index=True)
    created_at     = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def __repr__(self):
        return f"<ContactMessage from={self.email} status={self.status}>"