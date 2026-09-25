"""
Service protocols and their result types.

The pipeline orchestrator depends only on what is declared here, never on the
`sarvamai` SDK. That keeps the provider swappable and — more usefully day to
day — lets the whole pipeline be unit-tested against fakes with no network, no
credits, and no API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.models import ConversationTurn


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    transcript: str
    language_code: str | None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    audio_bytes: bytes
    sample_rate: int
    language_code: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionResult:
    text: str
    request_id: str | None = None
    #  True when the provider returned an empty body and we substituted a safe
    #  spoken fallback rather than dead-airing the caller.
    is_fallback: bool = False


@runtime_checkable
class SpeechToTextService(Protocol):
    async def transcribe(
        self, audio_bytes: bytes, *, filename: str, language_code: str | None = None
    ) -> TranscriptionResult: ...


@runtime_checkable
class TextToSpeechService(Protocol):
    async def synthesize(
        self, text: str, *, language_code: str | None = None, speaker: str | None = None
    ) -> SynthesisResult: ...


@runtime_checkable
class LanguageModelService(Protocol):
    async def complete(
        self, *, system_prompt: str, history: list[ConversationTurn]
    ) -> CompletionResult: ...
