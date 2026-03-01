from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List, Optional

class Settings(BaseSettings):
    APP_NAME: str = "Ethernal Backend API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 10000

    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors_origins(cls, v):
        if not v:
            return ["http://localhost:3000"]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["http://localhost:3000"]
            if v.startswith("["):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(',') if origin.strip()]
        if isinstance(v, list):
            return [str(origin).strip() for origin in v if origin]
        return v

    DATABASE_URL: str
    REDIS_URL: Optional[str] = None

    @field_validator('DATABASE_URL', mode='before')
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        return (
            v.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
             .replace("postgresql://", "postgresql+asyncpg://")
        )

    RPC_URL: str
    CHAIN_ID: int = 11155111

    FACTORY_ADDRESS: str
    TREASURY_ADDRESS: str
    USDC_ADDRESS: str
    PROTOCOL_REGISTRY_ADDRESS: str

    ADMIN_WALLET: str
    ADMIN_API_KEY: str
    API_KEY_HEADER: str = "X-API-Key"

    AUTH_MESSAGE: str = "Sign this message to authenticate with Ethernal. Nonce: {nonce}"
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440                                      # 24 horas

    FAUCET_AMOUNT: float = 100.0                                        # USDC
    FAUCET_COOLDOWN_HOURS: int = 24
    FAUCET_PRIVATE_KEY: Optional[str] = None

    INDEXER_INTERVAL_SECONDS: int = 30
    INDEXER_MAX_BLOCKS_PER_CYCLE: int = 10000
    INDEXER_START_BLOCK: int = 0

    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW: int = 60

    SENTRY_ENABLED: bool = False
    SENTRY_DSN: Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra='ignore',
    )

settings = Settings()