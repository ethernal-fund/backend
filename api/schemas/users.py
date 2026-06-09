from __future__ import annotations
from datetime   import datetime
from typing     import Optional
from pydantic   import BaseModel, Field, field_validator

class NonceRequest(BaseModel):
    wallet_address: str
    @field_validator("wallet_address")
    @classmethod
    def validate_wallet(cls, v: str) -> str:
        from web3 import Web3
        if not Web3.is_address(v):
            raise ValueError("Invalid Ethereum address")
        return Web3.to_checksum_address(v)

class NonceResponse(BaseModel):
    nonce:   str = Field(..., description="Nonce de un solo uso, expira en 5 minutos")
    message: str = Field(..., description="Mensaje SIWE exacto que el frontend debe firmar")

class AuthRequest(BaseModel):
    wallet_address: str  = Field(..., description="Dirección Ethereum (checksum o lowercase)")
    signature:      str  = Field(..., description="Firma ECDSA del mensaje SIWE")
    nonce:          str  = Field(..., description="Nonce obtenido de POST /users/nonce")

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet(cls, v: str) -> str:
        from web3 import Web3
        if not Web3.is_address(v):
            raise ValueError("Invalid Ethereum address")
        return Web3.to_checksum_address(v)

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("0x") or len(v) != 132:
            raise ValueError("signature must be a 65-byte hex string prefixed with 0x")
        return v

class AuthResponse(BaseModel):
    access_token:       str   = Field(..., description="JWT de corta duración (Bearer token)")
    refresh_token:      str   = Field(..., description="Token opaco para rotar el access token")
    token_type:         str   = "bearer"
    wallet_address:     str   = Field(..., description="Wallet autenticado (lowercase)")
    expires_in:         int   = Field(..., description="Segundos hasta que expira el access token")
    refresh_expires_in: int   = Field(..., description="Segundos hasta que expira el refresh token")
    is_new_user:        bool  = Field(
        False,
        description="True si el wallet se registró por primera vez en este request",
    )

class AuthStatusResponse(BaseModel):
    """Respuesta de GET /users/auth/status — verificación rápida sin DB."""
    authenticated:  bool            = Field(..., description="True si el token es válido y no fue revocado")
    wallet_address: Optional[str]   = Field(None, description="Wallet del token (solo si authenticated=True)")
    audience:       Optional[str]   = Field(None, description="Tipo de User")
    expires_at:     Optional[str]   = Field(None, description="ISO 8601 timestamp de expiración del access token")

class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10, description="Refresh token emitido en /auth o /auth/refresh")

class RefreshResponse(BaseModel):
    access_token:       str = Field(..., description="Nuevo JWT de acceso")
    refresh_token:      str = Field(..., description="Nuevo refresh token (el anterior queda invalidado)")
    token_type:         str = "bearer"
    expires_in:         int = Field(..., description="Segundos hasta que expira el nuevo access token")
    refresh_expires_in: int = Field(..., description="Segundos hasta que expira el nuevo refresh token")

class LogoutRequest(BaseModel):
    """
    Body de DELETE /users/auth/session.
    El refresh_token es opcional: si se envía, se invalida; si no, solo se
    blacklistea el access token del header Authorization.
    """
    refresh_token: Optional[str] = Field(
        None,
        description="Refresh token a invalidar (opcional — recomendado enviarlo siempre)",
    )

class SurveySubmit(BaseModel):
    age_range: str = Field(
        ..., description="18-25 | 26-35 | 36-45 | 46-55 | 55+"
    )
    risk_tolerance: int = Field(
        ..., ge=1, le=3, description="1=LOW  2=MEDIUM  3=HIGH"
    )
    crypto_experience: str = Field(
        ..., description="none | beginner | intermediate | advanced"
    )
    retirement_goal: str = Field(
        ..., description="capital_preservation | moderate_growth | aggressive_growth"
    )
    investment_horizon_years: int = Field(..., ge=1, le=50)
    monthly_income_range: str = Field(
        ..., description="0-1000 | 1000-3000 | 3000-7000 | 7000+"
    )
    country: str = Field(
        ..., min_length=2, max_length=2, description="ISO 3166-1 alpha-2 (AR, US, MX...)"
    )

class UserOut(BaseModel):
    wallet_address:           str
    survey_completed:         bool
    survey_completed_at:      Optional[datetime] = None
    age_range:                Optional[str]      = None
    risk_tolerance:           Optional[int]      = None
    crypto_experience:        Optional[str]      = None
    retirement_goal:          Optional[str]      = None
    investment_horizon_years: Optional[int]      = None
    monthly_income_range:     Optional[str]      = None
    country:                  Optional[str]      = None
    first_seen_at:            datetime
    last_active_at:           Optional[datetime] = None
    is_active:                bool

    model_config = {"from_attributes": True}