"""
Persona registry.

Each persona's behaviour contract lives in a sibling `.md` file rather than a
Python string literal, so prompts are reviewable as prose, diffable in isolation,
and editable without touching code. Catalog data lives in
`app/domain/data/*.json` for the same reason.

The registry is also the single source of truth the frontend reads for its agent
roster (name, role, pitch, tags, accent), so the UI can never drift out of sync
with which personas the engine will actually accept.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

_PERSONA_DIR = Path(__file__).resolve().parent
_DATA_DIR = _PERSONA_DIR.parent / "data"


class PersonaId(StrEnum):
    """The personas the engine accepts. Validated at the API boundary, so an
    unknown id is a 422 rather than a silent fallback to the default agent."""

    NovaConnect_DEFAULT = "novaconnect_default"
    KEVIN_L1 = "kevin_l1"
    JESSIKA_PRESALES = "jessika_presales"


DEFAULT_PERSONA = PersonaId.NovaConnect_DEFAULT

# The demo operator is Indian; the agents talk in the caller's local time.
OPERATOR_TIMEZONE = ZoneInfo("Asia/Kolkata")


class AccountPool(StrEnum):
    """Which existing subscribers a persona may be handed as its caller."""

    NONE = "none"  # a prospect with no account (pre-sales)
    ANY = "any"  # any subscriber
    TECHNICAL = "technical"  # only subscribers whose line or router is not healthy


class Persona(BaseModel):
    """Everything both the engine and the UI need to know about one agent."""

    id: PersonaId
    display_name: str
    initial: str = Field(..., max_length=2)
    role: str = Field(..., description="One-line role, shown under the name in the UI.")
    pitch: str = Field(..., description="Two-sentence customer-facing pitch for the roster card.")
    tags: list[str] = Field(default_factory=list)
    accent: str = Field(
        ...,
        description="Design-token name the frontend maps to this agent's accent colour.",
    )
    template_file: str = Field(..., exclude=True)
    catalog_file: str | None = Field(default=None, exclude=True)
    account_pool: AccountPool = Field(
        default=AccountPool.ANY,
        exclude=True,
        description="Whose calls this agent takes: NONE means a cold prospect, no CRM record.",
    )
    greetings: list[str] = Field(
        default_factory=list,
        exclude=True,
        description="Opening lines for a caller we recognise. Placeholders: {name}, {daypart}.",
    )
    anonymous_greetings: list[str] = Field(
        default_factory=list,
        exclude=True,
        description="Opening lines when there is no name to use. Placeholder: {daypart}.",
    )
    tts_speaker: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "Bulbul voice for this persona; must be a name from "
            "app.core.config.BULBUL_V3_SPEAKERS. None falls back to the "
            "engine's global TTS_SPEAKER default."
        ),
    )


PERSONAS: dict[PersonaId, Persona] = {
    PersonaId.NovaConnect_DEFAULT: Persona(
        id=PersonaId.NovaConnect_DEFAULT,
        display_name="NovaConnect Agent",
        initial="D",
        role="General Triage · All Enquiries",
        pitch=(
            "Non-linear intent triage with no menu tree. Reads your account live and routes "
            "billing, faults, upgrades, or escalation from plain language alone."
        ),
        tags=["Billing", "Faults", "Upgrades"],
        accent="agent-default",
        template_file="novaconnect_default.md",
        account_pool=AccountPool.ANY,
        greetings=[
            "{daypart}, {name}! You've reached NovaConnect Telecoms. What can I help you with today?",
            "Hi {name}, thanks for calling NovaConnect. How can I help?",
            "Hello {name}, NovaConnect Telecoms here. What can I do for you?",
        ],
        anonymous_greetings=[
            "{daypart}! You've reached NovaConnect Telecoms. How can I help you today?",
            "Hello, thanks for calling NovaConnect. What can I do for you?",
        ],
    ),
    PersonaId.KEVIN_L1: Persona(
        id=PersonaId.KEVIN_L1,
        display_name="Kevin",
        initial="K",
        role="L1 Technical Support · Network & Air Fiber",
        pitch=(
            "Calm under pressure. Reads your router and L.O.S. light live, tells you what's "
            "actually wrong, and gives you a real E.T.A. instead of a script."
        ),
        tags=["Outages", "Slow speeds", "Router resets"],
        accent="agent-kevin",
        template_file="kevin_l1.md",
        account_pool=AccountPool.TECHNICAL,
        greetings=[
            "Hi {name}, this is Kevin from NovaConnect technical support. What's going on?",
            "{daypart}, {name}. Kevin here at NovaConnect support. How can I help?",
            "Hello {name}, Kevin from NovaConnect's technical team. Tell me what's happening.",
        ],
        anonymous_greetings=[
            "Hi, this is Kevin from NovaConnect technical support. What's going on?",
            "{daypart}. Kevin here at NovaConnect support. How can I help?",
        ],
        tts_speaker="aditya",
    ),
    PersonaId.JESSIKA_PRESALES: Persona(
        id=PersonaId.JESSIKA_PRESALES,
        display_name="Jessika",
        initial="J",
        role="Sales Consultant · Veloce Fiber & Mobile SIM",
        pitch=(
            "Runs discovery like a working rep: checks if fiber covers your city, sizes the "
            "right plan, and quotes the price with G.S.T. built in, no guessing games."
        ),
        tags=["Veloce Fiber", "Mobile SIM", "Vanity numbers"],
        accent="agent-jessika",
        template_file="jessika_presales.md",
        account_pool=AccountPool.NONE,
        anonymous_greetings=[
            "Hi there, this is Jessika from NovaConnect Telecoms. Thanks for stopping by. "
            "What are you looking for today?",
            "{daypart}! Jessika here at NovaConnect. What brought you our way today?",
            "Hello, I'm Jessika with NovaConnect Telecoms. What can I help you find today?",
        ],
        tts_speaker="priya",
    ),
}


def _render_service_catalog(catalog: dict) -> str:
    """Flattens a services catalog JSON document into the prose block a prompt
    injects via `{product_catalog}`.

    Shaped to mirror the client's own ClientServices document, so the next
    catalog update is a data edit, not a prompt rewrite.
    """
    lines: list[str] = []
    for service in catalog["services"]:
        regions = ", ".join(service["serviceable_regions"])
        lines.append(
            f"{service['name'].upper()} ({service['category']}) - serviceable in {regions}."
        )
        if features := service.get("features"):
            lines.append("Features: " + "; ".join(features) + ".")
        for plan in service["plans"]:
            if "plan_name" in plan:
                benefits = "; ".join(plan["benefits"])
                lines.append(
                    f"- {plan['plan_name']}: {plan['speed']}, "
                    f"Rs.{plan['price_per_30_days']} per 30 days, "
                    f"{plan['data_limit']}. {benefits}."
                )
            else:
                lines.append(
                    f"- {plan['plan_type']}: Rs.{plan['price']}, "
                    f"{plan['validity_days']} day validity, {plan['data']} data, "
                    f"{plan['voice']}, {plan['sms']}."
                )
        lines.append("")
    lines.append(catalog["tax_disclaimer"] + ". State this every time you quote a price.")
    return "\n".join(lines).strip()


@cache
def load_common_rules() -> str:
    """The rules every agent speaks under, prepended to each persona's own prompt."""
    return (_PERSONA_DIR / "_common.md").read_text(encoding="utf-8")


