from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timezone

from api.db.models.protocol import DeFiProtocol

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class ProtocolRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_address(self, protocol_address: str) -> Optional[DeFiProtocol]:
        result = await self.db.execute(
            select(DeFiProtocol).where(
                DeFiProtocol.protocol_address == protocol_address.lower()
            )
        )
        return result.scalar_one_or_none()

    async def get_all_active(self, risk_level: Optional[int] = None) -> list[DeFiProtocol]:
        query = select(DeFiProtocol).where(DeFiProtocol.is_active == True)
        if risk_level:
            query = query.where(DeFiProtocol.risk_level == risk_level)
        query = query.order_by(DeFiProtocol.apy.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_all(self) -> list[DeFiProtocol]:
        result = await self.db.execute(
            select(DeFiProtocol).order_by(DeFiProtocol.apy.desc())
        )
        return list(result.scalars().all())

    async def upsert_from_chain(self, data: dict) -> DeFiProtocol:
        protocol = await self.get_by_address(data["protocol_address"])
        if protocol:
            for key, value in data.items():
                if hasattr(protocol, key):
                    setattr(protocol, key, value)
            protocol.synced_at = _utcnow()
        else:
            protocol = DeFiProtocol(**data)
            self.db.add(protocol)
        await self.db.flush()
        return protocol

    async def update_apy(self, protocol_address: str, apy: float) -> Optional[DeFiProtocol]:
        protocol = await self.get_by_address(protocol_address)
        if not protocol:
            return None
        now = _utcnow()
        protocol.apy            = apy
        protocol.last_updated_at = now
        protocol.synced_at       = now
        await self.db.flush()
        return protocol

    async def toggle_active(self, protocol_address: str, is_active: bool) -> Optional[DeFiProtocol]:
        protocol = await self.get_by_address(protocol_address)
        if not protocol:
            return None
        protocol.is_active       = is_active
        protocol.last_updated_at = _utcnow()
        await self.db.flush()
        return protocol

    async def mark_verified(self, protocol_address: str) -> Optional[DeFiProtocol]:
        protocol = await self.get_by_address(protocol_address)
        if not protocol:
            return None
        protocol.is_verified     = True
        protocol.last_updated_at = _utcnow()
        await self.db.flush()
        return protocol

    async def get_total_value_locked(self) -> float:
        result = await self.db.execute(
            select(func.sum(DeFiProtocol.total_deposited))
            .where(DeFiProtocol.is_active == True)
        )
        return float(result.scalar() or 0)