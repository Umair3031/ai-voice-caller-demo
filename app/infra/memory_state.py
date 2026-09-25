"""
In-process state implementations.

Used for local development, tests, and any deployment without Redis. Every
store is guarded by an `asyncio.Lock`, so concurrent sessions inside one worker
are safe — but nothing is shared across workers or survives a restart, which is
why `app.main` refuses to start more than one worker on this backend.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

from pydantic import ValidationError

from app.core.errors import CustomerNotFoundError, InvalidFieldUpdateError
from app.domain.models import (
    WRITABLE_CUSTOMER_FIELDS,
    CallTelemetry,
    ConversationTurn,
    CustomerRecord,
)
from app.infra.seed import normalize_msisdn, seed_customers


class InMemorySessionStore:
    def __init__(self, *, max_turns: int, ttl_seconds: int) -> None:
        # One conversational turn is a user+assistant message pair.
        self._max_messages = max_turns * 2
        self._ttl = ttl_seconds
        self._sessions: dict[str, deque[ConversationTurn]] = {}
        self._touched: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _evict_expired_locked(self) -> None:
        """Bounds the *number* of sessions, not just each session's length.

        The dict itself was previously unbounded: an abandoned call left its
        history resident forever, so memory grew with every caller who never
        pressed End Call.
        """
        cutoff = time.monotonic() - self._ttl
        for session_id, touched in list(self._touched.items()):
            if touched < cutoff:
                self._sessions.pop(session_id, None)
                self._touched.pop(session_id, None)

    async def append(self, session_id: str, turn: ConversationTurn) -> None:
        async with self._lock:
            self._evict_expired_locked()
            buffer = self._sessions.setdefault(session_id, deque(maxlen=self._max_messages))
            buffer.append(turn)
            self._touched[session_id] = time.monotonic()

    async def get_history(self, session_id: str) -> list[ConversationTurn]:
        async with self._lock:
            self._evict_expired_locked()
            return list(self._sessions.get(session_id, ()))

    async def reset(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)
            self._touched.pop(session_id, None)

    async def active_session_count(self) -> int:
        async with self._lock:
            self._evict_expired_locked()
            return len(self._sessions)


class InMemoryTelemetryStore:
    def __init__(self, *, max_size: int) -> None:
        self._buffer: deque[CallTelemetry] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()

    async def record(self, entry: CallTelemetry) -> None:
        async with self._lock:
            self._buffer.append(entry)

    async def history(self, limit: int | None = None) -> list[CallTelemetry]:
        async with self._lock:
            items = list(self._buffer)
        return items[-limit:] if limit else items


class InMemoryCustomerStore:
    def __init__(self) -> None:
        self._registry: dict[str, CustomerRecord] = {
            record.phone_number: record for record in seed_customers()
        }
        self._lock = asyncio.Lock()

    async def get(self, msisdn: str) -> CustomerRecord | None:
        key = normalize_msisdn(msisdn)
        async with self._lock:
            # CustomerRecord is frozen, so handing out the instance is safe —
            # no defensive copy needed.
            return self._registry.get(key)

    async def list_all(self) -> list[CustomerRecord]:
        async with self._lock:
            return list(self._registry.values())

    async def update_field(self, msisdn: str, field: str, value: object) -> CustomerRecord:
        key = normalize_msisdn(msisdn)
        if field not in WRITABLE_CUSTOMER_FIELDS:
            raise InvalidFieldUpdateError(f"'{field}' is not a writable CustomerRecord field")
        async with self._lock:
            record = self._registry.get(key)
            if record is None:
                raise CustomerNotFoundError(f"No subscriber found for MSISDN {key}")
            try:
                updated = record.with_field(field, value)
            except ValidationError as exc:
                raise InvalidFieldUpdateError(
                    f"{value!r} is not a valid value for '{field}'.",
                    public_message=f"'{value}' is not a valid value for {field}.",
                ) from exc
            self._registry[key] = updated
            return updated


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._windows: dict[tuple[str, int], int] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return True
        now = int(time.time())
        bucket = now // window_seconds
        async with self._lock:
            # Drop every window older than the current one.
            self._windows = {k: v for k, v in self._windows.items() if k[1] >= bucket}
            count = self._windows.get((key, bucket), 0) + 1
            self._windows[(key, bucket)] = count
            return count <= limit
