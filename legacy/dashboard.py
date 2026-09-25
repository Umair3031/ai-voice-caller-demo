"""
dashboard.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Operator Console (Streamlit)
------------------------------------------------------------------------------
A corporate operator dashboard styled after the Sarvam AI console: solid
white/off-white surfaces, sharp charcoal typography, thin hairline borders,
and solid black interactive controls — no dark mode, no decorative emoji.

This dashboard is a pure HTTP client of the FastAPI engine (`main.py`); it
never imports the agent/voice/CRM modules directly, so it can be deployed as
an independent service (see render.yaml) that only needs to know the API's
base URL, not any provider secrets.

Panels:
  * Simulate Voice Call Session — record or upload caller audio.
  * Text-fallback input box — bypass STT entirely.
  * Live execution log terminal.
  * CRM subscriber snapshot with live hardware telemetry.
  * End-to-end performance telemetry charts (per-stage + total latency).
==============================================================================
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import httpx
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------------------
# Design tokens — lifted from the validated palette used across the project.
# Light-only by design (the Sarvam console does not run a dark theme).
# ------------------------------------------------------------------------------
INK_PRIMARY = "#0B0B0B"
INK_SECONDARY = "#52514E"
INK_MUTED = "#898781"
SURFACE_WHITE = "#FFFFFF"
SURFACE_PAGE = "#F9FAFB"
BORDER_HAIRLINE = "#E5E7EB"
GRIDLINE = "#E1E0D9"
AXIS_LINE = "#C3C2B7"

# Fixed-order categorical slots (validated adjacent-pair CVD safety) used for
# the per-stage latency breakdown — never cycled, never reassigned per filter.
STAGE_COLOR_ORDER = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100"]
STATUS_GOOD = "#0CA30C"
STATUS_WARNING = "#FAB219"
STATUS_CRITICAL = "#D03B3B"

FONT_STACK = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

DEFAULT_API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
FALLBACK_PHONE_NUMBER = "+2348031234567"


# ------------------------------------------------------------------------------
# Page config + injected CSS (Sarvam-console look and feel)
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="NovaConnect Telecoms — IVR Console",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
    <style>
        html, body, [class*="css"] {{
            font-family: {FONT_STACK};
        }}
        .stApp {{
            background-color: {SURFACE_PAGE};
            color: {INK_PRIMARY};
        }}
        section[data-testid="stSidebar"] {{
            background-color: {SURFACE_WHITE};
            border-right: 1px solid {BORDER_HAIRLINE};
        }}
        h1, h2, h3, h4 {{
            color: {INK_PRIMARY};
            font-weight: 600;
            letter-spacing: -0.01em;
        }}
        p, span, label, div {{
            color: {INK_PRIMARY};
        }}
        .novaconnect-subtitle {{
            color: {INK_SECONDARY};
            font-size: 0.95rem;
            margin-top: -0.6rem;
        }}
        /* Real Streamlit bordered containers (st.container(border=True)) are
           what actually wrap each card's content — the previous approach of
           opening/closing a raw <div class="novaconnect-card"> across separate
           st.markdown() calls never nested anything: each call renders as
           its own isolated HTML fragment, so every "card" rendered as an
           empty floating box while its content floated past it unstyled. */
        div[class*="_card"] {{
            background-color: {SURFACE_WHITE};
            border: 1px solid {BORDER_HAIRLINE} !important;
            border-radius: 10px;
            padding: 1.1rem 1.3rem;
            margin-bottom: 1rem;
        }}
        div[class*="_card"] h4 {{
            margin-top: 0;
            border-bottom: 1px solid {BORDER_HAIRLINE};
            padding-bottom: 0.5rem;
            margin-bottom: 0.8rem;
        }}
        .novaconnect-terminal {{
            background-color: {SURFACE_WHITE};
            border: 1px solid {BORDER_HAIRLINE};
            border-radius: 10px;
            padding: 0.9rem 1.1rem;
            height: 220px;
            overflow-y: auto;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 0.82rem;
            color: {INK_SECONDARY};
            line-height: 1.55;
        }}
        .novaconnect-status-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.35rem 0;
            border-bottom: 1px solid {BORDER_HAIRLINE};
            font-size: 0.9rem;
        }}
        .novaconnect-status-row:last-child {{ border-bottom: none; }}
        .novaconnect-dot {{
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 0.5rem;
        }}
        .novaconnect-badge {{
            border: 1px solid {BORDER_HAIRLINE};
            border-radius: 999px;
            padding: 0.15rem 0.6rem;
            font-size: 0.78rem;
            color: {INK_SECONDARY};
        }}
        div.stButton > button {{
            background-color: {INK_PRIMARY};
            color: {SURFACE_WHITE};
            border: 1px solid {INK_PRIMARY};
            border-radius: 6px;
            font-weight: 500;
            padding: 0.5rem 1.1rem;
        }}
        /* The two blanket rules above force every <p>/<span>/<div> to near-
           black text, including the ones Streamlit wraps button labels in —
           re-assert white for anything inside our buttons so labels stay
           legible against the black fill. */
        div.stButton > button,
        div.stButton > button p,
        div.stButton > button span,
        div.stButton > button div {{
            color: {SURFACE_WHITE} !important;
        }}
        div.stButton > button:hover {{
            background-color: #1F2937;
            border-color: #1F2937;
            color: {SURFACE_WHITE};
        }}
        div.stButton > button:disabled,
        div.stButton > button:disabled p,
        div.stButton > button:disabled span,
        div.stButton > button:disabled div {{
            background-color: #D1D5DB;
            border-color: #D1D5DB;
            color: #6B7280 !important;
        }}
        /* Hide the Streamlit dev-toolbar chrome (Deploy button, hamburger
           menu, "Made with Streamlit" footer) — not relevant to an internal
           operator console. */
        #MainMenu, header [data-testid="stToolbar"], footer {{
            visibility: hidden;
        }}
        [data-testid="stMetricValue"] {{
            color: {INK_PRIMARY};
        }}
        [data-testid="stFileUploader"], [data-testid="stAudioInput"] {{
            border: 1px dashed {BORDER_HAIRLINE};
            border-radius: 8px;
            padding: 0.4rem;
        }}
        hr {{ border-color: {BORDER_HAIRLINE}; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------------------
# Session state
# ------------------------------------------------------------------------------
def _init_state() -> None:
    defaults = {
        "api_base_url": DEFAULT_API_BASE_URL,
        "session_id": os.urandom(8).hex(),
        "selected_phone": FALLBACK_PHONE_NUMBER,
        "logs": [],
        "last_transcript": None,
        "last_response_text": None,
        "last_audio_bytes": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


_init_state()


def log_line(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.append(f"[{timestamp}] {message}")
    st.session_state.logs = st.session_state.logs[-300:]


# ------------------------------------------------------------------------------
# Backend HTTP client helpers
# ------------------------------------------------------------------------------
def api_health(api_base_url: str) -> bool:
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{api_base_url}/health")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def fetch_customers(api_base_url: str) -> list[dict]:
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(f"{api_base_url}/api/customers")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        return []


def fetch_telemetry(api_base_url: str) -> list[dict]:
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(f"{api_base_url}/api/telemetry/history")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        return []


def call_process_voice(
    api_base_url: str,
    phone_number: str,
    session_id: str,
    text: Optional[str] = None,
    audio_bytes: Optional[bytes] = None,
    audio_filename: str = "call_audio.wav",
) -> httpx.Response:
    data = {"phone_number": phone_number, "session_id": session_id}
    if text:
        data["text"] = text
    files = None
    if audio_bytes:
        files = {"audio": (audio_filename, audio_bytes, "audio/wav")}
    with httpx.Client(timeout=30.0) as client:
        return client.post(f"{api_base_url}/api/process-voice", data=data, files=files)


def run_pipeline(audio_bytes: Optional[bytes] = None, text_value: Optional[str] = None) -> None:
    api_base_url = st.session_state.api_base_url
    phone_number = st.session_state.selected_phone
    session_id = st.session_state.session_id
    mode = "audio" if audio_bytes else "text"
    log_line(f"-> POST /api/process-voice  phone={phone_number}  mode={mode}")

    try:
        resp = call_process_voice(
            api_base_url, phone_number, session_id, text=text_value, audio_bytes=audio_bytes
        )
    except httpx.ConnectError:
        log_line(f"ERROR: could not reach IVR engine at {api_base_url}")
        st.error(f"Could not reach the IVR engine at {api_base_url}. Is `uvicorn main:app` running?")
        return
    except httpx.TimeoutException:
        log_line("ERROR: request to IVR engine timed out")
        st.error("The request to the IVR engine timed out.")
        return

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        log_line(f"ERROR {resp.status_code}: {detail}")
        st.error(f"Pipeline error ({resp.status_code}): {detail}")
        return

    content_type = resp.headers.get("content-type", "")
    if "audio/wav" in content_type:
        transcript = resp.headers.get("x-transcript", "")
        response_text = resp.headers.get("x-response-text", "")
        total_ms = resp.headers.get("x-total-ms", "?")
        log_line(f'Transcript: "{transcript}"')
        log_line(f'Agent reply: "{response_text}"  ({total_ms} ms total)')
        st.session_state.last_transcript = transcript
        st.session_state.last_response_text = response_text
        st.session_state.last_audio_bytes = resp.content
    else:
        payload = resp.json()
        log_line(f"DEGRADED (no audio): {payload.get('degraded_reason')}")
        log_line(f"Agent reply (text-only): \"{payload.get('response_text')}\"")
        st.session_state.last_transcript = payload.get("transcript")
        st.session_state.last_response_text = payload.get("response_text")
        st.session_state.last_audio_bytes = None
        st.warning(f"Audio unavailable — showing text reply. Reason: {payload.get('degraded_reason')}")


# ------------------------------------------------------------------------------
# Chart builders (validated categorical palette; see dataviz skill guidance)
# ------------------------------------------------------------------------------
def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        plot_bgcolor=SURFACE_WHITE,
        paper_bgcolor=SURFACE_WHITE,
        font=dict(family=FONT_STACK, color=INK_PRIMARY, size=13),
        margin=dict(l=48, r=24, t=28, b=48),
        height=height,
        showlegend=False,
    )
    return fig


def render_latency_trend(totals_ms: list[float]) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(range(1, len(totals_ms) + 1)),
            y=totals_ms,
            mode="lines+markers",
            line=dict(color=INK_PRIMARY, width=2),
            marker=dict(size=8, color=INK_PRIMARY),
            hovertemplate="Call #%{x}<br>Total latency: %{y:.0f} ms<extra></extra>",
        )
    )
    fig.update_xaxes(title="Call sequence", showgrid=False, showline=True, linecolor=AXIS_LINE, tickfont=dict(color=INK_MUTED))
    fig.update_yaxes(title="Latency (ms)", showgrid=True, gridcolor=GRIDLINE, zeroline=False, tickfont=dict(color=INK_MUTED))
    fig.update_layout(hovermode="x unified")
    return _base_layout(fig, height=320)


def _normalize_stage(stage_name: str) -> str:
    lowered = stage_name.lower()
    if "speech-to-text" in lowered or "text fallback" in lowered:
        return "Input Processing"
    if "crm" in lowered:
        return "CRM Lookup"
    if "sarvam text llm" in lowered or "response generated" in lowered:
        return "Sarvam Text LLM"
    if "audio generat" in lowered:
        return "TTS Synthesis"
    return stage_name.title()


def compute_stage_averages(history: list[dict]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for call in history:
        for stage in call.get("stages", []):
            key = _normalize_stage(stage["stage"])
            buckets.setdefault(key, []).append(stage["elapsed_ms"])

    averages = {key: sum(values) / len(values) for key, values in buckets.items() if values}
    canonical_order = ["Input Processing", "CRM Lookup", "Sarvam Text LLM", "TTS Synthesis"]
    ordered = {key: averages[key] for key in canonical_order if key in averages}
    for key, value in averages.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def render_stage_breakdown(stage_averages: dict[str, float]) -> go.Figure:
    stages = list(stage_averages.keys())
    values = list(stage_averages.values())
    colors = [STAGE_COLOR_ORDER[i % len(STAGE_COLOR_ORDER)] for i in range(len(stages))]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=stages,
            y=values,
            marker_color=colors,
            text=[f"{v:.0f} ms" for v in values],
            textposition="outside",
            textfont=dict(color=INK_PRIMARY),
            hovertemplate="%{x}<br>Avg: %{y:.0f} ms<extra></extra>",
        )
    )
    fig.update_xaxes(showgrid=False, showline=True, linecolor=AXIS_LINE, tickfont=dict(color=INK_SECONDARY))
    fig.update_yaxes(title="Avg latency (ms)", showgrid=True, gridcolor=GRIDLINE, zeroline=False, tickfont=dict(color=INK_MUTED))
    return _base_layout(fig, height=360)


# ------------------------------------------------------------------------------
# CRM status helpers
# ------------------------------------------------------------------------------
_STATUS_COLOR_MAP = {
    "Current": STATUS_GOOD,
    "Online": STATUS_GOOD,
    "Green": STATUS_GOOD,
    "Grace Period": STATUS_WARNING,
    "Degraded": STATUS_WARNING,
    "Rebooting": STATUS_WARNING,
    "Blinking Red": STATUS_WARNING,
    "Overdue": STATUS_CRITICAL,
    "Suspended": STATUS_CRITICAL,
    "Offline": STATUS_CRITICAL,
    "Red": STATUS_CRITICAL,
    "Off": INK_MUTED,
}


def _status_row(label: str, value: str) -> str:
    color = _STATUS_COLOR_MAP.get(value, INK_SECONDARY)
    return (
        f'<div class="novaconnect-status-row"><span>{label}</span>'
        f'<span><span class="novaconnect-dot" style="background-color:{color};"></span>{value}</span></div>'
    )


# ------------------------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------------------------
with st.sidebar:
    st.markdown("#### Connection")
    st.session_state.api_base_url = st.text_input(
        "IVR Engine API base URL", value=st.session_state.api_base_url
    )
    healthy = api_health(st.session_state.api_base_url)
    dot_color = STATUS_GOOD if healthy else STATUS_CRITICAL
    st.markdown(
        f'<div class="novaconnect-badge"><span class="novaconnect-dot" style="background-color:{dot_color};"></span>'
        f'{"Engine reachable" if healthy else "Engine unreachable"}</div>',
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("#### Test Subscriber")
    customers = fetch_customers(st.session_state.api_base_url)
    if customers:
        phone_options = [c["phone_number"] for c in customers]
        labels = {c["phone_number"]: f'{c["customer_name"]} — {c["phone_number"]}' for c in customers}
        st.session_state.selected_phone = st.selectbox(
            "Caller MSISDN",
            options=phone_options,
            format_func=lambda p: labels.get(p, p),
            index=phone_options.index(st.session_state.selected_phone)
            if st.session_state.selected_phone in phone_options
            else 0,
        )
    else:
        st.session_state.selected_phone = st.text_input(
            "Caller MSISDN (engine unreachable — enter manually)",
            value=st.session_state.selected_phone,
        )

    st.markdown("---")
    st.markdown("#### Session")
    st.caption(f"Session ID: `{st.session_state.session_id}`")
    if st.button("Start New Session", use_container_width=True):
        st.session_state.session_id = os.urandom(8).hex()
        st.session_state.logs = []
        st.session_state.last_transcript = None
        st.session_state.last_response_text = None
        st.session_state.last_audio_bytes = None
        st.rerun()


# ------------------------------------------------------------------------------
# Header
# ------------------------------------------------------------------------------
st.markdown("# NovaConnect Telecoms — IVR Console")
st.write("")

left_col, right_col = st.columns([2, 1], gap="large")

# ------------------------------------------------------------------------------
# Left column: Simulate Voice Call Session + Text fallback
# ------------------------------------------------------------------------------
with left_col:
    with st.container(border=True, key="voice_call_card"):
        st.markdown("#### Voice Call")
        recorded_audio = st.audio_input("Record")
        uploaded_audio = st.file_uploader(
            "Upload a clip", type=["wav", "mp3", "m4a", "ogg"]
        )
        run_voice = st.button("Run Simulation", type="primary")
        if run_voice:
            audio_source = recorded_audio if recorded_audio is not None else uploaded_audio
            if audio_source is None:
                st.error("Record or upload an audio clip first.")
            else:
                with st.spinner("Processing..."):
                    run_pipeline(audio_bytes=audio_source.getvalue())

    with st.container(border=True, key="text_input_card"):
        st.markdown("#### Text Input")
        fallback_text = st.text_area(
            "Message",
            placeholder="e.g. My internet has been down since this morning",
            height=90,
            label_visibility="collapsed",
        )
        if st.button("Send"):
            if not fallback_text.strip():
                st.error("Enter some text first.")
            else:
                with st.spinner("Processing..."):
                    run_pipeline(text_value=fallback_text.strip())

    if st.session_state.last_response_text:
        with st.container(border=True, key="last_exchange_card"):
            st.markdown("#### Last Exchange")
            if st.session_state.last_transcript:
                st.markdown(f"**Caller:** {st.session_state.last_transcript}")
            st.markdown(f"**NovaConnect Agent:** {st.session_state.last_response_text}")
            if st.session_state.last_audio_bytes:
                st.audio(st.session_state.last_audio_bytes, format="audio/wav")

    if st.session_state.logs:
        with st.container(border=True, key="execution_log_card"):
            st.markdown("#### Execution Log")
            log_text = "<br>".join(st.session_state.logs)
            st.markdown(f'<div class="novaconnect-terminal">{log_text}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# Right column: CRM subscriber snapshot
# ------------------------------------------------------------------------------
with right_col:
    customer = next(
        (c for c in customers if c["phone_number"] == st.session_state.selected_phone), None
    )
    if customer is not None:
        with st.container(border=True, key="subscriber_card"):
            st.markdown("#### Subscriber Snapshot")
            st.markdown(f"**{customer['customer_name']}**")
            st.caption(customer["account_id"])
            usage_ratio = (
                customer["plan_data_used_gb"] / customer["plan_data_cap_gb"]
                if customer["plan_data_cap_gb"]
                else 0.0
            )
            st.progress(min(usage_ratio, 1.0))
            st.caption(
                f"{customer['plan_data_used_gb']:.1f} GB of {customer['plan_data_cap_gb']:.1f} GB used — "
                f"{customer['active_plan']}"
            )
            st.markdown(
                _status_row("Account balance", f"{customer['account_balance']:.2f} {customer['currency']}")
                + _status_row("Payment status", customer["payment_status"])
                + _status_row("Router status", customer["router_status"])
                + _status_row("LOS light", customer["los_light"])
                + _status_row("Support tier", customer["support_tier"]),
                unsafe_allow_html=True,
            )


# ------------------------------------------------------------------------------
# Full-width: Performance telemetry
# ------------------------------------------------------------------------------
telemetry_history = fetch_telemetry(st.session_state.api_base_url)

if telemetry_history:
    with st.container(border=True, key="telemetry_card"):
        st.markdown("#### Performance Telemetry")

        total_calls = len(telemetry_history)
        success_count = sum(1 for c in telemetry_history if c.get("success"))
        avg_total_ms = sum(c["total_ms"] for c in telemetry_history) / total_calls

        m1, m2, m3 = st.columns(3)
        m1.metric("Total Calls", f"{total_calls}")
        m2.metric("Success Rate", f"{(success_count / total_calls) * 100:.0f}%")
        m3.metric("Avg. End-to-End Latency", f"{avg_total_ms:.0f} ms")

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.markdown("**Total Latency Trend**")
            totals = [c["total_ms"] for c in telemetry_history]
            st.plotly_chart(render_latency_trend(totals), use_container_width=True, config={"displayModeBar": False})
        with chart_col2:
            st.markdown("**Average Latency by Pipeline Stage**")
            stage_averages = compute_stage_averages(telemetry_history)
            st.plotly_chart(
                render_stage_breakdown(stage_averages), use_container_width=True, config={"displayModeBar": False}
            )
