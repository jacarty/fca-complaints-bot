"""Query the JAM-277 audit trail.

Read the append-only JSONL audit log and filter/summarise it. Examples:

    uv run python scripts/query_audit.py --session <id>
    uv run python scripts/query_audit.py --review-only --since 2026-06-01
    uv run python scripts/query_audit.py --json | jq .

Run from the repo root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make src/ importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit import DEFAULT_AUDIT_PATH, AuditRecord  # noqa: E402


def load_records(path: Path) -> list[AuditRecord]:
    """Parse the JSONL trail into AuditRecords (skips blank lines)."""
    if not path.exists():
        return []
    records: list[AuditRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            records.append(AuditRecord.model_validate_json(stripped))
    return records


def filter_records(
    records: list[AuditRecord],
    *,
    session: str | None = None,
    since: str | None = None,
    until: str | None = None,
    review_only: bool = False,
) -> list[AuditRecord]:
    """Filter records. ``since``/``until`` are lexicographic compares on the ISO
    timestamp, which is valid because the timestamps are zero-padded UTC."""
    out = []
    for r in records:
        if session and r.session_id != session:
            continue
        if since and r.timestamp < since:
            continue
        if until and r.timestamp > until:
            continue
        if review_only and not r.human_review_required:
            continue
        out.append(r)
    return out


def summarise(record: AuditRecord) -> str:
    """One-line human-readable summary of a record."""
    pii = ", ".join(f"{d.entity_type}:{d.count}" for d in record.input_detections) or "none"
    flag = "REVIEW" if record.human_review_required else "ok"
    return (
        f"{record.timestamp}  {record.session_id[:8]}  {flag:<6}  "
        f"tokens={record.total_tokens:<5} {record.latency_ms:.0f}ms  "
        f"input_pii=[{pii}]  hash={record.input_hash[:12]}..."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the FCA bot audit trail.")
    parser.add_argument("--path", type=Path, default=DEFAULT_AUDIT_PATH, help="audit JSONL path")
    parser.add_argument("--session", help="filter by session_id")
    parser.add_argument("--since", help="ISO timestamp lower bound, inclusive (e.g. 2026-06-01)")
    parser.add_argument("--until", help="ISO timestamp upper bound, inclusive")
    parser.add_argument(
        "--review-only", action="store_true", help="only records flagged for human review"
    )
    parser.add_argument("--json", action="store_true", help="emit full records as JSON lines")
    parser.add_argument("--limit", type=int, help="show only the most recent N matches")
    args = parser.parse_args(argv)

    records = load_records(args.path)
    records = filter_records(
        records,
        session=args.session,
        since=args.since,
        until=args.until,
        review_only=args.review_only,
    )
    if args.limit:
        records = records[-args.limit :]

    if not records:
        print(f"No matching records in {args.path}", file=sys.stderr)
        return 0

    for record in records:
        print(record.model_dump_json() if args.json else summarise(record))
    print(f"\n{len(records)} record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
