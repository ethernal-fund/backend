from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

class FundOut(BaseModel):
    # Identity
    contract_address: str
    owner_wallet:     str

    principal:         Decimal
    monthly_deposit:   Decimal
    current_age:       int
    retirement_age:    int
    desired_monthly:   Decimal
    years_payments:    int
    interest_rate:     int        # basis points
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

    # Counters
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
    auto_withdrawal_amount:           Optional[Decimal]  = None
    auto_withdrawal_interval_seconds: Optional[int]      = None
    next_auto_withdrawal_at:          Optional[datetime] = None
    retirement_started_at:            Optional[datetime] = None

    # Timestamps
    created_at:    datetime
    last_synced_at: Optional[datetime] = None
    created_block:  Optional[int]      = None

    class Config:
        from_attributes = True

class FundSyncRequest(BaseModel):
    contract_address: str = Field(..., description="Checksum Ethereum address of the fund contract")

class RegisterFundRequest(BaseModel):
    contract_address:       str     = Field(..., description="Checksum Ethereum address of the deployed fund")
    principal:              Decimal = Field(..., ge=0)
    monthly_deposit:        Decimal = Field(..., ge=0)
    desired_monthly_income: Decimal = Field(..., ge=0)
    current_age:            int     = Field(..., ge=1, le=120)
    retirement_age:         int     = Field(..., ge=1, le=120)
    payment_years:          int     = Field(..., ge=1, le=50)
    apy_percent:            Decimal = Field(..., ge=0)
    protocol_address:       str     = Field(..., description="Checksum Ethereum address of the chosen protocol")