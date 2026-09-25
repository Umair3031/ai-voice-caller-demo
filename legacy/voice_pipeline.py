"""
voice_pipeline.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Voice Pipeline (Sarvam AI STT/TTS)
------------------------------------------------------------------------------
Thin, error-hardened async wrapper around the official `sarvamai` SDK:

  * Speech-to-Text  -> Sarvam Saarika/Saaras ASR model family.
  * Text-to-Speech  -> Sarvam Bulbul TTS model family (Bulbul:v3).

Every call differentiates credit exhaustion, rate limiting, and network
timeouts into distinct exception types so `main.py` can surface an accurate,
caller-appropriate fallback (e.g. degrade to text-only) instead of a
generic 500.
==============================================================================
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from sarvamai import AsyncSarvamAI
from sarvamai.core.api_error import ApiError

from config import settings

logger = logging.getLogger("novaconnect_ivr.voice_pipeline")


# ------------------------------------------------------------------------------
# Exception hierarchy — lets main.py map failures to precise caller-facing
# behaviour (retry, escalate to human, or degrade to text-only response).
# ------------------------------------------------------------------------------
class VoicePipelineError(Exception):
    """Base class for all voice pipeline failures."""


class CreditExhaustedError(VoicePipelineError):
    """Raised when the Sarvam AI account is unauthorized or out of credits."""


class VoiceRateLimitedError(VoicePipelineError):
    """Raised when Sarvam AI throttles the request (HTTP 429)."""


class VoiceAPITimeoutError(VoicePipelineError):
    """Raised when a Sarvam AI call exceeds the configured timeout budget."""


# ------------------------------------------------------------------------------
# Result payloads
# ------------------------------------------------------------------------------
@dataclass
class STTResult:
    transcript: str
    language_code: str
    request_id: Optional[str] = None


@dataclass
class TTSResult:
    audio_bytes: bytes
    sample_rate: int
    request_id: Optional[str] = None


class VoicePipeline:
    """
    Async facade over Sarvam AI speech services.

    A single instance owns one `AsyncSarvamAI` client (and its pooled HTTP
    connections), reused across every concurrent IVR session in the process
    so parallel calls do not each pay TLS/connection setup cost.
    """

    def __init__(self) -> None:
        self._client = AsyncSarvamAI(api_subscription_key=settings.sarvam_api_key)
        self._timeout_seconds = settings.voice_api_timeout_seconds

    @staticmethod
    def _extract_error_message(exc: ApiError) -> str:
        """
        Pulls the human-readable message out of a Sarvam `ApiError`.

        `str(exc)` on the underlying SDK exception dumps the *entire* HTTP
        exchange (every response header, the full body, request id) into one
        line — harmless in a log, but unusable if it ever reaches a caller-
        or UI-facing message. Sarvam's error bodies are shaped like
        `{"error": {"message": "...", "code": "..."}}`, so pull that field
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
    def _classify_api_error(exc: ApiError) -> VoicePipelineError:
        """Maps a Sarvam `ApiError` status code onto our exception hierarchy."""
        status_code = getattr(exc, "status_code", None)
        if status_code in (401, 402, 403):
            return CreditExhaustedError(
                f"Sarvam AI rejected the request (HTTP {status_code}) — check API "
                f"key validity and remaining account credits."
            )
        if status_code == 429:
            return VoiceRateLimitedError(
                "Sarvam AI rate limit exceeded (HTTP 429). Back off and retry."
            )
        return VoicePipelineError(
            f"Sarvam AI rejected the request (HTTP {status_code}): "
            f"{VoicePipeline._extract_error_message(exc)}"
        )

    async def speech_to_text(
        self,
        audio_bytes: bytes,
        filename: str = "call_audio.wav",
        language_code: Optional[str] = None,
    ) -> STTResult:
        """
        Transcribes raw audio bytes using the Saarika/Saaras ASR model
        configured in `settings.stt_model`.

        Raises:
            CreditExhaustedError, VoiceRateLimitedError, VoiceAPITimeoutError,
            VoicePipelineError: on the respective failure class.
        """
        try:
            response = await self._client.speech_to_text.transcribe(
                file=(filename, io.BytesIO(audio_bytes)),
                model=settings.stt_model,
                mode="transcribe",  # same-language transcript, not translation
                language_code=language_code or settings.stt_language_code,
                request_options={"timeout_in_seconds": self._timeout_seconds},
            )
        except ApiError as exc:
            logger.error(
                "Sarvam STT ApiError: status=%s body=%s",
                getattr(exc, "status_code", "?"),
                getattr(exc, "body", "?"),
            )
            raise self._classify_api_error(exc) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            logger.error("Sarvam STT timed out after %.1fs", self._timeout_seconds)
            raise VoiceAPITimeoutError(
                f"Speech-to-text timed out after {self._timeout_seconds:.1f}s"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("Sarvam STT transport error: %s", exc)
            raise VoicePipelineError(f"Speech-to-text transport error: {exc}") from exc

        transcript = (getattr(response, "transcript", "") or "").strip()
        detected_language = getattr(response, "language_code", None) or (
            language_code or settings.stt_language_code
        )
        request_id = getattr(response, "request_id", None)

        if not transcript:
            logger.warning(
                "Sarvam STT returned an empty transcript (request_id=%s)", request_id
            )

        return STTResult(
            transcript=transcript,
            language_code=detected_language,
            request_id=request_id,
        )

    async def text_to_speech(
        self,
        text: str,
        speaker: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> TTSResult:
        """
        Synthesizes `text` into speech audio using the Bulbul TTS model
        configured in `settings.tts_model`.

        Raises:
            CreditExhaustedError, VoiceRateLimitedError, VoiceAPITimeoutError,
            VoicePipelineError: on the respective failure class.
        """
        if not text.strip():
            raise VoicePipelineError("Cannot synthesize empty text.")

        try:
            response = await self._client.text_to_speech.convert(
                text=text,
                language_code=language_code or settings.tts_target_language_code,
                speaker=speaker or settings.tts_speaker,
                model=settings.tts_model,
                output_audio_codec=settings.tts_output_audio_codec,
                speech_sample_rate=settings.tts_output_sample_rate,
                request_options={"timeout_in_seconds": self._timeout_seconds},
            )
        except ApiError as exc:
            logger.error(
                "Sarvam TTS ApiError: status=%s body=%s",
                getattr(exc, "status_code", "?"),
                getattr(exc, "body", "?"),
            )
            raise self._classify_api_error(exc) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            logger.error("Sarvam TTS timed out after %.1fs", self._timeout_seconds)
            raise VoiceAPITimeoutError(
                f"Text-to-speech timed out after {self._timeout_seconds:.1f}s"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("Sarvam TTS transport error: %s", exc)
            raise VoicePipelineError(f"Text-to-speech transport error: {exc}") from exc

        audio_chunks = getattr(response, "audios", None) or []
        if not audio_chunks:
            raise VoicePipelineError("Sarvam TTS returned no audio chunks.")
        if len(audio_chunks) > 1:
            logger.warning(
                "Sarvam TTS returned %d audio chunks; using the first — replies "
                "are capped short, so multi-chunk output is unexpected.",
                len(audio_chunks),
            )

        try:
            audio_bytes = base64.b64decode(audio_chunks[0])
        except (ValueError, TypeError) as exc:
            raise VoicePipelineError("Sarvam TTS returned malformed base64 audio.") from exc

        # TextToSpeechResponse only exposes {request_id, audios} — the sample
        # rate we requested is authoritative here, not something the response
        # echoes back.
        request_id = getattr(response, "request_id", None)

        return TTSResult(
            audio_bytes=audio_bytes,
            sample_rate=settings.tts_output_sample_rate,
            request_id=request_id,
        )


# Process-wide singleton, consistent with config.settings / crm_service.crm_service.
voice_pipeline = VoicePipeline()
