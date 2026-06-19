"""Tests for the PII-redaction eval logic (scripts/run_pii_eval.py).

Covers the span-matching and per-case scoring in isolation, on constructed
cases, so the metrics are trustworthy independent of the corpus content.

Run from the repo root:
    uv run pytest tests/test_pii_eval.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_pii_eval as ev  # noqa: E402

# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def test_spans_of_finds_all_occurrences():
    assert ev.spans_of("a cat and a cat", "cat") == [(2, 5), (12, 15)]


def test_spans_of_empty_substring():
    assert ev.spans_of("anything", "") == []


def test_overlaps_true_and_false():
    assert ev.overlaps([(0, 5)], [(3, 8)]) is True
    assert ev.overlaps([(0, 5)], [(5, 9)]) is False  # adjacent, not overlapping


def test_type_of_token():
    assert ev._type_of("[NAME_1]") == "NAME"
    assert ev._type_of("[SORT_CODE_2]") == "SORT_CODE"
    assert ev._type_of("not-a-token") == "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-case evaluation
# ---------------------------------------------------------------------------


def _case(text, entities):
    return ev.PiiCase(
        case_id="t",
        text=text,
        entities=[ev.GoldEntity(**e) for e in entities],
    )


def test_true_pii_is_detected_as_true_positive():
    case = _case(
        "My name is Mr John Smith and my email is jane@example.com.",
        [
            {"text": "Mr John Smith", "type": "NAME", "is_pii": True},
            {"text": "jane@example.com", "type": "EMAIL", "is_pii": True},
        ],
    )
    result = ev.evaluate_case(case)
    assert all(g.detected for g in result.gold)
    assert all(m.is_true_positive for m in result.masks)


def test_org_name_is_a_false_positive():
    case = _case(
        "I dispute a charge for Tottenham Hotspur tickets.",
        [{"text": "Tottenham Hotspur", "type": "ORG", "is_pii": False}],
    )
    result = ev.evaluate_case(case)
    fps = [m for m in result.masks if not m.is_true_positive]
    assert fps, "expected the org name to be over-masked"
    assert fps[0].fp_category == "ORG"


def test_lowercase_name_is_missed():
    case = _case(
        "the customer thierry henry says we ignored him.",
        [{"text": "thierry henry", "type": "NAME", "is_pii": True}],
    )
    result = ev.evaluate_case(case)
    assert result.gold[0].detected is False  # recall miss


def test_validator_protects_against_false_positive():
    # A Luhn-invalid 16-digit run labelled as a non-PII reference must not be
    # masked as a CARD.
    case = _case(
        "reference 1234 5678 9012 3456 is not my card.",
        [{"text": "1234 5678 9012 3456", "type": "REFERENCE", "is_pii": False}],
    )
    result = ev.evaluate_case(case)
    assert all(m.masked_type != "CARD" for m in result.masks)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_aggregate_computes_recall_and_precision():
    cases = [
        _case(
            "Email me at jane@example.com.",
            [{"text": "jane@example.com", "type": "EMAIL", "is_pii": True}],
        ),
        _case(
            "Charge for Tottenham Hotspur tickets.",
            [{"text": "Tottenham Hotspur", "type": "ORG", "is_pii": False}],
        ),
    ]
    agg = ev.aggregate([ev.evaluate_case(c) for c in cases])
    assert agg["overall_recall"] == 1.0  # the one true-PII email was caught
    assert agg["false_positives"] >= 1  # the org was over-masked
    assert 0.0 <= agg["overall_precision"] <= 1.0
