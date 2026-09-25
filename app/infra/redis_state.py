"""
Redis-backed state implementations.

Sharing state through Redis is what makes `--workers N` and horizontal scaling
safe: a call's conversation history, the telemetry feed, and subscriber records
are visible to every worker, and they survive a restart.

Key layout
----------
    ivr:session:{sid}      LIST   JSON turns, oldest first, LTRIM-bounded, TTL'd
    ivr:sessions:active    ZSET   sid -> last-touched unix ts (for a real count)
    ivr:telemetry          LIST   JSON entries, newest first, LTRIM-bounded
    ivr:crm:index          SET    known MSISDNs
    ivr:crm:{msisdn}       STRING JSON CustomerRecord
    ivr:rl:{key}:{bucket}  STRING fixed-window counter, TTL'd
"""

from __future__ import annotations

import time

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import WatchError

from app.core.errors import CustomerNotFoundError, InvalidFieldUpdateError
from app.core.logging import get_logger
from app.domain.models import (
    WRITABLE_CUSTOMER_FIELDS,
    CallTelemetry,
    ConversationTurn,
    CustomerRecord,
)
from app.infra.seed import normalize_msisdn, seed_customers

logger = get_logger("infra.redis")

_SESSION_KEY = "ivr:session:{sid}"
_ACTIVE_SESSIONS_KEY = "ivr:sessions:active"
_TELEMETRY_KEY = "ivr:telemetry"
_CRM_INDEX_KEY = "ivr:crm:index"
_CRM_KEY = "ivr:crm:{msisdn}"
_RATE_LIMIT_KEY = "ivr:rl:{key}:{bucket}"


def create_redis(url: str) -> Redis:
    """Builds the shared async client. `decode_responses` keeps every read a
    `str`, so JSON parsing never has to think about bytes."""
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
        retry_on_timeout=True,
    )


class RedisSessionStore:
    def __init__(self, client: Redis, *, max_turns: int, ttl_seconds: int) -> None:
        self._redis = client
        self._max_messages = max_turns * 2
        self._ttl = ttl_seconds

    async def append(self, session_id: str, turn: ConversationTurn) -> None:
        key = _SESSION_KEY.format(sid=session_id)
        now = time.time()
        pipe = self._redis.pipeline(transaction=True)
        pipe.rpush(key, turn.model_dump_json())
        pipe.ltrim(key, -self._max_messages, -1)
        pipe.expire(key, self._ttl)
        pipe.zadd(_ACTIVE_SESSIONS_KEY, {session_id: now})
        # Keep the active-session index honest: drop anything older than the TTL.
        pipe.zremrangebyscore(_ACTIVE_SESSIONS_KEY, "-inf", now - self._ttl)
        await pipe.execute()

    async def get_history(self, session_id: str) -> list[ConversationTurn]:
        raw = await self._redis.lrange(_SESSION_KEY.format(sid=session_id), 0, -1)
        return [ConversationTurn.model_validate_json(item) for item in raw]

    async def reset(self, session_id: str) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(_SESSION_KEY.format(sid=session_id))
        pipe.zrem(_ACTIVE_SESSIONS_KEY, session_id)
        await pipe.execute()

    async def active_session_count(self) -> int:
        await self._redis.zremrangebyscore(_ACTIVE_SESSIONS_KEY, "-inf", time.time() - self._ttl)
        return int(await self._redis.zcard(_ACTIVE_SESSIONS_KEY))


class RedisTelemetryStore:
    def __init__(self, client: Redis, *, max_size: int) -> None:
        self._redis = client
        self._max_size = max_size

    async def record(self, entry: CallTelemetry) -> None:
        pipe = self._redis.pipeline(transaction=True)
        pipe.lpush(_TELEMETRY_KEY, entry.model_dump_json())
        pipe.ltrim(_TELEMETRY_KEY, 0, self._max_size - 1)
        await pipe.execute()

    async def history(self, limit: int | None = None) -> list[CallTelemetry]:
        count = min(limit or self._max_size, self._max_size)
        raw = await self._redis.lrange(_TELEMETRY_KEY, 0, count - 1)
        # Stored newest-first; callers expect chronological order.
        return [CallTelemetry.model_validate_json(item) for item in reversed(raw)]


class RedisCustomerStore:
    def __init__(self, client: Redis) -> None:
        self._redis = client

    async def seed_if_empty(self) -> None:
        """Materializes the demo registry once. Uses SET NX so a concurrent
        second worker starting at the same moment cannot clobber records the
        first one already wrote (or overwrite live state on redeploy)."""
        pipe = self._redis.pipeline(transaction=False)
        records = seed_customers()
        for record in records:
            pipe.set(_CRM_KEY.format(msisdn=record.phone_number), record.model_dump_json(), nx=True)
        pipe.sadd(_CRM_INDEX_KEY, *[r.phone_number for r in records])
        results = await pipe.execute()
        written = sum(1 for r in results[: len(records)] if r)
        if written:
            logger.info("Seeded %d subscriber record(s) into Redis", written)

    async def get(self, msisdn: str) -> CustomerRecord | None:
        raw = await self._redis.get(_CRM_KEY.format(msisdn=normalize_msisdn(msisdn)))
        return CustomerRecord.model_validate_json(raw) if raw else None

    async def list_all(self) -> list[CustomerRecord]:
        keys = sorted(await self._redis.smembers(_CRM_INDEX_KEY))
        if not keys:
            return []
        raw_values = await self._redis.mget([_CRM_KEY.format(msisdn=k) for k in keys])
        return [CustomerRecord.model_validate_json(v) for v in raw_values if v]

    async def update_field(self, msisdn: str, field: str, value: object) -> CustomerRecord:
        if field not in WRITABLE_CUSTOMER_FIELDS:
            raise InvalidFieldUpdateError(f"'{field}' is not a writable CustomerRecord field")
        key = normalize_msisdn(msisdn)
        redis_key = _CRM_KEY.format(msisdn=key)

        # WATCH/MULTI so a concurrent write to the same subscriber cannot be
        # lost: if the record changed between read and write, retry the read.
        async with self._redis.pipeline(transaction=True) as pipe:
            for _ in range(5):
                try:
                    await pipe.watch(redis_key)
                    raw = await pipe.get(redis_key)
                    if raw is None:
                        raise CustomerNotFoundError(f"No subscriber found for MSISDN {key}")
                    try:
                        updated = CustomerRecord.model_validate_json(raw).with_field(field, value)
                    except ValidationError as exc:
                        raise InvalidFieldUpdateError(
                            f"{value!r} is not a valid value for '{field}'.",
                            public_message=f"'{value}' is not a valid value for {field}.",
                        ) from exc
                    pipe.multi()
                    pipe.set(redis_key, updated.model_dump_json())
                    pipe.sadd(_CRM_INDEX_KEY, key)
                    await pipe.execute()
                    return updated
                except WatchError:
                    continue
        raise CustomerNotFoundError(
            f"Subscriber {key} was being modified concurrently; please retry."
        )


class RedisRateLimiter:
    def __init__(self, client: Redis) -> None:
        self._redis = client

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        if limit <= 0:
            return True
        bucket = int(time.time()) // window_seconds
        redis_key = _RATE_LIMIT_KEY.format(key=key, bucket=bucket)
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds + 1)
        count, _ = await pipe.execute()
        return int(count) <= limit
