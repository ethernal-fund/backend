from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional
from api.db.models.contact import ContactMessage

class ContactRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, data: dict) -> ContactMessage:
        msg = ContactMessage(**data)
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> list[ContactMessage]:
        query = select(ContactMessage).order_by(desc(ContactMessage.created_at))
        if status:
            query = query.where(ContactMessage.status == status)
        query = query.offset(skip).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count(self, status: Optional[str] = None) -> int:
        query = select(func.count(ContactMessage.id))
        if status:
            query = query.where(ContactMessage.status == status)
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def mark_read(self, msg_id: int) -> Optional[ContactMessage]:
        result = await self.db.execute(
            select(ContactMessage).where(ContactMessage.id == msg_id)
        )
        msg = result.scalar_one_or_none()
        if msg:
            msg.status = "read"
            await self.db.flush()
        return msg