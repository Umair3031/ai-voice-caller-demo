"""
Measure how well the speech-to-text stage hears a telecom caller.

    python -m evals.run_stt                     # synthesise every case, transcribe, score
    python -m evals.run_stt --language en-IN    # pin the language instead of auto-detecting
    python -m evals.run_stt --limit 8           # a cheap smoke run

Audio for each case comes from, in order:

  1. a real recording at `evals/audio/<case id>.wav` - use these for anything you
     want to trust, ideally several accents and a noisy room;
  2. otherwise the case's text spoken by a Sarvam TTS voice, cached under
     `evals/.cache/` so a repeat run costs only the transcriptions.

Synthetic speech is clean and evenly paced, so it is a *ceiling*: real callers will
score worse. Its value is comparison - the same cases before and after a change, or
auto-detected language against a pinned one - and it needs no one to record anything.

Every run calls the live Sarvam API and spends credits. The report says how many
clips were synthetic and how many were real, so the two are never blended silently.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.services.sarvam import SarvamSpeechToText, SarvamTextToSpeech, create_sarvam_client
from evals import metrics

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases.jsonl"
AUDIO_DIR = ROOT / "audio"
CACHE_DIR = ROOT / ".cache"
RESULTS_DIR = ROOT / "results"

# Indian English voices, rotated across cases so one voice's quirks do not decide the score.
SPEAKERS = ("aditya", "priya", "rahul", "neha")


@dataclass
class CaseResult:
    id: str
    tags: list[str]
    source: str  # "recording" | "synthetic"
    reference: str
    heard: str
    detected_language: str | None
    wer: float
    digit_accuracy: float | None
    term_recall: float | None
    latency_ms: float
    error: str | None = None


@dataclass
class Report:
    started: str
    language: str
    results: list[CaseResult] = field(default_factory=list)


def load_cases(limit: int | None) -> list[dict]:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    return cases[:limit] if limit else cases


async def audio_for(case: dict, index: int, tts: SarvamTextToSpeech) -> tuple[bytes, str]:
    recording = AUDIO_DIR / f"{case['id']}.wav"
    if recording.exists():
        return recording.read_bytes(), "recording"

    speaker = SPEAKERS[index % len(SPEAKERS)]
    key = hashlib.sha256(f"{speaker}|{case['text']}".encode()).hexdigest()[:16]
    cached = CACHE_DIR / f"{case['id']}-{key}.wav"
    if not cached.exists():
        synthesis = await tts.synthesize(case["text"], language_code="en-IN", speaker=speaker)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(synthesis.audio_bytes)
    return cached.read_bytes(), "synthetic"


async def run_case(
    case: dict,
    index: int,
    stt: SarvamSpeechToText,
    tts: SarvamTextToSpeech,
    language: str | None,
) -> CaseResult:
    reference = case["text"]
    try:
        audio, source = await audio_for(case, index, tts)
        started = time.perf_counter()
        heard = await stt.transcribe(audio, filename=f"{case['id']}.wav", language_code=language)
        latency_ms = (time.perf_counter() - started) * 1000
    except Exception as exc:
        return CaseResult(
            id=case["id"],
            tags=case["tags"],
            source="unknown",
            reference=reference,
            heard="",
            detected_language=None,
            wer=1.0,
            digit_accuracy=None,
            term_recall=None,
            latency_ms=0.0,
            error=str(exc)[:200],
        )
    return CaseResult(
        id=case["id"],
        tags=case["tags"],
        source=source,
        reference=reference,
        heard=heard.transcript,
        detected_language=heard.language_code,
        wer=metrics.word_error_rate(reference, heard.transcript),
        digit_accuracy=metrics.digit_accuracy(reference, heard.transcript),
        term_recall=metrics.term_recall(case.get("terms", []), heard.transcript),
        latency_ms=latency_ms,
    )


def summarise(results: list[CaseResult]) -> dict[str, float | None]:
    ok = [r for r in results if r.error is None]
    latencies = [r.latency_ms for r in ok]
    english = [r for r in ok if (r.detected_language or "").lower().startswith("en")]
    digits = [r.digit_accuracy for r in ok if r.digit_accuracy is not None]
    terms = [r.term_recall for r in ok if r.term_recall is not None]
    return {
        "cases": len(results),
        "failed": len(results) - len(ok),
        "wer": metrics.mean([r.wer for r in ok]),
        "digit_accuracy": metrics.mean(digits),
        "term_recall": metrics.mean(terms),
        "language_id_english": len(english) / len(ok) if ok else None,
        "latency_p50_ms": metrics.percentile(latencies, 0.5),
        "latency_p95_ms": metrics.percentile(latencies, 0.95),
    }


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%" if pct else f"{value:.0f}"


def print_report(report: Report) -> None:
    results = report.results
    sources: dict[str, int] = defaultdict(int)
    for r in results:
        sources[r.source] += 1
    print(f"\nSTT evaluation - language: {report.language} - clips: {dict(sources)}")
    print("=" * 78)

    groups: dict[str, list[CaseResult]] = {"ALL": results}
    for r in results:
        for tag in r.tags:
            groups.setdefault(tag, []).append(r)

    header = ("group", "cases", "WER", "digits", "terms", "lang=en", "p50 ms", "p95 ms")
    print("".join(f"{h:<10}" if i == 0 else f"{h:>9}" for i, h in enumerate(header)))
    for name, rows in groups.items():
        s = summarise(rows)
        cells = (
            _fmt(s["cases"]),
            _fmt(s["wer"], True),
            _fmt(s["digit_accuracy"], True),
            _fmt(s["term_recall"], True),
            _fmt(s["language_id_english"], True),
            _fmt(s["latency_p50_ms"]),
            _fmt(s["latency_p95_ms"]),
        )
        print(f"{name:<10}" + "".join(f"{c:>9}" for c in cells))

    worst = sorted((r for r in results if r.error is None), key=lambda r: r.wer, reverse=True)[:5]
    print("\nWorst five (said -> heard):")
    for r in worst:
        print(f"  [{r.wer * 100:3.0f}%] {r.reference}\n         -> {r.heard or '(nothing)'}")
    for r in (r for r in results if r.error):
        print(f"  FAILED {r.id}: {r.error}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--language",
        default=None,
        help="a BCP-47 code such as en-IN (default: the engine's setting)",
    )
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    args = parser.parse_args()

    settings = get_settings()
    language = args.language or settings.stt_language_code
    client = create_sarvam_client(settings)
    stt, tts = SarvamSpeechToText(client, settings), SarvamTextToSpeech(client, settings)

    cases = load_cases(args.limit)
    # Sequential on purpose: a rate-limit error mid-run would skew both scores and latencies.
    results = [await run_case(case, i, stt, tts, language) for i, case in enumerate(cases)]

    report = Report(started=datetime.now(UTC).isoformat(), language=language, results=results)
    print_report(report)

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"stt-{datetime.now(UTC):%Y%m%d-%H%M%S}-{language}.json"
    out.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    asyncio.run(main())
