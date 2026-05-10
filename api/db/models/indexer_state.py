from sqlalchemy import Column, String, BigInteger, DateTime
from datetime import datetime, timezone
from api.db.base import Base

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class IndexerState(Base):
    __tablename__ = "indexer_state"

    source      = Column(String(50), primary_key=True)
    last_block  = Column(BigInteger, nullable=False, default=0)
    updated_at  = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<IndexerState source={self.source} last_block={self.last_block}>"