"""
API-level tests.

Cover the HTTP contract the frontend generates its client from, plus the access
controls that were previously absent entirely.
"""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

from app.core.container import Container
from app.core.errors import ProviderError
from app.domain.personas.registry import PersonaId
from tests.conftest import (
    OPERATOR_HEADERS,
    SILENT_WAV,
    FakeSpeechToText,
    FakeTextToSpeech,
)

TURN_URL = "/api/v1/calls/turn"


# ------------------------------------------------------------------------------
# Contract
# ------------------------------------------------------------------------------
async def test_turn_returns_one_declared_json_shape(client: AsyncClient) -> None:
    response = await client.post(
        TURN_URL,
        data={"phone_number": "+919810012345", "persona": "kevin_l1", "text": "internet is down"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["outcome"] == "completed"
    assert body["has_audio"] is True
    assert base64.b64decode(body["audio_base64"]) == SILENT_WAV
    assert body["audio_media_type"] == "audio/wav"
    assert body["session_id"]
    assert [stage["stage"] for stage in body["stages"]] == [
        "input_processing",
        "crm_lookup",
        "llm_generation",
        "tts_synthesis",
    ]


# ------------------------------------------------------------------------------
# Regression: transcripts and replies travelled in HTTP headers, which must be
# ASCII — so on an Indic voice stack every non-Latin script was silently erased
# to an empty string before it reached the UI.
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("transcript", "reply"),
    [
        ("मेरा इंटरनेट काम नहीं कर रहा है", "मैं आपका राउटर देख रहा हूँ।"),
        ("என் இணையம் வேலை செய்யவில்லை", "உங்கள் ரூட்டரை சரிபார்க்கிறேன்."),
        ("ನನ್ನ ಇಂಟರ್ನೆಟ್ ಕೆಲಸ ಮಾಡುತ್ತಿಲ್ಲ", "ನಿಮ್ಮ ರೂಟರ್ ಪರಿಶೀಲಿಸುತ್ತಿದ್ದೇನೆ."),
    ],
    ids=["hindi", "tamil", "kannada"],
)
async def test_non_ascii_transcripts_survive_the_round_trip(
    client: AsyncClient, fake_stt: FakeSpeechToText, fake_llm, transcript: str, reply: str
) -> None:
    fake_stt.transcript = transcript
    fake_stt.language_code = "hi-IN"
    fake_llm.reply = reply

    response = await client.post(
        TURN_URL,
        data={"phone_number": "+919810012345", "persona": "kevin_l1"},
        files={"audio": ("turn.wav", SILENT_WAV, "audio/wav")},
    )

    body = response.json()
    assert body["transcript"] == transcript
    assert body["response_text"] == reply


async def test_degraded_turn_is_still_a_200_with_text(
    client: AsyncClient, fake_tts: FakeTextToSpeech
) -> None:
    fake_tts.error = ProviderError("Bulbul is down")

    response = await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "degraded_text_only"
    assert body["audio_base64"] is None
    assert body["has_audio"] is False
    assert body["response_text"]
    assert body["degraded_reason"]


async def test_unknown_persona_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        TURN_URL,
        data={"phone_number": "+919810012345", "persona": "not_a_real_agent", "text": "hi"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_turn_without_audio_or_text_is_rejected(client: AsyncClient) -> None:
    response = await client.post(TURN_URL, data={"phone_number": "+919810012345"})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


async def test_error_responses_use_the_shared_envelope(client: AsyncClient, fake_llm) -> None:
    fake_llm.error = ProviderError("Sarvam internals the caller must never see")

    response = await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "hello"})

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "provider_error"
    assert "Sarvam internals" not in body["message"]  # no internal leakage
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]


