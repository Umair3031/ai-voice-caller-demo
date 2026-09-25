"""
FastAPI dependencies.

Routers ask for the collaborator they need; nothing reaches for a module-level
singleton. Overriding any of these in a test is a one-liner
(`app.dependency_overrides[get_pipeline] = ...`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings
from app.core.container import Container
from app.infra.state import RateLimiter, TelemetryStore
from app.services.conversation import ConversationService
from app.services.crm import CrmService
from app.services.pipeline import CallPipeline


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def get_settings_dep(container: Annotated[Container, Depends(get_container)]) -> Settings:
    return container.settings


def get_pipeline(container: Annotated[Container, Depends(get_container)]) -> CallPipeline:
    return container.pipeline


def get_conversation(
    container: Annotated[Container, Depends(get_container)],
) -> ConversationService:
    return container.conversation


def get_crm(container: Annotated[Container, Depends(get_container)]) -> CrmService:
    return container.crm


def get_telemetry(container: Annotated[Container, Depends(get_container)]) -> TelemetryStore:
    return container.telemetry


def get_rate_limiter(container: Annotated[Container, Depends(get_container)]) -> RateLimiter:
    return container.rate_limiter


ContainerDep = Annotated[Container, Depends(get_container)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
PipelineDep = Annotated[CallPipeline, Depends(get_pipeline)]
ConversationDep = Annotated[ConversationService, Depends(get_conversation)]
CrmDep = Annotated[CrmService, Depends(get_crm)]
TelemetryDep = Annotated[TelemetryStore, Depends(get_telemetry)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