@cache
def load_template(persona_id: PersonaId) -> str:
    """Reads one persona's prompt template from disk. Cached per process."""
    persona = PERSONAS[persona_id]
    return (_PERSONA_DIR / persona.template_file).read_text(encoding="utf-8")


@cache
def load_catalog(filename: str) -> str:
    """Reads and flattens a catalog JSON document. Cached per process."""
    raw = json.loads((_DATA_DIR / filename).read_text(encoding="utf-8"))
    return _render_service_catalog(raw)


def daypart(now: datetime | None = None) -> str:
    """'Good morning' / 'Good afternoon' / 'Good evening' in the operator's timezone."""
    hour = (now or datetime.now(OPERATOR_TIMEZONE)).astimezone(OPERATOR_TIMEZONE).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def render_greeting(
    persona_id: PersonaId,
    *,
    first_name: str | None,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> str:
    """The agent's opening line: what it says before the caller has said anything.

    Scripted rather than generated. A greeting has to land in well under a second
    and must never wander, so it is picked from a few hand-written variants — the
    generated conversation takes over from the caller's first word. The name is
    all it volunteers: what the agent can see about the account is saved for
    after the caller says why they called.
    """
    persona = PERSONAS[persona_id]
    chooser = rng or random
    if first_name and persona.greetings:
        template = chooser.choice(persona.greetings)
    else:
        template = chooser.choice(persona.anonymous_greetings or persona.greetings)
    return template.format(name=first_name or "", daypart=daypart(now)).strip()


def get_persona(persona_id: PersonaId) -> Persona:
    return PERSONAS[persona_id]


def list_personas() -> list[Persona]:
    """Roster in display order, for the frontend's agent picker."""
    return list(PERSONAS.values())


def build_system_prompt(
    persona_id: PersonaId,
    *,
    reply_max_words: int,
    crm_block: str,
    now: datetime | None = None,
) -> str:
    """Renders the shared voice rules plus a persona's template with this turn's live context."""
    persona = PERSONAS[persona_id]
    template = load_common_rules() + "\n\n" + load_template(persona_id)
    local_now = (now or datetime.now(OPERATOR_TIMEZONE)).astimezone(OPERATOR_TIMEZONE)
    fields: dict[str, str] = {
        "reply_max_words": str(reply_max_words),
        "crm_block": crm_block,
        "local_time": local_now.strftime("%A, %I:%M %p").lstrip("0") + " IST",
    }
    if "{product_catalog}" in template:
        catalog_file = persona.catalog_file or "veloce_services.json"
        fields["product_catalog"] = load_catalog(catalog_file)
    return template.format(**fields)


def verify_templates() -> None:
    """Boot-time check that every persona's template exists and renders.

    A typo'd placeholder is a KeyError on the first live call otherwise — this
    turns it into a startup failure, where it belongs.
    """
    for persona_id in PERSONAS:
        build_system_prompt(persona_id, reply_max_words=15, crm_block="- probe\n")
