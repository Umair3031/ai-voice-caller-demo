"""
main.py
==============================================================================
NovaConnect Telecoms Conversational IVR — FastAPI Execution Engine
------------------------------------------------------------------------------
Exposes the production voice pipeline — entirely on Sarvam AI, end to end:

    audio|text in -> STT -> CRM context -> Sarvam Text LLM (agent_brain) -> TTS -> audio out

High-precision, per-stage telemetry (e.g. "[0.2s] Sarvam Text LLM response
generated", "[0.6s] Audio generated") is recorded on every call into a
bounded in-memory ring buffer that the Streamlit dashboard polls for live
latency charts. The endpoint degrades gracefully to a JSON text response if
TTS is unavailable, rather than failing the whole call.
==============================================================================
"""

from __future__ import annotations

import io
import logging
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncio
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from agent_brain import (
    DEFAULT_PERSONA,
    AgentBrainError,
    AgentCreditExhaustedError,
    AgentRateLimitedError,
    AgentTimeoutError,
    agent_brain,
)
from config import settings
from crm_service import CustomerRecord, crm_service
from voice_pipeline import (
    CreditExhaustedError,
    VoiceAPITimeoutError,
    VoicePipelineError,
    VoiceRateLimitedError,
    voice_pipeline,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("novaconnect_ivr.main")


# ------------------------------------------------------------------------------
# Telemetry models + bounded ring buffer
# ------------------------------------------------------------------------------
class StageTiming(BaseModel):
    stage: str
    elapsed_ms: float


class CallTelemetry(BaseModel):
    call_id: str
    session_id: str
    phone_number: str
    timestamp: datetime
    input_mode: str  # "audio" | "text"
    transcript: Optional[str] = None
    response_text: Optional[str] = None
    stages: list[StageTiming]
    total_ms: float
    success: bool
    error: Optional[str] = None


class TelemetryStore:
    """Bounded, async-safe ring buffer of recent call telemetry for the dashboard."""

    def __init__(self, max_size: int) -> None:
        self._buffer: deque[CallTelemetry] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()

    async def record(self, entry: CallTelemetry) -> None:
        async with self._lock:
            self._buffer.append(entry)

    async def history(self) -> list[CallTelemetry]:
        async with self._lock:
            return list(self._buffer)


telemetry_store = TelemetryStore(max_size=settings.telemetry_history_size)


class StageTimer:
    """Accumulates high-precision [stage_name -> elapsed_ms] markers for one call."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last_mark = self._start
        self.stages: list[StageTiming] = []

    def mark(self, stage_name: str) -> None:
        now = time.perf_counter()
        elapsed_ms = (now - self._last_mark) * 1000.0
        self._last_mark = now
        self.stages.append(StageTiming(stage=stage_name, elapsed_ms=round(elapsed_ms, 2)))
        logger.info("[%.3fs] %s", now - self._start, stage_name)

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000.0, 2)


# ------------------------------------------------------------------------------
# App lifecycle
# ------------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "NovaConnect Telecoms IVR engine starting | env=%s | sarvam_text_model=%s | stt_model=%s | tts_model=%s",
        settings.app_env,
        settings.sarvam_text_model,
        settings.stt_model,
        settings.tts_model,
    )
    yield
    logger.info("NovaConnect Telecoms IVR engine shutting down.")


app = FastAPI(
    title="NovaConnect Telecoms Conversational IVR Engine",
    description="Production voice pipeline, entirely on Sarvam AI: STT -> CRM -> Sarvam Text LLM -> TTS",
    version="1.0.0",
    lifespan=lifespan,
)

# The Streamlit dashboard typically runs on a separate origin/port; allow it
# through CORS. No cookies/credentials are used between the two services.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


# ------------------------------------------------------------------------------
# Customer-facing landing page: talk to a persona live, in the browser.
# ------------------------------------------------------------------------------
@app.get("/", tags=["frontend"], include_in_schema=False)
async def live_line_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "live-line.html")


# ------------------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------------------
class CustomerResponse(BaseModel):
    phone_number: str
    customer_name: str
    account_id: str
    active_plan: str
    plan_data_cap_gb: float
    plan_data_used_gb: float
    account_balance: float
    currency: str
    payment_status: str
    support_tier: str
    router_status: str
    los_light: str
    last_updated: datetime


def _customer_to_response(record: CustomerRecord) -> CustomerResponse:
    return CustomerResponse(**record.model_dump())


class ProcessVoiceTextResponse(BaseModel):
    """Returned instead of an audio stream when TTS is unavailable (degraded mode)."""

    call_id: str
    session_id: str
    transcript: Optional[str]
    response_text: str
    stages: list[StageTiming]
    total_ms: float
    degraded_reason: str


# ------------------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------------------
async def _record_failure(
    timer: StageTimer,
    call_id: str,
    session_id: str,
    phone_number: str,
    input_mode: str,
    error: str,
    transcript: Optional[str] = None,
) -> None:
    await telemetry_store.record(
        CallTelemetry(
            call_id=call_id,
            session_id=session_id,
            phone_number=phone_number,
            timestamp=datetime.now(timezone.utc),
            input_mode=input_mode,
            transcript=transcript,
            response_text=None,
            stages=timer.stages,
            total_ms=timer.total_ms,
            success=False,
            error=error,
        )
    )


def _sanitize_header_value(value: str) -> str:
    """HTTP header values must be single-line and ASCII-safe."""
    return value.replace("\n", " ").replace("\r", " ").encode("ascii", "ignore").decode("ascii")


# ------------------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------------------
@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "active_sessions": await agent_brain.active_session_count(),
    }


# ------------------------------------------------------------------------------
# CRM read endpoints (used by the dashboard)
# ------------------------------------------------------------------------------
@app.get("/api/customers", response_model=list[CustomerResponse], tags=["crm"])
async def list_customers() -> list[CustomerResponse]:
    records = await crm_service.list_customers()
    return [_customer_to_response(r) for r in records]


@app.get("/api/customer/{phone_number}", response_model=CustomerResponse, tags=["crm"])
async def get_customer(phone_number: str) -> CustomerResponse:
    record = await crm_service.get_customer_by_phone(phone_number)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscriber found for that number.",
        )
    return _customer_to_response(record)


# ------------------------------------------------------------------------------
# Telemetry endpoint (used by the dashboard's performance charts)
# ------------------------------------------------------------------------------
@app.get("/api/telemetry/history", response_model=list[CallTelemetry], tags=["ops"])
async def telemetry_history() -> list[CallTelemetry]:
    return await telemetry_store.history()


# ------------------------------------------------------------------------------
# Session teardown (called by the frontend's "End call" action)
# ------------------------------------------------------------------------------
@app.delete("/api/session/{session_id}", tags=["pipeline"])
async def reset_session(session_id: str) -> dict:
    await agent_brain.reset_session(session_id)
    return {"status": "reset", "session_id": session_id}


# ------------------------------------------------------------------------------
# Core pipeline endpoint
# ------------------------------------------------------------------------------
@app.post("/api/process-voice", tags=["pipeline"])
async def process_voice(
    phone_number: str = Form(..., description="Caller MSISDN, e.g. +2348031234567"),
    session_id: Optional[str] = Form(None, description="Existing session id for multi-turn continuity"),
    text: Optional[str] = Form(None, description="Plain-text fallback input when no audio is supplied"),
    persona: Optional[str] = Form(
        None,
        description=(
            "Agent persona id (default: novaconnect_default; alt: kevin_l1, jessika_presales)"
        ),
    ),
    audio: Optional[UploadFile] = File(None, description="Caller audio clip (wav/mp3/m4a)"),
):
    """
    Runs the full conversational pipeline.

    Accepts EITHER a multipart `audio` file OR a plain-text `text` field
    (fallback handler) — audio takes precedence when both are supplied.
    Returns a streamed `audio/wav` response with telemetry embedded in
    response headers on success, or a JSON `ProcessVoiceTextResponse` if
    text-to-speech is unavailable (graceful degradation rather than a hard
    failure).
    """
    call_id = str(uuid.uuid4())
    resolved_session_id = session_id or str(uuid.uuid4())
    timer = StageTimer()

    # -- Step 1: resolve input text (audio takes precedence over text) -------
    transcript: Optional[str] = None
    input_mode: str

    if audio is not None:
        input_mode = "audio"
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded audio file is empty."
            )
        try:
            stt_result = await voice_pipeline.speech_to_text(
                audio_bytes=audio_bytes, filename=audio.filename or "call_audio.wav"
            )
        except CreditExhaustedError as exc:
            await _record_failure(timer, call_id, resolved_session_id, phone_number, input_mode, str(exc))
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc
        except VoiceRateLimitedError as exc:
            await _record_failure(timer, call_id, resolved_session_id, phone_number, input_mode, str(exc))
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
        except VoiceAPITimeoutError as exc:
            await _record_failure(timer, call_id, resolved_session_id, phone_number, input_mode, str(exc))
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
        except VoicePipelineError as exc:
            await _record_failure(timer, call_id, resolved_session_id, phone_number, input_mode, str(exc))
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        transcript = stt_result.transcript
        timer.mark("Speech-to-text complete")

        if not transcript:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not transcribe any speech from the provided audio.",
            )

    elif text and text.strip():
        # -- Fallback handler: plain-text payload, no audio file provided ----
        input_mode = "text"
        transcript = text.strip()
        timer.mark("Text fallback accepted (STT skipped)")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either an 'audio' file or a non-empty 'text' field.",
        )

    # -- Step 2: CRM context lookup -------------------------------------------
    customer = await crm_service.get_customer_by_phone(phone_number)
    timer.mark("CRM context loaded" if customer else "CRM lookup miss (unverified caller)")

    # -- Step 3: Sarvam Text LLM intent triage + response generation -----------
    try:
        response_text = await agent_brain.get_response(
            session_id=resolved_session_id,
            user_text=transcript,
            customer=customer,
            persona=persona or DEFAULT_PERSONA,
        )
    except AgentCreditExhaustedError as exc:
        await _record_failure(
            timer, call_id, resolved_session_id, phone_number, input_mode, str(exc), transcript=transcript
        )
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc
    except AgentRateLimitedError as exc:
        await _record_failure(
            timer, call_id, resolved_session_id, phone_number, input_mode, str(exc), transcript=transcript
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except AgentTimeoutError as exc:
        await _record_failure(
            timer, call_id, resolved_session_id, phone_number, input_mode, str(exc), transcript=transcript
        )
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except AgentBrainError as exc:
        await _record_failure(
            timer, call_id, resolved_session_id, phone_number, input_mode, str(exc), transcript=transcript
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    timer.mark("Sarvam Text LLM response generated")

    # -- Step 4: Text-to-speech synthesis (degrade gracefully on failure) ------
    try:
        tts_result = await voice_pipeline.text_to_speech(text=response_text)
    except VoicePipelineError as exc:
        timer.mark(f"Audio generation failed ({exc.__class__.__name__})")
        await telemetry_store.record(
            CallTelemetry(
                call_id=call_id,
                session_id=resolved_session_id,
                phone_number=phone_number,
                timestamp=datetime.now(timezone.utc),
                input_mode=input_mode,
                transcript=transcript,
                response_text=response_text,
                stages=timer.stages,
                total_ms=timer.total_ms,
                success=False,
                error=str(exc),
            )
        )
        return ProcessVoiceTextResponse(
            call_id=call_id,
            session_id=resolved_session_id,
            transcript=transcript,
            response_text=response_text,
            stages=timer.stages,
            total_ms=timer.total_ms,
            degraded_reason=f"Audio unavailable: {exc}",
        )

    timer.mark("Audio generated")

    await telemetry_store.record(
        CallTelemetry(
            call_id=call_id,
            session_id=resolved_session_id,
            phone_number=phone_number,
            timestamp=datetime.now(timezone.utc),
            input_mode=input_mode,
            transcript=transcript,
            response_text=response_text,
            stages=timer.stages,
            total_ms=timer.total_ms,
            success=True,
            error=None,
        )
    )

    headers = {
        "X-Call-Id": call_id,
        "X-Session-Id": resolved_session_id,
        "X-Transcript": _sanitize_header_value(transcript or ""),
        "X-Response-Text": _sanitize_header_value(response_text),
        "X-Total-Ms": str(timer.total_ms),
    }
    return StreamingResponse(io.BytesIO(tts_result.audio_bytes), media_type="audio/wav", headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
