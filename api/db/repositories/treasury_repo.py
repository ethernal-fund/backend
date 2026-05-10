from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime, timezone
from decimal import Decimal

from api.db.models.treasury import FeeRecord, EarlyRetirementRequest

def _now() -> datetime:
    return datetime.now(timezone.utc)

class TreasuryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_fee_record(self, fund_address: str) -> Optional[FeeRecord]:
        result = await self.db.execute(
            select(FeeRecord).where(FeeRecord.fund_address == fund_address.lower())
        )
        return result.scalar_one_or_none()

    async def upsert_fee_record(self, fund_address: str, amount: Decimal) -> FeeRecord:
        record = await self.get_fee_record(fund_address)
        if record:
            record.total_fees_paid += amount
            record.fee_count       += 1
            record.last_fee_at      = _now()  
            record.updated_at       = _now()  
        else:
            record = FeeRecord(
                fund_address    = fund_address.lower(),
                total_fees_paid = amount,
                fee_count       = 1,
                last_fee_at     = _now(),     
            )
            self.db.add(record)
        await self.db.flush()
        return record

    async def get_total_fees_collected(self) -> Decimal:
        result = await self.db.execute(select(func.sum(FeeRecord.total_fees_paid)))
        return result.scalar() or Decimal(0)

    async def get_request(self, fund_address: str) -> Optional[EarlyRetirementRequest]:
        """Última request de un fondo (cualquier estado)."""
        result = await self.db.execute(
            select(EarlyRetirementRequest)
            .where(EarlyRetirementRequest.fund_address == fund_address.lower())
            .order_by(EarlyRetirementRequest.requested_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_pending_request(self, fund_address: str) -> Optional[EarlyRetirementRequest]:
        result = await self.db.execute(
            select(EarlyRetirementRequest)
            .where(
                EarlyRetirementRequest.fund_address == fund_address.lower(),
                EarlyRetirementRequest.status       == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def get_request_by_id(self, request_id: str) -> Optional[EarlyRetirementRequest]:
        result = await self.db.execute(
            select(EarlyRetirementRequest)
            .where(EarlyRetirementRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def get_by_wallet(self, wallet_address: str) -> list[EarlyRetirementRequest]:
        result = await self.db.execute(
            select(EarlyRetirementRequest)
            .where(EarlyRetirementRequest.requester_wallet == wallet_address.lower())
            .order_by(EarlyRetirementRequest.requested_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending(self) -> list[EarlyRetirementRequest]:
        result = await self.db.execute(
            select(EarlyRetirementRequest)
            .where(EarlyRetirementRequest.status == "pending")
            .order_by(EarlyRetirementRequest.requested_at.asc())
        )
        return list(result.scalars().all())

    async def count_by_status(self) -> dict:
        result = await self.db.execute(
            select(EarlyRetirementRequest.status, func.count(EarlyRetirementRequest.id))
            .group_by(EarlyRetirementRequest.status)
        )
        return {row[0]: row[1] for row in result.all()}

    async def create_request(self, data: dict) -> EarlyRetirementRequest:
        req = EarlyRetirementRequest(**data)
        self.db.add(req)
        await self.db.flush()
        return req

    async def process_request(
        self,
        fund_address: str,       
        approved: bool,              # por fund_address, no por tx hash 
        processed_by: Optional[str] = None,
        admin_notes:  Optional[str] = None,
    ) -> Optional[EarlyRetirementRequest]:
        req = await self.get_pending_request(fund_address)
        if not req:
            return None

        req.processed    = True
        req.approved     = approved
        req.rejected     = not approved
        req.status       = "approved" if approved else "rejected"
        req.processed_at = _now()   
        req.processed_by = processed_by
        req.admin_notes  = admin_notes
        await self.db.flush()
        return req