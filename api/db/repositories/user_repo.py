from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models.user import User
import logging

logger = logging.getLogger(__name__)

class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_wallet(self, wallet_address: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where(User.wallet_address == wallet_address.lower())
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, wallet_address: str) -> Tuple[User, bool]:
        wallet = wallet_address.lower()
        now    = datetime.now(timezone.utc)
        stmt = (
            pg_insert(User)
            .values(
                wallet_address = wallet,
                first_seen_at  = now,
                last_active_at = now,
                is_active      = True,
            )
            .on_conflict_do_update(
                index_elements=["wallet_address"],
                set_={"last_active_at": now},
            )
            .returning(User)
        )
        result  = await self.db.execute(stmt)
        await self.db.flush() 
        user    = result.scalar_one()
        created = abs((user.first_seen_at - user.last_active_at).total_seconds()) < 1
        return user, created

    async def touch(self, wallet_address: str) -> None:
        """FIX: removido commit() — el caller gestiona el ciclo de vida de la transacción."""
        await self.db.execute(
            update(User)
            .where(User.wallet_address == wallet_address.lower())
            .values(last_active_at=datetime.now(timezone.utc))
        )

    async def update_survey(self, wallet_address: str, data: dict) -> User:
        """FIX: removido commit() — reemplazado por flush() para mantener atomicidad."""
        now = datetime.now(timezone.utc)
        ALLOWED = {
            "age_range",
            "risk_tolerance",
            "crypto_experience",
            "retirement_goal",
            "investment_horizon_years",
            "monthly_income_range",
            "country",
            "survey_completed",
            "survey_completed_at",
        }
        safe_data = {k: v for k, v in data.items() if k in ALLOWED}
        safe_data.setdefault("survey_completed",    True)
        safe_data.setdefault("survey_completed_at", now)
        safe_data["last_active_at"] = now
        await self.db.execute(
            update(User)
            .where(User.wallet_address == wallet_address.lower())
            .values(**safe_data)
        )
        await self.db.flush()  

        user = await self.get_by_wallet(wallet_address)
        if user is None:
            raise ValueError(f"User not found after survey update: {wallet_address}")
        return user

    async def count_total(self) -> int:
        result = await self.db.execute(select(func.count()).select_from(User))
        return result.scalar_one()

    async def count_survey_completed(self) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(User).where(User.survey_completed.is_(True))
        )
        return result.scalar_one()

    async def count_by_risk_tolerance(self) -> dict:
        rows = await self.db.execute(
            select(User.risk_tolerance, func.count().label("n"))
            .where(User.risk_tolerance.isnot(None))
            .group_by(User.risk_tolerance)
            .order_by(User.risk_tolerance)
        )
        return {str(row.risk_tolerance): row.n for row in rows}

    async def count_by_country(self, top_n: int = 10) -> list[dict]:
        rows = await self.db.execute(
            select(User.country, func.count().label("n"))
            .where(User.country.isnot(None))
            .group_by(User.country)
            .order_by(func.count().desc())
            .limit(top_n)
        )
        return [{"country": row.country, "count": row.n} for row in rows]