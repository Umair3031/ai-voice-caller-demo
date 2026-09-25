"""Aggregates every v1 router under a single mount point."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import calls, crm, health, telemetry

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(calls.router)
api_router.include_router(crm.router)
api_router.include_router(telemetry.router)
