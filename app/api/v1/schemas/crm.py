"""Subscriber (CRM) schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator

from app.domain.models import (
    WRITABLE_CUSTOMER_FIELDS,
    CustomerRecord,
    LOSLightState,
    PaymentStatus,
    RouterStatus,
)


class CustomerOut(BaseModel):
    phone_number: str
    customer_name: str
    account_id: str
    active_plan: str
    plan_data_cap_gb: float
    plan_data_used_gb: float
    account_balance: float
    currency: str
    payment_status: PaymentStatus
    support_tier: str
    router_status: RouterStatus
    los_light: LOSLightState
    last_updated: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_usage_ratio(self) -> float:
        """Computed server-side so every client renders the same usage bar
        instead of each one re-deriving it (and disagreeing on cap == 0)."""
        if self.plan_data_cap_gb <= 0:
            return 0.0
        return min(self.plan_data_used_gb / self.plan_data_cap_gb, 1.0)

    @classmethod
    def from_domain(cls, record: CustomerRecord) -> CustomerOut:
        return cls(**record.model_dump())


class CustomerUpdateRequest(BaseModel):
    """Single-field write, mirroring how a real BSS exposes subscriber
    mutations — and so every change is individually auditable."""

    field: str = Field(..., examples=["router_status"])
    value: Any = Field(..., examples=["Online"])

    @field_validator("field")
    @classmethod
    def _must_be_writable(cls, value: str) -> str:
        if value not in WRITABLE_CUSTOMER_FIELDS:
            allowed = ", ".join(sorted(WRITABLE_CUSTOMER_FIELDS))
            raise ValueError(f"'{value}' is not writable. Allowed fields: {allowed}")
        return value
