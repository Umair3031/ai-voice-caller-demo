"""
CRM service.

A thin, auditable facade over whichever `CustomerStore` is wired in. Writes are
field-level rather than whole-record replacement, mirroring how a real telco
BSS exposes subscriber mutations — and so every state change (a router reboot,
a captured payment, a plan change) is traceable one field at a time.
"""

from __future__ import annotations

import random

from app.core.logging import get_logger
from app.domain.models import CustomerRecord, PaymentStatus, RouterStatus
from app.domain.personas.registry import AccountPool
from app.infra.seed import normalize_msisdn, seed_customers
from app.infra.state import CustomerStore

logger = get_logger("services.crm")


class CrmService:
    def __init__(self, store: CustomerStore) -> None:
        self._store = store

    async def get_customer(self, phone_number: str) -> CustomerRecord | None:
        msisdn = normalize_msisdn(phone_number)
        record = await self._store.get(msisdn)
        if record is None:
            logger.info("CRM lookup miss", extra={"msisdn": msisdn})
        else:
            logger.info("CRM lookup hit", extra={"msisdn": msisdn, "account_id": record.account_id})
        return record

    async def pick_demo_subscriber(
        self, pool: AccountPool, *, rng: random.Random | None = None
    ) -> CustomerRecord | None:
        """A random subscriber from the demo cast, for an agent that takes existing
        customers. None for a pre-sales agent, whose caller is a prospect.

        Drawn from the seeded cast only (not whatever else the store might hold), and
        for the technical agent only from subscribers whose line is actually unwell,
        so Kevin is never handed a caller with nothing for him to look at.
        """
        if pool is AccountPool.NONE:
            return None
        cast = {record.phone_number for record in seed_customers()}
        candidates = [r for r in await self._store.list_all() if r.phone_number in cast]
        if pool is AccountPool.TECHNICAL:
            candidates = [r for r in candidates if r.router_status is not RouterStatus.ONLINE]
        if not candidates:
            return None
        return (rng or random).choice(candidates)

    async def list_customers(self) -> list[CustomerRecord]:
        return await self._store.list_all()

    async def update_field(self, phone_number: str, field: str, value: object) -> CustomerRecord:
        msisdn = normalize_msisdn(phone_number)
        updated = await self._store.update_field(msisdn, field, value)
        logger.info(
            "CRM write", extra={"msisdn": msisdn, "field": field, "value": str(value)[:100]}
        )
        return updated


def render_crm_block(customer: CustomerRecord | None) -> str:
    """Projects a subscriber record into the LIVE CRM CONTEXT block a persona
    prompt injects. The only place record fields become prompt text."""
    if customer is None:
        return (
            "- No subscriber record matched this caller's number.\n"
            "- Treat this caller as unverified until identity is confirmed.\n"
        )
    return (
        f"- Customer name: {customer.customer_name}\n"
        f"- Account ID: {customer.account_id}\n"
        f"- Active plan: {customer.active_plan}\n"
        f"- Data usage: {customer.plan_data_used_gb:.1f}GB of {customer.plan_data_cap_gb:.1f}GB\n"
        f"- Account balance: {customer.account_balance:.2f} {customer.currency}\n"
        f"- Payment status: {customer.payment_status}\n"
        f"- Router status (live telemetry): {customer.router_status}\n"
        f"- LOS light (live telemetry): {customer.los_light}\n"
    )


def suggest_opener(customer: CustomerRecord) -> str:
    """One line telling the visitor what this demo subscriber is going through, so a
    random caller identity does not leave them guessing what to say."""
    if customer.payment_status is PaymentStatus.SUSPENDED:
        return "My internet stopped working."
    if customer.router_status is RouterStatus.OFFLINE:
        return "My internet isn't working."
    if customer.router_status is RouterStatus.DEGRADED:
        return "My internet has been really slow."
    if customer.router_status is RouterStatus.REBOOTING:
        return "My router keeps restarting."
    if customer.payment_status in (PaymentStatus.OVERDUE, PaymentStatus.GRACE_PERIOD):
        return "Why does my account show a balance due?"
    if customer.data_usage_ratio >= 0.9:
        return "I'm almost out of data this month."
    return "I'd like to check my plan and usage."
