"""
Pipeline behaviour — including regression tests for every defect the rewrite
was meant to close.
"""

from __future__ import annotations

import pytest

from app.core.container import Container
from app.core.errors import (
    AudioTooLargeError,
    EmptyAudioError,
    NoSpeechDetectedError,
    ProviderError,
    ProviderRateLimitedError,
)
from app.domain.models import CallOutcome, InputMode, PipelineStage
from app.domain.personas.registry import PersonaId
from tests.conftest import SILENT_WAV, FakeSpeechToText, FakeTextToSpeech


async def test_audio_turn_runs_every_stage(container: Container) -> None:
    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, audio_bytes=SILENT_WAV
    )

    assert result.outcome is CallOutcome.COMPLETED
    assert result.input_mode is InputMode.AUDIO
    assert result.audio_bytes == SILENT_WAV
    assert [PipelineStage(s.stage) for s in result.stages] == [
        PipelineStage.INPUT_PROCESSING,
        PipelineStage.CRM_LOOKUP,
        PipelineStage.LLM_GENERATION,
        PipelineStage.TTS_SYNTHESIS,
    ]
    assert result.total_ms > 0


async def test_text_turn_skips_stt(container: Container, fake_stt: FakeSpeechToText) -> None:
    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.NovaConnect_DEFAULT, text="  my bill is wrong  "
    )

    assert result.input_mode is InputMode.TEXT
    assert result.transcript == "my bill is wrong"
    assert fake_stt.calls == []


# ------------------------------------------------------------------------------
# Regression: the reply was always synthesized at the configured default
# language, so a Hindi conversation came back as Hindi text read by an en-IN
# voice profile.
# ------------------------------------------------------------------------------
async def test_detected_language_is_threaded_into_synthesis(
    container: Container, fake_stt: FakeSpeechToText, fake_tts: FakeTextToSpeech
) -> None:
    fake_stt.language_code = "hi-IN"

    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, audio_bytes=SILENT_WAV
    )

    assert fake_tts.last_language == "hi-IN"
    assert result.detected_language == "hi-IN"
    assert result.spoken_language == "hi-IN"


async def test_falls_back_to_configured_language_when_none_detected(
    container: Container, fake_stt: FakeSpeechToText, fake_tts: FakeTextToSpeech
) -> None:
    fake_stt.language_code = None

    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, audio_bytes=SILENT_WAV
    )

    assert fake_tts.last_language == container.settings.tts_target_language_code
    assert result.detected_language is None


# ------------------------------------------------------------------------------
# Regression: a TTS failure must degrade to text, never fail the call.
# ------------------------------------------------------------------------------
async def test_tts_failure_degrades_to_text(
    container: Container, fake_tts: FakeTextToSpeech
) -> None:
    fake_tts.error = ProviderError("Bulbul is down")

    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
    )

    assert result.outcome is CallOutcome.DEGRADED_TEXT_ONLY
    assert result.audio_bytes is None
    assert result.response_text  # the caller still gets the answer
    assert result.degraded_reason


async def test_degraded_turn_is_still_recorded(
    container: Container, fake_tts: FakeTextToSpeech
) -> None:
    fake_tts.error = ProviderError("Bulbul is down")
    await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
    )

    history = await container.telemetry.history()
    assert len(history) == 1
    assert history[0].outcome is CallOutcome.DEGRADED_TEXT_ONLY
    assert history[0].success is True  # degraded is not a failure


# ------------------------------------------------------------------------------
# Regression: telemetry carried no persona, so per-agent latency was
# unknowable — every agent was averaged into one number.
# ------------------------------------------------------------------------------
async def test_telemetry_records_persona_and_language(
    container: Container, fake_stt: FakeSpeechToText
) -> None:
    fake_stt.language_code = "ta-IN"
    await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.JESSIKA_PRESALES, audio_bytes=SILENT_WAV
    )

    entry = (await container.telemetry.history())[0]
    assert entry.persona == PersonaId.JESSIKA_PRESALES.value
    assert entry.detected_language == "ta-IN"


async def test_failed_turn_records_partial_stages(container: Container, fake_llm) -> None:
    """A failure must appear in the charts, not vanish from them."""
    fake_llm.error = ProviderRateLimitedError("throttled")

    with pytest.raises(ProviderRateLimitedError):
        await container.pipeline.run_turn(
            phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
        )

    entry = (await container.telemetry.history())[0]
    assert entry.outcome is CallOutcome.FAILED
    assert entry.error_code == "provider_rate_limited"
    # STT + CRM completed before the LLM failed; both are on the record.
    assert [PipelineStage(s.stage) for s in entry.stages] == [
        PipelineStage.INPUT_PROCESSING,
        PipelineStage.CRM_LOOKUP,
    ]


# ------------------------------------------------------------------------------
# Input guards
# ------------------------------------------------------------------------------
async def test_empty_audio_rejected(container: Container) -> None:
    with pytest.raises(EmptyAudioError):
        await container.pipeline.run_turn(
            phone_number="+919810012345", persona=PersonaId.KEVIN_L1, audio_bytes=b""
        )


async def test_oversized_audio_rejected(container: Container) -> None:
    oversized = b"\x00" * (container.settings.max_audio_upload_bytes + 1)
    with pytest.raises(AudioTooLargeError):
        await container.pipeline.run_turn(
            phone_number="+919810012345", persona=PersonaId.KEVIN_L1, audio_bytes=oversized
        )


