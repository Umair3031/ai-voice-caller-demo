"""
Access control and throttling.

Two tiers:

  * Operator endpoints (subscriber records, telemetry, session inspection)
    require an `X-API-Key` header. These expose caller phone numbers, names,
    balances, and transcripts, so they are closed by default: with no keys
    configured the endpoints reject every request rather than serving them
    openly.
  * The public pipeline endpoint is unauthenticated by design — it is the
    customer-facing line — but rate-limited per caller so a paid, per-call
    billed provider cannot be drained by a loop.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.core.config import Settings
from app.core.deps import RateLimiterDep, SettingsDep
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.logging import get_logger

logger = get_logger("core.security")

API_KEY_HEADER = "X-API-Key"

_api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    description="Operator API key. Configure via OPERATOR_API_KEYS.",
)


def _matches_any(candidate: str, allowed: frozenset[str]) -> bool:
    # Constant-time comparison against each key so a timing side channel
    # cannot be used to recover one byte at a time.
    return any(hmac.compare_digest(candidate, key) for key in allowed)


async def require_operator_key(
    settings: SettingsDep,
    api_key: Annotated[str | None, Depends(_api_key_scheme)] = None,
) -> str:
    allowed = settings.operator_api_key_set
    if not allowed:
        logger.error("Operator endpoint refused: OPERATOR_API_KEYS is not configured")
        raise UnauthorizedError(
            "OPERATOR_API_KEYS is not configured; operator endpoints are disabled.",
            public_message=(
                "Operator endpoints are disabled because no API keys are configured on the server."
            ),
        )
    if not api_key or not _matches_any(api_key, allowed):
        raise UnauthorizedError("Missing or invalid operator API key.")
    return api_key


OperatorKeyDep = Annotated[str, Depends(require_operator_key)]


def _client_identity(request: Request) -> str:
    """Best-effort caller identity for throttling.

    Behind Render/any reverse proxy the socket peer is the proxy, so prefer the
    first hop in `X-Forwarded-For`. This is a throttling heuristic, not an
    authentication signal — the header is client-controlled and is never used
    for access decisions.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_turn_rate_limit(
    request: Request,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> None:
    limit = settings.rate_limit_turns_per_minute
    if limit <= 0:
        return
    identity = _client_identity(request)
    if not await limiter.allow(f"turn:{identity}", limit=limit, window_seconds=60):
        logger.warning("Rate limit exceeded", extra={"identity": identity, "limit": limit})
        raise RateLimitedError(f"Rate limit of {limit} turns/minute exceeded for {identity}.")


def build_cors_origins(settings: Settings) -> list[str]:
    """Explicit origins only; see `Settings._reject_wildcard_cors`."""
    return settings.cors_origin_list
