"""
Call pipeline orchestrator.

    audio | text  ->  STT  ->  CRM context  ->  LLM  ->  TTS  ->  audio out

Owns stage timing, telemetry recording, and graceful degradation. It is the
only place that knows the order of the stages; the routers above it just hand
in a request and render the result.

Three defects this layer exists to prevent:

  * Language continuity. The language STT detects is threaded into TTS, so a
    Hindi caller hears a Hindi reply instead of Hindi words spoken by an en-IN
    voice profile.
  * Telemetry dimensionality. Every record carries its persona, language, and
    a typed outcome, so the console can break latency down per agent instead of
    averaging all of them into one number.
  * Honest failure accounting. A turn that fails mid-pipeline is still recorded
    with the stages that did complete, so a failure is visible in the charts
    rather than missing from them.

A fourth: vocal compliance. Personas are prompted to stay under
`reply_max_words`, but a live audit (2026-09-19) caught Kevin at 40 words
against a 15-word budget — the prompt alone does not reliably hold. TTS
synthesis time scales with input length, so an over-budget reply is also
the pipeline's single biggest lever on its own latency. `_enforce_word_budget`
below is the hard backstop: it runs on every reply, after the soft
(log-only) check in `ConversationService`, right before that same text goes
to both TTS and the caller.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.errors import (
    AudioTooLargeError,
    EmptyAudioError,
    IVRError,
    NoSpeechDetectedError,
    ProviderError,
)
from app.core.logging import call_id_ctx, get_logger, session_id_ctx
from app.domain.models import (
    CallOutcome,
    CallTelemetry,
    CustomerRecord,
    InputMode,
    PipelineStage,
    StageTiming,
)
from app.domain.personas.registry import PersonaId, get_persona, render_greeting
from app.infra.state import TelemetryStore
from app.services.conversation import ConversationService
from app.services.crm import CrmService
from app.services.protocols import SpeechToTextService, TextToSpeechService

logger = get_logger("services.pipeline")

# Splits on a sentence-ending punctuation mark followed by whitespace. Good
# enough for the short, plain-spoken replies these personas produce; not
# meant to handle arbitrary prose (abbreviations, decimals, etc.).
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class StageTimer:
    """Accumulates per-stage elapsed times for one turn."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last_mark = self._start
        self.stages: list[StageTiming] = []

    def mark(self, stage: PipelineStage) -> None:
        now = time.perf_counter()
        elapsed_ms = (now - self._last_mark) * 1000.0
        self._last_mark = now
        self.stages.append(StageTiming(stage=stage, elapsed_ms=round(elapsed_ms, 2)))
        logger.info(
            "[%.3fs] %s",
            now - self._start,
            stage.label,
            extra={"stage": stage.value, "elapsed_ms": round(elapsed_ms, 2)},
        )

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000.0, 2)


@dataclass(slots=True)
class TurnResult:
    """Everything one pipeline turn produced. Rendered to JSON by the router."""

    call_id: str
    session_id: str
    persona: PersonaId
    input_mode: InputMode
    transcript: str
    response_text: str
    detected_language: str | None
    spoken_language: str | None
    audio_bytes: bytes | None
    sample_rate: int | None
    stages: list[StageTiming] = field(default_factory=list)
    total_ms: float = 0.0
    outcome: CallOutcome = CallOutcome.COMPLETED
    degraded_reason: str | None = None


@dataclass(slots=True)
class OpenCallResult:
    """The agent's opening line, plus who the demo assigned as the caller."""

    turn: TurnResult
    customer: CustomerRecord | None


ANONYMOUS_CALLER = "anonymous"


