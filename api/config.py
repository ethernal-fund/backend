from __future__ import annotations

import json
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):

    APP_NAME:    str = "Ethernal Backend API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "production"   # production | staging | development
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

    # Blockchain
    RPC_URL:  str
    CHAIN_ID: int = 421614   # Arbitrum Sepolia por defecto

    FACTORY_ADDRESS:           str
    TREASURY_ADDRESS:          str
    USDC_ADDRESS:              str
    PROTOCOL_REGISTRY_ADDRESS: str

    ADMIN_WALLET:   str
    ADMIN_API_KEY:  str
    API_KEY_HEADER: str = "X-API-Key"

    # Auth
    AUTH_MESSAGE: str = (
        "{domain} wants you to sign in with your Ethereum account:\n"
        "{wallet}\n\n"
        "Nonce: {nonce}\n\n"
        "URI: {uri}\n"
        "Version: 1\n"
        "Chain ID: {chain_id}\n"
        "Nonce: {nonce}"
    )

    JWT_SECRET:         str
    JWT_ALGORITHM:      str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440   # 24 horas

    # Faucet
    FAUCET_AMOUNT:         float = 10000.0
    FAUCET_COOLDOWN_HOURS: int   = 24
    FAUCET_PRIVATE_KEY:    Optional[str] = None

    # Indexer
    INDEXER_INTERVAL_SECONDS:     int = 30
    INDEXER_MAX_BLOCKS_PER_CYCLE: int = 10000

    # Sentry
    SENTRY_ENABLED:            bool = False
    SENTRY_DSN:                Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    @model_validator(mode="after")
    def validate_cross_field(self) -> "Settings":
        if self.RATE_LIMIT_ENABLED and not self.REDIS_URL:
            raise ValueError("REDIS_URL is required when RATE_LIMIT_ENABLED=True")
        if self.ENVIRONMENT == "production" and len(self.JWT_SECRET or "") < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters in production")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_parse_none_str="",
    )
settings = Settings()