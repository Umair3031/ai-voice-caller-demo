"""
Sarvam AI implementations of the speech and language protocols.

One `AsyncSarvamAI` client is shared by all three services (STT/Saaras,
chat-completions, TTS/Bulbul) — one account, one credit pool, one connection
pool — and is built in the app lifespan, not at import time.

Every call goes through `_with_retry`, which distinguishes the failure classes
that are worth retrying (429, 5xx, timeouts) from the ones that never are
(401/402/403 credit or key problems, 4xx request problems), so a single
transient blip no longer fails a caller's turn.
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx
from sarvamai import AsyncSarvamAI
from sarvamai.core.api_error import ApiError

from app.core.config import Settings
from app.core.errors import (
    ProviderCreditExhaustedError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
)
from app.core.logging import get_logger
from app.domain.models import ConversationTurn
from app.services.protocols import CompletionResult, SynthesisResult, TranscriptionResult

logger = get_logger("services.sarvam")

T = TypeVar("T")

# Sarvam reports an unresolved language as this sentinel rather than omitting
# the field; treat it as "no detection" everywhere.
_UNKNOWN_LANGUAGE = "unknown"

_SAFE_FALLBACK_REPLY = "Sorry, I'm having trouble with that. Let me connect you with a specialist."


def create_sarvam_client(settings: Settings) -> AsyncSarvamAI:
    return AsyncSarvamAI(api_subscription_key=settings.sarvam_api_key)


# ------------------------------------------------------------------------------
# Error translation + retry
# ------------------------------------------------------------------------------
def _error_message(exc: ApiError) -> str:
    """Pulls the human-readable message out of a Sarvam `ApiError`.

    `str(exc)` dumps the entire HTTP exchange — every response header, the full
    body, the request id — into one line. Fine in a log, unusable anywhere near
    a caller. Sarvam's bodies are `{"error": {"message": ..., "code": ...}}`.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error_field = body.get("error")
        if isinstance(error_field, dict):
            message = error_field.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return "the request was rejected"


def _translate(exc: ApiError) -> ProviderError:
    status_code = getattr(exc, "status_code", None)
    if status_code in (401, 402, 403):
        return ProviderCreditExhaustedError(
            f"Sarvam AI rejected the request (HTTP {status_code}) — check API key "
            f"validity and remaining account credits."
        )
    if status_code == 429:
        return ProviderRateLimitedError("Sarvam AI rate limit exceeded (HTTP 429).")
    return ProviderError(
        f"Sarvam AI rejected the request (HTTP {status_code}): {_error_message(exc)}"
    )


def _is_retryable(error: ProviderError) -> bool:
    if isinstance(error, ProviderCreditExhaustedError):
        return False  # More credits will not appear because we asked twice.
    if isinstance(error, ProviderRateLimitedError | ProviderTimeoutError):
        return True
    return error.status_code >= 500


async def _with_retry(
    operation: str,
    call: Callable[[], Awaitable[T]],
    *,
    settings: Settings,
) -> T:
    """Runs `call`, retrying transient provider failures with exponential
    backoff plus jitter (so concurrent callers don't retry in lockstep)."""
    attempts = settings.sarvam_max_retries + 1
    last_error: ProviderError | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except ApiError as exc:
            logger.warning(
                "Sarvam %s ApiError (attempt %d/%d)",
                operation,
                attempt,
                attempts,
                extra={
                    "operation": operation,
                    "status_code": getattr(exc, "status_code", None),
                    "body": str(getattr(exc, "body", ""))[:500],
                },
            )
            last_error = _translate(exc)
        except (httpx.TimeoutException, TimeoutError):
            logger.warning("Sarvam %s timed out (attempt %d/%d)", operation, attempt, attempts)
            last_error = ProviderTimeoutError(f"Sarvam {operation} timed out.")
        except httpx.HTTPError as exc:
            logger.warning(
                "Sarvam %s transport error (attempt %d/%d): %s",
                operation,
                attempt,
                attempts,
                exc,
            )
            last_error = ProviderError(f"Sarvam {operation} transport error: {exc}")

        if attempt == attempts or not _is_retryable(last_error):
            break

        delay = settings.sarvam_retry_base_delay_seconds * (2 ** (attempt - 1))
        await asyncio.sleep(delay + random.uniform(0, delay * 0.25))

    assert last_error is not None
    logger.error("Sarvam %s failed after %d attempt(s): %s", operation, attempts, last_error.detail)
    raise last_error


_SENTENCE_END = re.compile(r"[.!?।][\"')\]]*(?=\s|$)")


def complete_sentences(text: str) -> str:
    """Cuts `text` back to its last finished sentence.

    A reply that hit the token ceiling ends mid-word, and it would be spoken that
    way. Whole sentences only; if there is no sentence end at all the text is
    returned as it is, since nothing shorter would be complete either.
    """
    last_end = None
    for match in _SENTENCE_END.finditer(text):
        last_end = match.end()
    return text[:last_end].strip() if last_end else text