class CallPipeline:
    def __init__(
        self,
        *,
        stt: SpeechToTextService,
        tts: TextToSpeechService,
        conversation: ConversationService,
        crm: CrmService,
        telemetry: TelemetryStore,
        settings: Settings,
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._conversation = conversation
        self._crm = crm
        self._telemetry = telemetry
        self._settings = settings

    async def run_turn(
        self,
        *,
        phone_number: str | None,
        persona: PersonaId,
        session_id: str | None = None,
        text: str | None = None,
        audio_bytes: bytes | None = None,
        audio_filename: str = "turn.wav",
    ) -> TurnResult:
        call_id = str(uuid.uuid4())
        resolved_session_id = session_id or str(uuid.uuid4())
        call_id_ctx.set(call_id)
        session_id_ctx.set(resolved_session_id)

        timer = StageTimer()
        input_mode = InputMode.AUDIO if audio_bytes is not None else InputMode.TEXT
        transcript = ""
        detected_language: str | None = None

        try:
            # -- 1. Resolve input text (audio takes precedence over text) ------
            if audio_bytes is not None:
                transcript, detected_language = await self._transcribe(audio_bytes, audio_filename)
            else:
                transcript = (text or "").strip()
                if not transcript:
                    raise EmptyAudioError(
                        "Provide either an 'audio' file or a non-empty 'text' field.",
                        public_message="Provide either an audio clip or some text.",
                    )
            timer.mark(PipelineStage.INPUT_PROCESSING)

            # -- 2. CRM context -------------------------------------------------
            customer = await self._crm.get_customer(phone_number) if phone_number else None
            timer.mark(PipelineStage.CRM_LOOKUP)

            # -- 3. Dialogue generation -----------------------------------------
            response_text = await self._conversation.respond(
                session_id=resolved_session_id,
                user_text=transcript,
                customer=customer,
                persona=persona,
            )
            response_text = self._enforce_word_budget(response_text)
            timer.mark(PipelineStage.LLM_GENERATION)

        except IVRError as exc:
            await self._record(
                CallTelemetry(
                    call_id=call_id,
                    session_id=resolved_session_id,
                    phone_number=phone_number or ANONYMOUS_CALLER,
                    persona=persona.value,
                    input_mode=input_mode,
                    detected_language=detected_language,
                    transcript=transcript or None,
                    response_text=None,
                    stages=timer.stages,
                    total_ms=timer.total_ms,
                    outcome=CallOutcome.FAILED,
                    error_code=exc.code,
                    error_detail=exc.detail,
                )
            )
            raise

        # -- 4. Speech synthesis (degrade to text rather than failing) ---------
        spoken_language = self._resolve_tts_language(detected_language)
        audio: bytes | None = None
        sample_rate: int | None = None
        outcome = CallOutcome.COMPLETED
        degraded_reason: str | None = None

        try:
            synthesis = await self._tts.synthesize(
                response_text,
                language_code=spoken_language,
                speaker=get_persona(persona).tts_speaker,
            )
            audio = synthesis.audio_bytes
            sample_rate = synthesis.sample_rate
            spoken_language = synthesis.language_code
            timer.mark(PipelineStage.TTS_SYNTHESIS)
        except ProviderError as exc:
            # The caller still gets the answer as text — losing the voice is
            # bad, losing the reply entirely is worse.
            timer.mark(PipelineStage.TTS_SYNTHESIS)
            outcome = CallOutcome.DEGRADED_TEXT_ONLY
            degraded_reason = exc.public_message
            spoken_language = None
            logger.warning("TTS unavailable; degrading to text-only", extra={"error": exc.detail})

        await self._record(
            CallTelemetry(
                call_id=call_id,
                session_id=resolved_session_id,
                phone_number=phone_number or ANONYMOUS_CALLER,
                persona=persona.value,
                input_mode=input_mode,
                detected_language=detected_language,
                transcript=transcript,
                response_text=response_text,
                stages=timer.stages,
                total_ms=timer.total_ms,
                outcome=outcome,
                error_code=None if outcome is CallOutcome.COMPLETED else "tts_unavailable",
                error_detail=degraded_reason,
            )
        )

        return TurnResult(
            call_id=call_id,
            session_id=resolved_session_id,
            persona=persona,
            input_mode=input_mode,
            transcript=transcript,
            response_text=response_text,
            detected_language=detected_language,
            spoken_language=spoken_language,
            audio_bytes=audio,
            sample_rate=sample_rate,
            stages=timer.stages,
            total_ms=timer.total_ms,
            outcome=outcome,
            degraded_reason=degraded_reason,
        )

    async def open_call(self, *, persona: PersonaId) -> OpenCallResult:
        """Starts a call with the agent speaking first.

        Assigns the caller (a random demo subscriber for the agents that take
        existing customers, nobody for pre-sales), greets them by name, speaks the
        greeting, and stores it as the first turn so the caller's reply lands in
        context. Like a turn, losing the voice degrades to text instead of failing.
        """
        call_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        call_id_ctx.set(call_id)
        session_id_ctx.set(session_id)

        timer = StageTimer()
        profile = get_persona(persona)
        customer = await self._crm.pick_demo_subscriber(profile.account_pool)
        timer.mark(PipelineStage.CRM_LOOKUP)

        first_name = customer.customer_name.split()[0] if customer else None
        greeting = render_greeting(persona, first_name=first_name)
        await self._conversation.open_call(
            session_id=session_id, persona=persona, greeting=greeting
        )

        audio: bytes | None = None
        sample_rate: int | None = None
        outcome = CallOutcome.COMPLETED
        degraded_reason: str | None = None
        spoken_language: str | None = self._settings.tts_target_language_code
        try:
            synthesis = await self._tts.synthesize(
                greeting, language_code=spoken_language, speaker=profile.tts_speaker
            )
            audio = synthesis.audio_bytes
            sample_rate = synthesis.sample_rate
            spoken_language = synthesis.language_code
        except ProviderError as exc:
            outcome = CallOutcome.DEGRADED_TEXT_ONLY
            degraded_reason = exc.public_message
            spoken_language = None
            logger.warning(
                "TTS unavailable for greeting; degrading to text", extra={"error": exc.detail}
            )
        timer.mark(PipelineStage.TTS_SYNTHESIS)

        await self._record(
            CallTelemetry(
                call_id=call_id,
                session_id=session_id,
                phone_number=customer.phone_number if customer else ANONYMOUS_CALLER,
                persona=persona.value,
                input_mode=InputMode.GREETING,
                detected_language=None,
                transcript=None,
                response_text=greeting,
                stages=timer.stages,
                total_ms=timer.total_ms,
                outcome=outcome,
                error_code=None if outcome is CallOutcome.COMPLETED else "tts_unavailable",
                error_detail=degraded_reason,
            )
        )
        turn = TurnResult(
            call_id=call_id,
            session_id=session_id,
            persona=persona,
            input_mode=InputMode.GREETING,
            transcript="",
            response_text=greeting,
            detected_language=None,
            spoken_language=spoken_language,
            audio_bytes=audio,
            sample_rate=sample_rate,
            stages=timer.stages,
            total_ms=timer.total_ms,
            outcome=outcome,
            degraded_reason=degraded_reason,
        )
        return OpenCallResult(turn=turn, customer=customer)

    # --------------------------------------------------------------------------
    async def _transcribe(self, audio_bytes: bytes, filename: str) -> tuple[str, str | None]:
        if not audio_bytes:
            raise EmptyAudioError("Uploaded audio file is empty.")
        if len(audio_bytes) > self._settings.max_audio_upload_bytes:
            raise AudioTooLargeError(
                f"Audio upload is {len(audio_bytes)} bytes; the ceiling is "
                f"{self._settings.max_audio_upload_bytes}."
            )

        result = await self._stt.transcribe(audio_bytes, filename=filename)
        if not result.transcript:
            raise NoSpeechDetectedError("Could not transcribe any speech from the provided audio.")
        return result.transcript, result.language_code

    def _resolve_tts_language(self, detected_language: str | None) -> str:
        """Speak the reply in the language the caller used.

        Without this, every reply was synthesized at the configured default
        (en-IN) regardless of what STT detected — so a Hindi conversation came
        back as Hindi text read by an English voice profile.
        """
        if self._settings.tts_follow_detected_language and detected_language:
            return detected_language
        return self._settings.tts_target_language_code

    def _enforce_word_budget(self, text: str) -> str:
        """Hard backstop on `reply_hard_max_words`, applied to whatever is about to
        be spoken and returned to the caller. The prompt aims lower
        (`reply_max_words`); this only catches a reply that has run far past it.

        Keeps only as many whole leading sentences as fit the budget — never
        a mid-sentence chop. `ConversationService`'s own check on this same
        text is soft (log-only) by deliberate design, because truncating
        blind can silently drop the actual answer (a balance figure, a
        safety instruction). This backstop keeps that property: it drops
        whole trailing sentences, never cuts one open, and if even the first
        sentence alone exceeds the budget, that sentence is kept whole
        rather than mutilated further — an over-length reply is still
        better than a broken one.

        Shorter text is also the pipeline's biggest lever on its own
        latency: TTS synthesis time scales with input length. This does not
        *guarantee* any particular wall-clock synthesis time — that also
        depends on the provider and network — but it removes the one input
        this pipeline actually controls.
        """
        budget = self._settings.reply_hard_max_words
        words = text.split()
        if len(words) <= budget:
            return text

        sentences = [s for s in _SENTENCE_BOUNDARY.split(text.strip()) if s]
        if not sentences:
            return text

        kept: list[str] = []
        kept_words = 0
        for sentence in sentences:
            sentence_words = len(sentence.split())
            if kept and kept_words + sentence_words > budget:
                break
            kept.append(sentence)
            kept_words += sentence_words

        trimmed = " ".join(kept).strip() or sentences[0]
        if trimmed != text:
            logger.warning(
                "Trimmed an over-budget reply for vocal delivery",
                extra={
                    "original_words": len(words),
                    "trimmed_words": len(trimmed.split()),
                    "budget": budget,
                },
            )
        return trimmed

    async def _record(self, entry: CallTelemetry) -> None:
        try:
            await self._telemetry.record(entry)
        except Exception:
            # Telemetry is observability, not the product. Never fail a live
            # call because the metrics backend hiccuped.
            logger.exception("Failed to record call telemetry")
