"""
HTTP middleware.

Assigns each request a correlation id, binds it to the logging context so every
line the request produces carries it, and echoes it back on the response — so a
user reporting "call failed at 14:32" can be traced to the exact three Sarvam
round-trips behind it.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import call_id_ctx, get_logger, request_id_ctx, session_id_ctx

logger = get_logger("api.request")

REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Honour an upstream id (load balancer, frontend) so a trace survives
        # the hop; otherwise mint one.
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request_id_token = request_id_ctx.set(request_id)
        call_id_token = call_id_ctx.set(None)
        session_id_token = session_id_ctx.set(None)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled exception",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        finally:
            request_id_ctx.reset(request_id_token)
            call_id_ctx.reset(call_id_token)
            session_id_ctx.reset(session_id_token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        # Health probes fire constantly; logging them buries real traffic.
        if not request.url.path.endswith(("/healthz", "/readyz")):
            logger.info(
                "%s %s -> %d",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                },
            )
        return response
