"""
Call-pipeline routes — the customer-facing line.

Public but rate-limited. Everything the caller needs comes back in one declared
JSON shape, so a typed client can be generated straight from the OpenAPI
document.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.api.v1.schemas.calls import (
    ConversationTurnOut,
    OpenCallResponse,
    PersonaOut,
    SessionHistoryResponse,
    SessionResetResponse,
    TurnResponse,
)
from app.api.v1.schemas.common import ErrorResponse
from app.core.deps import ConversationDep, PipelineDep
from app.core.errors import BadRequestError
from app.core.security import OperatorKeyDep, enforce_turn_rate_limit
from app.domain.personas.registry import DEFAULT_PERSONA, PersonaId, list_personas

router = APIRouter(prefix="/calls", tags=["calls"])

_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "Malformed or empty input."},
    402: {"model": ErrorResponse, "description": "Provider credits exhausted."},
    413: {"model": ErrorResponse, "description": "Audio upload exceeds the size ceiling."},
    422: {"model": ErrorResponse, "description": "No speech could be transcribed."},
    429: {"model": ErrorResponse, "description": "Rate limited."},
    502: {"model": ErrorResponse, "description": "Upstream provider failure."},
    504: {"model": ErrorResponse, "description": "Upstream provider timeout."},
}


@router.get(
    "/personas",
    response_model=list[PersonaOut],
    summary="The agent roster",
    description=(
        "The personas this engine will accept. The frontend renders its agent picker "
        "from this list, so the UI cannot offer an agent the engine would reject."
    ),
)
async def get_personas() -> list[PersonaOut]:
    return [PersonaOut.from_domain(persona) for persona in list_personas()]


@router.post(
    "/open",
    response_model=OpenCallResponse,
    responses=_ERROR_RESPONSES,
    dependencies=[Depends(enforce_turn_rate_limit)],
    summary="Start a call: the agent speaks first",
    description=(
        "Assigns the caller (a random demo subscriber for the agents that take existing "
        "customers; nobody for pre-sales), greets them, and returns the greeting as text "
        "and audio. Pass the returned `session_id` and `caller.phone_number` on every "
        "following turn."
    ),
)
async def open_call(
    pipeline: PipelineDep,
    persona: Annotated[PersonaId, Form(description="Agent persona id.")] = DEFAULT_PERSONA,
) -> OpenCallResponse:
    return OpenCallResponse.from_open_result(await pipeline.open_call(persona=persona))


@router.post(
    "/turn",
    response_model=TurnResponse,
    responses=_ERROR_RESPONSES,
    dependencies=[Depends(enforce_turn_rate_limit)],
    summary="Run one conversational turn",
    description=(
        "Accepts EITHER a multipart `audio` file OR a plain-text `text` field; audio "
        "takes precedence when both are supplied. Always returns the same JSON shape — "
        "reply audio rides in `audio_base64`, and is null when speech synthesis was "
        "unavailable (`outcome: degraded_text_only`) rather than failing the call."
    ),
)
async def run_turn(
    pipeline: PipelineDep,
    persona: Annotated[PersonaId, Form(description="Agent persona id.")] = DEFAULT_PERSONA,
    phone_number: Annotated[
        str | None,
        Form(description="Caller MSISDN as returned by /calls/open. Omit for a prospect."),
    ] = None,
    session_id: Annotated[
        str | None, Form(description="Existing session id, for multi-turn continuity.")
    ] = None,
    text: Annotated[
        str | None, Form(description="Plain-text input when no audio is supplied.")
    ] = None,
    audio: Annotated[
        UploadFile | None, File(description="Caller audio clip (wav/mp3/m4a/ogg).")
    ] = None,
) -> TurnResponse:
    audio_bytes: bytes | None = None
    filename = "turn.wav"

    if audio is not None:
        audio_bytes = await audio.read()
        filename = audio.filename or filename
    elif not (text and text.strip()):
        raise BadRequestError(
            "Neither 'audio' nor 'text' was supplied.",
            public_message="Provide either an audio clip or some text.",
        )

    result = await pipeline.run_turn(
        phone_number=phone_number,
        persona=persona,
        session_id=session_id,
        text=text,
        audio_bytes=audio_bytes,
        audio_filename=filename,
    )
    return TurnResponse.from_result(result)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionHistoryResponse,
    responses={401: {"model": ErrorResponse}},
    summary="Read a session's conversation history",
    description=(
        "Operator-only: session transcripts contain what callers said. Used by the "
        "console to rehydrate a conversation after a reload."
    ),
)
async def get_session_history(
    session_id: str,
    conversation: ConversationDep,
    _key: OperatorKeyDep,
) -> SessionHistoryResponse:
    turns = await conversation.get_history(session_id)
    return SessionHistoryResponse(
        session_id=session_id,
        turns=[ConversationTurnOut.from_domain(turn) for turn in turns],
    )


@router.delete(
    "/sessions/{session_id}",
    response_model=SessionResetResponse,
    status_code=status.HTTP_200_OK,
    summary="End a call and clear its memory",
    description=(
        "Called by the frontend's End Call action. Idempotent — resetting an unknown "
        "or already-cleared session succeeds. Unauthenticated because a caller ending "
        "their own call must always work, and a session id is an unguessable UUID."
    ),
)
async def reset_session(session_id: str, conversation: ConversationDep) -> SessionResetResponse:
    await conversation.reset(session_id)
    return SessionResetResponse(session_id=session_id)