# ------------------------------------------------------------------------------
# Personas roster
# ------------------------------------------------------------------------------
async def test_persona_roster_matches_what_the_engine_accepts(client: AsyncClient) -> None:
    response = await client.get("/api/v1/calls/personas")
    assert response.status_code == 200
    roster = response.json()
    assert {p["id"] for p in roster} == {p.value for p in PersonaId}
    for persona in roster:
        assert persona["display_name"] and persona["role"] and persona["accent"]
        # Prompt-template paths must never be exposed to a browser.
        assert "template_file" not in persona


# ------------------------------------------------------------------------------
# Access control — every one of these endpoints was previously wide open.
# ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/customers",
        "/api/v1/customers/+919810012345",
        "/api/v1/telemetry",
        "/api/v1/telemetry/summary",
        "/api/v1/calls/sessions/some-session",
    ],
)
async def test_operator_endpoints_require_a_key(client: AsyncClient, url: str) -> None:
    response = await client.get(url)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_operator_endpoints_reject_a_wrong_key(client: AsyncClient) -> None:
    response = await client.get("/api/v1/customers", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


async def test_operator_endpoints_accept_a_valid_key(client: AsyncClient) -> None:
    response = await client.get("/api/v1/customers", headers=OPERATOR_HEADERS)
    assert response.status_code == 200
    customers = response.json()
    assert len(customers) == 10
    assert customers[0]["data_usage_ratio"] == pytest.approx(0.4126)


async def test_pipeline_endpoint_stays_public(client: AsyncClient) -> None:
    """The customer-facing line must not require a key."""
    response = await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "hello"})
    assert response.status_code == 200


