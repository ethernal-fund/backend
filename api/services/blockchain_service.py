from web3 import Web3
from datetime import datetime
from decimal import Decimal
from typing import Optional
import asyncio
import logging

from api.config import settings

logger = logging.getLogger(__name__)
USDC_DECIMALS = 6

def _from_usdc(raw: int) -> Decimal:
    return Decimal(raw) / Decimal(10 ** USDC_DECIMALS)

FUND_ABI = [
    {"name": "getFundInfo", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [
         {"type": "address"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"},
         {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"},
         {"type": "uint256"}, {"type": "uint256"}, {"type": "bool"},
     ]},
    {"name": "getBalances", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [
         {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"},
     ]},
    {"name": "getAutoWithdrawalInfo", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [
         {"type": "bool"}, {"type": "uint256"}, {"type": "uint256"},
         {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"},
     ]},
    {"name": "getTimelockInfo", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [
         {"type": "uint256"}, {"type": "uint256"}, {"type": "bool"},
     ]},
]

TREASURY_ABI = [
    {"name": "getTreasuryStats", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"components": [
         {"name": "totalFeesCollectedUSDC",      "type": "uint256"},
         {"name": "totalFeesCollectedAllTime",    "type": "uint256"},
         {"name": "totalFundsRegistered",         "type": "uint256"},
         {"name": "activeFundsCount",             "type": "uint256"},
         {"name": "totalEarlyRetirementRequests", "type": "uint256"},
         {"name": "approvedEarlyRetirements",     "type": "uint256"},
         {"name": "rejectedEarlyRetirements",     "type": "uint256"},
     ], "type": "tuple"}]},
    {"name": "getTreasuryBalance", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint256"}]},
]

PROTOCOL_REGISTRY_ABI = [
    {"name": "getAllProtocols", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "address[]"}]},
    {"name": "getProtocol", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "_protocolAddress", "type": "address"}],
     "outputs": [{"components": [
         {"name": "protocolAddress", "type": "address"},
         {"name": "name",            "type": "string"},
         {"name": "apy",             "type": "uint256"},
         {"name": "isActive",        "type": "bool"},
         {"name": "totalDeposited",  "type": "uint256"},
         {"name": "riskLevel",       "type": "uint8"},
         {"name": "addedTimestamp",  "type": "uint256"},
         {"name": "lastUpdated",     "type": "uint256"},
         {"name": "verified",        "type": "bool"},
     ], "type": "tuple"}]},
    {"name": "getGlobalStats", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"components": [
         {"name": "totalProtocols",   "type": "uint256"},
         {"name": "activeProtocols",  "type": "uint256"},
         {"name": "totalValueLocked", "type": "uint256"},
         {"name": "averageAPY",       "type": "uint256"},
     ], "type": "tuple"}]},
]

ZERO_ADDRESS = "0x" + "0" * 40

class BlockchainService:

    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(settings.RPC_URL))
        if not self.w3.is_connected():
            logger.warning("Web3 not connected — RPC may be unavailable")

    def is_connected(self) -> bool:
        try:
            return self.w3.is_connected()
        except Exception:
            return False

    def _fund_contract(self, address: str):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=FUND_ABI,
        )

    def _treasury_contract(self):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(settings.TREASURY_ADDRESS),
            abi=TREASURY_ABI,
        )

    def _registry_contract(self):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(settings.PROTOCOL_REGISTRY_ADDRESS),
            abi=PROTOCOL_REGISTRY_ABI,
        )

    async def get_fund_info(self, contract_address: str) -> dict:
        contract = self._fund_contract(contract_address)
        info, balances, auto = await asyncio.gather(
            asyncio.to_thread(contract.functions.getFundInfo().call),
            asyncio.to_thread(contract.functions.getBalances().call),
            asyncio.to_thread(contract.functions.getAutoWithdrawalInfo().call),
        )
        return {
            "total_balance":                    _from_usdc(balances[0]),
            "available_balance":                _from_usdc(balances[1]),
            "total_invested":                   _from_usdc(balances[2]),
            "retirement_started":               info[10],
            "auto_withdrawal_enabled":          auto[0],
            "auto_withdrawal_amount":           _from_usdc(auto[1]) if auto[1] else None,
            "auto_withdrawal_interval_seconds": auto[2],
            "next_auto_withdrawal_at":          datetime.utcfromtimestamp(auto[3]) if auto[3] else None,
            "auto_withdrawal_execution_count":  auto[4],
            "last_auto_withdrawal_at":          datetime.utcfromtimestamp(auto[5]) if auto[5] else None,
        }

    async def get_treasury_stats(self) -> dict:
        treasury = self._treasury_contract()
        stats, balance = await asyncio.gather(
            asyncio.to_thread(treasury.functions.getTreasuryStats().call),
            asyncio.to_thread(treasury.functions.getTreasuryBalance().call),
        )
        return {
            "balance_usdc":               float(_from_usdc(balance)),
            "total_fees_collected_usdc":  float(_from_usdc(stats[0])),
            "total_fees_all_time":        float(_from_usdc(stats[1])),
            "total_funds_registered":     stats[2],
            "active_funds":               stats[3],
            "early_retirement_requests":  stats[4],
            "approved_early_retirements": stats[5],
            "rejected_early_retirements": stats[6],
        }

    async def get_all_protocols(self) -> list[dict]:
        registry  = self._registry_contract()
        addresses = await asyncio.to_thread(registry.functions.getAllProtocols().call)
        valid_addresses = [a for a in addresses if a != ZERO_ADDRESS]

        async def _fetch_one(addr: str) -> Optional[dict]:
            try:
                p = await asyncio.to_thread(registry.functions.getProtocol(addr).call)
                return {
                    "protocol_address": addr.lower(),
                    "name":             p[1],
                    "apy":              p[2] / 100,
                    "is_active":        p[3],
                    "total_deposited":  _from_usdc(p[4]),
                    "risk_level":       p[5],
                    "added_at":         datetime.utcfromtimestamp(p[6]),
                    "last_updated_at":  datetime.utcfromtimestamp(p[7]) if p[7] else None,
                    "is_verified":      p[8],
                }
            except Exception as exc:
                logger.warning("Failed to get protocol %s: %s", addr, exc)
                return None
        results = await asyncio.gather(*(_fetch_one(addr) for addr in valid_addresses))
        return [r for r in results if r is not None]

    async def get_protocol_registry_stats(self) -> dict:
        registry = self._registry_contract()
        stats = await asyncio.to_thread(registry.functions.getGlobalStats().call)
        return {
            "total_protocols":    stats[0],
            "active_protocols":   stats[1],
            "total_value_locked": float(_from_usdc(stats[2])),
            "average_apy":        stats[3] / 100,
        }