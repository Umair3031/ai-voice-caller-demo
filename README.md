# NovaConnect Telecoms — Conversational IVR Engine

A natural-language voice line that replaces touch-tone menus. One turn runs:

```
audio | text  ->  speech-to-text  ->  CRM context  ->  text generation  ->  speech synthesis  ->  audio
```

Every stage runs on Sarvam AI — one account, one credit pool, one `SARVAM_API_KEY`.

---

## Layout

```
app/
  main.py                    app factory: lifespan, middleware, error handling, CORS
  api/
    middleware.py            request-id correlation
    v1/
      router.py              mounts every v1 router
      routers/               calls · crm · telemetry · health
      schemas/               HTTP request/response shapes (separate from domain types)
  core/
    config.py                typed settings, resolved through get_settings()
    container.py             composition root — everything is built here, once
    deps.py                  FastAPI dependencies
    errors.py                domain exceptions + their HTTP projection
    logging.py               structured JSON logs with request correlation
    security.py              operator API keys, rate limiting
  domain/
    models.py                CustomerRecord, ConversationTurn, CallTelemetry, ...
    personas/                one .md prompt per agent + the registry
    data/                    product catalog JSON
  infra/
    state.py                 store protocols
    memory_state.py          in-process implementations (dev/test)
    redis_state.py           Redis implementations (production)
    seed.py                  demo subscriber fixtures
  services/
    protocols.py             what the pipeline depends on — not the SDK
    sarvam.py                Sarvam adapters: error classification + retry
    conversation.py          the agent brain: prompt + history + LLM
    crm.py                   subscriber facade + prompt projection
    pipeline.py              turn orchestration, stage timing, telemetry
tests/                       offline tests: no network, no API key, no credits
evals/                       voice-quality measurement against the live speech API (spends credits)
scripts/export_openapi.py    regenerates openapi.json for frontend codegen
legacy/                      the v1 modules — superseded, see legacy/README.md
```

Dependencies point one way: `api -> services -> domain`, with `infra` behind
protocols. Nothing under `app/` constructs a client at import time, which is
why the whole test suite runs offline.

---

## Running it

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate on POSIX
pip install -r requirements.txt -e ".[dev]"

cp .env.example .env        # then set SARVAM_API_KEY
uvicorn app.main:app --reload
```

Interactive docs at <http://localhost:8000/docs>.

```bash
pytest                                      # offline
ruff check app tests && ruff format app tests
mypy app
python scripts/export_openapi.py --check    # fails if openapi.json drifted
```

### Redis

Optional locally, required in production:

```bash
docker run -d -p 6379:6379 redis:7-alpine
# then in .env:  REDIS_URL=redis://localhost:6379
```

Without it, sessions, telemetry, and subscriber records live in process memory.
That is fine for one worker, but **not shared between workers and lost on
restart** — so `app.main` refuses to boot with `WEB_CONCURRENCY > 1` and no
`REDIS_URL`, rather than silently dropping callers' conversation history
mid-call.

---

## API

Base path `/api/v1`. Full schema in [`openapi.json`](openapi.json).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/healthz` | public | Liveness. Never touches the network. |
| `GET` | `/readyz` | public | Readiness: state backend + persona templates. **Point deploy health checks here.** |
| `GET` | `/calls/personas` | public | The agent roster the frontend renders its picker from. |
| `POST` | `/calls/open` | public, rate-limited | Start a call: the agent speaks first. Assigns the caller. |
| `POST` | `/calls/turn` | public, rate-limited | Run one conversational turn. |
| `DELETE` | `/calls/sessions/{id}` | public | End a call, clear its memory. Idempotent. |
| `GET` | `/calls/sessions/{id}` | operator | Rehydrate a conversation transcript. |
| `GET` | `/customers` | operator | List subscribers. |
| `GET` | `/customers/{msisdn}` | operator | One subscriber. |
| `PATCH` | `/customers/{msisdn}` | operator | Write one field (auditable). |
| `GET` | `/telemetry` | operator | Recent turns + server-computed aggregates. |
| `GET` | `/telemetry/summary` | operator | Aggregates only. |

Operator endpoints require `X-API-Key`, matched against `OPERATOR_API_KEYS`.
They serve caller names, phone numbers, balances, and full transcripts, so they
are **closed by default**: with no keys configured they reject every request
rather than serving openly.

### The turn contract

`POST /calls/turn` always returns the same JSON shape, whatever happens:

```jsonc
{
  "call_id": "…", "session_id": "…",       // pass session_id back on the next turn
  "persona": "kevin_l1", "input_mode": "audio",
  "transcript": "मेरा इंटरनेट काम नहीं कर रहा है",
  "response_text": "मैं आपका राउटर देख रहा हूँ।",
  "detected_language": "hi-IN",            // what STT heard
  "spoken_language": "hi-IN",              // what TTS actually spoke
  "audio_base64": "UklGR…", "has_audio": true, "sample_rate": 22050,
  "stages": [ { "stage": "llm_generation", "label": "Sarvam Text LLM", "elapsed_ms": 412.7 } ],
  "total_ms": 1284.3,
  "outcome": "completed",                  // completed | degraded_text_only | failed
  "degraded_reason": null
}
```

Errors use one envelope: `{ "code", "message", "request_id" }`. `code` is
stable and machine-readable; `message` is safe to show a caller. Internal
provider strings never appear in either — they go to the log, keyed by the same
`request_id` the response carries in its `X-Request-Id` header.

When speech synthesis fails, the turn still returns **200** with
`outcome: "degraded_text_only"` and `audio_base64: null`. Losing the voice is
bad; losing the reply is worse.

---

## Design decisions worth knowing

**The agent speaks first, and the caller is assigned, not typed.** `POST /calls/open`
picks the caller, greets them, and stores the greeting as the first turn of the
session. NovaConnect Agent and Kevin take *existing customers*: a random subscriber from the
seeded cast (`app/infra/seed.py`, ten Indian subscribers with varied line and billing
states). Kevin is only handed subscribers whose line is unwell. Jessika takes a cold
prospect with no record. The greeting is scripted (a few variants per agent, in
`registry.py`) so it costs no LLM round trip, and it volunteers only the caller's
first name: what the agent can see about the account is saved for after the caller
says why they called. Pass the returned `session_id` and `caller.phone_number` back
on each turn.

**Replies aim for about 30 spoken words and are never cut open.** The prompt asks for
two short sentences (`REPLY_MAX_WORDS`). A hard ceiling (`REPLY_HARD_MAX_WORDS`)
drops whole trailing sentences past it, and a reply that hit the token limit is cut
back to its last finished sentence, never mid-word.

**Every agent has explicit authority.** Shared voice rules live in
`personas/_common.md` (honest about being an AI, no invented facts, no pretending to
transfer or file anything). Each persona file states what it can do, what it cannot,
and its single demo offer. The offers are demo fiction, used once per call under a
stated condition: late-fee waiver (NovaConnect Agent), free technician visit (Kevin), free
installation (Jessika).


**Audio travels in the JSON body, not as a stream with metadata in headers.**
HTTP headers must be ASCII. On an explicitly Indic voice stack, putting the
transcript in a header silently erased every Hindi, Tamil, and Kannada
conversation to an empty string. Base64 costs 33% on a payload of a few KB —
irrelevant next to transcripts that survive in any script and an OpenAPI
document complete enough to generate a typed client from.

**The language STT detects is threaded into TTS.** Previously it was computed
and discarded, so every reply was spoken with an `en-IN` voice profile
regardless of what the caller said. Set `TTS_FOLLOW_DETECTED_LANGUAGE=false` to
pin one voice.

**Persona prompts live in `app/domain/personas/*.md`, catalogs in
`app/domain/data/*.json`.** They are behaviour contracts written as prose;
keeping them as Python string literals made them unreviewable and made a
catalog update a code change. `verify_templates()` renders all of them at boot,
so a typo'd placeholder fails the deploy rather than the first live call.

**The persona roster is served from the engine.** `GET /calls/personas` is the
single source of truth for names, roles, pitches, and accent tokens, so the UI
cannot offer an agent the engine would reject.

**Stage names are an enum, not strings.** The console groups latency by
canonical stage id instead of substring-matching display labels, and every
telemetry record carries its persona — so per-agent latency is answerable
rather than averaged away.

**Retries distinguish what is worth retrying.** 429s, 5xx, and timeouts back
off with jitter; 401/402/403 do not, because more credits will not appear
because we asked twice.

---

## Frontend

`web/` — Next.js 15 (App Router) + TypeScript + Tailwind v4 + shadcn-style
primitives. **One app, one design system**, serving both surfaces:

| Route | Who it's for |
|---|---|
| `/` | The customer live line — pick an agent, it speaks first, talk hands-free (or tap-to-talk) |
| `/console` | The operator console — telemetry and subscriber state |

```
web/src/
  app/
    layout.tsx               AppShell + providers, shared by every route
    page.tsx                 live line
    console/page.tsx         operator console
    globals.css              ALL design tokens live here
    api/engine/[...path]/    server-side proxy to the engine
  components/
    ui/                      Button Card Badge Alert Field Stat Skeleton ...
    layout/                  AppShell, theme provider, query providers
  features/
    call/                    recorder, useCall state machine, roster, panel
    console/                 charts, persona table, subscriber panel
  lib/api/                   generated.ts (from openapi.json) + typed client
```

```bash
cd web
npm install
cp .env.example .env.local     # set ENGINE_BASE_URL and OPERATOR_API_KEY
npm run dev                    # http://localhost:3000

npm run generate:api           # regenerate types after an engine change
npm run typecheck && npm run lint && npm run build
```

### How it holds together

**One token layer, two themes.** Every colour, radius, and shadow on both pages
resolves to a variable in `app/globals.css`. Light and dark are a viewer
preference applied globally — not a per-page decision, and not an automatic
inversion: the dark values are stepped and re-validated separately. No component
hard-codes a hex value.

**Persona accent is a scope, not a prop.** A container sets
`data-agent="kevin_l1"` and everything inside renders in Kevin's teal through
`bg-agent` / `text-agent` / `border-agent`. Adding a persona is a token block
plus a backend entry — no component variants, no colour threaded down a tree.

**The roster comes from the engine.** `GET /calls/personas` drives the agent
picker, so the UI cannot offer an agent the engine would reject, and persona
copy has exactly one home.

**Types are generated, never written.** `npm run generate:api` regenerates
`lib/api/generated.ts` from `openapi.json`. A contract change is a compile
error, not a runtime surprise.

**The operator key never reaches the browser.** Every engine call goes through
`/api/engine/[...path]`, a server route that attaches `X-API-Key` to operator
requests only. As a side effect there is no browser-to-engine traffic at all,
so CORS needs no configuration and cannot be misconfigured.

**Chart colours were validated, not chosen.** The four-slot categorical ramp
passes lightness-band, chroma, contrast, and colour-vision-deficiency
separation checks against both surfaces. Slot 4 is violet rather than the
obvious amber because amber against slot 3's green measured ΔE 13.7 for normal
vision — below the legibility floor. Both charts also direct-label their values,
so identity never rests on colour alone.

**Calls are hands-free by default.** The mic stays open and `vad.ts` (a noise-floor-relative
voice-activity detector, unit-tested with `npm test`) finds where each turn starts and
ends. The caller can interrupt the agent mid-sentence; anything said while a reply is
still being fetched is queued, not lost. Tap-to-talk is one click away for noisy rooms.
Turns are encoded as 16 kHz mono WAV, which is what speech recognition uses, not the
browser's native 44.1 or 48 kHz.

**Audio capture uses an AudioWorklet.** Capture runs on the audio thread and
also drives the waveform from real microphone levels. The predecessor used
`ScriptProcessorNode`, which is deprecated and runs on the main thread, so a
busy UI could drop frames mid-turn.

## Measuring voice quality

```bash
python -m evals.run_stt --limit 8            # cheap smoke run
python -m evals.run_stt                      # all cases: WER, digit accuracy, term recall
python -m evals.run_stt --language en-IN     # pinned language vs auto-detect
```

Scores word error rate, exact-match digit accuracy (PIN codes and phone numbers, where
one wrong digit fails the call), brand-term recall, language ID and latency, per tag.
Cases live in `evals/cases.jsonl`. With no recording, a case is spoken by a Sarvam
voice, which is clean, so treat that as a ceiling: put real recordings at
`evals/audio/<case id>.wav` (different accents, a noisy room) for numbers you can trust.
Runs call the live API and spend credits.

## Deploying

`render.yaml` provisions Redis plus the API container. After the first deploy,
set `SARVAM_API_KEY` and `OPERATOR_API_KEYS` in the dashboard; `REDIS_URL` is
wired automatically.

Deploy `web/` as a separate Node service with `ENGINE_BASE_URL` pointing at the
API's public URL and `OPERATOR_API_KEY` set to one of the engine's keys. Because
the browser only ever talks to the Next.js server, `CORS_ALLOW_ORIGINS` on the
engine can stay empty.
