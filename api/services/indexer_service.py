from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from web3 import Web3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
import asyncio
import logging

from api.db.repositories.fund_repo import FundRepository
from api.db.repositories.transaction_repo import TransactionRepository
from api.db.repositories.treasury_repo import TreasuryRepository
from api.db.repositories.user_repo import UserRepository
from api.db.repositories.protocol_repo import ProtocolRepository
from api.services.blockchain_service import BlockchainService
from api.config import settings

logger = logging.getLogger(__name__)

USDC_DECIMALS = 6

def _ts(unix: int) -> datetime:
    return datetime.fromtimestamp(unix, tz=timezone.utc)

def _from_usdc(raw: int) -> Decimal:
    return Decimal(raw) / Decimal(10 ** USDC_DECIMALS)

FUND_EVENTS_ABI = [
    {"name": "Initialized", "type": "event", "inputs": [
        {"name": "owner",            "type": "address", "indexed": True},
        {"name": "treasury",         "type": "address", "indexed": False},
        {"name": "usdc",             "type": "address", "indexed": False},
        {"name": "selectedProtocol", "type": "address", "indexed": False},
        {"name": "initialDeposit",   "type": "uint256", "indexed": False},
        {"name": "feeAmount",        "type": "uint256", "indexed": False},
        {"name": "netToFund",        "type": "uint256", "indexed": False},
        {"name": "timestamp",        "type": "uint256", "indexed": False},
    ]},
    {"name": "MonthlyDeposited", "type": "event", "inputs": [
        {"name": "owner",         "type": "address", "indexed": True},
        {"name": "grossAmount",   "type": "uint256", "indexed": False},
        {"name": "feeAmount",     "type": "uint256", "indexed": False},
        {"name": "netToFund",     "type": "uint256", "indexed": False},
        {"name": "depositNumber", "type": "uint256", "indexed": False},
        {"name": "totalBalance",  "type": "uint256", "indexed": False},
        {"name": "timestamp",     "type": "uint256", "indexed": False},
    ]},
    {"name": "ExtraDeposited", "type": "event", "inputs": [
        {"name": "owner",        "type": "address", "indexed": True},
        {"name": "grossAmount",  "type": "uint256", "indexed": False},
        {"name": "feeAmount",    "type": "uint256", "indexed": False},
        {"name": "netToFund",    "type": "uint256", "indexed": False},
        {"name": "totalBalance", "type": "uint256", "indexed": False},
        {"name": "timestamp",    "type": "uint256", "indexed": False},
    ]},
    {"name": "Withdrawn", "type": "event", "inputs": [
        {"name": "recipient",        "type": "address", "indexed": True},
        {"name": "amount",           "type": "uint256", "indexed": False},
        {"name": "remainingBalance", "type": "uint256", "indexed": False},
        {"name": "timestamp",        "type": "uint256", "indexed": False},
    ]},
    {"name": "RetirementStarted", "type": "event", "inputs": [
        {"name": "owner",        "type": "address", "indexed": True},
        {"name": "totalBalance", "type": "uint256", "indexed": False},
        {"name": "timestamp",    "type": "uint256", "indexed": False},
    ]},
    {"name": "InvestedInProtocol", "type": "event", "inputs": [
        {"name": "protocol",      "type": "address", "indexed": True},
        {"name": "amount",        "type": "uint256", "indexed": False},
        {"name": "totalInvested", "type": "uint256", "indexed": False},
        {"name": "timestamp",     "type": "uint256", "indexed": False},
    ]},
    {"name": "AutoWithdrawalExecuted", "type": "event", "inputs": [
        {"name": "recipient",         "type": "address", "indexed": True},
        {"name": "amount",            "type": "uint256", "indexed": False},
        {"name": "executionNumber",   "type": "uint256", "indexed": False},
        {"name": "nextExecutionTime", "type": "uint256", "indexed": False},
        {"name": "timestamp",         "type": "uint256", "indexed": False},
    ]},
    {"name": "ExtraDepositReclaimed", "type": "event", "inputs": [
        {"name": "owner",         "type": "address", "indexed": True},
        {"name": "grossAmount",   "type": "uint256", "indexed": False},
        {"name": "penaltyAmount", "type": "uint256", "indexed": False},
        {"name": "refundAmount",  "type": "uint256", "indexed": False},
        {"name": "timestamp",     "type": "uint256", "indexed": False},
    ]},
    {"name": "MissedMonthRecorded", "type": "event", "inputs": [
        {"name": "owner",        "type": "address", "indexed": True},
        {"name": "missedMonths", "type": "uint256", "indexed": False},
        {"name": "timestamp",    "type": "uint256", "indexed": False},
    ]},
    {"name": "InvestmentMethodUpdated", "type": "event", "inputs": [
        {"name": "owner",       "type": "address", "indexed": True},
        {"name": "oldProtocol", "type": "address", "indexed": False},
        {"name": "newProtocol", "type": "address", "indexed": False},
        {"name": "timestamp",   "type": "uint256", "indexed": False},
    ]},
    {"name": "EarlyRetirementApproved", "type": "event", "inputs": [
        {"name": "approver",  "type": "address", "indexed": True},
        {"name": "timestamp", "type": "uint256", "indexed": False},
    ]},
]

