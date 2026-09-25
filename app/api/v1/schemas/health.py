"""
Health schemas.

Liveness and readiness are separate on purpose. The previous `/health` returned
`{"status": "ok"}` without checking anything, and the deploy's health check
pointed at it — so a container booted with a dead or expired API key reported
healthy while every call it accepted failed with a payment error.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness: the process is up and serving. Cheap, never touches a network."""

    status: str = Field(..., examples=["ok"])
    env: str
    version: str


class DependencyStatus(BaseModel):
    name: str
    ready: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Readiness: the process can actually handle a call right now."""

    status: str = Field(..., examples=["ready", "degraded"])
    env: str
    version: str
    state_backend: str = Field(..., examples=["redis", "memory"])
    active_sessions: int
    personas: list[str]
    dependencies: list[DependencyStatus]
