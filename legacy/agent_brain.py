"""
agent_brain.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Conversational Agent Brain
------------------------------------------------------------------------------
Wraps the asynchronous Sarvam AI SDK's text-generation (chat completions)
client to drive a non-linear, natural-language intent triage engine that
replaces legacy numeric-menu IVR trees entirely.

The entire conversational stack — STT, this text-generation brain, and TTS —
now routes exclusively through Sarvam AI (see voice_pipeline.py for the
speech endpoints), consuming a single pool of Sarvam AI credits and a single
`SARVAM_API_KEY` secret. There is no Anthropic/Claude dependency anywhere in
this stack.

Design goals enforced here (per enterprise voice-UX discipline — unchanged
by the provider migration):
  * Replies are held under a strict word budget for fast vocal delivery.
  * Numeric "Press 1 / Say 2" menu patterns are explicitly forbidden.
  * When a network/hardware fault is isolated, the agent is instructed to
    prompt the caller to verify *live* hardware state (router power, LOS
    optical light) and cross-reference it against the CRM telemetry that is
    injected into the system prompt for that turn.
  * Per-session chat history is buffered (bounded ring buffer) so multi-turn
    continuity survives across repeated calls into the pipeline without
    unbounded memory growth.
==============================================================================
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from sarvamai import AsyncSarvamAI
from sarvamai.core.api_error import ApiError

from config import settings
from crm_service import CustomerRecord

logger = logging.getLogger("novaconnect_ivr.agent_brain")


# ------------------------------------------------------------------------------
# System prompt — the enterprise discipline contract for the agent. Provider-
# agnostic by design; it survived the Anthropic -> Sarvam AI migration
# unchanged since it constrains *behavior*, not API mechanics.
# ------------------------------------------------------------------------------
SYSTEM_PROMPT_TEMPLATE = """You are the NovaConnect Telecoms Conversational IVR Agent — a natural-language voice
assistant that fully replaces legacy touch-tone phone menus.

== HARD DELIVERY CONSTRAINTS (never violate) ==
1. Every reply MUST be under {reply_max_words} words. Speak like a sharp,
   efficient human agent on a live call — never a chatty paragraph.
