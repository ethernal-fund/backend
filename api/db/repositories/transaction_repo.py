from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional
from decimal import Decimal
from datetime import datetime

from api.db.models.transaction import Transaction

class TransactionRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_hash(self, tx_hash: str) -> Optional[Transaction]:
        result = await self.db.execute(
            select(Transaction).where(Transaction.id == tx_hash.lower())
        )
        return result.scalar_one_or_none()

    async def create(self, data: dict) -> Transaction:
        existing = await self.get_by_hash(data["id"])
        if existing:
            return existing
        tx = Transaction(**data)
        self.db.add(tx)
        await self.db.flush()
        return tx

    async def get_by_wallet(
        self,
        wallet_address: str,
        event_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Transaction]:
        query = select(Transaction).where(
            Transaction.wallet_address == wallet_address.lower()
        )
        if event_type:
            query = query.where(Transaction.event_type == event_type)
        query = query.order_by(desc(Transaction.block_timestamp)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_fund(
        self,
        fund_address: str,
        event_type: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Transaction]:
        query = select(Transaction).where(
            Transaction.fund_address == fund_address.lower()
        )
        if event_type:
            query = query.where(Transaction.event_type == event_type)
        query = query.order_by(desc(Transaction.block_timestamp)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_last_indexed_block(self) -> int:
        result = await self.db.execute(
            select(Transaction.block_number)
            .order_by(desc(Transaction.block_number))
            .limit(1)
        )
        return result.scalar_one_or_none() or 0

    async def get_total_deposited(self) -> Decimal:
        """Suma de net_amount para eventos de depósito."""
        result = await self.db.execute(
            select(func.sum(Transaction.net_amount)).where(
                Transaction.event_type.in_(["monthly_deposited", "extra_deposited", "initialized"])
            )
        )
        return result.scalar() or Decimal(0)

    async def get_total_withdrawn(self) -> Decimal:
        result = await self.db.execute(
            select(func.sum(Transaction.gross_amount)).where(
                Transaction.event_type.in_(["withdrawn", "auto_withdrawal_executed"])
            )
        )
        return result.scalar() or Decimal(0)

    async def count_by_event_type(self) -> dict:
        result = await self.db.execute(
            select(Transaction.event_type, func.count(Transaction.id))
            .group_by(Transaction.event_type)
        )
        return {row[0]: row[1] for row in result.all()}