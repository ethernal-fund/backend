from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, model_validator

MIN_TIMELOCK_YEARS = 15   # espejo de la constante del Factory


class FundOut(BaseModel):
    contract_address: str
    owner_wallet:     str

    principal:         Decimal
    monthly_deposit:   Decimal
    current_age:       int
    retirement_age:    int
    desired_monthly:   Decimal
    years_payments:    int
    interest_rate:     int          # basis points
    timelock_years:    int
    timelock_end:      datetime
    selected_protocol: Optional[str] = None

    total_gross_deposited: Decimal
    total_fees_paid:       Decimal
    total_net_to_fund:     Decimal
    total_balance:         Decimal
    available_balance:     Decimal
    total_invested:        Decimal
    total_withdrawn:       Decimal

    monthly_deposit_count:           int
    extra_deposit_count:             int
    withdrawal_count:                int
    auto_withdrawal_execution_count: int

    # Status flags
    is_active:                 bool
    retirement_started:        bool
    early_retirement_approved: bool
    auto_withdrawal_enabled:   bool

    # Auto-withdrawal config
    # FIX: default=0 en lugar de Optional sin default para evitar errores de
    # serialización cuando el fondo no tiene auto-withdrawal configurado.
    auto_withdrawal_amount:           Optional[Decimal] = None
    auto_withdrawal_interval_seconds: int               = 0
    next_auto_withdrawal_at:          Optional[datetime] = None
    retirement_started_at:            Optional[datetime] = None

    # Timestamps
    created_at:     datetime
    last_synced_at: Optional[datetime] = None
    created_block:  Optional[int]      = None

    class Config:
        from_attributes = True


class FundSyncRequest(BaseModel):
    contract_address: str = Field(
        ..., description="Checksum Ethereum address of the fund contract"
    )


class RegisterFundRequest(BaseModel):
    contract_address:       str     = Field(..., description="Checksum Ethereum address of the deployed fund")
    principal:              Decimal = Field(..., ge=0)
    monthly_deposit:        Decimal = Field(..., ge=0)
    desired_monthly_income: Decimal = Field(..., ge=0)
    current_age:            int     = Field(..., ge=18, le=80)
    retirement_age:         int     = Field(..., ge=55, le=120)
    payment_years:          int     = Field(..., ge=1,  le=50)
    apy_percent:            Decimal = Field(..., ge=0,  description="APY as a percentage, e.g. 5.5")
    protocol_address:       str     = Field(..., description="Checksum Ethereum address of the chosen protocol")

    timelock_years: int      = Field(default=0)
    timelock_end:   datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def derive_and_validate(self) -> "RegisterFundRequest":
        if self.retirement_age <= self.current_age:
            raise ValueError("retirement_age must be greater than current_age")

        derived_timelock = self.retirement_age - self.current_age

        # FIX: el Factory exige _timelockYears >= 15. La versión anterior
        # derivaba el timelock de retirement_age - current_age sin validar
        # el mínimo, por lo que el backend podría enviar valores que el
        # contrato rechazaría (ej: current_age=40, retirement_age=50 → 10 años).
        if derived_timelock < MIN_TIMELOCK_YEARS:
            raise ValueError(
                f"The difference between retirement_age and current_age must be "
                f"at least {MIN_TIMELOCK_YEARS} years "
                f"(got {derived_timelock}). "
                f"This matches the Factory contract minimum timelock requirement."
            )

        self.timelock_years = derived_timelock
        self.timelock_end   = datetime.now(timezone.utc) + timedelta(days=self.timelock_years * 365)
        return self

    @property
    def interest_rate_bps(self) -> int:
        return int(self.apy_percent * 100)