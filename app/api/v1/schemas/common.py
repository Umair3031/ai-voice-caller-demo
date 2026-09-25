"""Shared response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Uniform error envelope for every non-2xx response.

    `code` is a stable machine-readable identifier the frontend switches on;
    `message` is safe to show a caller. Internal provider strings never appear
    in either.
    """

    code: str = Field(..., examples=["provider_rate_limited"])
    message: str = Field(..., examples=["We're handling a lot of calls right now."])
    request_id: str | None = Field(
        default=None, description="Correlates this response with server logs."
    )


class StageTimingOut(BaseModel):
    stage: str = Field(..., description="Canonical stage id.", examples=["llm_generation"])
    label: str = Field(..., description="Display label.", examples=["Sarvam Text LLM"])
    elapsed_ms: float
