"""
Composition root.

Every service and store is constructed exactly once, here, during the app
lifespan — never at import time. That is what makes `import app.services.x`
free of side effects, lets tests build a container from fakes, and keeps the
choice of state backend (Redis vs in-memory) in one readable place.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.infra.memory_state import (
    InMemoryCustomerStore,
    InMemoryRateLimiter,
    InMemorySessionStore,
    InMemoryTelemetryStore,
)
from app.infra.redis_state import (
    RedisCustomerStore,
    RedisRateLimiter,
    RedisSessionStore,
    RedisTelemetryStore,
    create_redis,
)
from app.infra.state import CustomerStore, RateLimiter, SessionStore, TelemetryStore
from app.services.conversation import ConversationService
from app.services.crm import CrmService
from app.services.pipeline import CallPipeline
from app.services.sarvam import (
    SarvamLanguageModel,
    SarvamSpeechToText,
    SarvamTextToSpeech,
    create_sarvam_client,
)

logger = get_logger("core.container")


@dataclass(slots=True)
class Container:
    settings: Settings
    pipeline: CallPipeline
    conversation: ConversationService
    crm: CrmService
    telemetry: TelemetryStore
    rate_limiter: RateLimiter
    redis: Redis | None = None

    async def aclose(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()

    async def check_state_backend(self) -> bool:
        """Readiness probe for the state backend."""
        if self.redis is None:
            return True  # In-memory state is always 'reachable'.
        try:
            return bool(await self.redis.ping())
        except Exception:
            logger.exception("Redis ping failed")
            return False


async def build_container(settings: Settings) -> Container:
    redis: Redis | None = None
    sessions: SessionStore
    telemetry: TelemetryStore
    customers: CustomerStore
    rate_limiter: RateLimiter

    if settings.uses_redis:
        redis = create_redis(settings.redis_url)
        await redis.ping()  # Fail the boot, not the first call.
        sessions = RedisSessionStore(
            redis,
            max_turns=settings.max_history_turns,
            ttl_seconds=settings.session_ttl_seconds,
        )
        telemetry = RedisTelemetryStore(redis, max_size=settings.telemetry_history_size)
        redis_customers = RedisCustomerStore(redis)
        await redis_customers.seed_if_empty()
        customers = redis_customers
        rate_limiter = RedisRateLimiter(redis)
        logger.info("State backend: Redis (shared across workers)")
    else:
        sessions = InMemorySessionStore(
            max_turns=settings.max_history_turns,
            ttl_seconds=settings.session_ttl_seconds,
        )
        telemetry = InMemoryTelemetryStore(max_size=settings.telemetry_history_size)
        customers = InMemoryCustomerStore()
        rate_limiter = InMemoryRateLimiter()
        logger.warning(
            "State backend: in-process memory. State is NOT shared across workers "
            "and does NOT survive a restart. Set REDIS_URL for production."
        )

    sarvam = create_sarvam_client(settings)
    conversation = ConversationService(
        language_model=SarvamLanguageModel(sarvam, settings),
        sessions=sessions,
        settings=settings,
    )
    crm = CrmService(customers)
    pipeline = CallPipeline(
        stt=SarvamSpeechToText(sarvam, settings),
        tts=SarvamTextToSpeech(sarvam, settings),
        conversation=conversation,
        crm=crm,
        telemetry=telemetry,
        settings=settings,
    )

    return Container(
        settings=settings,
        pipeline=pipeline,
        conversation=conversation,
        crm=crm,
        telemetry=telemetry,
        rate_limiter=rate_limiter,
        redis=redis,
    )
