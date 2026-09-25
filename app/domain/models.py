"""
Domain models.

These are the engine's own types — independent of both the HTTP schemas in
`app.api.v1.schemas` and the Sarvam SDK's response objects, so neither can
leak into the other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------------------
# Subscriber state — mirrors the finite state sets a real BSS/CRM exposes.
# ------------------------------------------------------------------------------
class PaymentStatus(StrEnum):
    CURRENT = "Current"
    OVERDUE = "Overdue"
    SUSPENDED = "Suspended"
    GRACE_PERIOD = "Grace Period"


class RouterStatus(StrEnum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    REBOOTING = "Rebooting"
    DEGRADED = "Degraded"


class LOSLightState(StrEnum):
    GREEN = "Green"
    RED = "Red"
    BLINKING_RED = "Blinking Red"
    OFF = "Off"


class CustomerRecord(BaseModel):
    """Canonical subscriber record as materialized from a BSS/CRM."""

    model_config = ConfigDict(frozen=True)

    phone_number: str = Field(..., description="MSISDN in E.164 format, e.g. +919810012345.")
    customer_name: str
    account_id: str
    active_plan: str
    plan_data_cap_gb: float = Field(ge=0)
    plan_data_used_gb: float = Field(ge=0)
    account_balance: float
    currency: str = "INR"
    payment_status: PaymentStatus
    support_tier: str = "Standard"
    router_status: RouterStatus
    los_light: LOSLightState
    last_updated: datetime = Field(default_factory=utcnow)

    @property
    def data_usage_ratio(self) -> float:
        if self.plan_data_cap_gb <= 0:
            return 0.0
        return min(self.plan_data_used_gb / self.plan_data_cap_gb, 1.0)

    def with_field(self, field: str, value: object) -> Self:
        """Returns a validated copy with one field changed and `last_updated`
        advanced.

        Goes through `model_validate` rather than `model_copy`, which skips
        validation entirely — that is how an arbitrary string could previously
        be written into an enum-typed telemetry field.
        """
        data = self.model_dump()
        data[field] = value
        data["last_updated"] = utcnow()
        return type(self).model_validate(data)


# Fields an operator (or, later, an agent tool call) may write. Deliberately
# narrower than the full model: identity fields are never writable here.
WRITABLE_CUSTOMER_FIELDS: frozenset[str] = frozenset(
    {
        "active_plan",
        "plan_data_cap_gb",
        "plan_data_used_gb",
        "account_balance",
        "payment_status",
        "support_tier",
        "router_status",
        "los_light",
    }
)


# ------------------------------------------------------------------------------
# Conversation
# ------------------------------------------------------------------------------
class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationTurn(BaseModel):
    role: Role
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    persona: str | None = Field(
        default=None,
        description=(
            "Which persona produced this turn. None for turns stored before this "
            "field existed — treated as belonging to whichever persona is current, "
            "never as evidence of a switch on its own."
        ),
    )


# ------------------------------------------------------------------------------
# Telemetry
# ------------------------------------------------------------------------------
class InputMode(StrEnum):
    AUDIO = "audio"
    TEXT = "text"
    GREETING = "greeting"  # the agent's opening line; the caller has not spoken yet


class PipelineStage(StrEnum):
    """Canonical stage names. The dashboard groups by these, so they are a
    contract — not free-form strings normalized with string matching."""

    INPUT_PROCESSING = "input_processing"
    CRM_LOOKUP = "crm_lookup"
    LLM_GENERATION = "llm_generation"
    TTS_SYNTHESIS = "tts_synthesis"

    @property
    def label(self) -> str:
        return {
            PipelineStage.INPUT_PROCESSING: "Input Processing",
            PipelineStage.CRM_LOOKUP: "CRM Lookup",
            PipelineStage.LLM_GENERATION: "Sarvam Text LLM",
            PipelineStage.TTS_SYNTHESIS: "TTS Synthesis",
        }[self]


class StageTiming(BaseModel):
    stage: PipelineStage
    elapsed_ms: float


class CallOutcome(StrEnum):
    COMPLETED = "completed"
    DEGRADED_TEXT_ONLY = "degraded_text_only"
    FAILED = "failed"


class CallTelemetry(BaseModel):
    """One pipeline turn, as recorded for the operator console."""

    call_id: str
    session_id: str
    phone_number: str
    persona: str
    timestamp: datetime = Field(default_factory=utcnow)
    input_mode: InputMode
    detected_language: str | None = None
    transcript: str | None = None
    response_text: str | None = None
    stages: list[StageTiming] = Field(default_factory=list)
    total_ms: float
    outcome: CallOutcome
    error_code: str | None = None
    error_detail: str | None = None

    @property
    def success(self) -> bool:
        return self.outcome is not CallOutcome.FAILED
