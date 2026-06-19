"""Tests for src.audit -- structured, PII-free audit logging.

Exercises the module in isolation: no app, no pipeline, no AWS. The HMAC key is
pinned via monkeypatch so hashing is deterministic.

Run from the repo root:
    uv run pytest tests/test_audit.py -v
"""

from __future__ import annotations

import pytest

import src.audit as audit
from src.audit import (
    AuditRecord,
    build_audit_record,
    hash_input,
    merge_detections,
    write_audit_record,
)
from src.pii import Detection


@pytest.fixture
def fixed_key(monkeypatch):
    """Pin the HMAC key so hash_input is deterministic across the test."""
    monkeypatch.setattr(audit, "_HMAC_KEY", b"unit-test-key-0123456789abcdef0")
    yield


def _sample_record(**overrides) -> AuditRecord:
    args = {
        "session_id": "sess-1234abcd",
        "raw_question": "Mr John Smith complained about a six-week delay",
        "masked_output": "[NAME_1] complained about a six-week delay",
        "masked_review_reason": "",
        "input_detections": [Detection(entity_type="NAME", count=1)],
        "output_detections": [Detection(entity_type="NAME", count=1)],
        "model_id": "eu.anthropic.claude-sonnet-4-6",
        "cited_provisions": ["DISP 1.6.2R"],
        "retrieved_chunk_ids": ["s3://bucket/disp1s6.md"],
        "human_review_required": True,
        "insufficient_context": False,
        "latency_ms": 1234.5,
        "retrieval_latency_ms": 300.0,
        "generation_latency_ms": 900.0,
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }
    args.update(overrides)
    return build_audit_record(**args)


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def test_hash_is_deterministic_for_same_input(fixed_key):
    assert hash_input("a complaint") == hash_input("a complaint")


def test_hash_differs_for_different_input(fixed_key):
    assert hash_input("complaint A") != hash_input("complaint B")


def test_hash_is_hex_sha256_length(fixed_key):
    assert len(hash_input("anything")) == 64
    int(hash_input("anything"), 16)  # parses as hex


def test_hash_is_not_the_raw_text(fixed_key):
    assert hash_input("Mr John Smith") != "Mr John Smith"


def test_hash_depends_on_the_key(monkeypatch):
    monkeypatch.setattr(audit, "_HMAC_KEY", b"key-aaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with_key_a = hash_input("same input")
    monkeypatch.setattr(audit, "_HMAC_KEY", b"key-bbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    with_key_b = hash_input("same input")
    assert with_key_a != with_key_b


# ---------------------------------------------------------------------------
# merge_detections
# ---------------------------------------------------------------------------


def test_merge_detections_sums_per_type():
    merged = merge_detections(
        [Detection(entity_type="NAME", count=1), Detection(entity_type="EMAIL", count=1)],
        [Detection(entity_type="NAME", count=2)],
    )
    counts = {d.entity_type: d.count for d in merged}
    assert counts == {"NAME": 3, "EMAIL": 1}


def test_merge_detections_is_sorted_by_type():
    merged = merge_detections(
        [Detection(entity_type="POSTCODE", count=1)],
        [Detection(entity_type="EMAIL", count=1)],
    )
    assert [d.entity_type for d in merged] == ["EMAIL", "POSTCODE"]


def test_merge_detections_empty():
    assert merge_detections() == []
    assert merge_detections([], []) == []


# ---------------------------------------------------------------------------
# build_audit_record
# ---------------------------------------------------------------------------


def test_build_record_hashes_input_and_keeps_no_raw_pii(fixed_key):
    record = _sample_record()
    dumped = record.model_dump_json()
    assert "John Smith" not in dumped
    assert record.input_hash != "Mr John Smith complained about a six-week delay"
    assert len(record.input_hash) == 64


def test_build_record_carries_masked_output(fixed_key):
    record = _sample_record()
    assert record.output == "[NAME_1] complained about a six-week delay"


def test_build_record_maps_usage_to_token_fields(fixed_key):
    record = _sample_record()
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (10, 20, 30)


def test_build_record_tolerates_empty_usage(fixed_key):
    record = _sample_record(usage={})
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (0, 0, 0)


def test_build_record_has_a_timestamp(fixed_key):
    record = _sample_record()
    assert record.timestamp  # ISO string, non-empty


def test_build_record_preserves_provisions_and_chunks(fixed_key):
    record = _sample_record()
    assert record.cited_provisions == ["DISP 1.6.2R"]
    assert record.retrieved_chunk_ids == ["s3://bucket/disp1s6.md"]


# ---------------------------------------------------------------------------
# write_audit_record + round-trip
# ---------------------------------------------------------------------------


def test_write_appends_one_json_line(fixed_key, tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit_record(_sample_record(), path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    AuditRecord.model_validate_json(lines[0])  # parses back


def test_write_is_append_only(fixed_key, tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit_record(_sample_record(), path=path)
    write_audit_record(_sample_record(), path=path)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_write_creates_parent_directory(fixed_key, tmp_path):
    path = tmp_path / "nested" / "dir" / "audit.jsonl"
    write_audit_record(_sample_record(), path=path)
    assert path.exists()


def test_record_round_trips_through_json(fixed_key):
    record = _sample_record()
    restored = AuditRecord.model_validate_json(record.model_dump_json())
    assert restored == record
