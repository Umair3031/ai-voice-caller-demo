"""
Health routes.

`/healthz` answers "is this process alive" and never touches the network, so a
slow dependency cannot get a healthy container killed.

`/readyz` answers "can this process take a call", and is what a deploy's health
check should point at: it verifies the state backend responds and that persona
templates render. Both are public — they expose no caller data.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.v1.schemas.health import DependencyStatus, HealthResponse, ReadinessResponse
from app.core.deps import ContainerDep, ConversationDep
from app.core.logging import get_logger
from app.domain.personas.registry import PERSONAS, verify_templates

router = APIRouter(tags=["ops"])
logger = get_logger("api.health")

API_VERSION = "2.0.0"


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz(container: ContainerDep) -> HealthResponse:
    return HealthResponse(status="ok", env=container.settings.app_env, version=API_VERSION)


@router.get("/readyz", response_model=ReadinessResponse, summary="Readiness probe")
async def readyz(
    container: ContainerDep,
    conversation: ConversationDep,
    response: Response,
) -> ReadinessResponse:
    dependencies: list[DependencyStatus] = []

    backend_name = "redis" if container.redis is not None else "memory"
    backend_ready = await container.check_state_backend()
    dependencies.append(
        DependencyStatus(
            name=f"state:{backend_name}",
            ready=backend_ready,
            detail=None if backend_ready else "Backend did not respond to a ping.",
        )
    )

    try:
        verify_templates()
        dependencies.append(DependencyStatus(name="persona_templates", ready=True))
    except Exception as exc:
        logger.exception("Persona templates failed to render")
        dependencies.append(
            DependencyStatus(name="persona_templates", ready=False, detail=str(exc)[:200])
        )

    active_sessions = 0
    if backend_ready:
        try:
            active_sessions = await conversation.active_session_count()
        except Exception:
            logger.exception("Could not read active session count")

    ready = all(dep.ready for dep in dependencies)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if ready else "degraded",
        env=container.settings.app_env,
        version=API_VERSION,
        state_backend=backend_name,
        active_sessions=active_sessions,
        personas=[persona.value for persona in PERSONAS],
        dependencies=dependencies,
    )
