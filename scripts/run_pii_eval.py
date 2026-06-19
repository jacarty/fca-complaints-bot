"""PII-redaction evaluation (JAM-277).

Measures how well ``src.pii.redact`` performs against a labelled corpus:

  * **Recall** (the safety-critical number): of the real PII spans, what fraction
    did we mask? A miss is a leak.
  * **Precision / over-mask rate**: of everything we masked, what fraction was
    real PII? A false positive is over-masking (render-safe, but it degrades
    model context and pollutes the audit trail).

Method: for each case, run redact on a fresh Vault, recover exactly what was
masked from ``vault.token_to_value`` (value + type-from-token), locate those
spans in the original text, and match them against the gold spans by character
overlap. A masked span that overlaps a true-PII gold span is a true positive;
anything else is a false positive (categorised against labelled negatives where
possible, else 'spurious'). Standalone -- no AWS, no app.

Usage:
    uv run python scripts/run_pii_eval.py
    uv run python scripts/run_pii_eval.py --verbose      # list false positives
    uv run python scripts/run_pii_eval.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

# Make src/ importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pii import Vault, redact  # noqa: E402

DEFAULT_CASES_PATH = Path("data/eval/pii_cases.json")
_TOKEN_TYPE_RE = re.compile(r"\[([A-Z][A-Z_]*)_\d+\]")


# ---------------------------------------------------------------------------
# Corpus schema
# ---------------------------------------------------------------------------


class GoldEntity(BaseModel):
    text: str
    type: str
    is_pii: bool = True


class PiiCase(BaseModel):
    case_id: str
    source: str = "synthetic"
    text: str
    entities: list[GoldEntity] = Field(default_factory=list)


def load_cases(path: Path) -> list[PiiCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [PiiCase.model_validate(c) for c in data["cases"]]


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def spans_of(text: str, sub: str) -> list[tuple[int, int]]:
    """All (start, end) character spans where `sub` occurs in `text`."""
    out: list[tuple[int, int]] = []
    if not sub:
        return out
    start = text.find(sub)
    while start != -1:
        out.append((start, start + len(sub)))
        start = text.find(sub, start + 1)
    return out


def overlaps(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(a_s < b_e and b_s < a_e for a_s, a_e in a for b_s, b_e in b)


def _type_of(token: str) -> str:
    m = _TOKEN_TYPE_RE.match(token)
    return m.group(1) if m else "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-case evaluation
# ---------------------------------------------------------------------------


class MaskOutcome(BaseModel):
    value: str
    masked_type: str
    is_true_positive: bool
    fp_category: str | None = None  # for false positives: negative type or 'spurious'


class GoldOutcome(BaseModel):
    text: str
    type: str
    detected: bool


class CaseResult(BaseModel):
    case_id: str
    source: str
    masks: list[MaskOutcome]
    gold: list[GoldOutcome]


def evaluate_case(case: PiiCase) -> CaseResult:
    vault = Vault()
    redact(case.text, vault)

    # Recover what was masked, with type and located spans.
    masks: list[tuple[str, str, list[tuple[int, int]]]] = []
    for token, value in vault.token_to_value.items():
        masks.append((value, _type_of(token), spans_of(case.text, value)))

    gold_spans = {id(e): spans_of(case.text, e.text) for e in case.entities}
    true_pii = [e for e in case.entities if e.is_pii]
    negatives = [e for e in case.entities if not e.is_pii]

    # Recall: was each true-PII span masked?
    gold_outcomes = [
        GoldOutcome(
            text=e.text,
            type=e.type,
            detected=any(overlaps(gold_spans[id(e)], mspans) for _, _, mspans in masks),
        )
        for e in true_pii
    ]

    # Precision: did each mask land on real PII?
    mask_outcomes: list[MaskOutcome] = []
    for value, mtype, mspans in masks:
        tp = any(overlaps(mspans, gold_spans[id(e)]) for e in true_pii)
        category = None
        if not tp:
            neg = next((e for e in negatives if overlaps(mspans, gold_spans[id(e)])), None)
            category = neg.type if neg else "spurious"
        mask_outcomes.append(
            MaskOutcome(value=value, masked_type=mtype, is_true_positive=tp, fp_category=category)
        )

    return CaseResult(
        case_id=case.case_id, source=case.source, masks=mask_outcomes, gold=gold_outcomes
    )


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------


def _safe_div(n: int, d: int) -> float:
    return n / d if d else 0.0


def aggregate(results: list[CaseResult]) -> dict:
    recall_by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [hit, total]
    tp_total = fp_total = 0
    fp_by_category: dict[str, int] = defaultdict(int)

    for r in results:
        for g in r.gold:
            recall_by_type[g.type][1] += 1
            if g.detected:
                recall_by_type[g.type][0] += 1
        for m in r.masks:
            if m.is_true_positive:
                tp_total += 1
            else:
                fp_total += 1
                fp_by_category[m.fp_category or "spurious"] += 1

    total_true = sum(t for _, t in recall_by_type.values())
    total_hit = sum(h for h, _ in recall_by_type.values())
    total_masks = tp_total + fp_total

    return {
        "recall_by_type": {
            t: {"recall": _safe_div(h, n), "hit": h, "total": n}
            for t, (h, n) in sorted(recall_by_type.items())
        },
        "overall_recall": _safe_div(total_hit, total_true),
        "overall_precision": _safe_div(tp_total, total_masks),
        "true_positives": tp_total,
        "false_positives": fp_total,
        "total_masks": total_masks,
        "over_mask_rate": _safe_div(fp_total, total_masks),
        "fp_by_category": dict(sorted(fp_by_category.items(), key=lambda kv: -kv[1])),
        "n_cases": len(results),
    }


def format_report(agg: dict) -> str:
    lines = [
        "# PII Redaction Evaluation",
        "",
        f"Cases: {agg['n_cases']}",
        "",
        "## Recall by entity type (PII protection \u2014 a miss is a leak)",
        "",
        "| Type | Recall | Detected/Total |",
        "|------|-------:|----------------|",
    ]
    for t, d in agg["recall_by_type"].items():
        lines.append(f"| {t} | {d['recall']:.0%} | {d['hit']}/{d['total']} |")
    lines += [
        f"| **Overall** | **{agg['overall_recall']:.0%}** | "
        f"{sum(d['hit'] for d in agg['recall_by_type'].values())}/"
        f"{sum(d['total'] for d in agg['recall_by_type'].values())} |",
        "",
        "## Precision / over-masking",
        "",
        f"- Masks made: {agg['total_masks']}  (TP {agg['true_positives']}, FP {agg['false_positives']})",
        f"- Precision: {agg['overall_precision']:.0%}",
        f"- Over-mask rate: {agg['over_mask_rate']:.0%}",
        "",
        "False positives by cause:",
    ]
    for cat, n in agg["fp_by_category"].items():
        lines.append(f"- {cat}: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate PII redaction precision/recall.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--json", type=Path, help="write full results to this path")
    parser.add_argument("--verbose", action="store_true", help="list false positives and misses")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    results = [evaluate_case(c) for c in cases]
    agg = aggregate(results)

    print(format_report(agg))

    if args.verbose:
        print("\n## False positives (over-masked)")
        for r in results:
            fps = [m for m in r.masks if not m.is_true_positive]
            if fps:
                shown = ", ".join(f"{m.value!r} ({m.fp_category})" for m in fps)
                print(f"- {r.case_id}: {shown}")
        print("\n## Misses (true PII not masked)")
        for r in results:
            misses = [g for g in r.gold if not g.detected]
            if misses:
                shown = ", ".join(f"{g.text!r} ({g.type})" for g in misses)
                print(f"- {r.case_id}: {shown}")

    if args.json:
        args.json.write_text(
            json.dumps(
                {"aggregate": agg, "cases": [r.model_dump() for r in results]},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nFull results written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
