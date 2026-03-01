from sqlalchemy.ext.asyncio import AsyncSession

from api.db.repositories.user_repo import UserRepository
from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.faucet_repo import FaucetRepository
from api.db.repositories.transaction_repo import TransactionRepository

class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.users  = UserRepository(db)
        self.funds  = FundRepository(db)
        self.faucet = FaucetRepository(db)
        self.txs    = TransactionRepository(db)

    async def get_full_profile(self, wallet_address: str) -> dict:
        user = await self.users.get_by_wallet(wallet_address)
        if not user:
            return None

        fund  = await self.funds.get_by_owner(wallet_address)
        faucet_requests = await self.faucet.get_by_wallet(wallet_address, limit=5)
        recent_txs = await self.txs.get_by_wallet(wallet_address, limit=5)

        return {
            "wallet_address": user.wallet_address,
            "survey_completed": user.survey_completed,
            "survey_completed_at": user.survey_completed_at,
            "profile": {
                "age_range":               user.age_range,
                "risk_tolerance":          user.risk_tolerance,
                "crypto_experience":       user.crypto_experience,
                "retirement_goal":         user.retirement_goal,
                "investment_horizon_years": user.investment_horizon_years,
                "monthly_income_range":    user.monthly_income_range,
                "country":                 user.country,
            },
            "has_fund": fund is not None,
            "fund": {
                "contract_address":  fund.contract_address,
                "total_balance":     float(fund.total_balance),
                "total_invested":    float(fund.total_invested),
                "retirement_started": fund.retirement_started,
                "timelock_end":      fund.timelock_end.isoformat() if fund.timelock_end else None,
            } if fund else None,
            "first_seen_at":   user.first_seen_at,
            "last_active_at":  user.last_active_at,
            "recent_transactions": [
                {
                    "event_type":  tx.event_type,
                    "net_amount":  float(tx.net_amount) if tx.net_amount else None,
                    "block_timestamp": tx.block_timestamp,
                }
                for tx in recent_txs
            ],
            "recent_faucet_requests": [
                {
                    "amount":       float(fr.amount),
                    "status":       fr.status,
                    "requested_at": fr.requested_at,
                }
                for fr in faucet_requests
            ],
        }

    async def submit_survey(self, wallet_address: str, survey_data: dict) -> dict:
        user = await self.users.update_survey(wallet_address, survey_data)

        return {
            "success": True,
            "wallet_address": user.wallet_address,
            "survey_completed_at": user.survey_completed_at,
            "risk_tolerance": user.risk_tolerance,
            "message": "Survey completed. Your profile has been created.",
        }

    async def get_admin_user_stats(self) -> dict:
        total         = await self.users.count_total()
        completed     = await self.users.count_survey_completed()
        by_risk       = await self.users.count_by_risk_tolerance()
        by_country    = await self.users.count_by_country()

        return {
            "total_users":          total,
            "survey_completed":     completed,
            "survey_pending":       total - completed,
            "completion_rate":      round(completed / total * 100, 1) if total else 0,
            "by_risk_tolerance":    by_risk,
            "top_countries":        by_country,
        }