"""
crm_service.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Simulated BSS/CRM Data Plane
------------------------------------------------------------------------------
This module stands in for a real telecom Business Support System (BSS) /
Customer Relationship Management (CRM) integration (e.g. Amdocs, Netcracker,
or a homegrown subscriber-management backend). In production this class
would be swapped for a client that calls out to those systems over
gRPC/REST/SOAP; the async function signatures below are deliberately shaped
to match that eventual integration so the rest of the stack (agent_brain,
main) never needs to change.

Data is held in an in-memory registry seeded with a realistic Nigerian
(+234) test subscriber, including live "hardware telemetry" fields
(router power state, LOS optical light) that the conversational agent uses
to ground network-fault troubleshooting in actual (simulated) field state.

All access is guarded by an `asyncio.Lock` so concurrent IVR sessions can
safely read/write shared subscriber state without corrupting it.
==============================================================================
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("novaconnect_ivr.crm_service")


# ------------------------------------------------------------------------------
# Enumerations — mirror the finite state sets a real BSS/CRM would expose.
# ------------------------------------------------------------------------------
class PaymentStatus(str, Enum):
    CURRENT = "Current"
    OVERDUE = "Overdue"
    SUSPENDED = "Suspended"
    GRACE_PERIOD = "Grace Period"


class RouterStatus(str, Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    REBOOTING = "Rebooting"
    DEGRADED = "Degraded"


class LOSLightState(str, Enum):
    GREEN = "Green"
    RED = "Red"
    BLINKING_RED = "Blinking Red"
    OFF = "Off"


# ------------------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------------------
class CustomerRecord(BaseModel):
    """Canonical subscriber record as it would be materialized from a BSS/CRM."""

    phone_number: str = Field(..., description="MSISDN in E.164 format, e.g. +2348031234567.")
    customer_name: str
    account_id: str
    active_plan: str
    plan_data_cap_gb: float
    plan_data_used_gb: float
    account_balance: float
    currency: str = "NGN"
    payment_status: PaymentStatus
    support_tier: str = "Standard"
    router_status: RouterStatus
    los_light: LOSLightState
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(use_enum_values=True)


# ------------------------------------------------------------------------------
# Seed data — realistic NovaConnect Telecoms Nigerian (+234) test subscriber base.
# ------------------------------------------------------------------------------
def _seed_registry() -> dict[str, CustomerRecord]:
    """
    Builds the initial in-memory subscriber registry.

    Includes one primary test line provisioned with a premium +234 (Nigeria)
    MSISDN prefix, plus two supporting records so lookups, plan variety, and
    fault-state variety can all be exercised during demos and integration
    tests.
    """
    now = datetime.now(timezone.utc)
    records = [
        CustomerRecord(
            phone_number="+2348031234567",
            customer_name="Adaeze Okafor",
            account_id="NovaConnect-NG-100234",
            active_plan="NovaConnect Fibre Prime 100Mbps",
            plan_data_cap_gb=1000.0,
            plan_data_used_gb=812.4,
            account_balance=-4500.00,
            currency="NGN",
            payment_status=PaymentStatus.OVERDUE,
            support_tier="Prime",
            router_status=RouterStatus.OFFLINE,
            los_light=LOSLightState.RED,
            last_updated=now,
        ),
        CustomerRecord(
            phone_number="+2348052239981",
            customer_name="Tunde Balogun",
            account_id="NovaConnect-NG-100589",
            active_plan="NovaConnect Fibre Essential 50Mbps",
            plan_data_cap_gb=500.0,
            plan_data_used_gb=120.7,
            account_balance=1500.00,
            currency="NGN",
            payment_status=PaymentStatus.CURRENT,
            support_tier="Standard",
            router_status=RouterStatus.ONLINE,
            los_light=LOSLightState.GREEN,
            last_updated=now,
        ),
        CustomerRecord(
            phone_number="+2347015567342",
            customer_name="Chiamaka Nwosu",
            account_id="NovaConnect-NG-100812",
            active_plan="NovaConnect Fibre Business 200Mbps",
            plan_data_cap_gb=2000.0,
            plan_data_used_gb=1998.9,
            account_balance=0.00,
            currency="NGN",
            payment_status=PaymentStatus.GRACE_PERIOD,
            support_tier="Business",
            router_status=RouterStatus.DEGRADED,
            los_light=LOSLightState.BLINKING_RED,
            last_updated=now,
        ),
    ]
    return {record.phone_number: record for record in records}


class CustomerNotFoundError(Exception):
    """Raised when a lookup MSISDN has no matching subscriber record."""


class InvalidFieldUpdateError(Exception):
    """Raised when a write targets a field that does not exist on CustomerRecord."""


class CRMService:
    """
    Async-safe facade over the simulated subscriber registry.

    Mirrors the shape of a real BSS/CRM client: normalized lookups by MSISDN,
    and narrow, auditable field-level writes (rather than free-form record
    replacement) so telemetry state changes (router reboots, payments, plan
    changes) are traceable one field at a time, just as they would be against
    a real telco backend's field-level update APIs.
    """

    def __init__(self) -> None:
        self._registry: dict[str, CustomerRecord] = _seed_registry()
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize_msisdn(phone_number: str) -> str:
        """Strips whitespace/formatting so lookups are tolerant of dashes/spaces."""
        cleaned = "".join(ch for ch in phone_number.strip() if ch.isdigit() or ch == "+")
        if not cleaned.startswith("+"):
            cleaned = f"+{cleaned}"
        return cleaned

    async def get_customer_by_phone(self, phone_number: str) -> Optional[CustomerRecord]:
        """
        Looks up a subscriber by MSISDN.

        Returns a defensive copy so callers (e.g. agent_brain building a
        prompt context) cannot mutate shared state by accident; all writes
        must go through `update_customer_field`.
        """
        msisdn = self._normalize_msisdn(phone_number)
        async with self._lock:
            record = self._registry.get(msisdn)
            if record is None:
                logger.info("CRM lookup miss for MSISDN=%s", msisdn)
                return None
            logger.info("CRM lookup hit for MSISDN=%s (account=%s)", msisdn, record.account_id)
            return deepcopy(record)

    async def update_customer_field(
        self, phone_number: str, field: str, value: Any
    ) -> CustomerRecord:
        """
        Writes a single field back to the subscriber record (e.g. flipping
        `router_status` to ONLINE after a guided reboot, or advancing
        `payment_status` after a payment is captured).

        Raises:
            CustomerNotFoundError: if no record exists for the given MSISDN.
            InvalidFieldUpdateError: if `field` is not a real record attribute.
        """
        msisdn = self._normalize_msisdn(phone_number)
        async with self._lock:
            record = self._registry.get(msisdn)
            if record is None:
                raise CustomerNotFoundError(f"No subscriber found for MSISDN {msisdn}")
            if field not in CustomerRecord.model_fields:
                raise InvalidFieldUpdateError(f"'{field}' is not a valid CustomerRecord field")

            updated = record.model_copy(
                update={field: value, "last_updated": datetime.now(timezone.utc)}
            )
            self._registry[msisdn] = updated
            logger.info(
                "CRM write MSISDN=%s field=%s value=%s", msisdn, field, value
            )
            return deepcopy(updated)

    async def list_customers(self) -> list[CustomerRecord]:
        """Returns defensive copies of every subscriber record (used by the dashboard)."""
        async with self._lock:
            return [deepcopy(record) for record in self._registry.values()]


# Process-wide singleton, mirroring the `config.settings` pattern so every
# module shares one consistent view of subscriber state.
crm_service = CRMService()
