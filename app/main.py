"""
Application factory.

Wires configuration, logging, middleware, exception handling, CORS, and the v1
router, and manages the lifetime of every shared client through the lifespan —
nothing is constructed at import time.

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.api.v1.router import api_router
from app.api.v1.schemas.common import ErrorResponse
from app.core.config import BULBUL_V3_SPEAKERS, Settings, get_settings
from app.core.container import build_container
from app.core.errors import IVRError
from app.core.logging import configure_logging, get_logger, request_id_ctx
from app.domain.personas.registry import PERSONAS, verify_templates

logger = get_logger("main")

API_V1_PREFIX = "/api/v1"


def _assert_worker_safety(settings: Settings) -> None:
    """Refuse a configuration that silently corrupts live calls.

    Without a shared state backend, conversation history, telemetry, and
    subscriber records live inside one process. Running several workers then
    routes consecutive turns of the same call to different processes, and the
    caller's conversation resets mid-sentence with no error anywhere. Failing
    to boot is strictly better than serving that.
    """
    if settings.uses_redis:
        return
    raw = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS") or "1"
    try:
        workers = int(raw)
    except ValueError:
        workers = 1
    if workers > 1:
        raise RuntimeError(
            f"{workers} workers are configured but REDIS_URL is not set. Conversation "
            f"state would not be shared between them and calls would lose their "
            f"history mid-conversation. Set REDIS_URL, or run a single worker."
        )


def _assert_persona_voices(settings: Settings) -> None:
    """Refuse to boot with a per-persona voice the TTS model will reject.

    Same failure mode `Settings._speaker_must_suit_the_model` guards against
    for the global default: an invalid speaker returns HTTP 400 on every
    synthesis call for that persona rather than failing loudly once, here.
    """
    if not settings.tts_model.startswith("bulbul:v3"):
        return
    for persona in PERSONAS.values():
        if persona.tts_speaker is not None and persona.tts_speaker not in BULBUL_V3_SPEAKERS:
            raise RuntimeError(
                f"Persona {persona.id!r} has tts_speaker={persona.tts_speaker!r}, which is "
                f"not a valid voice for {settings.tts_model!r}. Valid voices: "
                f"{', '.join(sorted(BULBUL_V3_SPEAKERS))}."
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    _assert_worker_safety(settings)

    # A malformed persona template is a KeyError on the first live call
    # otherwise; surface it at boot where it belongs.
    verify_templates()
    _assert_persona_voices(settings)

    container = await build_container(settings)
    app.state.container = container
    logger.info(
        "IVR engine ready",
        extra={
            "env": settings.app_env,
            "text_model": settings.sarvam_text_model,
            "stt_model": settings.stt_model,
            "tts_model": settings.tts_model,
            "state_backend": "redis" if container.redis else "memory",
        },
    )
    try:
        yield
    finally:
        await container.aclose()
        logger.info("IVR engine shut down")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    request_id = request_id_ctx.get()
    payload = ErrorResponse(code=code, message=message, request_id=request_id)
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(status_code=status_code, content=payload.model_dump(), headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="NovaConnect Telecoms Conversational IVR Engine",
        description=(
            "Voice pipeline on Sarvam AI: speech-to-text, CRM context, text generation, "
            "and speech synthesis. Every response is a declared JSON schema, so a typed "
            "client can be generated directly from this document."
        ),
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(RequestContextMiddleware)

    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Key", REQUEST_ID_HEADER],
            expose_headers=[REQUEST_ID_HEADER],
            max_age=600,
        )
    else:
        logger.info("CORS disabled (no CORS_ALLOW_ORIGINS configured) — same-origin only")

    # -- Exception handling ----------------------------------------------------
    @app.exception_handler(IVRError)
    async def handle_ivr_error(_: Request, exc: IVRError) -> JSONResponse:
        # `detail` goes to the log; only `public_message` reaches the caller.
        log = logger.warning if exc.status_code < 500 else logger.error
        log("%s: %s", exc.code, exc.detail, extra={"error_code": exc.code})
        return _error_response(exc.status_code, exc.code, exc.public_message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in exc.errors()
        )
        logger.warning("Request validation failed: %s", problems)
        return _error_response(422, "validation_error", problems or "Invalid request.")

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error: %s", exc)
        return _error_response(
            500, "internal_error", "Something went wrong on our side. Please try again."
        )

    app.include_router(api_router, prefix=API_V1_PREFIX)
    return app


app = create_app()
