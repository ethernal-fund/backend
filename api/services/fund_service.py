from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.transaction_repo import TransactionRepository
from api.db.repositories.treasury_repo import TreasuryRepository
from api.db.repositories.protocol_repo import ProtocolRepository
from api.services.blockchain_service import BlockchainService
from api.core.exceptions import FundNotFound

class FundService:
    def __init__(self, db: AsyncSession):
        self.db          = db
        self.funds       = FundRepository(db)
        self.txs         = TransactionRepository(db)
        self.treasury    = TreasuryRepository(db)
        self.protocols   = ProtocolRepository(db)
        self.blockchain  = BlockchainService()

    async def get_fund_dashboard(self, wallet_address: str) -> dict:
        fund = await self.funds.get_by_owner(wallet_address)
        if not fund:
            raise FundNotFound(wallet_address)

        # Historial de transacciones
        deposits = await self.txs.get_by_fund(
            fund.contract_address,
            event_type=None,
            limit=20,
        )
        early_request = await self.treasury.get_request(fund.contract_address)
        protocol = None
        if fund.selected_protocol:
            protocol = await self.protocols.get_by_address(fund.selected_protocol)
        projection = self._calculate_projection(fund)

        return {
            "fund": {
                "contract_address":      fund.contract_address,
                "principal":             float(fund.principal),
                "monthly_deposit":       float(fund.monthly_deposit),
                "current_age":           fund.current_age,
                "retirement_age":        fund.retirement_age,
                "desired_monthly":       float(fund.desired_monthly),
                "years_payments":        fund.years_payments,
                "timelock_end":          fund.timelock_end.isoformat(),
                "selected_protocol":     fund.selected_protocol,
            },
            "balances": {
                "total_balance":         float(fund.total_balance),
                "available_balance":     float(fund.available_balance),
                "total_invested":        float(fund.total_invested),
                "total_gross_deposited": float(fund.total_gross_deposited),
                "total_fees_paid":       float(fund.total_fees_paid),
                "total_withdrawn":       float(fund.total_withdrawn),
            },
            "status": {
                "is_active":                  fund.is_active,
                "retirement_started":         fund.retirement_started,
                "retirement_started_at":      fund.retirement_started_at,
                "early_retirement_approved":  fund.early_retirement_approved,
                "auto_withdrawal_enabled":    fund.auto_withdrawal_enabled,
                "auto_withdrawal_amount":     float(fund.auto_withdrawal_amount) if fund.auto_withdrawal_amount else None,
                "next_auto_withdrawal_at":    fund.next_auto_withdrawal_at,
            },
            "counters": {
                "monthly_deposit_count": fund.monthly_deposit_count,
                "extra_deposit_count":   fund.extra_deposit_count,
                "withdrawal_count":      fund.withdrawal_count,
            },
            "projection": projection,
            "protocol": {
                "address":    protocol.protocol_address,
                "name":       protocol.name,
                "apy":        float(protocol.apy),
                "risk_level": protocol.risk_level,
            } if protocol else None,
            "early_retirement_request": {
                "status":       early_request.status,
                "reason":       early_request.reason,
                "requested_at": early_request.requested_at,
                "processed_at": early_request.processed_at,
            } if early_request else None,
            "recent_transactions": [
                {
                    "tx_hash":         tx.id,
                    "event_type":      tx.event_type,
                    "gross_amount":    float(tx.gross_amount) if tx.gross_amount else None,
                    "fee_amount":      float(tx.fee_amount)   if tx.fee_amount   else None,
                    "net_amount":      float(tx.net_amount)   if tx.net_amount   else None,
                    "block_timestamp": tx.block_timestamp,
                    "protocol":        tx.protocol_address,
                }
                for tx in deposits
            ],
            "last_synced_at": fund.last_synced_at,
        }

    async def sync_from_blockchain(self, contract_address: str) -> dict:
        fund = await self.funds.get_by_contract(contract_address)
        if not fund:
            raise FundNotFound(contract_address)
        on_chain = await self.blockchain.get_fund_info(contract_address)
        updated  = await self.funds.update_balances(contract_address, on_chain)

        return {
            "success":      True,
            "synced_at":    updated.last_synced_at,
            "total_balance": float(updated.total_balance),
        }

    def _calculate_projection(self, fund) -> dict:
        if not fund.current_age or not fund.retirement_age:
            return {}

        age_diff      = fund.retirement_age - fund.current_age
        months_until  = age_diff * 12
        monthly_dep   = float(fund.monthly_deposit or 0)
        current_bal   = float(fund.total_balance or 0)

        estimated_deposits = monthly_dep * months_until
        estimated_total    = current_bal + estimated_deposits
        total_payment_months = (fund.years_payments or 20) * 12
        monthly_payment = estimated_total / total_payment_months if total_payment_months > 0 else 0

        return {
            "months_until_retirement": months_until,
            "estimated_total_at_retirement": round(estimated_total, 2),
            "estimated_monthly_payment": round(monthly_payment, 2),
            "deposits_remaining": max(0, months_until - fund.monthly_deposit_count),
        }

    async def get_admin_fund_stats(self) -> dict:
        total_funds     = await self.funds.count_total()
        active_funds    = await self.funds.count_active()
        in_retirement   = await self.funds.count_in_retirement()
        tvl             = await self.funds.get_total_value_locked()
        total_fees      = await self.funds.get_total_fees_paid()
        total_deposited = await self.txs.get_total_deposited()
        total_withdrawn = await self.txs.get_total_withdrawn()
        by_event        = await self.txs.count_by_event_type()

        return {
            "funds": {
                "total":        total_funds,
                "active":       active_funds,
                "in_retirement": in_retirement,
            },
            "financials": {
                "total_value_locked":  float(tvl),
                "total_fees_paid":     float(total_fees),
                "total_deposited":     float(total_deposited),
                "total_withdrawn":     float(total_withdrawn),
            },
            "transactions_by_type": by_event,
        }