async def test_silent_audio_reports_no_speech(
    container: Container, fake_stt: FakeSpeechToText
) -> None:
    fake_stt.transcript = ""
    with pytest.raises(NoSpeechDetectedError):
        await container.pipeline.run_turn(
            phone_number="+919810012345", persona=PersonaId.KEVIN_L1, audio_bytes=SILENT_WAV
        )


# ------------------------------------------------------------------------------
# Multi-turn continuity
# ------------------------------------------------------------------------------
async def test_session_history_accumulates_across_turns(container: Container) -> None:
    first = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="my internet is down"
    )
    await container.pipeline.run_turn(
        phone_number="+919810012345",
        persona=PersonaId.KEVIN_L1,
        session_id=first.session_id,
        text="the light is red",
    )

    history = await container.conversation.get_history(first.session_id)
    assert [turn.content for turn in history] == [
        "my internet is down",
        "Checking your router now.",
        "the light is red",
        "Checking your router now.",
    ]


async def test_crm_context_reaches_the_prompt(container: Container, fake_llm) -> None:
    await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
    )
    prompt = fake_llm.prompts[0]
    assert "Rahul Sharma" in prompt
    assert "Offline" in prompt  # live router telemetry


async def test_unknown_caller_prompt_marks_unverified(container: Container, fake_llm) -> None:
    await container.pipeline.run_turn(
        phone_number="+9999999999", persona=PersonaId.KEVIN_L1, text="hello"
    )
    assert "unverified" in fake_llm.prompts[0]


# ------------------------------------------------------------------------------
# Vocal compliance — hard ceiling on reply length
#
# The prompt aims for `reply_max_words`; `_enforce_word_budget` only catches a
# reply that has run past `reply_hard_max_words`, and then always on whole
# sentences, never mid-sentence.
# ------------------------------------------------------------------------------
def _sentence(words: int, tag: str) -> str:
    return " ".join([tag] + ["word"] * (words - 1)) + "."


async def test_reply_within_the_hard_ceiling_is_left_alone(container: Container, fake_llm) -> None:
    """A 45-word answer is over the 30-word target but is not clipped: the
    caller gets the complete thought."""
    fake_llm.reply = f"{_sentence(20, 'One')} {_sentence(25, 'Two')}"
    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
    )
    assert result.response_text == fake_llm.reply


async def test_reply_past_the_hard_ceiling_is_trimmed_to_whole_sentences(
    container: Container, fake_llm, fake_tts: FakeTextToSpeech
) -> None:
    ceiling = container.settings.reply_hard_max_words
    first, second = _sentence(25, "One"), _sentence(20, "Two")
    fake_llm.reply = f"{first} {second} {_sentence(ceiling, 'Three')}"
    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
    )

    assert result.response_text == f"{first} {second}"
    assert len(result.response_text.split()) <= ceiling
    # What's shown to the caller and what's actually spoken must match exactly.
    assert fake_tts.calls[-1][0] == result.response_text


async def test_single_over_budget_sentence_is_kept_whole(container: Container, fake_llm) -> None:
    """An over-length reply is still better than one chopped mid-thought — if
    even the first sentence alone exceeds the ceiling, it is kept whole."""
    fake_llm.reply = _sentence(container.settings.reply_hard_max_words + 20, "Long")
    result = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="hello"
    )
    assert result.response_text == fake_llm.reply


# ------------------------------------------------------------------------------
# Session isolation across a persona switch
#
# Regression: nothing locks a session_id to the persona that started it, so a
# caller handed from one agent to another mid-session had the new persona's
# LLM call receive the old persona's raw turns verbatim — no signal that a
# different specialist said them.
# ------------------------------------------------------------------------------
async def test_persona_switch_isolates_prior_history_from_the_llm(
    container: Container, fake_llm
) -> None:
    first = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="my internet is down"
    )
    await container.pipeline.run_turn(
        phone_number="+919810012345",
        persona=PersonaId.JESSIKA_PRESALES,
        session_id=first.session_id,
        text="actually, tell me about upgrade plans",
    )

    # The second call's LLM history must not carry Kevin's raw turns verbatim.
    contents = [turn.content for turn in fake_llm.histories[-1]]
    assert "my internet is down" not in contents
    assert "Checking your router now." not in contents
    assert any("handoff note" in c.lower() for c in contents)

    # Nothing is lost in storage — only in what's fed back to the model.
    stored = await container.conversation.get_history(first.session_id)
    assert [t.content for t in stored][:2] == ["my internet is down", "Checking your router now."]


async def test_same_persona_across_turns_is_unaffected(container: Container, fake_llm) -> None:
    """The isolation logic must be a no-op for the common (no switch) case."""
    first = await container.pipeline.run_turn(
        phone_number="+919810012345", persona=PersonaId.KEVIN_L1, text="my internet is down"
    )
    await container.pipeline.run_turn(
        phone_number="+919810012345",
        persona=PersonaId.KEVIN_L1,
        session_id=first.session_id,
        text="the light is red",
    )

    assert [turn.content for turn in fake_llm.histories[-1]] == [
        "my internet is down",
        "Checking your router now.",
        "the light is red",
    ]
