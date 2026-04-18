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

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 10000

    # Acepta string CSV ("http://a.com,http://b.com") o JSON array ("[...]")
    CORS_ORIGINS: str = "http://localhost:3000"

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def parse_cors_origins(cls, v) -> List[str]:
        if not v:
            return ["http://localhost:3000"]
        if isinstance(v, list):
            return [str(o).strip() for o in v if o]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["http://localhost:3000"]
            if v.startswith("["):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    DATABASE_URL: str  # requerido — sin default

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """
        Normaliza la URL para usar asyncpg independientemente de cómo
        esté configurada (Supabase suele dar URLs con psycopg2 o sin driver).
        """
        if not v:
            raise ValueError("DATABASE_URL is required")
        return (
            v.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
             .replace("postgresql://", "postgresql+asyncpg://")
        )

    REDIS_URL: Optional[str] = None

    RATE_LIMIT_ENABLED:  bool = True
    RATE_LIMIT_REQUESTS: int  = 100
    RATE_LIMIT_WINDOW:   int  = 60   # segundos

    RPC_URL:  str  # requerido
    CHAIN_ID: int = 11155111  # Sepolia testnet

    FACTORY_ADDRESS:           str  # requerido
    TREASURY_ADDRESS:          str  # requerido
    USDC_ADDRESS:              str  # requerido
    PROTOCOL_REGISTRY_ADDRESS: str  # requerido

    ADMIN_WALLET:   str  # requerido
    ADMIN_API_KEY:  str  # requerido
    API_KEY_HEADER: str = "X-API-Key"

    AUTH_MESSAGE:       str = "Sign this message to authenticate with Ethernal. Nonce: {nonce}"
    JWT_SECRET:         str  # requerido
    JWT_ALGORITHM:      str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 horas

    FAUCET_AMOUNT:        float         = 10000.0
    FAUCET_COOLDOWN_HOURS: int          = 24
    FAUCET_PRIVATE_KEY:   Optional[str] = None

    INDEXER_INTERVAL_SECONDS:    int = 30
    INDEXER_MAX_BLOCKS_PER_CYCLE: int = 10000
    INDEXER_START_BLOCK:         int = 0

    SENTRY_ENABLED:            bool         = False
    SENTRY_DSN:                Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float         = 0.1

    @model_validator(mode="after")
    def validate_cross_field(self) -> "Settings":
        """
        Validaciones que dependen de múltiples campos.
        @model_validator(mode="after") corre cuando TODOS los campos
        ya fueron validados individualmente — sin problemas de ordering.
        """
        # Redis requerido si rate limiting está habilitado
        if self.RATE_LIMIT_ENABLED and not self.REDIS_URL:
            raise ValueError(
                "REDIS_URL is required when RATE_LIMIT_ENABLED=True. "
                "Either set REDIS_URL or set RATE_LIMIT_ENABLED=false."
            )

        # JWT_SECRET debe tener mínimo 32 chars en producción
        if self.ENVIRONMENT == "production" and len(self.JWT_SECRET) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",           # ignorar vars desconocidas (seguro para forward compat)
        env_parse_none_str="",    # string vacío se trata como None en Optional fields
    )

settings = Settings()