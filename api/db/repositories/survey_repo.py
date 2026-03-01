from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Optional
from api.db.models.survey import AnonymousSurvey, SurveyFollowUp

class SurveyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_survey(self, data: dict) -> AnonymousSurvey:
        survey = AnonymousSurvey(**data)
        self.db.add(survey)
        await self.db.flush()
        return survey

    async def create_followup(self, data: dict) -> SurveyFollowUp:
        followup = SurveyFollowUp(**data)
        self.db.add(followup)
        await self.db.flush()
        return followup

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[AnonymousSurvey]:
        result = await self.db.execute(
            select(AnonymousSurvey)
            .order_by(desc(AnonymousSurvey.created_at))
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_total(self) -> int:
        result = await self.db.execute(select(func.count(AnonymousSurvey.id)))
        return result.scalar() or 0

    async def count_by_age(self) -> list[dict]:
        result = await self.db.execute(
            select(AnonymousSurvey.age, func.count(AnonymousSurvey.id).label("count"))
            .group_by(AnonymousSurvey.age)
            .order_by(desc(func.count(AnonymousSurvey.id)))
        )
        return [{"age": row[0], "count": row[1]} for row in result.all()]

    async def get_averages(self) -> dict:
        result = await self.db.execute(
            select(
                func.avg(AnonymousSurvey.trust_traditional).label("trust_traditional"),
                func.avg(AnonymousSurvey.blockchain_familiarity).label("blockchain_familiarity"),
                func.avg(AnonymousSurvey.retirement_concern).label("retirement_concern"),
                func.avg(AnonymousSurvey.has_retirement_plan).label("has_retirement_plan"),
                func.avg(AnonymousSurvey.values_in_retirement).label("values_in_retirement"),
                func.avg(AnonymousSurvey.interested_in_blockchain).label("interested_in_blockchain"),
            )
        )
        row = result.one()
        return {
            "trust_traditional":        round(float(row[0] or 0), 2),
            "blockchain_familiarity":   round(float(row[1] or 0), 2),
            "retirement_concern":       round(float(row[2] or 0), 2),
            "has_retirement_plan":      round(float(row[3] or 0), 2),
            "values_in_retirement":     round(float(row[4] or 0), 2),
            "interested_in_blockchain": round(float(row[5] or 0), 2),
        }

    async def count_followups_wanting_info(self) -> int:
        result = await self.db.execute(
            select(func.count(SurveyFollowUp.id))
            .where(SurveyFollowUp.wants_more_info == True)
        )
        return result.scalar() or 0