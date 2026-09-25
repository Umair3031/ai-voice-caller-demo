"""
Structured logging with request correlation.

Every log line emitted while handling a request carries the same `request_id`,
so the three Sarvam round-trips of one call (STT, chat, TTS) can be stitched
together in a log aggregator.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
call_id_ctx: ContextVar[str | None] = ContextVar("call_id", default=None)
session_id_ctx: ContextVar[str | None] = ContextVar("session_id", default=None)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


class ContextFilter(logging.Filter):
    """Stamps the active request/call/session ids onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        record.call_id = call_id_ctx.get()
        record.session_id = session_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line — the shape log aggregators expect."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "call_id", "session_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything passed through `logger.info(..., extra={...})`.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable local-development format."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = getattr(record, "request_id", None)
        return f"{base}  [req={rid}]" if rid else base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Installs the root handler. Idempotent — safe to call from a test fixture."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"novaconnect_ivr.{name}")
