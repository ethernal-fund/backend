from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3
from datetime import datetime
from decimal import Decimal
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


def _from_usdc(raw: int) -> Decimal:
    return Decimal(raw) / Decimal(10 ** USDC_DECIMALS)

FUND_EVENTS_ABI = [
    {"name": "Initialized",        "type": "event", "inputs": [
        {"name": "owner",          "type": "address", "indexed": True},
        {"name": "treasury",       "type": "address", "indexed": False},
        {"name": "usdc",           "type": "address", "indexed": False},
        {"name": "selectedProtocol","type": "address","indexed": False},
        {"name": "initialDeposit", "type": "uint256", "indexed": False},
        {"name": "feeAmount",      "type": "uint256", "indexed": False},
        {"name": "netToFund",      "type": "uint256", "indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
    {"name": "MonthlyDeposited",   "type": "event", "inputs": [
        {"name": "owner",          "type": "address", "indexed": True},
        {"name": "grossAmount",    "type": "uint256", "indexed": False},
        {"name": "feeAmount",      "type": "uint256", "indexed": False},
        {"name": "netToFund",      "type": "uint256", "indexed": False},
        {"name": "depositNumber",  "type": "uint256", "indexed": False},
        {"name": "totalBalance",   "type": "uint256", "indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
    {"name": "ExtraDeposited",     "type": "event", "inputs": [
        {"name": "owner",          "type": "address", "indexed": True},
        {"name": "grossAmount",    "type": "uint256", "indexed": False},
        {"name": "feeAmount",      "type": "uint256", "indexed": False},
        {"name": "netToFund",      "type": "uint256", "indexed": False},
        {"name": "totalBalance",   "type": "uint256", "indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
    {"name": "Withdrawn",          "type": "event", "inputs": [
        {"name": "recipient",      "type": "address", "indexed": True},
        {"name": "amount",         "type": "uint256", "indexed": False},
        {"name": "remainingBalance","type": "uint256","indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
    {"name": "RetirementStarted",  "type": "event", "inputs": [
        {"name": "owner",          "type": "address", "indexed": True},
        {"name": "totalBalance",   "type": "uint256", "indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
    {"name": "InvestedInProtocol", "type": "event", "inputs": [
        {"name": "protocol",       "type": "address", "indexed": True},
        {"name": "amount",         "type": "uint256", "indexed": False},
        {"name": "totalInvested",  "type": "uint256", "indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
    {"name": "AutoWithdrawalExecuted", "type": "event", "inputs": [
        {"name": "recipient",          "type": "address", "indexed": True},
        {"name": "amount",             "type": "uint256", "indexed": False},
        {"name": "executionNumber",    "type": "uint256", "indexed": False},
        {"name": "nextExecutionTime",  "type": "uint256", "indexed": False},
        {"name": "timestamp",          "type": "uint256", "indexed": False},
    ]},
]

FACTORY_EVENTS_ABI = [
    {"name": "FundCreated", "type": "event", "inputs": [
        {"name": "fundAddress",    "type": "address", "indexed": True},
        {"name": "owner",          "type": "address", "indexed": True},
        {"name": "initialDeposit", "type": "uint256", "indexed": False},
        {"name": "principal",      "type": "uint256", "indexed": False},
        {"name": "monthlyDeposit", "type": "uint256", "indexed": False},
        {"name": "selectedProtocol","type": "address","indexed": False},
        {"name": "retirementAge",  "type": "uint256", "indexed": False},
        {"name": "timelockEnd",    "type": "uint256", "indexed": False},
        {"name": "timestamp",      "type": "uint256", "indexed": False},
    ]},
]


class IndexerService:
    def __init__(self, db: AsyncSession):
        self.db         = db
        self.funds      = FundRepository(db)
        self.txs        = TransactionRepository(db)
        self.treasury   = TreasuryRepository(db)
        self.users      = UserRepository(db)
        self.protocols  = ProtocolRepository(db)
        self.blockchain = BlockchainService()
        self.w3         = self.blockchain.w3

    async def run_cycle(self) -> dict:
        last_block    = await self.txs.get_last_indexed_block()
        current_block = self.w3.eth.block_number
        from_block    = max(last_block + 1, current_block - 10000)  # max 10k bloques por ciclo

        if from_block > current_block:
            return {"indexed": 0, "message": "Already up to date"}

        logger.info(f"Indexing blocks {from_block} → {current_block}")
        fund_created   = await self._index_fund_created(from_block, current_block)
        fund_events    = await self._index_fund_events(from_block, current_block)
        fee_events     = await self._index_fee_events(from_block, current_block)

        total = fund_created + fund_events + fee_events
        logger.info(f"Indexed {total} events")
        return {
            "indexed":      total,
            "from_block":   from_block,
            "to_block":     current_block,
            "fund_created": fund_created,
            "fund_events":  fund_events,
            "fee_events":   fee_events,
        }

    async def _index_fund_created(self, from_block: int, to_block: int) -> int:
        factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(settings.FACTORY_ADDRESS),
            abi=FACTORY_EVENTS_ABI,
        )
        try:
            events = factory.events.FundCreated.get_logs(
                fromBlock=from_block, toBlock=to_block
            )
        except Exception as e:
            logger.error(f"Error fetching FundCreated events: {e}")
            return 0

        indexed = 0
        for event in events:
            try:
                args  = event["args"]
                block = self.w3.eth.get_block(event["blockNumber"])
                await self.users.get_or_create(args["owner"])
                timelock_end = datetime.utcfromtimestamp(args["timelockEnd"])
                await self.funds.create_from_event({
                    "contract_address": args["fundAddress"].lower(),
                    "owner_wallet":     args["owner"].lower(),
                    "principal":        _from_usdc(args["principal"]),
                    "monthly_deposit":  _from_usdc(args["monthlyDeposit"]),
                    "retirement_age":   args["retirementAge"],
                    "current_age":      0,  
                    "desired_monthly":  Decimal(0),
                    "years_payments":   0,
                    "interest_rate":    0,
                    "timelock_years":   0,
                    "timelock_end":     timelock_end,
                    "selected_protocol": args["selectedProtocol"].lower() if args["selectedProtocol"] != "0x" + "0" * 40 else None,
                    "created_block":    event["blockNumber"],
                    "created_at":       datetime.utcfromtimestamp(block["timestamp"]),
                })
                await self.txs.create({
                    "id":              event["transactionHash"].hex().lower(),
                    "fund_address":    args["fundAddress"].lower(),
                    "wallet_address":  args["owner"].lower(),
                    "event_type":      "initialized",
                    "gross_amount":    _from_usdc(args["initialDeposit"]),
                    "block_number":    event["blockNumber"],
                    "block_timestamp": datetime.utcfromtimestamp(block["timestamp"]),
                    "log_index":       event["logIndex"],
                })

                indexed += 1

            except Exception as e:
                logger.error(f"Error indexing FundCreated {event['transactionHash'].hex()}: {e}")

        return indexed

    async def _index_fund_events(self, from_block: int, to_block: int) -> int:
        active_funds = await self.funds.get_all_active(limit=10000)
        indexed = 0

        EVENT_MAP = {
            "MonthlyDeposited":    "monthly_deposited",
            "ExtraDeposited":      "extra_deposited",
            "Withdrawn":           "withdrawn",
            "RetirementStarted":   "retirement_started",
            "InvestedInProtocol":  "invested_in_protocol",
            "AutoWithdrawalExecuted": "auto_withdrawal_executed",
        }

        for fund in active_funds:
            try:
                contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(fund.contract_address),
                    abi=FUND_EVENTS_ABI,
                )

                for event_name, event_type in EVENT_MAP.items():
                    try:
                        event_obj = getattr(contract.events, event_name, None)
                        if not event_obj:
                            continue
                        events = event_obj.get_logs(fromBlock=from_block, toBlock=to_block)

                        for event in events:
                            args  = event["args"]
                            block = self.w3.eth.get_block(event["blockNumber"])
                            ts    = datetime.utcfromtimestamp(block["timestamp"])

                            tx_data = {
                                "id":              event["transactionHash"].hex().lower(),
                                "fund_address":    fund.contract_address,
                                "wallet_address":  fund.owner_wallet,
                                "event_type":      event_type,
                                "block_number":    event["blockNumber"],
                                "block_timestamp": ts,
                                "log_index":       event["logIndex"],
                                "extra_data":      {},
                            }

                            # Mapear campos según el tipo de evento
                            if "grossAmount" in args:
                                tx_data["gross_amount"] = _from_usdc(args["grossAmount"])
                                tx_data["fee_amount"]   = _from_usdc(args["feeAmount"])
                                tx_data["net_amount"]   = _from_usdc(args["netToFund"])
                            if "amount" in args:
                                tx_data["gross_amount"] = _from_usdc(args["amount"])
                            if "totalBalance" in args:
                                tx_data["resulting_balance"] = _from_usdc(args["totalBalance"])
                            if "protocol" in args:
                                tx_data["protocol_address"] = args["protocol"].lower()
                            if "depositNumber" in args:
                                tx_data["extra_data"]["deposit_number"] = args["depositNumber"]
                            if "executionNumber" in args:
                                tx_data["extra_data"]["execution_number"] = args["executionNumber"]

                            await self.txs.create(tx_data)
                            if event_type == "retirement_started":
                                await self.funds.mark_retirement_started(fund.contract_address)

                            indexed += 1

                    except Exception as e:
                        logger.warning(f"Error indexing {event_name} for {fund.contract_address}: {e}")

            except Exception as e:
                logger.error(f"Error processing fund {fund.contract_address}: {e}")

        return indexed

    async def _index_fee_events(self, from_block: int, to_block: int) -> int:
        TREASURY_FEE_ABI = [
            {"name": "FeeReceived", "type": "event", "inputs": [
                {"name": "fundAddress", "type": "address", "indexed": True},
                {"name": "amount",      "type": "uint256", "indexed": False},
                {"name": "totalFromFund","type": "uint256","indexed": False},
                {"name": "timestamp",   "type": "uint256", "indexed": False},
            ]},
        ]

        treasury = self.w3.eth.contract(
            address=Web3.to_checksum_address(settings.TREASURY_ADDRESS),
            abi=TREASURY_FEE_ABI,
        )

        try:
            events = treasury.events.FeeReceived.get_logs(
                fromBlock=from_block, toBlock=to_block
            )
        except Exception as e:
            logger.error(f"Error fetching FeeReceived events: {e}")
            return 0

        indexed = 0
        for event in events:
            try:
                args      = event["args"]
                amount    = _from_usdc(args["amount"])
                fund_addr = args["fundAddress"].lower()
                await self.treasury.upsert_fee_record(fund_addr, amount)
                fund = await self.funds.get_by_contract(fund_addr)
                if fund:
                    block = self.w3.eth.get_block(event["blockNumber"])
                    await self.txs.create({
                        "id":             event["transactionHash"].hex().lower() + f"_fee_{event['logIndex']}",
                        "fund_address":   fund_addr,
                        "wallet_address": fund.owner_wallet,
                        "event_type":     "fee_received",
                        "fee_amount":     amount,
                        "block_number":   event["blockNumber"],
                        "block_timestamp": datetime.utcfromtimestamp(block["timestamp"]),
                        "log_index":      event["logIndex"],
                    })
                indexed += 1
            except Exception as e:
                logger.error(f"Error indexing FeeReceived: {e}")

        return indexed