# ------------------------------------------------------------------------------
# CRM writes — the store had this capability but no route could reach it.
# ------------------------------------------------------------------------------
async def test_customer_field_can_be_written(client: AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/customers/+919810012345",
        json={"field": "router_status", "value": "Online"},
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["router_status"] == "Online"

    confirmed = await client.get("/api/v1/customers/+919810012345", headers=OPERATOR_HEADERS)
    assert confirmed.json()["router_status"] == "Online"


async def test_identity_fields_are_not_writable(client: AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/customers/+919810012345",
        json={"field": "customer_name", "value": "Somebody Else"},
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 422


async def test_invalid_enum_value_is_rejected(client: AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/customers/+919810012345",
        json={"field": "router_status", "value": "Exploded"},
        headers=OPERATOR_HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_field_update"


async def test_unknown_customer_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/customers/+10000000000", headers=OPERATOR_HEADERS)
    assert response.status_code == 404
    assert response.json()["code"] == "customer_not_found"


# ------------------------------------------------------------------------------
# Sessions
# ------------------------------------------------------------------------------
async def test_session_history_can_be_rehydrated(client: AsyncClient) -> None:
    first = await client.post(
        TURN_URL, data={"phone_number": "+919810012345", "text": "my internet is down"}
    )
    session_id = first.json()["session_id"]

    history = await client.get(f"/api/v1/calls/sessions/{session_id}", headers=OPERATOR_HEADERS)
    assert history.status_code == 200
    turns = history.json()["turns"]
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "my internet is down"


async def test_ending_a_call_clears_its_memory(client: AsyncClient) -> None:
    first = await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "hello"})
    session_id = first.json()["session_id"]

    deleted = await client.delete(f"/api/v1/calls/sessions/{session_id}")
    assert deleted.status_code == 200

    history = await client.get(f"/api/v1/calls/sessions/{session_id}", headers=OPERATOR_HEADERS)
    assert history.json()["turns"] == []


async def test_ending_an_unknown_call_is_idempotent(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/calls/sessions/never-existed")
    assert response.status_code == 200


# ------------------------------------------------------------------------------
# Throttling
# ------------------------------------------------------------------------------
async def test_turns_are_rate_limited(client: AsyncClient, container: Container) -> None:
    limit = container.settings.rate_limit_turns_per_minute
    payload = {"phone_number": "+919810012345", "text": "hello"}

    for _ in range(limit):
        assert (await client.post(TURN_URL, data=payload)).status_code == 200

    blocked = await client.post(TURN_URL, data=payload)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"


# ------------------------------------------------------------------------------
# Health
# ------------------------------------------------------------------------------
async def test_liveness_is_public_and_cheap(client: AsyncClient) -> None:
    response = await client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_reports_dependencies(client: AsyncClient) -> None:
    response = await client.get("/api/v1/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["state_backend"] == "memory"
    names = {dep["name"] for dep in body["dependencies"]}
    assert "persona_templates" in names
    assert all(dep["ready"] for dep in body["dependencies"])


async def test_readiness_reports_active_sessions(client: AsyncClient) -> None:
    await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "hello"})
    body = (await client.get("/api/v1/readyz")).json()
    assert body["active_sessions"] == 1


# ------------------------------------------------------------------------------
# Telemetry
# ------------------------------------------------------------------------------
async def test_telemetry_summary_breaks_down_by_persona(client: AsyncClient) -> None:
    for persona in ("kevin_l1", "jessika_presales", "kevin_l1"):
        await client.post(
            TURN_URL,
            data={"phone_number": "+919810012345", "persona": persona, "text": "hi"},
        )

    body = (await client.get("/api/v1/telemetry", headers=OPERATOR_HEADERS)).json()
    summary = body["summary"]
    assert summary["total_calls"] == 3
    assert summary["success_rate"] == 1.0
    by_persona = {row["persona"]: row["call_count"] for row in summary["by_persona"]}
    assert by_persona == {"kevin_l1": 2, "jessika_presales": 1}
    assert [row["stage"] for row in summary["stage_averages"]] == [
        "input_processing",
        "crm_lookup",
        "llm_generation",
        "tts_synthesis",
    ]


async def test_telemetry_can_be_filtered_by_persona(client: AsyncClient) -> None:
    for persona in ("kevin_l1", "jessika_presales"):
        await client.post(
            TURN_URL,
            data={"phone_number": "+919810012345", "persona": persona, "text": "hi"},
        )

    body = (
        await client.get(
            "/api/v1/telemetry", params={"persona": "kevin_l1"}, headers=OPERATOR_HEADERS
        )
    ).json()
    assert body["summary"]["total_calls"] == 1
    assert body["entries"][0]["persona"] == "kevin_l1"


async def test_failed_turns_lower_the_success_rate(client: AsyncClient, fake_llm) -> None:
    await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "ok"})
    fake_llm.error = ProviderError("upstream down")
    await client.post(TURN_URL, data={"phone_number": "+919810012345", "text": "boom"})

    summary = (await client.get("/api/v1/telemetry/summary", headers=OPERATOR_HEADERS)).json()
    assert summary["total_calls"] == 2
    assert summary["success_rate"] == 0.5


# ------------------------------------------------------------------------------
# The agent speaks first
# ------------------------------------------------------------------------------
async def test_open_call_returns_a_greeting_and_the_assigned_caller(client: AsyncClient) -> None:
    response = await client.post("/api/v1/calls/open", data={"persona": "kevin_l1"})
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == ""
    assert body["has_audio"] is True
    assert body["input_mode"] == "greeting"
    assert body["caller"]["name"].split()[0] in body["response_text"]
    assert body["caller"]["phone_number"].startswith("+91")
    assert body["caller"]["suggestion"]

    # The session continues with the returned ids.
    follow_up = await client.post(
        TURN_URL,
        data={
            "phone_number": body["caller"]["phone_number"],
            "persona": "kevin_l1",
            "session_id": body["session_id"],
            "text": "my internet is down",
        },
    )
    assert follow_up.status_code == 200
    assert follow_up.json()["session_id"] == body["session_id"]


async def test_presales_open_call_has_no_caller(client: AsyncClient) -> None:
    response = await client.post("/api/v1/calls/open", data={"persona": "jessika_presales"})
    assert response.status_code == 200
    assert response.json()["caller"] is None