PERSONALFUNDFACTORY_EVENTS_ABI = [
    {"name": "FundCreated", "type": "event", "inputs": [
        {"name": "fundAddress",      "type": "address", "indexed": True},
        {"name": "owner",            "type": "address", "indexed": True},
        {"name": "initialDeposit",   "type": "uint256", "indexed": False},
        {"name": "principal",        "type": "uint256", "indexed": False},
        {"name": "monthlyDeposit",   "type": "uint256", "indexed": False},
        {"name": "selectedProtocol", "type": "address", "indexed": False},
        {"name": "retirementAge",    "type": "uint256", "indexed": False},
        {"name": "timelockEnd",      "type": "uint256", "indexed": False},
        {"name": "timestamp",        "type": "uint256", "indexed": False},
    ]},
]

TREASURY_FEE_ABI = [
    {"name": "FeeReceived", "type": "event", "inputs": [
        {"name": "fundAddress",   "type": "address", "indexed": True},
        {"name": "amount",        "type": "uint256", "indexed": False},
        {"name": "totalFromFund", "type": "uint256", "indexed": False},
        {"name": "timestamp",     "type": "uint256", "indexed": False},
    ]},
    {"name": "EarlyRetirementRequested", "type": "event", "inputs": [
        {"name": "fundAddress", "type": "address", "indexed": True},
        {"name": "requester",   "type": "address", "indexed": True},
        {"name": "reason",      "type": "string",  "indexed": False},
        {"name": "timestamp",   "type": "uint256", "indexed": False},
    ]},
    {"name": "EarlyRetirementApproved", "type": "event", "inputs": [
        {"name": "fundAddress", "type": "address", "indexed": True},
        {"name": "approver",    "type": "address", "indexed": True},
        {"name": "timestamp",   "type": "uint256", "indexed": False},
    ]},
    {"name": "EarlyRetirementRejected", "type": "event", "inputs": [
        {"name": "fundAddress", "type": "address", "indexed": True},
        {"name": "timestamp",   "type": "uint256", "indexed": False},
    ]},
]

ZERO_ADDRESS = "0x" + "0" * 40

from sqlalchemy import Column, String, BigInteger, DateTime
from api.db.base import Base

class IndexerState(Base):
    __tablename__ = "indexer_state"
    source     = Column(String(64), primary_key=True)
    last_block = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))


