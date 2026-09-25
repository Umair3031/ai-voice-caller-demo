"""
State-store protocols.

Every piece of mutable state the engine keeps — conversation history, call
telemetry, subscriber records, rate-limit counters — is defined here as a
Protocol and implemented twice: in-process (`memory_state`) and Redis-backed
(`redis_state`).

This is the fix for the engine's most severe historical defect: state used to
live in module-level dicts while the container ran `uvicorn --workers 2`, so
consecutive turns of one call landed on different workers and conversation
memory silently reset mid-call. Behind these protocols, the service layer is
identical either way; only `app.main` decides which implementation is wired in,
and it refuses to run multiple workers without Redis.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import CallTelemetry, ConversationTurn, CustomerRecord


@runtime_checkable
class SessionStore(Protocol):
    """Bounded, TTL'd conversation history keyed by session id."""

    async def append(self, session_id: str, turn: ConversationTurn) -> None: ...

    async def get_history(self, session_id: str) -> list[ConversationTurn]: ...

    async def reset(self, session_id: str) -> None: ...

    async def active_session_count(self) -> int: ...


@runtime_checkable
class TelemetryStore(Protocol):
    """Capped, newest-first log of recent pipeline turns."""

    async def record(self, entry: CallTelemetry) -> None: ...

    async def history(self, limit: int | None = None) -> list[CallTelemetry]: ...


@runtime_checkable
class CustomerStore(Protocol):
    """Subscriber records. Stands in for a real BSS/CRM client — the async
    signatures are shaped to match one, so swapping this for a gRPC/REST client
    touches nothing above it."""

    async def get(self, msisdn: str) -> CustomerRecord | None: ...

    async def list_all(self) -> list[CustomerRecord]: ...

    async def update_field(self, msisdn: str, field: str, value: object) -> CustomerRecord: ...


@runtime_checkable
class RateLimiter(Protocol):
    """Fixed-window counter. Returns False when the caller is over budget."""

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool: ...


@runtime_checkable
class HealthCheckable(Protocol):
    """Implemented by backends that a readiness probe can ping."""

    async def ping(self) -> bool: ...
