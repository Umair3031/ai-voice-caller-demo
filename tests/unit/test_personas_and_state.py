"""Persona templates, prompt rendering, and state-store behaviour."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import CustomerNotFoundError, InvalidFieldUpdateError
from app.domain.models import (
    CallOutcome,
    CallTelemetry,
    ConversationTurn,
    InputMode,
    PipelineStage,
    Role,
)
from app.domain.personas.registry import (
    PERSONAS,
    PersonaId,
    build_system_prompt,
    list_personas,
    load_catalog,
    verify_templates,
)
from app.infra.memory_state import (
    InMemoryCustomerStore,
    InMemoryRateLimiter,
    InMemorySessionStore,
    InMemoryTelemetryStore,
)
from app.infra.seed import normalize_msisdn
from app.services.crm import render_crm_block


# ------------------------------------------------------------------------------
# Personas
# ------------------------------------------------------------------------------
def test_every_persona_template_renders() -> None:
    """A typo'd placeholder must fail at boot, not on the first live call."""
    verify_templates()


@pytest.mark.parametrize("persona_id", list(PersonaId))
def test_prompt_carries_the_word_budget_and_crm_block(persona_id: PersonaId) -> None:
    prompt = build_system_prompt(persona_id, reply_max_words=30, crm_block="- probe marker\n")
    assert "30 words" in " ".join(prompt.split())  # the prompt is hard-wrapped
    assert "probe marker" in prompt
    # No placeholder may survive rendering.
    assert "{reply_max_words}" not in prompt
    assert "{crm_block}" not in prompt
    assert "{product_catalog}" not in prompt


@pytest.mark.parametrize("persona_id", list(PersonaId))
def test_agent_does_not_greet_again_after_its_own_opening_line(persona_id: PersonaId) -> None:
    """The agent speaks first (see `render_greeting`), so the prompt must tell it
    the introduction is already done — otherwise it greets a second time."""
    prompt = build_system_prompt(persona_id, reply_max_words=30, crm_block="- none\n")
    assert "already" in prompt
    assert "opening line" in prompt


@pytest.mark.parametrize("persona_id", list(PersonaId))
def test_every_agent_admits_to_being_an_ai_and_forbids_fake_actions(persona_id: PersonaId) -> None:
    prompt = build_system_prompt(persona_id, reply_max_words=30, crm_block="- none\n")
    flat = " ".join(prompt.split())  # the prompt is hard-wrapped
    assert "AI voice assistant" in flat
    assert "Never claim to be human" in flat
    assert "transferring you" in flat  # named only to forbid it


@pytest.mark.parametrize("persona_id", list(PersonaId))
def test_every_agent_states_exactly_one_demo_offer(persona_id: PersonaId) -> None:
    prompt = build_system_prompt(persona_id, reply_max_words=30, crm_block="- none\n")
    assert prompt.count("YOUR ONE DEMO OFFER") == 1


def test_prompt_knows_the_local_time() -> None:
    from datetime import UTC, datetime

    prompt = build_system_prompt(
        PersonaId.KEVIN_L1,
        reply_max_words=30,
        crm_block="- none\n",
        now=datetime(2026, 9, 21, 9, 30, tzinfo=UTC),  # 15:00 in India
    )
    assert "3:00 PM IST" in prompt


def test_jessika_prompt_includes_the_catalog() -> None:
    prompt = build_system_prompt(
        PersonaId.JESSIKA_PRESALES, reply_max_words=30, crm_block="- none\n"
    )
    assert "Fiber Premium" in prompt
    assert "Bengaluru" in prompt
    assert "18% GST" in prompt


def test_catalog_renders_both_plan_shapes() -> None:
    """Fiber plans and SIM plans have different keys; both must flatten."""
    catalog = load_catalog("veloce_services.json")
    assert "Fiber Value: 100 Mbps, Rs.799 per 30 days" in catalog
    assert "Prepaid Annual Value: Rs.2999, 365 day validity" in catalog


def test_roster_never_exposes_template_paths() -> None:
    for persona in list_personas():
        assert "template_file" not in persona.model_dump()


def test_registry_and_enum_agree() -> None:
    assert set(PERSONAS) == set(PersonaId)


# ------------------------------------------------------------------------------
# CRM prompt projection
# ------------------------------------------------------------------------------
async def test_crm_block_for_a_known_caller() -> None:
    record = await InMemoryCustomerStore().get("+919810012345")
    assert record is not None
    block = render_crm_block(record)
    assert "Rahul Sharma" in block
    assert "Offline" in block
    assert "Red" in block
    assert "412.6GB of 1000.0GB" in block


def test_crm_block_for_an_unknown_caller_says_unverified() -> None:
    assert "unverified" in render_crm_block(None)


# ------------------------------------------------------------------------------
# MSISDN normalization
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+919810012345", "+919810012345"),
        ("919810012345", "+919810012345"),
        (" +91 98100 12345 ", "+919810012345"),
        ("+91-98100-12345", "+919810012345"),
        ("(91) 98100 12345", "+919810012345"),
    ],
)
def test_msisdn_normalization(raw: str, expected: str) -> None:
    assert normalize_msisdn(raw) == expected


# ------------------------------------------------------------------------------
# Session store
# ------------------------------------------------------------------------------
async def test_history_is_bounded_per_session() -> None:
    store = InMemorySessionStore(max_turns=2, ttl_seconds=60)
    for i in range(10):
        await store.append("s1", ConversationTurn(role=Role.USER, content=f"msg-{i}"))

    history = await store.get_history("s1")
    assert len(history) == 4  # max_turns * 2 messages
    assert history[-1].content == "msg-9"


