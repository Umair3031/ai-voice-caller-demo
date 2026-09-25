"""
Shared fixtures.

Every test runs against in-memory stores and fake speech/language services —
no network, no API key, no credits. This is only possible because nothing in
`app` constructs a client at import time.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest

# Must be set before anything under `app` is imported: Settings validates the
# key at construction, and `app.main` builds an app at module scope.
os.environ.setdefault("SARVAM_API_KEY", "test-key-not-real")
os.environ.setdefault("OPERATOR_API_KEYS", "test-operator-key,second-key")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "")

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.container import Container
from app.domain.models import ConversationTurn
from app.infra.memory_state import (
    InMemoryCustomerStore,
    InMemoryRateLimiter,
    InMemorySessionStore,
    InMemoryTelemetryStore,
)
from app.main import create_app
from app.services.conversation import ConversationService
from app.services.crm import CrmService
from app.services.pipeline import CallPipeline
from app.services.protocols import (
    CompletionResult,
    SynthesisResult,
    TranscriptionResult,
)

OPERATOR_KEY = "test-operator-key"
OPERATOR_HEADERS = {"X-API-Key": OPERATOR_KEY}

# A real 44-byte WAV header + one silent sample, so anything that inspects the
# bytes sees a valid container rather than a magic string.
SILENT_WAV = (
    b"RIFF"
    + (36 + 2).to_bytes(4, "little")
    + b"WAVEfmt "
    + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little")
    + (1).to_bytes(2, "little")
    + (22050).to_bytes(4, "little")
    + (44100).to_bytes(4, "little")
    + (2).to_bytes(2, "little")
    + (16).to_bytes(2, "little")
    + b"data"
    + (2).to_bytes(4, "little")
    + b"\x00\x00"
)


# ------------------------------------------------------------------------------
# Fakes
# ------------------------------------------------------------------------------
class FakeSpeechToText:
    """Records what it was asked to transcribe and returns a scripted result."""

    def __init__(
        self, transcript: str = "my internet is down", language_code: str | None = "en-IN"
    ) -> None:
        self.transcript = transcript
        self.language_code = language_code
        self.error: Exception | None = None
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(
        self, audio_bytes: bytes, *, filename: str, language_code: str | None = None
    ) -> TranscriptionResult:
        self.calls.append((audio_bytes, filename))
        if self.error:
            raise self.error
        return TranscriptionResult(
            transcript=self.transcript, language_code=self.language_code, request_id="stt-1"
        )


class FakeTextToSpeech:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[tuple[str, str | None]] = []

    async def synthesize(
        self, text: str, *, language_code: str | None = None, speaker: str | None = None
    ) -> SynthesisResult:
        self.calls.append((text, language_code))
        if self.error:
            raise self.error
        return SynthesisResult(
            audio_bytes=SILENT_WAV,
            sample_rate=22050,
            language_code=language_code or "en-IN",
            request_id="tts-1",
        )

    @property
    def last_language(self) -> str | None:
        return self.calls[-1][1] if self.calls else None


class FakeLanguageModel:
    def __init__(self, reply: str = "Checking your router now.") -> None:
        self.reply = reply
        self.error: Exception | None = None
        self.prompts: list[str] = []
        self.histories: list[list[ConversationTurn]] = []

    async def complete(
        self, *, system_prompt: str, history: list[ConversationTurn]
    ) -> CompletionResult:
        self.prompts.append(system_prompt)
        self.histories.append(list(history))
        if self.error:
            raise self.error
        return CompletionResult(text=self.reply, request_id="llm-1")


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def fake_stt() -> FakeSpeechToText:
    return FakeSpeechToText()


@pytest.fixture
def fake_tts() -> FakeTextToSpeech:
    return FakeTextToSpeech()


@pytest.fixture
def fake_llm() -> FakeLanguageModel:
    return FakeLanguageModel()


@pytest.fixture
def container(
    settings: Settings,
    fake_stt: FakeSpeechToText,
    fake_tts: FakeTextToSpeech,
    fake_llm: FakeLanguageModel,
) -> Container:
    sessions = InMemorySessionStore(
        max_turns=settings.max_history_turns, ttl_seconds=settings.session_ttl_seconds
    )
    telemetry = InMemoryTelemetryStore(max_size=settings.telemetry_history_size)
    crm = CrmService(InMemoryCustomerStore())
    conversation = ConversationService(
        language_model=fake_llm, sessions=sessions, settings=settings
    )
    pipeline = CallPipeline(
        stt=fake_stt,
        tts=fake_tts,
        conversation=conversation,
        crm=crm,
        telemetry=telemetry,
        settings=settings,
    )
    return Container(
        settings=settings,
        pipeline=pipeline,
        conversation=conversation,
        crm=crm,
        telemetry=telemetry,
        rate_limiter=InMemoryRateLimiter(),
        redis=None,
    )


@pytest.fixture
def app(container: Container) -> Iterator[FastAPI]:
    """App with the lifespan bypassed — the container is injected directly, so
    no real client is ever constructed."""
    application = create_app(container.settings)
    application.state.container = container
    yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
