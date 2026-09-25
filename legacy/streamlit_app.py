"""
streamlit_app.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Streamlit Entrypoint (multipage)
------------------------------------------------------------------------------
Routes between the operator console (dashboard.py, unchanged) and the
customer-facing Voice AI page (voice_ai_page.py), giving Voice AI a stable
`/voice-ai` URL via `st.Page(..., url_path=...)`.

Run this instead of `dashboard.py` directly to get both pages navigable;
`streamlit run dashboard.py` still works standalone for just the console.
==============================================================================
"""

import streamlit as st

pg = st.navigation(
    [
        st.Page(
            "dashboard.py",
            title="Operator Console",
            icon=":material/dashboard:",
            url_path="",
            default=True,
        ),
        st.Page(
            "voice_ai_page.py",
            title="Voice AI",
            icon=":material/mic:",
            url_path="voice-ai",
        ),
    ]
)
pg.run()
