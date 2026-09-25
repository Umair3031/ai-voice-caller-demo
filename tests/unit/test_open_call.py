"""The agent speaks first: greeting, caller assignment, and the state it leaves behind."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from app.core.container import Container
from app.domain.models import InputMode, PaymentStatus, RouterStatus
from app.domain.personas.registry import (
    PersonaId,
    daypart,
    render_greeting,
)
from app.services.crm import suggest_opener
from app.services.sarvam import complete_sentences
from tests.conftest import FakeTextToSpeech


# -- Greetings ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("utc_hour", "expected"),
    [(3, "Good morning"), (8, "Good afternoon"), (13, "Good evening")],  # IST = UTC + 5:30
)
def test_daypart_is_in_indian_time(utc_hour: int, expected: str) -> None:
    assert daypart(datetime(2026, 9, 21, utc_hour, 0, tzinfo=UTC)) == expected


@pytest.mark.parametrize("persona", [PersonaId.NovaConnect_DEFAULT, PersonaId.KEVIN_L1])
def test_account_agents_greet_by_first_name_and_nothing_else(persona: PersonaId) -> None:
    for seed in range(20):
        line = render_greeting(persona, first_name="Rahul", rng=random.Random(seed))
        assert "Rahul" in line
        assert "{" not in line
        # Volunteers no account detail: the caller leads.
        for leaked in ("router", "bill", "overdue", "offline", "balance"):
            assert leaked not in line.lower()


def test_presales_never_pretends_to_know_the_caller() -> None:
    for seed in range(20):
        line = render_greeting(PersonaId.JESSIKA_PRESALES, first_name=None, rng=random.Random(seed))
        assert "Jessika" in line
        assert "{" not in line


def test_account_agent_without_a_name_falls_back_to_a_nameless_greeting() -> None:
    line = render_greeting(PersonaId.KEVIN_L1, first_name=None, rng=random.Random(1))
    assert "Kevin" in line and "{name}" not in line


# -- Opening a call -------------------------------------------------------------
async def test_open_call_assigns_a_caller_and_speaks_first(
    container: Container, fake_tts: FakeTextToSpeech, fake_llm
) -> None:
    result = await container.pipeline.open_call(persona=PersonaId.NovaConnect_DEFAULT)

    assert result.customer is not None
    assert result.customer.customer_name.split()[0] in result.turn.response_text
    assert result.turn.audio_bytes is not None
    assert fake_tts.calls[-1][0] == result.turn.response_text
    assert result.turn.input_mode is InputMode.GREETING
    assert result.turn.transcript == ""
    assert fake_llm.prompts == []  # a greeting costs no LLM round trip


async def test_open_call_stores_the_greeting_as_the_first_turn(
    container: Container, fake_llm
) -> None:
    opened = await container.pipeline.open_call(persona=PersonaId.KEVIN_L1)
    history = await container.conversation.get_history(opened.turn.session_id)
    assert [t.role for t in history] == ["assistant"]

    await container.pipeline.run_turn(
        phone_number=opened.customer.phone_number,
        persona=PersonaId.KEVIN_L1,
        session_id=opened.turn.session_id,
        text="my internet is down",
    )
    sent = fake_llm.histories[-1]
    # A chat model needs a caller turn before the agent's own opening line.
    assert sent[0].role == "user" and "picked up" in sent[0].content
    assert sent[1].content == opened.turn.response_text
    assert sent[-1].content == "my internet is down"


async def test_presales_call_has_no_account(container: Container) -> None:
    result = await container.pipeline.open_call(persona=PersonaId.JESSIKA_PRESALES)
    assert result.customer is None
    assert "Jessika" in result.turn.response_text


async def test_technical_agent_only_gets_callers_with_something_to_look_at(
    container: Container,
) -> None:
    for _ in range(40):
        result = await container.pipeline.open_call(persona=PersonaId.KEVIN_L1)
        assert result.customer is not None
        assert result.customer.router_status is not RouterStatus.ONLINE


async def test_open_call_degrades_to_text_when_speech_fails(
    container: Container, fake_tts: FakeTextToSpeech
) -> None:
    from app.core.errors import ProviderError

    fake_tts.error = ProviderError("tts down")
    result = await container.pipeline.open_call(persona=PersonaId.NovaConnect_DEFAULT)
    assert result.turn.audio_bytes is None
    assert result.turn.response_text  # the greeting still arrives as text


async def test_open_call_is_recorded_in_telemetry(container: Container) -> None:
    await container.pipeline.open_call(persona=PersonaId.KEVIN_L1)
    (entry,) = await container.telemetry.history()
    assert entry.input_mode is InputMode.GREETING
    assert entry.persona == "kevin_l1"


async def test_prospect_turn_needs_no_phone_number(container: Container, fake_llm) -> None:
    result = await container.pipeline.run_turn(
        phone_number=None, persona=PersonaId.JESSIKA_PRESALES, text="I want fibre"
    )
    assert result.response_text
    assert "No subscriber record" in fake_llm.prompts[0]


# -- The visitor's cue ------------------------------------------------------------
async def test_every_subscriber_in_the_cast_has_a_sensible_opener(container: Container) -> None:
    for customer in await container.crm.list_customers():
        assert suggest_opener(customer).endswith((".", "?"))


def test_suspended_account_is_framed_as_an_outage_not_a_bill() -> None:
    # The whole point of that subscriber: the visitor describes a dead line, and
    # a good agent finds the bill.
    from app.infra.seed import seed_customers

    suspended = next(c for c in seed_customers() if c.payment_status is PaymentStatus.SUSPENDED)
    assert "internet" in suggest_opener(suspended)


# -- Replies that hit the token ceiling ----------------------------------------------
@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("Right, I can see that. Your router is off and then the", "Right, I can see that."),
        ("Two done. Three done! And a fourth that never", "Two done. Three done!"),
        ("No sentence end here", "No sentence end here"),
    ],
)
def test_truncated_reply_is_cut_back_to_a_whole_sentence(raw: str, kept: str) -> None:
    assert complete_sentences(raw) == kept