class IndexerService:
    def __init__(self, db: AsyncSession):
        self.db        = db
        self.funds     = FundRepository(db)
        self.txs       = TransactionRepository(db)
        self.treasury  = TreasuryRepository(db)
        self.users     = UserRepository(db)
        self.protocols = ProtocolRepository(db)
        self.blockchain = BlockchainService()
        self.w3        = self.blockchain.w3

    async def _block_number(self) -> int:
        return await asyncio.to_thread(lambda: self.w3.eth.block_number)

    async def _get_block(self, block_number: int) -> dict:
        return await asyncio.to_thread(self.w3.eth.get_block, block_number)

    async def _get_logs(self, event_obj, from_block: int, to_block: int) -> list:
        return await asyncio.to_thread(
            event_obj.get_logs,
            from_block=from_block,
            to_block=to_block,
        )

    async def _get_last_block(self, source: str) -> int:
        """Lee el último bloque procesado para una fuente dada."""
        result = await self.db.execute(
            select(IndexerState).where(IndexerState.source == source)
        )
        state = result.scalar_one_or_none()
        return state.last_block if state else 0

    async def _save_last_block(self, source: str, block: int) -> None:
        """Persiste el último bloque procesado para una fuente."""
        now = datetime.now(timezone.utc)
        existing = await self.db.execute(
            select(IndexerState).where(IndexerState.source == source)
        )
        state = existing.scalar_one_or_none()
        if state:
            await self.db.execute(
                update(IndexerState)
                .where(IndexerState.source == source)
                .values(last_block=block, updated_at=now)
            )
        else:
            self.db.add(IndexerState(source=source, last_block=block, updated_at=now))
        await self.db.flush()

    async def run_cycle(self) -> dict:
        current_block = await self._block_number()
        factory_from  = await self._from_block("factory",  current_block)
        treasury_from = await self._from_block("treasury", current_block)
        funds_from    = await self._from_block("funds",    current_block)

        if all(fb > current_block for fb in [factory_from, treasury_from, funds_from]):
            return {"indexed": 0, "message": "Already up to date"}

        logger.info(
            "Indexing — factory:%d→%d  treasury:%d→%d  funds:%d→%d",
            factory_from, current_block,
            treasury_from, current_block,
            funds_from, current_block,
        )

        fund_created, fee_events = await asyncio.gather(
            self._index_fund_created(factory_from, current_block),
            self._index_treasury_events(treasury_from, current_block),
        )
        fund_events = await self._index_fund_events(funds_from, current_block)

        await self._save_last_block("factory",  current_block)
        await self._save_last_block("treasury", current_block)
        await self._save_last_block("funds",    current_block)

        total = fund_created + fund_events + fee_events
        logger.info("Indexed %d events total", total)
        return {
            "indexed":      total,
            "to_block":     current_block,
            "fund_created": fund_created,
            "fund_events":  fund_events,
            "fee_events":   fee_events,
        }

    def _from_block(self, source: str, current_block: int):
        """Devuelve corrutina; se usa en gather o await."""
        return self._get_last_block(source).then if False else \
               self.__from_block_async(source, current_block)

    async def __from_block_async(self, source: str, current_block: int) -> int:
        last = await self._get_last_block(source)
        return max(last + 1, current_block - settings.INDEXER_MAX_BLOCKS_PER_CYCLE)

    async def _index_fund_created(self, from_block: int, to_block: int) -> int:
        factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(settings.PERSONALFUNDFACTORY_ADDRESS),
            abi=PERSONALFUNDFACTORY_EVENTS_ABI,
        )
        try:
            events = await self._get_logs(factory.events.FundCreated, from_block, to_block)
        except Exception as exc:
            logger.error("Error fetching FundCreated events: %s", exc)
            return 0

        indexed = 0
        for event in events:
            try:
                args         = event["args"]
                block        = await self._get_block(event["blockNumber"])
                ts           = _ts(block["timestamp"])
                timelock_end = _ts(args["timelockEnd"])
                fund_address = args["fundAddress"].lower()
                owner        = args["owner"].lower()

                await self.users.get_or_create(owner)
                await self.funds.create_from_event({
                    "contract_address":  fund_address,
                    "owner_wallet":      owner,
                    "principal":         _from_usdc(args["principal"]),
                    "monthly_deposit":   _from_usdc(args["monthlyDeposit"]),
                    "retirement_age":    args["retirementAge"],
                    "current_age":       0,      
                    "desired_monthly":   Decimal(0),
                    "years_payments":    0,
                    "interest_rate":     0,
                    "timelock_years":    0,
                    "timelock_end":      timelock_end,
                    "selected_protocol": args["selectedProtocol"].lower()
                                         if args["selectedProtocol"] != ZERO_ADDRESS else None,
                    "created_block":     event["blockNumber"],
                    "created_at":        ts,
                })

                await self.txs.create({
                    "id":              event["transactionHash"].hex().lower(),
                    "fund_address":    fund_address,
                    "wallet_address":  owner,
                    "event_type":      "initialized",
                    "gross_amount":    _from_usdc(args["initialDeposit"]),
                    "block_number":    event["blockNumber"],
                    "block_timestamp": ts,
                    "log_index":       event["logIndex"],
                })
                indexed += 1
            except Exception as exc:
                logger.error(
                    "Error indexing FundCreated %s: %s",
                    event["transactionHash"].hex(), exc,
                )
        return indexed

    async def _index_fund_events(self, from_block: int, to_block: int) -> int:
        active_funds = await self.funds.get_all_active(limit=10000)
        if not active_funds:
            return 0
        fund_map = {f.contract_address.lower(): f for f in active_funds}
        addresses = [
            Web3.to_checksum_address(f.contract_address) for f in active_funds
        ]

        dummy_contract = self.w3.eth.contract(
            address=addresses[0],
            abi=FUND_EVENTS_ABI,
        )

        EVENT_MAP = {
            "Initialized":            "initialized_fund",   # completa campos faltantes
            "MonthlyDeposited":       "monthly_deposited",
            "ExtraDeposited":         "extra_deposited",
            "Withdrawn":              "withdrawn",
            "RetirementStarted":      "retirement_started",
            "InvestedInProtocol":     "invested_in_protocol",
            "AutoWithdrawalExecuted": "auto_withdrawal_executed",
            "ExtraDepositReclaimed":  "extra_deposit_reclaimed",
            "MissedMonthRecorded":    "missed_month_recorded",
            "InvestmentMethodUpdated":"investment_method_updated",
            "EarlyRetirementApproved":"early_retirement_approved_onchain",
        }

        indexed = 0
        for event_name, event_type in EVENT_MAP.items():
            event_obj = getattr(dummy_contract.events, event_name, None)
            if not event_obj:
                continue
            try:
                all_events = await asyncio.to_thread(
                    event_obj.get_logs,
                    from_block=from_block,
                    to_block=to_block,
                    address=addresses,
                )
            except Exception as exc:
                logger.warning("Error fetching %s events: %s", event_name, exc)
                continue

            for event in all_events:
                try:
                    contract_addr = event["address"].lower()
                    fund = fund_map.get(contract_addr)
                    if not fund:
                        continue

                    args  = event["args"]
                    block = await self._get_block(event["blockNumber"])
                    ts    = _ts(block["timestamp"])
                    tx_hash = event["transactionHash"].hex().lower()
                    if event_type == "initialized_fund":
                        await self._complete_fund_from_initialized(fund, event, ts)
                        indexed += 1
                        continue

                    tx_data: dict = {
                        "id":              tx_hash,
                        "fund_address":    fund.contract_address,
                        "wallet_address":  fund.owner_wallet,
                        "event_type":      event_type,
                        "block_number":    event["blockNumber"],
                        "block_timestamp": ts,
                        "log_index":       event["logIndex"],
                        "extra_data":      {},
                    }

                    if "grossAmount" in args:
                        tx_data["gross_amount"] = _from_usdc(args["grossAmount"])
                        tx_data["fee_amount"]   = _from_usdc(args["feeAmount"])
                        tx_data["net_amount"]   = _from_usdc(args["netToFund"])
                    if "amount" in args and "grossAmount" not in args:
                        tx_data["gross_amount"] = _from_usdc(args["amount"])
                    if "refundAmount" in args:
                        tx_data["net_amount"]   = _from_usdc(args["refundAmount"])
                        tx_data["fee_amount"]   = _from_usdc(args["penaltyAmount"])
                        tx_data["gross_amount"] = _from_usdc(args["grossAmount"])
                    if "totalBalance" in args:
                        tx_data["resulting_balance"] = _from_usdc(args["totalBalance"])
                    if "protocol" in args:
                        tx_data["protocol_address"] = args["protocol"].lower()
                    if "newProtocol" in args:
                        tx_data["protocol_address"] = args["newProtocol"].lower()
                    if "depositNumber" in args:
                        tx_data["extra_data"]["deposit_number"] = args["depositNumber"]
                    if "executionNumber" in args:
                        tx_data["extra_data"]["execution_number"] = args["executionNumber"]
                    if "missedMonths" in args:
                        tx_data["extra_data"]["missed_months"] = args["missedMonths"]

                    await self.txs.create(tx_data)

                    if event_type == "retirement_started":
                        await self.funds.mark_retirement_started(fund.contract_address)

                    indexed += 1

                except Exception as exc:
                    logger.warning(
                        "Error indexing %s event %s: %s",
                        event_name, event["transactionHash"].hex(), exc,
                    )

        return indexed

    async def _complete_fund_from_initialized(self, fund, event: dict, ts: datetime) -> None:
        """
        FIX: Completa los campos que FundCreated del Factory no emite.
        El evento Initialized del propio contrato de fondo incluye todos los
        parámetros de inicialización. Se llama una sola vez por fondo (el
        evento Initialized solo se emite una vez).
        """
        # El evento Initialized no tiene currentAge, desiredMonthly, etc.
        # directamente, pero podemos leerlos del contrato via getFundInfo.
        # Alternativa: llamar on-chain solo si los campos siguen en 0.
        if fund.current_age != 0:
            return  # ya completado en un ciclo anterior

        try:
            info = await asyncio.to_thread(
                self.w3.eth.contract(
                    address=Web3.to_checksum_address(fund.contract_address),
                    abi=[{
                        "name": "getFundInfo", "type": "function",
                        "stateMutability": "view", "inputs": [],
                        "outputs": [
                            {"name": "owner",               "type": "address"},
                            {"name": "principal",           "type": "uint256"},
                            {"name": "monthlyDeposit",      "type": "uint256"},
                            {"name": "retirementAge",       "type": "uint256"},
                            {"name": "totalGrossDeposited", "type": "uint256"},
                            {"name": "totalFeesPaid",       "type": "uint256"},
                            {"name": "totalNetToFund",      "type": "uint256"},
                            {"name": "totalBalance",        "type": "uint256"},
                            {"name": "availableBalance",    "type": "uint256"},
                            {"name": "totalInvested",       "type": "uint256"},
                            {"name": "retirementStarted",   "type": "bool"},
                        ],
                    }, {
                        "name": "currentAge",     "type": "function",
                        "stateMutability": "view", "inputs": [],
                        "outputs": [{"type": "uint256"}],
                    }, {
                        "name": "desiredMonthly", "type": "function",
                        "stateMutability": "view", "inputs": [],
                        "outputs": [{"type": "uint256"}],
                    }, {
                        "name": "yearsPayments",  "type": "function",
                        "stateMutability": "view", "inputs": [],
                        "outputs": [{"type": "uint256"}],
                    }, {
                        "name": "interestRate",   "type": "function",
                        "stateMutability": "view", "inputs": [],
                        "outputs": [{"type": "uint256"}],
                    }, {
                        "name": "timelockPeriod", "type": "function",
                        "stateMutability": "view", "inputs": [],
                        "outputs": [{"type": "uint256"}],
                    }],
                ).functions.currentAge().call
            )
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(fund.contract_address),
                abi=[
                    {"name": "currentAge",     "type": "function", "stateMutability": "view",
                     "inputs": [], "outputs": [{"type": "uint256"}]},
                    {"name": "desiredMonthly", "type": "function", "stateMutability": "view",
                     "inputs": [], "outputs": [{"type": "uint256"}]},
                    {"name": "yearsPayments",  "type": "function", "stateMutability": "view",
                     "inputs": [], "outputs": [{"type": "uint256"}]},
                    {"name": "interestRate",   "type": "function", "stateMutability": "view",
                     "inputs": [], "outputs": [{"type": "uint256"}]},
                    {"name": "timelockPeriod", "type": "function", "stateMutability": "view",
                     "inputs": [], "outputs": [{"type": "uint256"}]},
                ],
            )
            current_age, desired_monthly, years_payments, interest_rate, timelock_years = \
                await asyncio.gather(
                    asyncio.to_thread(contract.functions.currentAge().call),
                    asyncio.to_thread(contract.functions.desiredMonthly().call),
                    asyncio.to_thread(contract.functions.yearsPayments().call),
                    asyncio.to_thread(contract.functions.interestRate().call),
                    asyncio.to_thread(contract.functions.timelockPeriod().call),
                )
            await self.funds.update_balances(fund.contract_address, {
                "current_age":     current_age,
                "desired_monthly": _from_usdc(desired_monthly),
                "years_payments":  years_payments,
                "interest_rate":   interest_rate,
                "timelock_years":  timelock_years,
            })
            logger.debug(
                "Completed fund fields from chain | fund=%s age=%d",
                fund.contract_address, current_age,
            )
        except Exception as exc:
            logger.warning(
                "Failed to complete fund fields for %s: %s",
                fund.contract_address, exc,
            )

    async def _index_treasury_events(self, from_block: int, to_block: int) -> int:
        treasury = self.w3.eth.contract(
            address=Web3.to_checksum_address(settings.TREASURY_ADDRESS),
            abi=TREASURY_FEE_ABI,
        )

        fee_events, req_events, apr_events, rej_events = await asyncio.gather(
            self._safe_get_logs(treasury.events.FeeReceived,              from_block, to_block),
            self._safe_get_logs(treasury.events.EarlyRetirementRequested, from_block, to_block),
            self._safe_get_logs(treasury.events.EarlyRetirementApproved,  from_block, to_block),
            self._safe_get_logs(treasury.events.EarlyRetirementRejected,  from_block, to_block),
        )

        indexed = 0
        indexed += await self._process_fee_events(fee_events)
        indexed += await self._process_retirement_requested(req_events)
        indexed += await self._process_retirement_approved(apr_events)
        indexed += await self._process_retirement_rejected(rej_events)
        return indexed

    async def _safe_get_logs(self, event_obj, from_block: int, to_block: int) -> list:
        try:
            return await self._get_logs(event_obj, from_block, to_block)
        except Exception as exc:
            logger.error("Error fetching %s: %s", event_obj.event_name, exc)
            return []

    async def _process_fee_events(self, events: list) -> int:
        indexed = 0
        for event in events:
            try:
                args      = event["args"]
                amount    = _from_usdc(args["amount"])
                fund_addr = args["fundAddress"].lower()
                await self.treasury.upsert_fee_record(fund_addr, amount)
                fund = await self.funds.get_by_contract(fund_addr)
                if fund:
                    block = await self._get_block(event["blockNumber"])
                    tx_id = f"{event['transactionHash'].hex().lower()}_fee_{event['logIndex']}"
                    await self.txs.create({
                        "id":              tx_id,
                        "fund_address":    fund_addr,
                        "wallet_address":  fund.owner_wallet,
                        "event_type":      "fee_received",
                        "fee_amount":      amount,
                        "block_number":    event["blockNumber"],
                        "block_timestamp": _ts(block["timestamp"]),
                        "log_index":       event["logIndex"],
                    })
                indexed += 1
            except Exception as exc:
                logger.error("Error indexing FeeReceived: %s", exc)
        return indexed

    async def _process_retirement_requested(self, events: list) -> int:
        indexed = 0
        for event in events:
            try:
                args      = event["args"]
                tx_hash   = event["transactionHash"].hex().lower()
                fund_addr = args["fundAddress"].lower()
                requester = args["requester"].lower()

                existing = await self.treasury.get_request_by_id(tx_hash)
                if existing:
                    continue

                await self.treasury.create_request({
                    "id":               tx_hash,
                    "fund_address":     fund_addr,
                    "requester_wallet": requester,
                    "reason":           args["reason"],
                    "status":           "pending",
                })
                indexed += 1
            except Exception as exc:
                logger.error("Error indexing EarlyRetirementRequested: %s", exc)
        return indexed

    async def _process_retirement_approved(self, events: list) -> int:
        indexed = 0
        for event in events:
            try:
                args      = event["args"]
                fund_addr = args["fundAddress"].lower()
                # Buscar el request pendiente por fund_address
                req = await self.treasury.get_request(fund_addr)
                if req and req.status == "pending":
                    await self.treasury.process_request(
                        tx_hash=req.id,
                        approved=True,
                        processed_by=args["approver"].lower(),
                    )
                indexed += 1
            except Exception as exc:
                logger.error("Error indexing EarlyRetirementApproved: %s", exc)
        return indexed

    async def _process_retirement_rejected(self, events: list) -> int:
        indexed = 0
        for event in events:
            try:
                args      = event["args"]
                fund_addr = args["fundAddress"].lower()
                req = await self.treasury.get_request(fund_addr)
                if req and req.status == "pending":
                    await self.treasury.process_request(
                        tx_hash=req.id,
                        approved=False,
                    )
                indexed += 1
            except Exception as exc:
                logger.error("Error indexing EarlyRetirementRejected: %s", exc)
        return indexed