async def test_sessions_expire_so_the_store_stays_bounded() -> None:
    """Each session's history was bounded, but the *set* of sessions was not —
    an abandoned call stayed resident forever."""
    store = InMemorySessionStore(max_turns=4, ttl_seconds=0)
    await store.append("abandoned", ConversationTurn(role=Role.USER, content="hi"))
    assert await store.active_session_count() == 0


async def test_sessions_are_isolated() -> None:
    store = InMemorySessionStore(max_turns=4, ttl_seconds=60)
    await store.append("a", ConversationTurn(role=Role.USER, content="from-a"))
    await store.append("b", ConversationTurn(role=Role.USER, content="from-b"))

    assert [t.content for t in await store.get_history("a")] == ["from-a"]
    assert await store.active_session_count() == 2

    await store.reset("a")
    assert await store.get_history("a") == []
    assert await store.active_session_count() == 1


# ------------------------------------------------------------------------------
# Telemetry store
# ------------------------------------------------------------------------------
def _entry(total_ms: float, outcome: CallOutcome = CallOutcome.COMPLETED) -> CallTelemetry:
    return CallTelemetry(
        call_id="c",
        session_id="s",
        phone_number="+1",
        persona="kevin_l1",
        input_mode=InputMode.TEXT,
        stages=[{"stage": PipelineStage.LLM_GENERATION, "elapsed_ms": total_ms}],
        total_ms=total_ms,
        outcome=outcome,
    )


async def test_telemetry_ring_buffer_drops_oldest() -> None:
    store = InMemoryTelemetryStore(max_size=3)
    for i in range(5):
        await store.record(_entry(float(i)))

    history = await store.history()
    assert [e.total_ms for e in history] == [2.0, 3.0, 4.0]


async def test_failed_entries_are_not_counted_as_successes() -> None:
    """Regression: `success` compared an enum with `is`, against a field that
    `use_enum_values` had already turned into a plain string — so every failed
    call reported as a success."""
    assert _entry(1.0, CallOutcome.FAILED).success is False
    assert _entry(1.0, CallOutcome.DEGRADED_TEXT_ONLY).success is True
    assert _entry(1.0, CallOutcome.COMPLETED).success is True


# ------------------------------------------------------------------------------
# Customer store
# ------------------------------------------------------------------------------
async def test_write_then_read_back() -> None:
    store = InMemoryCustomerStore()
    updated = await store.update_field("+919810012345", "router_status", "Online")
    assert updated.router_status == "Online"

    reread = await store.get("+919810012345")
    assert reread is not None
    assert reread.router_status == "Online"
    assert reread.last_updated >= updated.last_updated


async def test_write_rejects_a_non_writable_field() -> None:
    store = InMemoryCustomerStore()
    with pytest.raises(InvalidFieldUpdateError):
        await store.update_field("+919810012345", "account_id", "HACKED")


async def test_write_rejects_an_invalid_enum_value() -> None:
    store = InMemoryCustomerStore()
    with pytest.raises(InvalidFieldUpdateError):
        await store.update_field("+919810012345", "los_light", "Purple")


async def test_write_to_an_unknown_subscriber_fails() -> None:
    store = InMemoryCustomerStore()
    with pytest.raises(CustomerNotFoundError):
        await store.update_field("+10000000000", "router_status", "Online")


async def test_records_are_immutable_to_callers() -> None:
    """Frozen records mean a prompt builder cannot corrupt shared state."""
    store = InMemoryCustomerStore()
    record = await store.get("+919810012345")
    assert record is not None
    with pytest.raises(ValidationError):
        record.customer_name = "Tampered"  # type: ignore[misc]


# ------------------------------------------------------------------------------
# Rate limiter
# ------------------------------------------------------------------------------
async def test_rate_limiter_blocks_over_budget() -> None:
    limiter = InMemoryRateLimiter()
    assert all([await limiter.allow("k", limit=3, window_seconds=60) for _ in range(3)])
    assert await limiter.allow("k", limit=3, window_seconds=60) is False


async def test_rate_limiter_is_per_key() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        await limiter.allow("a", limit=3, window_seconds=60)
    assert await limiter.allow("b", limit=3, window_seconds=60) is True


async def test_zero_limit_disables_throttling() -> None:
    limiter = InMemoryRateLimiter()
    assert all([await limiter.allow("k", limit=0, window_seconds=60) for _ in range(50)])


# ------------------------------------------------------------------------------
# Configuration guards
# ------------------------------------------------------------------------------
def test_invalid_tts_speaker_is_rejected_at_construction() -> None:
    """Regression: 'anushka' was deployed as the voice for bulbul:v3, which the
    API rejects with HTTP 400 on every synthesis call. The pipeline degraded
    each turn to text, so the service looked healthy while never speaking."""
    with pytest.raises(ValidationError, match="not a valid voice"):
        Settings(sarvam_api_key="k", tts_model="bulbul:v3", tts_speaker="anushka")


def test_valid_tts_speaker_is_accepted() -> None:
    settings = Settings(sarvam_api_key="k", tts_model="bulbul:v3", tts_speaker="priya")
    assert settings.tts_speaker == "priya"


def test_speaker_is_not_policed_for_other_models() -> None:
    """The known-voice list is bulbul:v3's; another model brings its own."""
    settings = Settings(sarvam_api_key="k", tts_model="bulbul:v2", tts_speaker="anushka")
    assert settings.tts_speaker == "anushka"


def test_placeholder_api_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not set"):
        Settings(sarvam_api_key="your_sarvam_key_here")


def test_wildcard_cors_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="explicit origins"):
        Settings(sarvam_api_key="k", cors_allow_origins="*")
