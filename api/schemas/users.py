from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator

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
    nonce:   str
    message: str

class AuthRequest(BaseModel):
    wallet_address: str
    signature:      str
    nonce:          str

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet(cls, v: str) -> str:
        from web3 import Web3
        if not Web3.is_address(v):
            raise ValueError("Invalid Ethereum address")
        return Web3.to_checksum_address(v)

class AuthResponse(BaseModel):
    access_token:          str
    refresh_token:         str          
    token_type:            str = "bearer"
    wallet_address:        str
    expires_in:            int          # segundos hasta expiración del access token
    refresh_expires_in:    int          # segundos hasta expiración del refresh token

class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10)

class RefreshResponse(BaseModel):
    access_token:       str
    refresh_token:      str  
    token_type:         str = "bearer"
    expires_in:         int
    refresh_expires_in: int

class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None   # opcional — se invalida si se envía

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