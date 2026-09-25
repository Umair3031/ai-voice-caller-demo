"""
voice_ai_page.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Voice AI Page (Streamlit navigation)
------------------------------------------------------------------------------
Embeds the real customer-facing "Live Line" page (served by the FastAPI
engine at API_BASE_URL — see main.py's `/` route) inside an iframe with
microphone access explicitly delegated. Streamlit's own iframe helpers
(`st.iframe` / `components.v1.iframe`) don't expose an `allow` attribute, so
this renders the frame via raw HTML instead — the only way to grant
getUserMedia access to a nested browsing context.
==============================================================================
"""

from __future__ import annotations

import os

import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Voice AI · NovaConnect Telecoms", layout="wide")

st.markdown(
    f"""
    <div style="margin-bottom:0.75rem;font-size:0.85rem;">
        <a href="{API_BASE_URL}/" target="_blank" rel="noopener" style="color:#52514E;">
            Microphone not working here? Open the live line in its own tab.
        </a>
    </div>
    <iframe
        src="{API_BASE_URL}/"
        allow="microphone; autoplay"
        style="width:100%;height:88vh;border:1px solid #E5E7EB;border-radius:10px;"
    ></iframe>
    """,
    unsafe_allow_html=True,
)
