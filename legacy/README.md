# Legacy (superseded — kept for reference, not wired into anything)

These are the v1 modules, moved here when the engine was restructured into the
`app/` package. **Nothing in this directory is imported, served, or deployed.**
It is kept only so the v2 frontend build has the original design and copy to
work from, and so behaviour can be diffed if something looks wrong.

Delete the whole directory once the Next.js frontend has landed.

## What replaced what

| Legacy file | Replacement |
|---|---|
| `main.py` | `app/main.py` + `app/api/v1/routers/` |
| `config.py` | `app/core/config.py` |
| `agent_brain.py` | `app/services/conversation.py`, `app/services/sarvam.py`, `app/domain/personas/` |
| `voice_pipeline.py` | `app/services/sarvam.py` |
| `crm_service.py` | `app/services/crm.py` + `app/infra/*_state.py` |
| `dashboard.py`, `streamlit_app.py`, `voice_ai_page.py` | Next.js console route (pending) |
| `static/live-line.html` | Next.js live-line route (pending) |
| `.streamlit/config.toml` | — (Streamlit retired) |

## Why they were retired

Beyond the restructure, each carried defects the rewrite closed:

- **`main.py`** put the transcript and reply in HTTP response headers, which
  must be ASCII. On an Indic voice stack that silently erased every Hindi,
  Tamil, and Kannada transcript to an empty string. It also returned either an
  audio stream or JSON with no `response_model`, so OpenAPI described neither
  branch and no typed client could be generated. All API state
  (telemetry, sessions, CRM) lived in process memory while the container ran
  two workers, so consecutive turns of one call landed on different processes
  and conversation history reset mid-call. Every endpoint was unauthenticated,
  including the ones serving subscriber records and full call transcripts.
- **`voice_pipeline.py`** computed the language STT detected and then discarded
  it, so every reply was synthesized at the configured default (`en-IN`)
  regardless of what the caller actually spoke.
- **`agent_brain.py`** held three ~100-line prompt templates and the client's
  product catalog as Python literals in one ~800-line module, and built its
  provider client at import time — so importing it required a live API key and
  nothing in it could be unit-tested.
- **`crm_service.py`**'s `update_customer_field` had no callers and no route:
  the agent could diagnose "your router is offline" but nothing in the system
  could ever mark it rebooted.
- **`dashboard.py`** styled itself by injecting CSS that targeted Streamlit's
  internal DOM (`div[class*="_card"]`, `[data-testid="stAudioInput"]`), which
  broke on Streamlit upgrades — the last three commits before the rewrite were
  all repairs to exactly that. It also never sent the `persona` field, so the
  operator console could only ever exercise the default agent, never Kevin or
  Jessika.

## Useful bits to carry forward

`static/live-line.html` is the visual reference for the customer-facing route:
the dark palette, the Kevin/Jessika accent colours, the waveform and call-panel
layout, and the roster copy. The persona copy itself already moved into
`app/domain/personas/registry.py`, which the frontend now reads from
`GET /api/v1/calls/personas` instead of hardcoding.