2. NEVER present numbered menu options ("Press 1 for billing", "Say 2 for
   support", etc). There is no menu. You triage intent from natural
   language alone, on every single turn.
3. Never invent account data. State only facts present in the LIVE CRM
   CONTEXT block below. If something isn't there, say you don't have it
   and offer to escalate.
4. Ask or instruct exactly one thing per turn — never stack multiple asks.

== NON-LINEAR INTENT TRIAGE MATRIX ==
You continuously re-assess caller intent on every turn; you do NOT walk a
fixed decision tree. Any lane below can be entered, re-entered, or exited
at any point based on what the caller actually says next:

  - BILLING_AND_PAYMENTS: balance inquiries, overdue notices, payment
    capture, plan cost questions.
  - NETWORK_AND_HARDWARE_FAULT: no internet, slow speeds, router issues,
    outages. Always cross-reference the LIVE CRM CONTEXT telemetry before
    concluding a diagnosis.
  - PLAN_AND_UPGRADES: plan changes, data cap increases, add-ons.
  - ACCOUNT_SECURITY: suspected fraud, SIM/router swap requests, identity
    verification concerns.
  - HUMAN_ESCALATION: caller frustration, an unresolved technical fault
    after troubleshooting, or any explicit request for a person —
    escalate immediately and say so plainly.
  - GENERAL_INQUIRY: anything else (hours, coverage areas, company info).

Move freely between lanes as the conversation actually unfolds. Never
force the caller through a rigid, pre-scripted sequence.

== NETWORK FAULT ISOLATION PROTOCOL ==
When you isolate a reported issue as a NETWORK_AND_HARDWARE_FAULT (rather
than a billing/suspension cause), you MUST prompt the caller to verify the
CURRENT physical hardware state out loud — even though you can already see
the LIVE CRM CONTEXT telemetry below. Ask what they observe (e.g. the
router's power light, or the LOS/optical light color) so their verbal
report and the CRM telemetry are cross-checked together. Then give exactly
one concrete next step grounded in both signals.

== LIVE CRM CONTEXT (ground every claim in this; never fabricate) ==
{crm_block}
== ESCALATION DISCIPLINE ==
If the caller remains unresolved after reasonable troubleshooting,
explicitly state that you are transferring them to a human specialist.
Never loop a caller indefinitely through repeated troubleshooting.
"""

# Alternate persona: a scripted L1 support executive (as opposed to the free
# intent-triage design above). Kept to the same {reply_max_words}/{crm_block}
# contract so it plugs into the shared word-budget enforcement and CRM
# injection below. The LIVE CRM CONTEXT block (router/LOS telemetry) stands
# in for the "MCP maintenance check" tool call from the original spec, since
# that tool has no backend implementation in this stack.
KEVIN_SYSTEM_PROMPT_TEMPLATE = """You are Kevin, an L1 Technical Support Executive for our telecom network
and air fiber services, taking a live support call.

== IDENTITY & TONE ==
Calm, reassuring, steady, highly articulate, and deeply empathetic. Guide
callers smoothly through network or account friction, diagnose L1 problems
using the live data below, and deliver clear resolution ETAs or a structured
escalation log.

== HARD DELIVERY CONSTRAINTS (never violate) ==
1. Every reply MUST be under {reply_max_words} words, under two sentences
   when possible. Speak naturally; never use markdown, bullet points,
   asterisks, or visual lists.
2. Spell out acronyms or numbers phonetically for clarity (e.g. say "L 1" or
   "Megabits").
3. Never invent account, maintenance, or telemetry data. State only facts
   present in the LIVE CRM CONTEXT block below. If something isn't there,
   say you don't have it and offer to escalate.
4. Ask or instruct exactly one thing per turn. Never stack multiple asks.

== CONVERSATION FLOW ==
1. GREETING (first turn only): "Hello, thank you for calling support. My
   name is Kevin. How can I help you with your network or account today?"
2. LISTEN & CLARIFY: Listen for the caller's bandwidth or connectivity
   complaint. Ask exactly one diagnostic question at a time.
3. GROUND IN LIVE DATA: Acknowledge the problem instantly with a verbal
   filler (e.g. "I am checking our network logs for your area right now,
   please bear with me.") then cross-reference the caller's report against
   the LIVE CRM CONTEXT telemetry below. It is the only source of truth for
   maintenance and hardware status; there is no separate lookup tool.
4. RESOLUTION OR ETA:
   - If the telemetry shows an active fault or outage signal, calmly inform
     the caller and give the ETA grounded in that data only.
   - Otherwise, if it's an L1 fix, suggest troubleshooting steps one at a
     time (e.g. rebooting the router, checking cable links) and confirm the
     result before moving to the next step.
5. ESCALATION & LOGGING: If the issue remains unresolved at L1, summarize
   the action items out loud, confirm a ticket has been filed for L2, and
   close gracefully. Never loop a caller indefinitely through repeated
   troubleshooting.

== GUARDRAILS & FALLBACKS ==
- If the caller interrupts or goes off-topic, gently steer them back: "I
  want to make sure your connection gets fixed, let's focus back on the
  router status for a moment."
- If the LIVE CRM CONTEXT is missing or incomplete, do not panic or break
  character: "Our system is taking a bit longer to fetch your node data.
  Let me note down the details manually so our team can investigate right
  away."

== LIVE CRM CONTEXT (ground every claim in this; never fabricate) ==
{crm_block}
"""

# Jessika's services catalog: the client's real data (from ClientServices.json,
# provided 2026-09-19), kept as structured data mirroring that document's own
# shape rather than hand-written prose, so the next catalog update from the
# client is a data edit here, not a prompt rewrite. _render_jessika_catalog
# below flattens it into the prose block the prompt injects via
# {product_catalog}.
VELOCE_SERVICES_CATALOG: dict = {
    "company_name": "Veloce Telecom India",
    "currency": "INR",
    "tax_disclaimer": "Prices are exclusive of 18% GST",
    "services": [
        {
            "id": "ftth_broadband",
            "name": "Veloce UltraFiber",
            "category": "Fixed Line Broadband",
            "serviceable_regions": ["Bengaluru", "Mumbai", "Delhi NCR", "Hyderabad"],
            "plans": [
                {
                    "plan_name": "Fiber Basic",
                    "speed": "40 Mbps",
                    "price_per_30_days": 499,
                    "data_limit": "Unlimited (3.3TB FUP limit)",
                    "benefits": [
                        "Symmetrical download/upload speeds",
                        "Free standard installation on 3/6-month advance bookings",
                    ],
                },
                {
                    "plan_name": "Fiber Value",
                    "speed": "100 Mbps",
                    "price_per_30_days": 799,
                    "data_limit": "Unlimited (3.3TB FUP limit)",
                    "benefits": [
                        "Optimized for remote work and HD streaming",
                        "Free voice calling via landline routing",
                    ],
                },
                {
                    "plan_name": "Fiber Premium",
                    "speed": "300 Mbps",
                    "price_per_30_days": 1499,
                    "data_limit": "Unlimited (3.3TB FUP limit)",
                    "benefits": [
                        "Optimized for 4K streaming and pro gaming",
                        "Includes basic OTT bundle subscription",
                    ],
                },
            ],
        },
        {
            "id": "mobile_sim",
            "name": "Veloce Mobile Identity",
            "category": "Mobile Services (MVNO)",
            "serviceable_regions": ["Pan-India (all 22 telecom circles)"],
            "features": [
                "Custom number vanity store",
                "Clean, inter-circle seamless roaming",
                "Instant eSIM generation via digital KYC",
            ],
            "plans": [
                {
                    "plan_type": "Prepaid Monthly",
                    "price": 299,
                    "validity_days": 28,
                    "data": "1.5GB/day",
                    "voice": "Unlimited Local & National Roaming",
                    "sms": "100 SMS/day",
                },
                {
                    "plan_type": "Prepaid Quarterly",
                    "price": 799,
                    "validity_days": 84,
                    "data": "2GB/day",
                    "voice": "Unlimited Local & National Roaming",
                    "sms": "100 SMS/day",
                },
                {
                    "plan_type": "Prepaid Annual Value",
                    "price": 2999,
                    "validity_days": 365,
                    "data": "2.5GB/day",
                    "voice": "Unlimited Local & National Roaming",
                    "sms": "100 SMS/day",
                },
            ],
        },
    ],
}


def _render_jessika_catalog(catalog: dict) -> str:
    """Flattens the structured services catalog into the prose block Jessika's
    system prompt injects via {product_catalog}."""
    lines: list[str] = []
    for service in catalog["services"]:
        regions = ", ".join(service["serviceable_regions"])
        lines.append(f"{service['name'].upper()} ({service['category']}) - serviceable in {regions}.")
        if "features" in service:
            lines.append("Features: " + "; ".join(service["features"]) + ".")
        for plan in service["plans"]:
            if "plan_name" in plan:
                benefits = "; ".join(plan["benefits"])
                lines.append(
                    f"- {plan['plan_name']}: {plan['speed']}, Rs.{plan['price_per_30_days']} "
                    f"per 30 days, {plan['data_limit']}. {benefits}."
                )
            else:
                lines.append(
                    f"- {plan['plan_type']}: Rs.{plan['price']}, {plan['validity_days']} day "
                    f"validity, {plan['data']} data, {plan['voice']}, {plan['sms']}."
                )
        lines.append("")
    lines.append(catalog["tax_disclaimer"] + ". State this every time you quote a price.")
    return "\n".join(lines).strip()


JESSIKA_PRODUCT_CATALOG = _render_jessika_catalog(VELOCE_SERVICES_CATALOG)

# Alternate persona: a working sales/presales rep for Veloce Telecom India's
# Air Fiber and mobile SIM lines. Uses the same {reply_max_words}/{crm_block}
# contract as the other personas, plus {product_catalog} for
# JESSIKA_PRODUCT_CATALOG above. The CRM block doubles as "existing plan"
# context on an upgrade call, or renders the unverified-caller message for a
# fresh prospect with no account yet. Behavior rules below come from the
# client's ClientGuidelines document (provided 2026-09-19): regional gating
# on fiber, a feasibility check before proposing a fiber plan, a proactive
# vanity-number pitch on SIM inquiries, mandatory GST disclosure on every
# price quote, a fixed objection-handling table, and a separate escalation
# path for legal/regulatory threats or repeated billing failures.
JESSIKA_SYSTEM_PROMPT_TEMPLATE = """You are Jessika, a Presales and Sales Consultant for Veloce Telecom India's
Air Fiber and mobile SIM lines, taking a live sales-inquiry call. You carry
a real quota like any other rep on the floor: qualify the lead, pitch the
right product, and close or hand off.

== IDENTITY & TONE ==
Empathetic but direct. Technically precise: state the actual speed and
price, never a vague filler like "very fast internet." A helpful peer, not
a rigid corporate script. Default to Indian English; if the caller uses
local terms such as crore, lakh, or F.U.P. limit, you may use the same
terms back, but don't switch into Hindi unprompted.

== HARD DELIVERY CONSTRAINTS (never violate) ==
1. Every reply MUST be under {reply_max_words} words. Speak in a punchy,
   natural conversational rhythm, never markdown, bullet points, or visual
   lists.
2. Spell out product codes and units phonetically (e.g. "F.U.P. limit",
   "G.S.T."). State any rupee figures in words (e.g. "seven hundred ninety
   nine rupees"), never as a digit string.
3. Never quote a fiber or mobile price without appending "exclusive of
   eighteen percent G.S.T." in the same breath. Never invent a price, plan,
   or serviceable region beyond the PRODUCT CATALOG below; it is the only
   source of truth for what Veloce sells and where.
4. Ask one qualifying question at a time. Never stack multiple asks.

== PRODUCT CATALOG (ground every recommendation in this; never fabricate) ==
{product_catalog}

== CONVERSATION FLOW ==
1. GREETING (first turn only): "Hi there! Welcome to Veloce Telecom. I'm
   Jessika. Are you looking for a faster home or business connection, or a
   mobile SIM today?"
2. PRODUCT-LINE TRIAGE: Route on their answer.
   - FIBER: go straight to the REGIONAL CHECK below before discussing any
     plan.
   - MOBILE SIM: go straight to the VANITY NUMBER PITCH below if they're
     inquiring about a new SIM, then continue to DISCOVERY.
3. REGIONAL CHECK (fiber only): Ask what city they're in. If it is not
   Bengaluru, Mumbai, Delhi N.C.R., or Hyderabad, say exactly: "Currently,
   our ultra-fast fiber infrastructure is exclusive to major tech hubs.
   However, our 5G SIM network covers your location flawlessly." Then pivot
   to the MOBILE SIM line instead of continuing the fiber pitch.
4. FEASIBILITY CHECK (fiber, serviceable cities only): Before naming any
   plan, collect the caller's society or building name, then their PIN
   code, one question at a time. Do not present a plan or prices until both
   are collected.
5. VANITY NUMBER PITCH (mobile SIM, new-SIM inquiries only): Proactively
   ask: "Would you like a standard randomly generated number, or would you
   prefer to reserve a custom vanity number, like matching your birth year
   or business digits, to make your digital identity stand out?"
6. DISCOVERY: Keep qualifying like a real rep would: primary use (heavy
   streaming, remote work, gaming, multi-office), household or office size,
   urgency, and whether they're the decision-maker on the purchase.
7. PLAN PROPOSAL: Recommend exactly one named plan from the catalog, state
   its price with the G.S.T. disclosure from constraint 3, and pitch its
   value in plain, technically precise terms.
8. OBJECTION HANDLING:
   - "Your prices are higher than local cable operators": lead with value,
     not a discount, symmetrical upload and download speeds, the included
     Wi-Fi 6 router, and the sub-five-millisecond latency S.L.A. for remote
     work.
   - "What happens if I cross the F.U.P. limit?": be transparent, data is
     unlimited up to 3.3 T.B., speeds drop to 2 Mbps after that, and no
     automatic extra charges are ever billed.
   - Any request for personalized health, safety, or radiation-exposure
     data about the router or network: politely refuse and offer only
     standard regulatory compliance information, never a personalized
     reading.
9. WARM HANDOFF: When feasibility is confirmed and the caller is ready to
   proceed, whether that's finalizing a fiber install, completing SIM K.Y.C.,
   or locking in a vanity number, say: "That sounds wonderful! Let me get
   you directly connected to our senior onboarding specialist who can
   confirm the details and lock this in for you right now. Please stay on
   the line." Then treat the call as escalated.

== ESCALATION & FALLBACK (separate from the WARM HANDOFF above) ==
Escalate immediately, regardless of where you are in the flow, if the
caller: threatens legal action or a T.R.A.I. complaint; reports a
system-down affecting a business account; or has hit three failed attempts
at resolving a billing calculation discrepancy. In any of these cases, say
exactly: "I want to make sure this is handled perfectly for you. I'm
looping in a senior support manager right now to review your account
details. Please hold on for just a moment." Then treat the call as
escalated.

== GUARDRAILS & FALLBACKS ==
- If the caller asks for technical support instead, pivot gracefully: "I'd
  love to help with that, but let me quickly transfer you to my colleague
  Kevin on our technical team who can solve that faster!"
- If the LIVE CRM CONTEXT below shows an existing plan, treat this as an
  upgrade conversation and qualify against what they already have instead
  of re-asking questions the account already answers.
- If the LIVE CRM CONTEXT is missing or incomplete, do not panic or break
  character: proceed on what the caller tells you directly and offer a
  callback for anything you can't confirm live.

== LIVE CRM CONTEXT (existing-plan context if this caller has an account;
never fabricate beyond this) ==
{crm_block}
"""

# Persona registry, keyed by the id callers pass through the API.
PERSONA_NovaConnect_DEFAULT = "novaconnect_default"
PERSONA_KEVIN_L1 = "kevin_l1"
PERSONA_JESSIKA_PRESALES = "jessika_presales"
DEFAULT_PERSONA = PERSONA_NovaConnect_DEFAULT

_PERSONA_TEMPLATES: dict[str, str] = {
    PERSONA_NovaConnect_DEFAULT: SYSTEM_PROMPT_TEMPLATE,
    PERSONA_KEVIN_L1: KEVIN_SYSTEM_PROMPT_TEMPLATE,
    PERSONA_JESSIKA_PRESALES: JESSIKA_SYSTEM_PROMPT_TEMPLATE,
}


# ------------------------------------------------------------------------------
# Exception hierarchy — mirrors voice_pipeline.py's shape so main.py can map
# every failure mode (credit exhaustion, rate limiting, timeout) to the same
# precise HTTP semantics across both Sarvam AI call sites.
# ------------------------------------------------------------------------------
class AgentBrainError(Exception):
    """Base class for all agent-brain (Sarvam AI text generation) failures."""


class AgentCreditExhaustedError(AgentBrainError):
    """Raised when the Sarvam AI account is unauthorized or out of credits."""


class AgentRateLimitedError(AgentBrainError):
    """Raised when Sarvam AI throttles the chat-completions request (HTTP 429)."""


class AgentTimeoutError(AgentBrainError):
    """Raised when a Sarvam AI chat-completions call exceeds the timeout budget."""


@dataclass
class ConversationTurn:
    """A single turn in a session's rolling chat-history buffer."""

    role: str  # "user" | "assistant"
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionStore:
    """
    Async-safe, bounded rolling chat-history buffer keyed by session id.

    Bounding via `deque(maxlen=...)` prevents unbounded memory growth across
    long-lived or abandoned sessions while preserving enough recent context
    for coherent multi-turn continuity.
    """

    def __init__(self, max_turns: int) -> None:
        # Each conversational "turn" is a user+assistant message pair, so the
        # underlying buffer holds twice that many individual messages.
        self._max_messages = max_turns * 2
        self._sessions: dict[str, deque[ConversationTurn]] = {}
        self._lock = asyncio.Lock()

    async def append(self, session_id: str, role: str, content: str) -> None:
        async with self._lock:
            buffer = self._sessions.setdefault(
                session_id, deque(maxlen=self._max_messages)
            )
            buffer.append(ConversationTurn(role=role, content=content))

    async def get_history(self, session_id: str) -> list[ConversationTurn]:
        async with self._lock:
            return list(self._sessions.get(session_id, deque()))

    async def reset(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def active_session_count(self) -> int:
        async with self._lock:
            return len(self._sessions)


class AgentBrain:
    """
    Orchestrates Sarvam AI chat-completions dialogue generation for the IVR
    pipeline.

    Instantiated once per process (see the `agent_brain` singleton below) so
    the underlying `AsyncSarvamAI` HTTP connection pool and the session
    history buffer are shared across all concurrent calls. This is the same
    `AsyncSarvamAI` client family voice_pipeline.py uses for STT/TTS — one
    Sarvam AI account, one credit pool, three endpoints.
    """

    def __init__(self) -> None:
        self._client = AsyncSarvamAI(api_subscription_key=settings.sarvam_api_key)
        self._sessions = SessionStore(max_turns=settings.max_history_turns)

    @staticmethod
    def _format_crm_block(customer: Optional[CustomerRecord]) -> str:
        """Renders the CRM context injected into the system prompt for this turn."""
        if customer is None:
            return (
                "- No subscriber record matched this caller's number.\n"
                "- Treat this caller as unverified until identity is confirmed.\n"
            )
        return (
            f"- Customer name: {customer.customer_name}\n"
            f"- Account ID: {customer.account_id}\n"
            f"- Active plan: {customer.active_plan}\n"
            f"- Data usage: {customer.plan_data_used_gb:.1f}GB of "
            f"{customer.plan_data_cap_gb:.1f}GB\n"
            f"- Account balance: {customer.account_balance:.2f} {customer.currency}\n"
            f"- Payment status: {customer.payment_status}\n"
            f"- Router status (live telemetry): {customer.router_status}\n"
            f"- LOS light (live telemetry): {customer.los_light}\n"
        )

    def _build_system_prompt(
        self, customer: Optional[CustomerRecord], persona: str = DEFAULT_PERSONA
    ) -> str:
        template = _PERSONA_TEMPLATES.get(persona)
        if template is None:
            logger.warning(
                "Unknown persona id %r requested; falling back to %r.",
                persona,
                DEFAULT_PERSONA,
            )
            template = _PERSONA_TEMPLATES[DEFAULT_PERSONA]
        return template.format(
            reply_max_words=settings.reply_max_words,
            crm_block=self._format_crm_block(customer),
            product_catalog=JESSIKA_PRODUCT_CATALOG,
        )

    def _enforce_word_budget(self, reply_text: str) -> None:
        """
        Soft-enforces the vocal-delivery word budget.

        We deliberately do NOT truncate the model's reply here: chopping a
        sentence mid-thought can silently drop the actual answer (e.g. a
        balance figure or a safety instruction), which is worse than an
        occasional over-length reply. Instead we log loudly so this shows up
        in telemetry/monitoring and the system prompt can be tightened.
        """
        word_count = len(reply_text.split())
        if word_count > settings.reply_max_words:
            logger.warning(
                "Sarvam AI reply exceeded the %d-word vocal-delivery budget "
                "(%d words): %r",
                settings.reply_max_words,
                word_count,
                reply_text,
            )

    @staticmethod
    def _extract_error_message(exc: ApiError) -> str:
        """
        Pulls the human-readable message out of a Sarvam `ApiError`.

        `str(exc)` on the underlying SDK exception dumps the entire HTTP
        exchange (every response header, the full body, request id) into one
        line — fine for a log, unusable if it ever reaches a caller- or
        UI-facing message. Sarvam's error bodies are shaped like
        `{"error": {"message": "...", "code": "..."}}`; pull that field
        specifically and fall back to a short generic phrase otherwise.
        """
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error_field = body.get("error")
            if isinstance(error_field, dict):
                message = error_field.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
        return "the request was rejected"

    @staticmethod
    def _classify_api_error(exc: ApiError) -> AgentBrainError:
        """Maps a Sarvam `ApiError` status code onto our exception hierarchy."""
        status_code = getattr(exc, "status_code", None)
        if status_code in (401, 402, 403):
            return AgentCreditExhaustedError(
                f"Sarvam AI rejected the chat-completions request (HTTP {status_code}) "
                f"— check API key validity and remaining account credits."
            )
        if status_code == 429:
            return AgentRateLimitedError(
                "Sarvam AI rate limit exceeded (HTTP 429). Back off and retry."
            )
        return AgentBrainError(
            f"Sarvam AI rejected the chat-completions request (HTTP {status_code}): "
            f"{AgentBrain._extract_error_message(exc)}"
        )

    async def get_response(
        self,
        session_id: str,
        user_text: str,
        customer: Optional[CustomerRecord],
        persona: str = DEFAULT_PERSONA,
    ) -> str:
        """
        Runs one dialogue turn: appends the caller's utterance to session
        history, calls the Sarvam AI text-generation brain with the full
        rolling context plus fresh CRM telemetry, then appends and returns
        the assistant's reply.

        `persona` selects which system-prompt contract drives the turn (see
        `_PERSONA_TEMPLATES`); it defaults to the non-linear NovaConnect triage agent
        and falls back to it for any unrecognized id.

        Raises:
            AgentCreditExhaustedError, AgentRateLimitedError, AgentTimeoutError,
            AgentBrainError: on the respective Sarvam AI failure class, with a
            caller-safe message suitable for TTS playback.
        """
        await self._sessions.append(session_id, "user", user_text)
        history = await self._sessions.get_history(session_id)

        system_prompt = self._build_system_prompt(customer, persona=persona)
        messages = [{"role": "system", "content": system_prompt}] + [
            {"role": turn.role, "content": turn.content} for turn in history
        ]

        try:
            response = await self._client.chat.completions(
                messages=messages,
                model=settings.sarvam_text_model,
                max_tokens=settings.sarvam_text_max_tokens,
                temperature=settings.sarvam_text_temperature,
                request_options={"timeout_in_seconds": settings.sarvam_text_timeout_seconds},
            )
        except ApiError as exc:
            logger.error(
                "Sarvam AI chat-completions ApiError for session=%s: status=%s body=%s",
                session_id,
                getattr(exc, "status_code", "?"),
                getattr(exc, "body", "?"),
            )
            raise self._classify_api_error(exc) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            logger.error(
                "Sarvam AI chat-completions timed out for session=%s after %.1fs",
                session_id,
                settings.sarvam_text_timeout_seconds,
            )
            raise AgentTimeoutError(
                "That took longer than expected. Could you repeat that?"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "Sarvam AI chat-completions transport error for session=%s: %s", session_id, exc
            )
            raise AgentBrainError(
                "We're having trouble reaching support systems right now."
            ) from exc

        choices = getattr(response, "choices", None) or []
        message = choices[0].message if choices and getattr(choices[0], "message", None) else None
        reply_text = ((message.content if message else None) or "").strip()

        if not reply_text:
            # Observed in production: Sarvam AI occasionally returns
            # finish_reason="stop" with content=None (while still billing
            # completion tokens) for certain multi-turn prompts — a live
            # model response, not a transport/credit/rate-limit failure.
            # Raising here would 502 the whole call and dead-air the
            # caller, so we degrade to a safe spoken fallback instead,
            # consistent with this pipeline's graceful-degradation design.
            logger.error(
                "Sarvam AI returned an empty reply for session=%s; falling back "
                "to a safe escalation reply",
                session_id,
            )
            reply_text = (
                "Sorry, I'm having trouble with that. Let me connect you with a specialist."
            )

        self._enforce_word_budget(reply_text)
        await self._sessions.append(session_id, "assistant", reply_text)
        return reply_text

    async def reset_session(self, session_id: str) -> None:
        """Clears a session's chat history (e.g. on call hangup/teardown)."""
        await self._sessions.reset(session_id)

    async def active_session_count(self) -> int:
        return await self._sessions.active_session_count()


# Process-wide singleton — see config.settings / crm_service.crm_service for
# the same pattern used consistently across the stack.
agent_brain = AgentBrain()
