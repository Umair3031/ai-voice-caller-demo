"""
Scoring for the speech-to-text evaluation.

Pure functions, no network, so the numbers can be trusted before anything is
measured with them (see `tests/unit/test_eval_metrics.py`).

The normalisation matters as much as the arithmetic: a recogniser that writes
"five six zero zero three four" for "560034" has not made an error, and one that
writes "Wi-Fi" for "wifi" has not either. Scoring those as misses would make
every engine look worse than it is and hide the mistakes that do matter.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def normalize(text: str) -> list[str]:
    """Lower-cased word tokens with punctuation gone and spoken digits as digits.

    Runs of single digits are joined, so "5 6 0 0 3 4" and "560034" compare equal.
    """
    cleaned = re.sub(r"[^\w\s]", " ", text.lower().replace("-", ""))
    tokens = [_DIGIT_WORDS.get(token, token) for token in cleaned.split()]

    merged: list[str] = []
    run = ""
    for token in tokens:
        if len(token) == 1 and token.isdigit():
            run += token
            continue
        if run:
            merged.append(run)
            run = ""
        merged.append(token)
    if run:
        merged.append(run)
    return merged


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Levenshtein distance over tokens."""
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (left != right),  # substitution
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word error rate: edits needed to turn the hypothesis into the reference,
    as a fraction of the reference's length. 0.0 is perfect; it can exceed 1.0."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def digit_sequences(text: str) -> list[str]:
    """Every run of digits of length 3 or more, after normalisation — PIN codes,
    phone numbers and account numbers, where one wrong digit is a failed call."""
    return [token for token in normalize(text) if token.isdigit() and len(token) >= 3]


def digit_accuracy(reference: str, hypothesis: str) -> float | None:
    """Fraction of the reference's digit sequences reproduced exactly. None when
    the reference has none, so the case is left out of the average."""
    expected = digit_sequences(reference)
    if not expected:
        return None
    heard = digit_sequences(hypothesis)
    return sum(1 for sequence in expected if sequence in heard) / len(expected)


def term_recall(terms: Sequence[str], hypothesis: str) -> float | None:
    """Fraction of the domain terms (brand and product words) that came through.
    Compared with spaces and punctuation removed, so "Wi-Fi 6" matches "wifi 6"."""
    if not terms:
        return None
    haystack = "".join(normalize(hypothesis))
    found = sum(1 for term in terms if "".join(normalize(term)) in haystack)
    return found / len(terms)


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile, `fraction` in 0..1. 0.0 for no data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
