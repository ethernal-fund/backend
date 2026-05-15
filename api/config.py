from __future__ import annotations

import json
from typing import List, Optional, Set, Dict

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):

    APP_NAME:    str = "Ethernal Backend API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "production"
    DEBUG:       bool = False
    LOG_LEVEL:   str = "INFO"

    # URLs base
    APP_URL:    str = "https://ethernal.fund"
    APP_DOMAIN: str = "ethernal.fund"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 10000
    ALLOWED_ORIGINS: List[str] = ["https://ethernal.fund", "https://www.ethernal.fund", "http://localhost:5173"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v) -> List[str]:
        if not v:
            return ["http://localhost:5173"]
        if isinstance(v, list):
            return [str(o).strip() for o in v if o]
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [o.strip() for o in v.split(",") if o.strip()]
        return ["http://localhost:5173"]

    DATABASE_URL: str

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL is required")
        return (
            v.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
             .replace("postgresql://", "postgresql+asyncpg://")
        )

    REDIS_URL: Optional[str] = None

    # Rate Limiting
    RATE_LIMIT_ENABLED:  bool = True
    RATE_LIMIT_REQUESTS: int  = 100
    RATE_LIMIT_WINDOW:   int  = 60

    # BLOCKCHAIN - MULTICHAIN 
    RPC_URL:  str
    CHAIN_ID: int = 421614  

    # Configuración completa de contratos por chain
    CONTRACT_ADDRESSES: Dict[int, Dict[str, str]] = {
        421614: {  # Arbitrum Sepolia
            "PERSONALFUNDFACTORY_ADDRESS": "0x078D8C19f52B50B6f11CC41C011dD1f55f6505Bf",
            "PROTOCOLREGISTRY_ADDRESS":    "0x5F44eaed859B3b426D02d5E596C16eDF387abB75",
            "TREASURY_ADDRESS":            "0x9a6397E5D17d8FDB16f3554e9774c764343C311b",
            "USDC_ADDRESS":                "0x253A19C8A3AFD13c5F54fB0694e356e2d3167AFa",   # MockUSDC
        },
        11155111: {  # Ethereum Sepolia
            "PERSONALFUNDFACTORY_ADDRESS": "0xD346f0e4253251F80A79C8ebA1EF2fe5DBa6559E",
            "PROTOCOLREGISTRY_ADDRESS":    "0x680CAd1cFdB5460DbA02591A06C23FFE7716091d",
            "TREASURY_ADDRESS":            "0xaF6C9A8D5524f3Da304A981c428BF0FAbAe26d94",
            "USDC_ADDRESS":                "0xa27dc7dd223a00E89B885CE6968E6379F7146CD3",   # MockUSDC
        },
    }

    # Variables legacy (mantengo compatibilidad)
    PERSONALFUNDFACTORY_ADDRESS: str = ""
    PROTOCOLREGISTRY_ADDRESS:    str = ""
    TREASURY_ADDRESS:            str = ""
    USDC_ADDRESS:                str = ""

    # Admin
    ADMIN_WALLET:   str
    ADMIN_WALLETS:  Optional[str] = None  
    ADMIN_API_KEY:  str
    API_KEY_HEADER: str = "X-API-Key"

    @field_validator("ADMIN_API_KEY", mode="before")
    @classmethod
    def validate_admin_api_key(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError("ADMIN_API_KEY must be at least 32 characters")
        return v

    def get_admin_wallets(self) -> Set[str]:
        wallets = {self.ADMIN_WALLET.lower()}
        if self.ADMIN_WALLETS:
            for w in self.ADMIN_WALLETS.split(","):
                w = w.strip().lower()
                if w:
                    wallets.add(w)
        return wallets

    # MULTICHAIN HELPER 
    def get_contract_address(self, contract_name: str, chain_id: Optional[int] = None) -> str:
        """Obtiene la dirección de un contrato según la chain activa"""
        if chain_id is None:
            chain_id = self.CHAIN_ID

        chain_config = self.CONTRACT_ADDRESSES.get(chain_id)
        if chain_config and contract_name in chain_config:
            addr = chain_config[contract_name]
            if addr and addr.startswith("0x"):
                return addr

        # Fallback a variable legacy
        return getattr(self, contract_name, "")

    # Auth, JWT, Faucet, etc.
    AUTH_MESSAGE: str = (
        "{domain} wants you to sign in with your Ethereum account:\n"
        "{wallet}\n\n"
        "Sign in to Ethernal Fund\n\n"
        "URI: {uri}\n"
        "Version: 1\n"
        "Chain ID: {chain_id}\n"
        "Nonce: {nonce}\n"
        "Issued At: {issued_at}"
    )

    JWT_SECRET:                str
    JWT_ALGORITHM:             str = "HS256"
    JWT_EXPIRE_MINUTES:        int = 60    
    JWT_REFRESH_EXPIRE_MINUTES: int = 10080

    FAUCET_AMOUNT:         float = 10000.0
    FAUCET_COOLDOWN_HOURS: int   = 24
    FAUCET_PRIVATE_KEY:    Optional[str] = None

    @field_validator("FAUCET_PRIVATE_KEY", mode="before")
    @classmethod
    def validate_faucet_key(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip()
        key = v.removeprefix("0x")
        if len(key) != 64 or not all(c in "0123456789abcdefABCDEF" for c in key):
            raise ValueError("FAUCET_PRIVATE_KEY must be a 32-byte hex string")
        return v

    INDEXER_INTERVAL_SECONDS:     int = 30
    INDEXER_MAX_BLOCKS_PER_CYCLE: int = 10000

    SENTRY_ENABLED:            bool = False
    SENTRY_DSN:                Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    @model_validator(mode="after")
    def validate_cross_field(self) -> "Settings":
        if self.RATE_LIMIT_ENABLED and not self.REDIS_URL:
            raise ValueError("REDIS_URL is required when RATE_LIMIT_ENABLED=True")

        if self.ENVIRONMENT == "production":
            if len(self.JWT_SECRET or "") < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters in production")
            if self.JWT_EXPIRE_MINUTES > 120:
                raise ValueError("JWT_EXPIRE_MINUTES should not exceed 120 in production")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_parse_none_str="",
    )

settings = Settings()