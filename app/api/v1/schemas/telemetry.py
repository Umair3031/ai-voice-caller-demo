"""
Telemetry schemas.

The console's charts read these. Two things the previous shape could not do,
which this one can:

  * Break latency down per persona — `CallTelemetry` carries `persona`, so
    Kevin's and Jessika's timings are no longer averaged into one number.
  * Group stages reliably — stages are canonical ids, not free-form strings
    the dashboard re-derived with substring matching on display labels.

The aggregate is computed server-side so every client shows identical numbers.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import CallOutcome, CallTelemetry, InputMode, PipelineStage


class TelemetryEntryOut(BaseModel):
    call_id: str
    session_id: str
    phone_number: str
    persona: str
    timestamp: datetime
    input_mode: InputMode
    detected_language: str | None
    transcript: str | None
    response_text: str | None
    stages: dict[str, float] = Field(
        default_factory=dict, description="Canonical stage id -> elapsed ms."
    )
    total_ms: float
    outcome: CallOutcome
    error_code: str | None
    error_detail: str | None

    @classmethod
    def from_domain(cls, entry: CallTelemetry) -> TelemetryEntryOut:
        data = entry.model_dump()
        data["stages"] = {s.stage: s.elapsed_ms for s in entry.stages}
        return cls(**data)


class StageAverage(BaseModel):
    stage: str
    label: str
    avg_ms: float
    sample_count: int


class PersonaBreakdown(BaseModel):
    persona: str
    call_count: int
    success_rate: float
    avg_total_ms: float


class TelemetrySummary(BaseModel):
    total_calls: int
    success_rate: float = Field(..., description="0.0-1.0, counting degraded turns as successes.")
    avg_total_ms: float
    p95_total_ms: float
    stage_averages: list[StageAverage]
    by_persona: list[PersonaBreakdown]

    @classmethod
    def from_history(cls, history: list[CallTelemetry]) -> TelemetrySummary:
        if not history:
            return cls(
                total_calls=0,
                success_rate=0.0,
                avg_total_ms=0.0,
                p95_total_ms=0.0,
                stage_averages=[],
                by_persona=[],
            )

        totals = sorted(entry.total_ms for entry in history)
        successes = sum(1 for entry in history if entry.success)

        stage_buckets: dict[str, list[float]] = defaultdict(list)
        for entry in history:
            for timing in entry.stages:
                stage_buckets[str(timing.stage)].append(timing.elapsed_ms)

        # Fixed canonical order, so the chart's bars never reshuffle between
        # polls just because a stage was absent from the latest window.
        stage_averages = [
            StageAverage(
                stage=stage.value,
                label=stage.label,
                avg_ms=round(sum(values) / len(values), 2),
                sample_count=len(values),
            )
            for stage in PipelineStage
            if (values := stage_buckets.get(stage.value))
        ]

        persona_buckets: dict[str, list[CallTelemetry]] = defaultdict(list)
        for entry in history:
            persona_buckets[entry.persona].append(entry)

        by_persona = [
            PersonaBreakdown(
                persona=persona,
                call_count=len(entries),
                success_rate=round(sum(1 for e in entries if e.success) / len(entries), 4),
                avg_total_ms=round(sum(e.total_ms for e in entries) / len(entries), 2),
            )
            for persona, entries in sorted(persona_buckets.items())
        ]

        # Nearest-rank p95: with the small windows this buffer holds,
        # interpolation would imply precision the sample size cannot support.
        p95_index = max(0, min(len(totals) - 1, round(0.95 * len(totals)) - 1))

        return cls(
            total_calls=len(history),
            success_rate=round(successes / len(history), 4),
            avg_total_ms=round(sum(totals) / len(totals), 2),
            p95_total_ms=round(totals[p95_index], 2),
            stage_averages=stage_averages,
            by_persona=by_persona,
        )


class TelemetryResponse(BaseModel):
    summary: TelemetrySummary
    entries: list[TelemetryEntryOut]
