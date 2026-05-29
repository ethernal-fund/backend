from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Set

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_logger = logging.getLogger(__name__)

class Settings(BaseSettings):

    APP_NAME:    str  = "Ethernal Backend API"
    APP_VERSION: str  = "0.1.0"
    ENVIRONMENT: str  = "production"
    DEBUG:       bool = False
    LOG_LEVEL:   str  = "INFO"

    APP_URL:    str = "https://ethernal.fund"
    APP_DOMAIN: str = "ethernal.fund"

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 10000

    ALLOWED_ORIGINS: List[str] = [
        "https://ethernal.fund",
        "https://www.ethernal.fund",
        "http://localhost:5173",
    ]

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
    RATE_LIMIT_ENABLED:  bool = True
    RATE_LIMIT_REQUESTS: int  = 100
    RATE_LIMIT_WINDOW:   int  = 60

    # ── Blockchain principal (protocolo Ethernal — Arbitrum Sepolia) ──────────
    # Usado por el indexer del protocolo (PersonalFund, ProtocolRegistry, etc.)
    # NO es el mismo chain que la sale — ver sección "Token Sale" más abajo.
    RPC_URL:  str
    CHAIN_ID: int = 421614   # Arbitrum Sepolia

    CONTRACT_ADDRESSES: Dict[int, Dict[str, str]] = {
        421614: {
            "PERSONALFUNDFACTORY_ADDRESS": "0x078D8C19f52B50B6f11CC41C011dD1f55f6505Bf",
            "PROTOCOLREGISTRY_ADDRESS":    "0x5F44eaed859B3b426D02d5E596C16eDF387abB75",
            "TREASURY_ADDRESS":            "0x9a6397E5D17d8FDB16f3554e9774c764343C311b",
            "USDC_ADDRESS":                "0x253A19C8A3AFD13c5F54fB0694e356e2d3167AFa",
        },
        11155111: {
            "PERSONALFUNDFACTORY_ADDRESS": "0xD346f0e4253251F80A79C8ebA1EF2fe5DBa6559E",
            "PROTOCOLREGISTRY_ADDRESS":    "0x680CAd1cFdB5460DbA02591A06C23FFE7716091d",
            "TREASURY_ADDRESS":            "0xaF6C9A8D5524f3Da304A981c428BF0FAbAe26d94",
            "USDC_ADDRESS":                "0xa27dc7dd223a00E89B885CE6968E6379F7146CD3",
        },
    }

    # Variables legacy — mantener para compatibilidad con código existente
    PERSONALFUNDFACTORY_ADDRESS: str = ""
    PROTOCOLREGISTRY_ADDRESS:    str = ""
    TREASURY_ADDRESS:            str = ""
    USDC_ADDRESS:                str = ""

    def get_contract_address(self, contract_name: str, chain_id: Optional[int] = None) -> str:
        if chain_id is None:
            chain_id = self.CHAIN_ID
        chain_config = self.CONTRACT_ADDRESSES.get(chain_id)
        if chain_config and contract_name in chain_config:
            addr = chain_config[contract_name]
            if addr and addr.startswith("0x"):
                return addr
        return getattr(self, contract_name, "")

    # ── Token Sale / Crowdfunding (SaleETRF + VestingETRF) ───────────────────
    #
    # La sale corre en una chain distinta al protocolo principal.
    # Actualmente:
    #   Protocolo → Arbitrum Sepolia (chain 421614)  — RPC_URL / CHAIN_ID
    #   Sale      → Ethereum Sepolia (chain 11155111) — SALE_RPC_URL / SALE_CHAIN_ID
    #
    # Si en el futuro ambos están en la misma chain, SALE_RPC_URL puede
    # apuntar al mismo endpoint que RPC_URL.
    #
    # Opcionales en startup: si no se configuran, chain.py lanza ConfigError
    # (HTTP 501) en los endpoints /sale/* sin romper el resto del backend.
    # Esto permite deployar sin la sale activa durante el desarrollo.

    SALE_CHAIN_ID:            int           = 11155111   # Ethereum Sepolia
    SALE_RPC_URL:             Optional[str] = None       # Alchemy/Infura endpoint
    SALE_CONTRACT_ADDRESS:    Optional[str] = None       # SaleETRF deployed address
    VESTING_CONTRACT_ADDRESS: Optional[str] = None       # VestingETRF deployed address

    @field_validator("SALE_CONTRACT_ADDRESS", "VESTING_CONTRACT_ADDRESS", mode="before")
    @classmethod
    def normalize_eth_address(cls, v: Optional[str]) -> Optional[str]:
        """Normaliza a lowercase y valida formato básico 0x + 40 hex."""
        if not v:
            return None
        v = v.strip().lower()
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError(
                f"Dirección Ethereum inválida: {v!r}. "
                "Debe ser 0x seguido de 40 caracteres hex."
            )
        return v

    @property
    def sale_enabled(self) -> bool:
        """True si los tres campos de sale están configurados."""
        return bool(
            self.SALE_RPC_URL
            and self.SALE_CONTRACT_ADDRESS
            and self.VESTING_CONTRACT_ADDRESS
        )

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

    # ── Autenticación JWT + SIWE ──────────────────────────────────────────────
    #
    # AUTH_MESSAGE: template del mensaje EIP-4361 que se construye en
    # core/auth.py → build_siwe_message(). Debe mantenerse sincronizado
    # con el mensaje que construye el frontend en useSiwe.ts.
    # Si cambiás el template, cambiá también el frontend.
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

    JWT_SECRET:    str
    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES:  int = 60       # 1 hora
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10_080   # 7 días

    # Nombre de la cookie HttpOnly donde se guarda el access token.
    # Debe coincidir con lo que setea el endpoint POST /auth/verify-siwe
    # y lo que lee extract_token_from_request() en core/auth.py.
    ACCESS_TOKEN_COOKIE: str = "access_token"

    # TTL del nonce SIWE en Redis. 300s = 5 minutos — ventana razonable
    # para que el usuario confirme la firma en su wallet sin que el nonce expire.
    NONCE_TTL_SECONDS: int = 300

    # Aliases de retrocompatibilidad — no usar en código nuevo
    @property
    def JWT_EXPIRE_MINUTES(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES

    @property
    def JWT_REFRESH_EXPIRE_MINUTES(self) -> int:
        return self.REFRESH_TOKEN_EXPIRE_MINUTES

    # ── Faucet (mock-usdc service — configuración referencial) ───────────────
    # El faucet corre como servicio separado en Render.
    # Estas vars son solo para el backend principal si necesita consultarlo.
    FAUCET_AMOUNT:         float         = 10_000.0
    FAUCET_COOLDOWN_HOURS: int           = 24
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

    # ── Indexer del protocolo principal ───────────────────────────────────────
    # Controla el loop de IndexerService en main.py (PersonalFund, Registry, etc.)
    # No aplica al sale indexer (verify-purchase es fire-and-forget por ahora).
    INDEXER_INTERVAL_SECONDS:     int = 30
    INDEXER_MAX_BLOCKS_PER_CYCLE: int = 10_000

    SENTRY_ENABLED:            bool          = False
    SENTRY_DSN:                Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float         = 0.1

    # ── Validaciones cruzadas 
    @model_validator(mode="after")
    def validate_cross_field(self) -> "Settings":
        # Redis obligatorio si el rate limiting está activo
        if self.RATE_LIMIT_ENABLED and not self.REDIS_URL:
            raise ValueError("REDIS_URL is required when RATE_LIMIT_ENABLED=True")

        if self.ENVIRONMENT == "production":
            # JWT_SECRET mínimo 32 chars en producción
            if len(self.JWT_SECRET or "") < 32:
                raise ValueError(
                    "JWT_SECRET must be at least 32 characters in production"
                )

            # Sale: si está parcialmente configurada, advertir sin romper startup.
            # Los endpoints /sale/* retornarán 501 hasta que estén todas las vars.
            sale_vars = {
                "SALE_RPC_URL":             self.SALE_RPC_URL,
                "SALE_CONTRACT_ADDRESS":    self.SALE_CONTRACT_ADDRESS,
                "VESTING_CONTRACT_ADDRESS": self.VESTING_CONTRACT_ADDRESS,
            }
            configured = {k for k, v in sale_vars.items() if v}
            missing    = {k for k, v in sale_vars.items() if not v}

            if configured and missing:
                # Configuración parcial — advertir en startup
                _logger.warning(
                    "Token sale parcialmente configurada — faltan: %s. "
                    "Los endpoints /sale/* retornarán 501 hasta que estén todas.",
                    ", ".join(sorted(missing)),
                )
            elif not configured:
                # No configurada en absoluto — solo debug, no es un error
                _logger.debug(
                    "Token sale no configurada (SALE_RPC_URL, SALE_CONTRACT_ADDRESS, "
                    "VESTING_CONTRACT_ADDRESS ausentes). Los endpoints /sale/* "
                    "retornarán 501."
                )

        return self

    # Pydantic settings config 
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        env_parse_none_str="",
    )

settings = Settings()