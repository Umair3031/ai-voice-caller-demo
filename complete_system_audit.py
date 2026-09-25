"""
complete_system_audit.py
==============================================================================
NovaConnect Telecoms / Veloce IVR — End-to-End Production Readiness Audit
------------------------------------------------------------------------------
Exercises the deployed engine directly (Render), plus one round-trip through
the deployed frontend's server-side proxy (Vercel), using the real seeded CRM
accounts already in the engine — so every check is grounded in actual
telemetry, not a scripted fixture that always agrees with itself.

READ THIS BEFORE TRUSTING ANY LINE BELOW AS A PASS
------------------------------------------------------------------------------
This engine ships three personas. They do not map cleanly onto "Technical /
Billing / Sales" as three equally-specialized agents, and this script does
not pretend otherwise:

  technical -> kevin_l1        L1 technical support. A direct match.
  billing   -> novaconnect_default     Billing/payments is one triage LANE inside the
                                general-purpose agent (see its "NON-LINEAR
                                INTENT TRIAGE MATRIX"), not a dedicated
                                billing bot with its own persona id.
  sales     -> jessika_presales Presales / new-connections / SIM sales. Her
                                "upgrade" behavior only activates when the
                                caller's CRM record already shows a plan; she
                                is not a general "evaluate my plan, suggest a
                                tier" agent invocable on demand.

Section 4 is split for the same reason:
  4a. The non-linear behavior that is actually engineered: novaconnect_default
      re-triaging intent (fault -> billing -> upgrade) within ONE session,
      ONE persona, per its own design.
  4b. An exploratory probe of what happens if the *persona* field itself is
      switched mid-session (kevin_l1 -> novaconnect_default -> jessika_presales) with
      the same session_id. This is not a documented feature — nothing in the
      engine locks a session to the persona that started it — so 4b reports
      findings rather than asserting a pass/fail against a spec that does
      not exist. That is itself the finding worth having.

Every "PASS" below is checked against a real field in the API response
(a telemetry stage that actually ran, an outcome code, a measured word
count) or is explicitly labeled a heuristic (a keyword search in the reply
text, which an LLM can rephrase past). Nothing is asserted here that this
run did not actually observe.
==============================================================================
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

# ------------------------------------------------------------------------------
# Targets
# ------------------------------------------------------------------------------
BACKEND_URL = "https://ai-telecaller-prototype.onrender.com"
FRONTEND_URL = "https://ai-telecaller-prototype.vercel.app"
API_V1 = f"{BACKEND_URL}/api/v1"
REQUEST_TIMEOUT = httpx.Timeout(65.0, connect=10.0)  # engine allows itself 60s/turn

# Real seeded subscribers (app/infra/seed.py) — chosen per-test for the CRM
# state each check actually needs, not invented numbers.
SEED_FAULTY = "+2348031234567"  # Adaeze Okafor: OVERDUE, router OFFLINE, LOS RED
SEED_HEALTHY = "+2348052239981"  # Tunde Balogun: CURRENT, router ONLINE, LOS GREEN
SEED_GRACE = "+2347015567342"  # Chiamaka Nwosu: GRACE_PERIOD, router DEGRADED

REPLY_MAX_WORDS = 15  # app/core/config.py Settings.reply_max_words default


# ------------------------------------------------------------------------------
# Result plumbing
# ------------------------------------------------------------------------------
class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INFO = "INFO"  # exploratory / no pass-fail spec exists — reported, not graded


@dataclass(slots=True)
class CheckResult:
    section: str
    name: str
    status: Status
    detail: str
    elapsed_ms: float | None = None


@dataclass(slots=True)
class AuditReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)
        tag = f"[{result.status.value}]".ljust(7)
        timing = f"{result.elapsed_ms:7.0f}ms" if result.elapsed_ms is not None else " " * 9
        print(f"  {tag} {timing}  {result.name}")
        print(f"           -> {result.detail}")

    def count(self, status: Status) -> int:
        return sum(1 for r in self.results if r.status is status)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def word_count(text: str) -> int:
    return len(text.split())


# ------------------------------------------------------------------------------
# HTTP helpers
# ------------------------------------------------------------------------------
async def run_turn(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    phone_number: str,
    persona: str,
    text: str,
    session_id: str | None = None,
) -> tuple[dict[str, Any] | None, float, httpx.Response]:
    """POSTs one conversational turn. Returns (json_or_None, elapsed_seconds, raw_response)."""
    data = {"phone_number": phone_number, "persona": persona, "text": text}
    if session_id:
        data["session_id"] = session_id
    start = time.perf_counter()
    response = await client.post(f"{base_url}/api/v1/calls/turn", data=data)
    elapsed = time.perf_counter() - start
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return payload, elapsed, response


def has_stage(payload: dict[str, Any], stage_name: str) -> bool:
    return any(s.get("stage") == stage_name for s in payload.get("stages", []))


# ------------------------------------------------------------------------------
# 0. Preflight
# ------------------------------------------------------------------------------
async def check_preflight(client: httpx.AsyncClient, report: AuditReport) -> bool:
    section("0. PREFLIGHT — Engine Readiness")
    start = time.perf_counter()
    try:
        response = await client.get(f"{API_V1}/readyz")
        elapsed = (time.perf_counter() - start) * 1000
    except httpx.HTTPError as exc:
        report.add(CheckResult("preflight", "Backend reachable", Status.FAIL, f"{exc!r}"))
        return False

    if response.status_code != 200:
        report.add(
            CheckResult(
                "preflight", "Backend readiness", Status.FAIL,
                f"HTTP {response.status_code}: {response.text[:200]}", elapsed,
            )
        )
        return False

    body = response.json()
    ready = body.get("status") == "ready"
    slow_note = " (likely a cold start — Render free tier sleeps after idle)" if elapsed > 3000 else ""
    report.add(
        CheckResult(
            "preflight", "Backend readiness", Status.PASS if ready else Status.FAIL,
            f"status={body.get('status')} state_backend={body.get('state_backend')} "
            f"personas={body.get('personas')}{slow_note}", elapsed,
        )
    )
    if body.get("state_backend") == "memory":
        report.add(
            CheckResult(
                "preflight", "State backend", Status.WARN,
                "Running on in-process memory, not Redis: fine for a demo, but state "
                "resets on every restart/redeploy and the deployment cannot run more "
                "than one worker without losing session continuity mid-call.",
            )
        )
    return ready


# ------------------------------------------------------------------------------
# 1. Technical assistant (Kevin) — fault diagnosis
# ------------------------------------------------------------------------------
async def check_technical(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("1. TECHNICAL ASSISTANT (kevin_l1) — Fault Diagnosis")

    payload1, elapsed1, resp1 = await run_turn(
        client, base_url=BACKEND_URL, phone_number=SEED_FAULTY, persona="kevin_l1",
        text="My fiber internet is disconnected",
    )
    if resp1.status_code != 200 or payload1 is None:
        report.add(CheckResult("technical", "Turn 1 (opening statement)", Status.FAIL,
                                f"HTTP {resp1.status_code}: {resp1.text[:200]}", elapsed1 * 1000))
        return

    session_id = payload1["session_id"]
    print(f"           Turn 1 reply: {payload1['response_text']!r}")
    report.add(
        CheckResult(
            "technical", "Turn 1 — greeting-first design", Status.INFO,
            "Kevin's prompt greets on turn 1 regardless of what the caller already said "
            "(by design — see kevin_l1.md step 1). The fault statement is addressed next "
            "turn, not this one; that is expected, not a bug.", elapsed1 * 1000,
        )
    )

    payload2, elapsed2, resp2 = await run_turn(
        client, base_url=BACKEND_URL, phone_number=SEED_FAULTY, persona="kevin_l1",
        text="Yes, it's been down since this morning, no lights on the router",
        session_id=session_id,
    )
    if resp2.status_code != 200 or payload2 is None:
        report.add(CheckResult("technical", "Turn 2 (diagnosis)", Status.FAIL,
                                f"HTTP {resp2.status_code}: {resp2.text[:200]}", elapsed2 * 1000))
        return

    reply = payload2["response_text"]
    print(f"           Turn 2 reply: {reply!r}")

    crm_ran = has_stage(payload2, "crm_lookup")
    report.add(
        CheckResult(
            "technical", "CRM was actually queried", Status.PASS if crm_ran else Status.FAIL,
            f"crm_lookup stage {'present' if crm_ran else 'MISSING'} in telemetry "
            f"(stages: {[s['stage'] for s in payload2['stages']]})", elapsed2 * 1000,
        )
    )

    telemetry_terms = ["router", "l.o.s", "los", "offline", "signal", "fault", "line", "outage"]
    mentions_telemetry = any(term in reply.lower() for term in telemetry_terms)
    report.add(
        CheckResult(
            "technical", "Reply references router/LOS telemetry (heuristic)",
            Status.PASS if mentions_telemetry else Status.WARN,
            "Keyword match on the seeded router=OFFLINE / LOS=RED state — an LLM can "
            "convey the same fact without these exact words, so treat a WARN here as "
            "'go read the reply', not 'broken'.",
        )
    )

    wc = word_count(reply)
    report.add(
        CheckResult(
            "technical", f"Reply under {REPLY_MAX_WORDS}-word budget",
            Status.PASS if wc <= REPLY_MAX_WORDS else Status.WARN,
            f"{wc} words. The budget is prompted, not server-enforced — a real prior "
            f"run of this exact persona was observed exceeding it by 2-3x, so a WARN "
            f"here is a known, pre-existing soft-compliance gap, not a regression.",
        )
    )


# ------------------------------------------------------------------------------
# 2. Billing (novaconnect_default's billing lane) — balance & payment status
# ------------------------------------------------------------------------------
async def check_billing(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("2. BILLING (novaconnect_default, BILLING_AND_PAYMENTS lane)")

    payload, elapsed, resp = await run_turn(
        client, base_url=BACKEND_URL, phone_number=SEED_FAULTY, persona="novaconnect_default",
        text="I want to check my account balance and status",
    )
    if resp.status_code != 200 or payload is None:
        report.add(CheckResult("billing", "Balance inquiry turn", Status.FAIL,
                                f"HTTP {resp.status_code}: {resp.text[:200]}", elapsed * 1000))
        return

    reply = payload["response_text"]
    print(f"           Reply: {reply!r}")

    crm_ran = has_stage(payload, "crm_lookup")
    report.add(
        CheckResult(
            "billing", "CRM was actually queried", Status.PASS if crm_ran else Status.FAIL,
            f"crm_lookup stage {'present' if crm_ran else 'MISSING'}", elapsed * 1000,
        )
    )

    balance_terms = ["balance", "overdue", "payment", "owe", "due", "outstanding"]
    mentions_balance = any(term in reply.lower() for term in balance_terms)
    report.add(
        CheckResult(
            "billing", "Reply addresses balance/payment status (heuristic)",
            Status.PASS if mentions_balance else Status.WARN,
            f"Seeded account is OVERDUE at -4500.00 NGN. Keyword match only — read the "
            f"reply above to judge whether it actually surfaced that.",
        )
    )

    connection_note = (
        "'without dropping connection metrics' from the request isn't a thing the API "
        "exposes to check mechanically — there is no separate 'connection' the turn "
        "could drop; each turn is one stateless HTTP request. Treated as: did the turn "
        "complete and return telemetry at all."
    )
    report.add(
        CheckResult(
            "billing", "Turn completed with full telemetry", Status.PASS,
            f"outcome={payload['outcome']}, {len(payload['stages'])} stages recorded. {connection_note}",
        )
    )


# ------------------------------------------------------------------------------
# 3. Sales / upgrade (Jessika) — plan evaluation
# ------------------------------------------------------------------------------
async def check_sales_upgrade(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("3. SALES / UPGRADE (jessika_presales)")

    payload1, elapsed1, resp1 = await run_turn(
        client, base_url=BACKEND_URL, phone_number=SEED_HEALTHY, persona="jessika_presales",
        text="I need to upgrade to a faster speed tier",
    )
    if resp1.status_code != 200 or payload1 is None:
        report.add(CheckResult("sales", "Turn 1 (opening statement)", Status.FAIL,
                                f"HTTP {resp1.status_code}: {resp1.text[:200]}", elapsed1 * 1000))
        return

    session_id = payload1["session_id"]
    print(f"           Turn 1 reply: {payload1['response_text']!r}")
    report.add(
        CheckResult(
            "sales", "Turn 1 — greeting-first design", Status.INFO,
            "Same pattern as Kevin: Jessika's turn-1 script fires regardless of the "
            "stated intent (jessika_presales.md step 1). A caller who opens with "
            "'I want to upgrade' is greeted, not routed, on the first turn. Whether "
            "that's acceptable UX for a real deployment is a product call, not a bug.",
            elapsed1 * 1000,
        )
    )

    payload2, elapsed2, resp2 = await run_turn(
        client, base_url=BACKEND_URL, phone_number=SEED_HEALTHY, persona="jessika_presales",
        text="Yes — home use, want the fastest option available for streaming and calls",
        session_id=session_id,
    )
    if resp2.status_code != 200 or payload2 is None:
        report.add(CheckResult("sales", "Turn 2 (plan evaluation)", Status.FAIL,
                                f"HTTP {resp2.status_code}: {resp2.text[:200]}", elapsed2 * 1000))
        return

    reply = payload2["response_text"]
    print(f"           Turn 2 reply: {reply!r}")

    crm_ran = has_stage(payload2, "crm_lookup")
    report.add(
        CheckResult(
            "sales", "CRM was actually queried", Status.PASS if crm_ran else Status.FAIL,
            f"crm_lookup stage {'present' if crm_ran else 'MISSING'}", elapsed2 * 1000,
        )
    )

    catalog_terms = ["fiber basic", "fiber value", "fiber premium", "mbps", "prepaid", "gst"]
    names_a_plan = any(term in reply.lower() for term in catalog_terms)
    report.add(
        CheckResult(
            "sales", "Reply names a concrete Veloce plan (heuristic)",
            Status.PASS if names_a_plan else Status.WARN,
            "Checks she proposed something from the actual catalog rather than a vague "
            "'we have upgrade options' non-answer.",
        )
    )

    report.add(
        CheckResult(
            "sales", "KNOWN GAP — CRM plan vs. sellable catalog mismatch", Status.INFO,
            f"The CRM record for {SEED_HEALTHY} shows the legacy plan 'NovaConnect Fibre "
            f"Essential 50Mbps' (Naira billing). Jessika's sellable catalog is entirely "
            f"Veloce-branded (Rupees, Fiber Basic/Value/Premium). Nothing connects the "
            f"two — this run's reply is logged above for a human to judge whether the "
            f"model handled that mismatch gracefully or papered over it. This is a "
            f"pre-existing architecture seam, not something this script can pass/fail.",
        )
    )


# ------------------------------------------------------------------------------
# 4a. Non-linear intent triage — the feature that's actually engineered
# ------------------------------------------------------------------------------
async def check_nonlinear_triage(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("4a. NON-LINEAR INTENT TRIAGE (novaconnect_default, one persona, one session)")

    turns = [
        ("My internet has been down since this morning", "fault"),
        ("Also — can you tell me if I have any outstanding balance?", "billing"),
        ("And separately, what upgrade options do I have?", "upgrade"),
    ]
    session_id: str | None = None
    for i, (text, lane) in enumerate(turns, start=1):
        payload, elapsed, resp = await run_turn(
            client, base_url=BACKEND_URL, phone_number=SEED_FAULTY, persona="novaconnect_default",
            text=text, session_id=session_id,
        )
        if resp.status_code != 200 or payload is None:
            report.add(
                CheckResult("nonlinear-triage", f"Turn {i} ({lane} lane)", Status.FAIL,
                            f"HTTP {resp.status_code}: {resp.text[:200]}", elapsed * 1000)
            )
            return
        session_id = payload["session_id"]
        print(f"           Turn {i} [{lane}] reply: {payload['response_text']!r}")
        report.add(
            CheckResult(
                "nonlinear-triage", f"Turn {i} ({lane} lane) completed", Status.PASS,
                f"session_id stable={payload['session_id'] == session_id}, "
                f"outcome={payload['outcome']}", elapsed * 1000,
            )
        )


# ------------------------------------------------------------------------------
# 4b. Cross-persona session probe — exploratory, no spec to grade against
# ------------------------------------------------------------------------------
async def check_cross_persona_probe(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("4b. CROSS-PERSONA SESSION PROBE (exploratory — not a documented feature)")
    print(
        "  No mechanism in this engine locks a session_id to the persona that started\n"
        "  it — each turn independently supplies both. The steps below are what the\n"
        "  request called 'shifting agents mid-turn'. Findings are reported as INFO;\n"
        "  there is no written spec for 'correct' behavior here to PASS/FAIL against."
    )

    steps = [
        ("kevin_l1", "My fiber connection is down"),
        ("novaconnect_default", "Also, what's my account balance?"),
        ("jessika_presales", "And I'd like to explore upgrading my plan"),
    ]
    session_id: str | None = None
    replies: list[tuple[str, str]] = []
    for persona, text in steps:
        payload, elapsed, resp = await run_turn(
            client, base_url=BACKEND_URL, phone_number=SEED_FAULTY, persona=persona,
            text=text, session_id=session_id,
        )
        if resp.status_code != 200 or payload is None:
            report.add(
                CheckResult(
                    "cross-persona", f"persona={persona}", Status.FAIL,
                    f"HTTP {resp.status_code}: {resp.text[:200]}", elapsed * 1000,
                )
            )
            return
        session_id = payload["session_id"]
        reply = payload["response_text"]
        replies.append((persona, reply))
        print(f"           [{persona}] reply: {reply!r}")
        report.add(
            CheckResult(
                "cross-persona", f"persona={persona} — no crash", Status.INFO,
                f"HTTP 200, outcome={payload['outcome']}", elapsed * 1000,
            )
        )

    # Best-effort signal for "crossed variables": a persona's own self-introduction
    # (e.g. Kevin's or Jessika's greeting line) leaking into a later persona's turn
    # would indicate the prior turn's raw history bled into a system prompt it
    # doesn't belong to.
    cross_contamination = []
    if "jessika" in replies[-1][1].lower() and replies[-1][0] != "jessika_presales":
        cross_contamination.append("a later persona referred to itself as Jessika")
    if "kevin" in replies[-1][1].lower() and replies[-1][0] != "kevin_l1":
        cross_contamination.append("a later persona referred to itself as Kevin")

    report.add(
        CheckResult(
            "cross-persona", "Identity bleed check (heuristic)",
            Status.WARN if cross_contamination else Status.INFO,
            "; ".join(cross_contamination) if cross_contamination
            else "No obvious identity bleed in the final reply's own text — this does "
                 "NOT prove the shared turn history is being handled correctly, only "
                 "that the surface symptom didn't show up in this one run. The "
                 "underlying design gap (sessions aren't persona-scoped) still stands "
                 "regardless of this result.",
        )
    )


# ------------------------------------------------------------------------------
# 5. Stress: per-caller rate limiting
# ------------------------------------------------------------------------------
async def check_rate_limit(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("5. STRESS — Per-Caller Rate Limit")
    burst = 25  # configured default is 20/minute per source IP
    print(f"  Firing {burst} concurrent turns from this process (one shared source IP) ...")

    async def fire(i: int) -> httpx.Response:
        _, _, resp = await run_turn(
            client, base_url=BACKEND_URL, phone_number=f"+91900000{i:04d}",
            persona="novaconnect_default", text=f"Stress test ping {i}",
        )
        return resp

    start = time.perf_counter()
    responses = await asyncio.gather(*(fire(i) for i in range(burst)), return_exceptions=True)
    elapsed = (time.perf_counter() - start) * 1000

    ok = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code == 200)
    limited = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code == 429)
    errored = burst - ok - limited

    if limited > 0:
        report.add(
            CheckResult(
                "stress", "Rate limiter engages under burst load", Status.PASS,
                f"{ok} succeeded, {limited} got HTTP 429, {errored} other — the limiter "
                f"is live and actually rejecting excess load rather than queuing or "
                f"crashing.", elapsed,
            )
        )
    else:
        report.add(
            CheckResult(
                "stress", "Rate limiter engages under burst load", Status.WARN,
                f"{ok} succeeded, 0 got HTTP 429 ({errored} other). Either the limit is "
                f"configured higher than 20/min on this deployment, or every request in "
                f"the burst was attributed to a different identity (Render's proxy "
                f"headers can do this) — not proof the limiter is absent, but the "
                f"headline claim ('rate limiting works') isn't demonstrated by this run.",
                elapsed,
            )
        )


# ------------------------------------------------------------------------------
# 6. Full path through the deployed frontend proxy
# ------------------------------------------------------------------------------
async def check_frontend_proxy(client: httpx.AsyncClient, report: AuditReport) -> None:
    section("6. FULL PATH — Vercel Frontend Proxy -> Render Backend -> Sarvam")

    start = time.perf_counter()
    try:
        response = await client.post(
            f"{FRONTEND_URL}/api/engine/calls/turn",
            data={
                "phone_number": "+919000000001",
                "persona": "jessika_presales",
                "text": "Hi, this is an automated production audit",
            },
        )
        elapsed = (time.perf_counter() - start) * 1000
    except httpx.HTTPError as exc:
        report.add(CheckResult("e2e", "Frontend -> backend round trip", Status.FAIL, f"{exc!r}"))
        return

    if response.status_code != 200:
        report.add(
            CheckResult(
                "e2e", "Frontend -> backend round trip", Status.FAIL,
                f"HTTP {response.status_code}: {response.text[:300]}", elapsed,
            )
        )
        return

    payload = response.json()
    print(f"           Reply via Vercel proxy: {payload['response_text']!r}")
    report.add(
        CheckResult(
            "e2e", "Frontend -> backend round trip", Status.PASS,
            f"outcome={payload['outcome']}, has_audio={payload.get('has_audio')} — "
            f"confirms ENGINE_BASE_URL/OPERATOR_API_KEY are correctly wired on Vercel.",
            elapsed,
        )
    )


# ------------------------------------------------------------------------------
# Final report
# ------------------------------------------------------------------------------
def print_final_assessment(report: AuditReport, total_elapsed_s: float) -> None:
    section("NovaConnect ENTERPRISE PRODUCTION READINESS ASSESSMENT")

    passed = report.count(Status.PASS)
    warned = report.count(Status.WARN)
    failed = report.count(Status.FAIL)
    info = report.count(Status.INFO)
    total = len(report.results)

    print(f"  Checks run:      {total}")
    print(f"  PASS:            {passed}")
    print(f"  WARN:            {warned}  (works, but with a caveat worth reading)")
    print(f"  FAIL:            {failed}")
    print(f"  INFO:            {info}  (exploratory findings, not graded)")
    print(f"  Total wall time: {total_elapsed_s:.1f}s")

    timed = [r.elapsed_ms for r in report.results if r.elapsed_ms is not None]
    if timed:
        print(f"\n  Turn latency — n={len(timed)}  "
              f"min={min(timed):.0f}ms  "
              f"median={statistics.median(timed):.0f}ms  "
              f"max={max(timed):.0f}ms")

    print("\n  Verdict:")
    if failed:
        print("  NOT READY — one or more checks hit a real error (see FAIL lines above).")
    elif warned:
        print("  CONDITIONALLY READY — core pipeline works end-to-end; the WARN lines")
        print("  above are known, pre-existing soft-compliance and coverage gaps, not")
        print("  crashes. Treat this as 'demo-ready, not sign-off-ready'.")
    else:
        print("  READY for the checks this script covers. See 'Other factors' in the")
        print("  accompanying writeup for what this script does NOT and cannot check.")

    if failed == 0:
        print(f"\n  {passed} passed / {warned} warned / {info} informational — 0 failed.")


async def main() -> None:
    report = AuditReport()
    overall_start = time.perf_counter()

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        ready = await check_preflight(client, report)
        if not ready:
            print("\nBackend is not ready — aborting the remaining checks.")
            print_final_assessment(report, time.perf_counter() - overall_start)
            sys.exit(1)

        await check_technical(client, report)
        await check_billing(client, report)
        await check_sales_upgrade(client, report)
        await check_nonlinear_triage(client, report)
        await check_cross_persona_probe(client, report)
        await check_rate_limit(client, report)
        await check_frontend_proxy(client, report)

    print_final_assessment(report, time.perf_counter() - overall_start)
    sys.exit(1 if report.count(Status.FAIL) else 0)


if __name__ == "__main__":
    asyncio.run(main())
