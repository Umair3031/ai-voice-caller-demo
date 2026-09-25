"""
Seed subscriber data for the simulated BSS/CRM.

Kept separate from any store implementation so both the in-memory and Redis
backends materialize an identical starting registry, and so tests can assert
against one known fixture set.

The pool is the demo's cast. Each visitor is handed one of these at random, so
the set is deliberately varied: outages, degraded links, an account suspended
for non-payment that *looks* like an outage, an overdue bill, a customer about
to hit their data cap, and a couple with nothing wrong at all. The first record
is the stable fixture the tests and the operator docs refer to.
"""

from __future__ import annotations

from app.domain.models import (
    CustomerRecord,
    LOSLightState,
    PaymentStatus,
    RouterStatus,
    utcnow,
)

# Reference subscriber: router offline, red LOS light, account in good standing.
REFERENCE_MSISDN = "+919810012345"


def seed_customers() -> list[CustomerRecord]:
    now = utcnow()

    def record(
        phone: str,
        name: str,
        account: str,
        plan: str,
        cap: float,
        used: float,
        balance: float,
        payment_state: PaymentStatus,
        tier: str,
        router_state: RouterStatus,
        los_state: LOSLightState,
    ) -> CustomerRecord:
        return CustomerRecord(
            phone_number=phone,
            customer_name=name,
            account_id=account,
            active_plan=plan,
            plan_data_cap_gb=cap,
            plan_data_used_gb=used,
            account_balance=balance,
            currency="INR",
            payment_status=payment_state,
            support_tier=tier,
            router_status=router_state,
            los_light=los_state,
            last_updated=now,
        )

    prime = "NovaConnect Fibre Prime 100Mbps"
    essential = "NovaConnect Fibre Essential 50Mbps"
    business = "NovaConnect Fibre Business 200Mbps"
    pay, router, los = PaymentStatus, RouterStatus, LOSLightState

    return [
        # -- Connection problems ------------------------------------------------
        record(
            REFERENCE_MSISDN,
            "Rahul Sharma",
            "NovaConnect-IN-100234",
            prime,
            1000,
            412.6,
            0.0,
            pay.CURRENT,
            "Prime",
            router.OFFLINE,
            los.RED,
        ),
        record(
            "+919845678021",
            "Arjun Mehta",
            "NovaConnect-IN-100589",
            business,
            2000,
            1998.9,
            0.0,
            pay.GRACE_PERIOD,
            "Business",
            router.DEGRADED,
            los.BLINKING_RED,
        ),
        record(
            "+919820334455",
            "Meera Joshi",
            "NovaConnect-IN-100931",
            essential,
            500,
            288.4,
            0.0,
            pay.CURRENT,
            "Standard",
            router.DEGRADED,
            los.BLINKING_RED,
        ),
        record(
            "+919886115577",
            "Karthik Subramanian",
            "NovaConnect-IN-101144",
            business,
            2000,
            640.2,
            0.0,
            pay.CURRENT,
            "Business",
            router.REBOOTING,
            los.GREEN,
        ),
        record(
            "+919811776644",
            "Divya Patel",
            "NovaConnect-IN-101378",
            essential,
            500,
            301.7,
            -899.0,
            pay.OVERDUE,
            "Standard",
            router.OFFLINE,
            los.RED,
        ),
        # Suspended for non-payment: the internet is down, but the cause is the
        # bill, not the line. A good agent catches this before troubleshooting.
        record(
            "+919899221100",
            "Vikram Singh",
            "NovaConnect-IN-101502",
            essential,
            500,
            455.0,
            -2398.0,
            pay.SUSPENDED,
            "Standard",
            router.OFFLINE,
            los.OFF,
        ),
        # -- Billing and plan questions -----------------------------------------
        record(
            "+919833445566",
            "Sneha Iyer",
            "NovaConnect-IN-100812",
            prime,
            1000,
            520.3,
            -1499.0,
            pay.OVERDUE,
            "Prime",
            router.ONLINE,
            los.GREEN,
        ),
        record(
            "+919841002233",
            "Ananya Reddy",
            "NovaConnect-IN-101690",
            prime,
            1000,
            962.5,
            0.0,
            pay.CURRENT,
            "Prime",
            router.ONLINE,
            los.GREEN,
        ),
        # -- Nothing wrong -------------------------------------------------------
        record(
            "+919867543210",
            "Priya Nair",
            "NovaConnect-IN-101815",
            essential,
            500,
            120.7,
            0.0,
            pay.CURRENT,
            "Standard",
            router.ONLINE,
            los.GREEN,
        ),
        record(
            "+919812908877",
            "Imran Khan",
            "NovaConnect-IN-101923",
            prime,
            1000,
            233.0,
            500.0,
            pay.CURRENT,
            "Prime",
            router.ONLINE,
            los.GREEN,
        ),
    ]


def normalize_msisdn(phone_number: str) -> str:
    """Tolerant E.164 normalization so lookups survive dashes, spaces, and
    parentheses in whatever the caller or switch hands us."""
    cleaned = "".join(ch for ch in phone_number.strip() if ch.isdigit() or ch == "+")
    cleaned = "+" + cleaned.lstrip("+").replace("+", "")
    return cleaned
