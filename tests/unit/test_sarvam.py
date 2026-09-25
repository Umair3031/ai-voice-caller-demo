"""
Provider adapter: error classification and retry policy.

A single transient blip used to fail a caller's turn outright. These pin down
which failures are retried and which are not.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from sarvamai.core.api_error import ApiError

from app.core.config import Settings
from app.core.errors import (
    ProviderCreditExhaustedError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
)
from app.services.sarvam import (
    SarvamLanguageModel,
    SarvamSpeechToText,
    SarvamTextToSpeech,
    _translate,
)


class _Namespace:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class FakeEndpoint:
    """Raises the scripted errors in order, then returns `result`."""

    def __init__(self, result: object, errors: list[Exception] | None = None) -> None:
        self.result = result
        self.errors = list(errors or [])
        self.call_count = 0

    async def __call__(self, **kwargs: object) -> object:
        self.call_count += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def _client(**endpoints: FakeEndpoint) -> _Namespace:
    return _Namespace(
        speech_to_text=_Namespace(transcribe=endpoints.get("stt")),
        text_to_speech=_Namespace(convert=endpoints.get("tts")),
        chat=_Namespace(completions=endpoints.get("chat")),
    )


@pytest.fixture
def fast_settings(settings: Settings) -> Settings:
    # Keep the backoff from making the suite slow.
    return settings.model_copy(
        update={"sarvam_max_retries": 2, "sarvam_retry_base_delay_seconds": 0.001}
    )


# ------------------------------------------------------------------------------
# Classification
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, ProviderCreditExhaustedError),
        (402, ProviderCreditExhaustedError),
        (403, ProviderCreditExhaustedError),
        (429, ProviderRateLimitedError),
        (500, ProviderError),
        (400, ProviderError),
    ],
)
async def test_status_codes_map_to_error_types(status_code: int, expected: type) -> None:
    assert type(_translate(ApiError(status_code=status_code, body={}))) is expected


async def test_provider_message_is_extracted_not_dumped() -> None:
    """`str(ApiError)` dumps the whole HTTP exchange; only the message field
    may reach anything user-facing."""
    error = _translate(
        ApiError(status_code=400, body={"error": {"message": "speaker not found", "code": "x"}})
    )
    assert "speaker not found" in error.detail


# ------------------------------------------------------------------------------
# Retry policy
# ------------------------------------------------------------------------------
async def test_transient_failure_is_retried_then_succeeds(fast_settings: Settings) -> None:
    endpoint = FakeEndpoint(
        result=_Namespace(transcript="hello", language_code="en-IN", request_id="r"),
        errors=[ApiError(status_code=429, body={}), httpx.ReadTimeout("slow")],
    )
    stt = SarvamSpeechToText(_client(stt=endpoint), fast_settings)

    result = await stt.transcribe(b"audio", filename="turn.wav")

    assert result.transcript == "hello"
    assert endpoint.call_count == 3


async def test_retries_are_bounded(fast_settings: Settings) -> None:
    endpoint = FakeEndpoint(result=None, errors=[ApiError(status_code=503, body={})] * 10)
    stt = SarvamSpeechToText(_client(stt=endpoint), fast_settings)

    with pytest.raises(ProviderError):
        await stt.transcribe(b"audio", filename="turn.wav")

    assert endpoint.call_count == fast_settings.sarvam_max_retries + 1


async def test_credit_exhaustion_is_not_retried(fast_settings: Settings) -> None:
    """More credits will not appear because we asked a second time."""
    endpoint = FakeEndpoint(result=None, errors=[ApiError(status_code=402, body={})] * 5)
    stt = SarvamSpeechToText(_client(stt=endpoint), fast_settings)

    with pytest.raises(ProviderCreditExhaustedError):
        await stt.transcribe(b"audio", filename="turn.wav")

    assert endpoint.call_count == 1


async def test_timeout_surfaces_as_timeout_error(fast_settings: Settings) -> None:
    endpoint = FakeEndpoint(result=None, errors=[httpx.ReadTimeout("slow")] * 5)
    tts = SarvamTextToSpeech(_client(tts=endpoint), fast_settings)

    with pytest.raises(ProviderTimeoutError):
        await tts.synthesize("hello")


# ------------------------------------------------------------------------------
# Response handling
# ------------------------------------------------------------------------------
async def test_unknown_language_sentinel_becomes_none(fast_settings: Settings) -> None:
    """Sarvam reports an unresolved language as the string 'unknown'; passing
    that straight into TTS as a language code would be nonsense."""
    endpoint = FakeEndpoint(
        result=_Namespace(transcript="hi", language_code="unknown", request_id="r")
    )
    stt = SarvamSpeechToText(_client(stt=endpoint), fast_settings)

    assert (await stt.transcribe(b"a", filename="t.wav")).language_code is None


async def test_synthesis_decodes_base64_audio(fast_settings: Settings) -> None:
    payload = b"RIFFfake-wav-bytes"
    endpoint = FakeEndpoint(
        result=_Namespace(audios=[base64.b64encode(payload).decode()], request_id="r")
    )
    tts = SarvamTextToSpeech(_client(tts=endpoint), fast_settings)

    result = await tts.synthesize("hello", language_code="hi-IN")

    assert result.audio_bytes == payload
    assert result.language_code == "hi-IN"


async def test_malformed_audio_is_rejected(fast_settings: Settings) -> None:
    endpoint = FakeEndpoint(result=_Namespace(audios=["!!!not base64!!!"], request_id="r"))
    tts = SarvamTextToSpeech(_client(tts=endpoint), fast_settings)

    with pytest.raises(ProviderError, match="malformed"):
        await tts.synthesize("hello")


async def test_empty_completion_degrades_instead_of_raising(fast_settings: Settings) -> None:
    """Sarvam occasionally returns finish_reason='stop' with content=None.
    Raising would 502 the call and dead-air the caller."""
    endpoint = FakeEndpoint(
        result=_Namespace(choices=[_Namespace(message=_Namespace(content=None))], request_id="r")
    )
    llm = SarvamLanguageModel(_client(chat=endpoint), fast_settings)

    result = await llm.complete(system_prompt="you are an agent", history=[])

    assert result.is_fallback is True
    assert "specialist" in result.text


async def test_empty_synthesis_text_is_rejected_before_a_network_call(
    fast_settings: Settings,
) -> None:
    endpoint = FakeEndpoint(result=None)
    tts = SarvamTextToSpeech(_client(tts=endpoint), fast_settings)

    with pytest.raises(ProviderError):
        await tts.synthesize("   ")

    assert endpoint.call_count == 0


async def test_reply_that_hit_the_token_ceiling_is_cut_to_a_whole_sentence(
    fast_settings: Settings,
) -> None:
    message = _Namespace(content="Your router is offline. I would restart it and then")
    endpoint = FakeEndpoint(
        result=_Namespace(
            choices=[_Namespace(message=message, finish_reason="length")], request_id="r"
        )
    )
    llm = SarvamLanguageModel(_client(chat=endpoint), fast_settings)

    result = await llm.complete(system_prompt="you are an agent", history=[])

    assert result.text == "Your router is offline."