def _normalize_language(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() == _UNKNOWN_LANGUAGE:
        return None
    return cleaned


# ------------------------------------------------------------------------------
# Services
# ------------------------------------------------------------------------------
class SarvamSpeechToText:
    """Saaras-family ASR."""

    def __init__(self, client: AsyncSarvamAI, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def transcribe(
        self, audio_bytes: bytes, *, filename: str, language_code: str | None = None
    ) -> TranscriptionResult:
        settings = self._settings

        async def _call() -> Any:
            return await self._client.speech_to_text.transcribe(
                file=(filename, io.BytesIO(audio_bytes)),
                model=settings.stt_model,
                mode="transcribe",  # same-language transcript, not translation
                language_code=language_code or settings.stt_language_code,
                request_options={"timeout_in_seconds": settings.voice_api_timeout_seconds},
            )

        response = await _with_retry("speech-to-text", _call, settings=settings)

        transcript = (getattr(response, "transcript", "") or "").strip()
        request_id = getattr(response, "request_id", None)
        if not transcript:
            logger.warning(
                "Sarvam STT returned an empty transcript", extra={"request_id": request_id}
            )

        return TranscriptionResult(
            transcript=transcript,
            language_code=_normalize_language(getattr(response, "language_code", None)),
            request_id=request_id,
        )


class SarvamTextToSpeech:
    """Bulbul-family TTS."""

    def __init__(self, client: AsyncSarvamAI, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def synthesize(
        self, text: str, *, language_code: str | None = None, speaker: str | None = None
    ) -> SynthesisResult:
        settings = self._settings
        if not text.strip():
            raise ProviderError("Cannot synthesize empty text.")

        target_language = language_code or settings.tts_target_language_code

        async def _call() -> Any:
            return await self._client.text_to_speech.convert(
                text=text,
                language_code=target_language,
                speaker=speaker or settings.tts_speaker,
                model=settings.tts_model,
                output_audio_codec=settings.tts_output_audio_codec,
                speech_sample_rate=settings.tts_output_sample_rate,
                request_options={"timeout_in_seconds": settings.voice_api_timeout_seconds},
            )

        response = await _with_retry("text-to-speech", _call, settings=settings)

        chunks = getattr(response, "audios", None) or []
        if not chunks:
            raise ProviderError("Sarvam TTS returned no audio chunks.")
        if len(chunks) > 1:
            logger.warning(
                "Sarvam TTS returned %d audio chunks; using the first — replies are "
                "capped short, so multi-chunk output is unexpected.",
                len(chunks),
            )

        try:
            audio_bytes = base64.b64decode(chunks[0])
        except (ValueError, TypeError) as exc:
            raise ProviderError("Sarvam TTS returned malformed base64 audio.") from exc

        # The response echoes back only {request_id, audios} — the rate we
        # requested is authoritative.
        return SynthesisResult(
            audio_bytes=audio_bytes,
            sample_rate=settings.tts_output_sample_rate,
            language_code=target_language,
            request_id=getattr(response, "request_id", None),
        )


class SarvamLanguageModel:
    """Chat-completions text brain."""

    def __init__(self, client: AsyncSarvamAI, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def complete(
        self, *, system_prompt: str, history: list[ConversationTurn]
    ) -> CompletionResult:
        settings = self._settings
        messages = [{"role": "system", "content": system_prompt}] + [
            {"role": str(turn.role), "content": turn.content} for turn in history
        ]

        async def _call() -> Any:
            return await self._client.chat.completions(
                messages=messages,
                model=settings.sarvam_text_model,
                max_tokens=settings.sarvam_text_max_tokens,
                temperature=settings.sarvam_text_temperature,
                request_options={"timeout_in_seconds": settings.sarvam_text_timeout_seconds},
            )

        response = await _with_retry("chat-completions", _call, settings=settings)

        choices = getattr(response, "choices", None) or []
        message = choices[0].message if choices and getattr(choices[0], "message", None) else None
        text = ((getattr(message, "content", None) if message else None) or "").strip()
        request_id = getattr(response, "request_id", None)

        if not text:
            # Observed in production: Sarvam occasionally returns
            # finish_reason="stop" with content=None (while still billing
            # completion tokens). That is a live model response, not a
            # transport/credit failure — raising would 502 the call and
            # dead-air the caller, so degrade to a safe spoken fallback.
            logger.error(
                "Sarvam chat-completions returned an empty reply; using safe fallback",
                extra={"request_id": request_id},
            )
            return CompletionResult(
                text=_SAFE_FALLBACK_REPLY, request_id=request_id, is_fallback=True
            )

        finish_reason = getattr(choices[0], "finish_reason", None)
        if finish_reason == "length":
            # Ran into the token ceiling mid-sentence; speak only what was finished.
            trimmed = complete_sentences(text)
            logger.warning(
                "Reply hit the token ceiling; trimmed to its last whole sentence",
                extra={
                    "request_id": request_id,
                    "kept": len(trimmed),
                    "cut": len(text) - len(trimmed),
                },
            )
            text = trimmed

        return CompletionResult(text=text, request_id=request_id)
