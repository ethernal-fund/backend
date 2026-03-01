from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional
from datetime import datetime

from api.db.models.user import User
from api.core.exceptions import WalletNotFound

class UserRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_wallet(self, wallet_address: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.wallet_address == wallet_address.lower())
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, wallet_address: str) -> tuple[User, bool]:
        wallet = wallet_address.lower()
        user = await self.get_by_wallet(wallet)
        if user:
            user.last_active_at = datetime.utcnow()
            return user, False
        user = User(wallet_address=wallet)
        self.db.add(user)
        await self.db.flush()
        return user, True

    async def update_survey(self, wallet_address: str, survey_data: dict) -> User:
        user = await self.get_by_wallet(wallet_address)
        if not user:
            raise WalletNotFound(wallet_address)
        for key, value in survey_data.items():
            if hasattr(user, key):
                setattr(user, key, value)
        user.survey_completed    = True
        user.survey_completed_at = datetime.utcnow()
        await self.db.flush()
        return user

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[User]:
        result = await self.db.execute(
            select(User).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def count_total(self) -> int:
        result = await self.db.execute(select(func.count(User.wallet_address)))
        return result.scalar() or 0

    async def count_survey_completed(self) -> int:
        result = await self.db.execute(
            select(func.count(User.wallet_address))
            .where(User.survey_completed == True)
        )
        return result.scalar() or 0

    async def count_by_risk_tolerance(self) -> dict:
        result = await self.db.execute(
            select(User.risk_tolerance, func.count(User.wallet_address))
            .where(User.risk_tolerance.isnot(None))
            .group_by(User.risk_tolerance)
        )
        labels = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}
        return {labels.get(row[0], str(row[0])): row[1] for row in result.all()}

    async def count_by_country(self, limit: int = 10) -> list[dict]:
        result = await self.db.execute(
            select(User.country, func.count(User.wallet_address).label("count"))
            .where(User.country.isnot(None))
            .group_by(User.country)
            .order_by(func.count(User.wallet_address).desc())
            .limit(limit)
        )
        return [{"country": row[0], "count": row[1]} for row in result.all()]