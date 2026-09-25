"""The scoring has to be right before any number it produces is worth reading."""

from __future__ import annotations

import pytest

from evals import metrics


def test_spoken_digits_and_written_digits_compare_equal() -> None:
    assert metrics.normalize("PIN five six zero zero three four") == ["pin", "560034"]
    assert metrics.normalize("PIN 560034") == ["pin", "560034"]
    assert metrics.normalize("5 6 0 0 3 4") == ["560034"]


def test_punctuation_and_case_do_not_count_as_errors() -> None:
    assert metrics.word_error_rate("Wi-Fi 6, please!", "wifi 6 please") == 0.0


@pytest.mark.parametrize(
    ("reference", "heard", "expected"),
    [
        ("my internet is down", "my internet is down", 0.0),
        ("my internet is down", "my internet is up", 0.25),  # one substitution in four
        ("my internet is down", "my internet", 0.5),  # two deletions in four
        ("my internet is down", "well my internet is down now", 0.5),  # two insertions
    ],
)
def test_word_error_rate(reference: str, heard: str, expected: float) -> None:
    assert metrics.word_error_rate(reference, heard) == pytest.approx(expected)


def test_empty_reference_is_handled() -> None:
    assert metrics.word_error_rate("", "") == 0.0
    assert metrics.word_error_rate("", "something") == 1.0


def test_one_wrong_digit_fails_the_whole_sequence() -> None:
    reference = "my PIN code is five six zero zero three four"
    assert metrics.digit_accuracy(reference, "my pin code is 560034") == 1.0
    assert metrics.digit_accuracy(reference, "my pin code is 560035") == 0.0


def test_digit_accuracy_is_none_when_there_is_nothing_to_check() -> None:
    assert metrics.digit_accuracy("my internet is down", "my internet is down") is None


def test_term_recall_ignores_spacing_and_punctuation() -> None:
    assert metrics.term_recall(["Wi-Fi 6", "router"], "does it have wifi 6 and a router") == 1.0
    assert metrics.term_recall(["Veloce", "UltraFiber"], "about velocity ultra fiber") == 0.5
    assert metrics.term_recall([], "anything") is None


def test_percentile() -> None:
    values = [100.0, 200.0, 300.0, 400.0, 500.0]
    assert metrics.percentile(values, 0.5) == 300.0
    assert metrics.percentile(values, 0.95) == 500.0
    assert metrics.percentile([], 0.5) == 0.0
