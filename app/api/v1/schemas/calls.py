"""
Call-pipeline schemas.

The turn response is deliberately **one** JSON shape, always. The previous
engine returned either an `audio/wav` stream or a JSON body depending on
whether TTS succeeded, with transcript and reply text smuggled through response
headers. That had two consequences worth naming:

  * Headers must be ASCII, so every Hindi / Tamil / Kannada transcript was
    silently erased to an empty string on an explicitly Indic voice stack.
  * OpenAPI could describe neither branch, so no typed client could be
    generated and both frontends hand-sniffed `Content-Type`.

Audio now rides in the JSON body as base64. Replies are a couple of short sentences, so
the payload is a few KB and the 33% base64 overhead is irrelevant next to a
generated, type-safe client and transcripts that survive in any script.
"""

from __future__ import annotations

import base64

from pydantic import BaseModel, Field, computed_field

from app.api.v1.schemas.common import StageTimingOut
from app.domain.models import CallOutcome, ConversationTurn, InputMode, PipelineStage
from app.domain.personas.registry import Persona, PersonaId
from app.services.crm import suggest_opener
from app.services.pipeline import OpenCallResult, TurnResult


class PersonaOut(BaseModel):
    """One agent in the roster. The frontend renders its picker from this, so
    the UI can never offer a persona the engine would reject."""

    id: PersonaId
    display_name: str
    initial: str
    role: str
    pitch: str
    tags: list[str]
    accent: str

    @classmethod
    def from_domain(cls, persona: Persona) -> PersonaOut:
        return cls(**persona.model_dump())


class TurnResponse(BaseModel):
    call_id: str
    session_id: str = Field(
        ..., description="Pass this back on the next turn to continue the conversation."
    )
    persona: PersonaId
    input_mode: InputMode

    transcript: str = Field(..., description="What the caller said, in the original script.")
    response_text: str = Field(..., description="What the agent replied, in the original script.")

    detected_language: str | None = Field(
        default=None, description="BCP-47 language STT detected, if any."
    )
    spoken_language: str | None = Field(
        default=None, description="BCP-47 language the reply was actually synthesized in."
    )

    audio_base64: str | None = Field(
        default=None, description="Base64-encoded WAV of the reply. Null when TTS was unavailable."
    )
    audio_media_type: str | None = Field(default=None, examples=["audio/wav"])
    sample_rate: int | None = None

    stages: list[StageTimingOut]
    total_ms: float
    outcome: CallOutcome
    degraded_reason: str | None = Field(
        default=None, description="Set when `outcome` is `degraded_text_only`."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_audio(self) -> bool:
        return self.audio_base64 is not None

    @classmethod
    def from_result(cls, result: TurnResult) -> TurnResponse:
        return cls(
            call_id=result.call_id,
            session_id=result.session_id,
            persona=result.persona,
            input_mode=result.input_mode,
            transcript=result.transcript,
            response_text=result.response_text,
            detected_language=result.detected_language,
            spoken_language=result.spoken_language,
            audio_base64=(
                base64.b64encode(result.audio_bytes).decode("ascii") if result.audio_bytes else None
            ),
            audio_media_type="audio/wav" if result.audio_bytes else None,
            sample_rate=result.sample_rate,
            stages=[
                StageTimingOut(
                    stage=timing.stage,
                    label=PipelineStage(timing.stage).label,
                    elapsed_ms=timing.elapsed_ms,
                )
                for timing in result.stages
            ],
            total_ms=result.total_ms,
            outcome=result.outcome,
            degraded_reason=result.degraded_reason,
        )


class CallerOut(BaseModel):
    """Who the demo assigned as the caller, so the page can tell the visitor who
    they are for this call. Absent for a pre-sales call: a prospect has no account."""

    name: str
    phone_number: str
    plan: str
    suggestion: str = Field(
        ..., description="A line the visitor could open with, given this account."
    )


class OpenCallResponse(TurnResponse):
    """The agent's opening line. Same shape as a turn — `transcript` is empty
    because the caller has not spoken — plus who the caller is."""

    caller: CallerOut | None = Field(
        default=None, description="Null when the persona takes prospects with no account."
    )

    @classmethod
    def from_open_result(cls, result: OpenCallResult) -> OpenCallResponse:
        base = TurnResponse.from_result(result.turn)
        customer = result.customer
        caller = (
            CallerOut(
                name=customer.customer_name,
                phone_number=customer.phone_number,
                plan=customer.active_plan,
                suggestion=suggest_opener(customer),
            )
            if customer
            else None
        )
        return cls(**base.model_dump(exclude={"has_audio"}), caller=caller)


class ConversationTurnOut(BaseModel):
    role: str
    content: str
    timestamp: str
    persona: str | None = Field(
        default=None, description="Which persona produced this turn, if known."
    )

    @classmethod
    def from_domain(cls, turn: ConversationTurn) -> ConversationTurnOut:
        return cls(
            role=str(turn.role),
            content=turn.content,
            timestamp=turn.timestamp.isoformat(),
            persona=turn.persona,
        )


class SessionHistoryResponse(BaseModel):
    """Lets a frontend rehydrate a conversation after a reload, instead of
    losing the transcript with the page."""

    session_id: str
    turns: list[ConversationTurnOut]


class SessionResetResponse(BaseModel):
    session_id: str
    status: str = "reset"
