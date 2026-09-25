"""
Telemetry routes.

Operator-only: entries carry caller phone numbers and full transcripts.
Aggregates are computed here rather than in each client, so the console, a CLI,
and any future alerting agree on what "p95" means.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.v1.schemas.common import ErrorResponse
from app.api.v1.schemas.telemetry import (
    TelemetryEntryOut,
    TelemetryResponse,
    TelemetrySummary,
)
from app.core.deps import TelemetryDep
from app.core.security import OperatorKeyDep

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get(
    "",
    response_model=TelemetryResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Recent call telemetry with server-computed aggregates",
)
async def get_telemetry(
    telemetry: TelemetryDep,
    _key: OperatorKeyDep,
    limit: Annotated[int, Query(ge=1, le=500, description="Most recent N turns.")] = 200,
    persona: Annotated[str | None, Query(description="Filter to one persona id.")] = None,
) -> TelemetryResponse:
    history = await telemetry.history(limit=limit)
    if persona:
        history = [entry for entry in history if entry.persona == persona]
    return TelemetryResponse(
        summary=TelemetrySummary.from_history(history),
        entries=[TelemetryEntryOut.from_domain(entry) for entry in history],
    )


@router.get(
    "/summary",
    response_model=TelemetrySummary,
    responses={401: {"model": ErrorResponse}},
    summary="Aggregates only — for a polling dashboard header",
)
async def get_telemetry_summary(
    telemetry: TelemetryDep,
    _key: OperatorKeyDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> TelemetrySummary:
    return TelemetrySummary.from_history(await telemetry.history(limit=limit))
