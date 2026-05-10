from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models.fund import PersonalFund
from api.core.exceptions import FundAlreadyExists, FundNotFound

class FundRepository:

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_contract(self, contract_address: str) -> Optional[PersonalFund]:
        result = await self.db.execute(
            select(PersonalFund).where(
                PersonalFund.contract_address == contract_address.lower()
            )
        )
        return result.scalar_one_or_none()

    async def get_by_owner(self, wallet_address: str) -> Optional[PersonalFund]:
        result = await self.db.execute(
            select(PersonalFund).where(
                PersonalFund.owner_wallet == wallet_address.lower()
            )
        )
        return result.scalar_one_or_none()

    async def get_all_active(self, skip: int = 0, limit: int = 1000) -> list[PersonalFund]:
        result = await self.db.execute(
            select(PersonalFund)
            .where(PersonalFund.is_active == True)
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, data: dict) -> PersonalFund:
        """Create a fund; raises FundAlreadyExists if the owner already has one."""
        existing = await self.get_by_owner(data["owner_wallet"])
        if existing:
            raise FundAlreadyExists(data["owner_wallet"])
        fund = PersonalFund(**data)
        self.db.add(fund)
        await self.db.flush()
        return fund

    async def create_from_event(self, data: dict) -> PersonalFund:
        existing = await self.get_by_contract(data["contract_address"])
        if existing:
            return existing
        fund = PersonalFund(**data)
        self.db.add(fund)
        await self.db.flush()
        return fund

    async def update_balances(
        self, contract_address: str, balances: dict
    ) -> PersonalFund:
        fund = await self.get_by_contract(contract_address)
        if not fund:
            raise FundNotFound(contract_address)
        for key, value in balances.items():
            if hasattr(fund, key):
                setattr(fund, key, value)
        fund.last_synced_at = datetime.utcnow()
        await self.db.flush()
        return fund

    async def mark_retirement_started(
        self, contract_address: str
    ) -> Optional[PersonalFund]:
        fund = await self.get_by_contract(contract_address)
        if not fund:
            return None
        fund.retirement_started    = True
        fund.retirement_started_at = datetime.utcnow()
        await self.db.flush()
        return fund

    async def count_total(self) -> int:
        result = await self.db.execute(
            select(func.count(PersonalFund.contract_address))
        )
        return result.scalar() or 0

    async def count_active(self) -> int:
        result = await self.db.execute(
            select(func.count(PersonalFund.contract_address))
            .where(PersonalFund.is_active == True)
        )
        return result.scalar() or 0

    async def count_in_retirement(self) -> int:
        result = await self.db.execute(
            select(func.count(PersonalFund.contract_address))
            .where(PersonalFund.retirement_started == True)
        )
        return result.scalar() or 0

    async def get_total_value_locked(self) -> Decimal:
        result = await self.db.execute(
            select(func.sum(PersonalFund.total_balance))
            .where(PersonalFund.is_active == True)
        )
        return result.scalar() or Decimal(0)

    async def get_total_fees_paid(self) -> Decimal:
        result = await self.db.execute(
            select(func.sum(PersonalFund.total_fees_paid))
        )
        return result.scalar() or Decimal(0)