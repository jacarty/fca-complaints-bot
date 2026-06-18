"""Structured audit logging for the FCA complaints bot (JAM-277).

Writes one JSON line per ``/ask`` turn to an append-only trail. Each record
carries enough to answer "what did the system tell handler X about complaint Y,
when, with which model and provisions" -- WITHOUT storing raw PII:

  * ``input_hash``: HMAC-SHA256(secret_key, raw_input). A *keyed* correlation
    token, not the text. A compliance officer can match a known complaint to its
    record by re-computing the HMAC; for free-text input it is not reversible by
    enumeration. This is pseudonymisation, not anonymisation -- the trail is
    still personal data under GDPR, so retention limits, access control, and
    DSAR/erasure obligations still apply.
  * ``output`` / ``human_review_reason``: the *masked* model output (Vault
    tokens, no PII).
  * ``input_detections`` / ``output_detections``: entity types + counts only --
    the evidence that redaction fired, on each side, with no raw values.

The HMAC key comes from ``AUDIT_HMAC_KEY`` (set it from a secrets manager in
production). If unset, an ephemeral per-process key is generated and a warning
emitted: the app still runs, but ``input_hash`` correlation will not survive a
restart -- set the env var for a stable trail.

British English throughout.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from src.pii import Detection

logger = logging.getLogger(__name__)

# Sink path -- override with AUDIT_LOG_PATH. Runtime data; do not commit it.
DEFAULT_AUDIT_PATH = Path(os.getenv("AUDIT_LOG_PATH", "logs/audit.jsonl"))

_WRITE_LOCK = threading.Lock()  # the route runs in a threadpool; serialise appends
_KEY_LOCK = threading.Lock()
_HMAC_KEY: bytes | None = None


# ---------------------------------------------------------------------------
# Keyed hashing
# ---------------------------------------------------------------------------


def _get_hmac_key() -> bytes:
    """Return the HMAC key, reading ``AUDIT_HMAC_KEY`` once and caching it.

    Falls back to an ephemeral per-process key (with a warning) if unset, so the
    app runs out of the box -- correlation then resets on each restart.
    """
    global _HMAC_KEY
    if _HMAC_KEY is not None:
        return _HMAC_KEY
    with _KEY_LOCK:
        if _HMAC_KEY is None:
            env = os.getenv("AUDIT_HMAC_KEY")
            if env:
                _HMAC_KEY = env.encode("utf-8")
            else:
                _HMAC_KEY = secrets.token_bytes(32)
                logger.warning(
                    "AUDIT_HMAC_KEY not set -- using an ephemeral key; audit "
                    "input_hash correlation will not survive a restart. Set "
                    "AUDIT_HMAC_KEY (from a secrets manager in production)."
                )
    return _HMAC_KEY


def hash_input(raw_text: str) -> str:
    """Keyed correlation token for a raw handler message (HMAC-SHA256 hex)."""
    return hmac.new(_get_hmac_key(), raw_text.encode("utf-8"), hashlib.sha256).hexdigest()


def merge_detections(*detection_lists: list[Detection]) -> list[Detection]:
    """Sum per-type counts across several redaction passes into one summary."""
    totals: dict[str, int] = {}
    for detections in detection_lists:
        for d in detections:
            totals[d.entity_type] = totals.get(d.entity_type, 0) + d.count
    return [Detection(entity_type=etype, count=n) for etype, n in sorted(totals.items())]


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


class AuditRecord(BaseModel):
    """One audited ``/ask`` turn. Contains no raw PII by construction."""

    timestamp: str = Field(description="ISO 8601 UTC time the turn was audited")
    session_id: str
    input_hash: str = Field(description="HMAC-SHA256 of the raw input; a token, not text")
    input_detections: list[Detection] = Field(default_factory=list)
    output_detections: list[Detection] = Field(default_factory=list)
    output: str = Field(description="Masked model output (handler answer + customer draft)")
    model_id: str = ""
    cited_provisions: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    human_review_reason: str = Field(default="", description="Masked review reason")
    insufficient_context: bool = False
    latency_ms: float = 0.0
    retrieval_latency_ms: float = 0.0
    generation_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def build_audit_record(
    *,
    session_id: str,
    raw_question: str,
    masked_output: str,
    masked_review_reason: str,
    input_detections: list[Detection],
    output_detections: list[Detection],
    model_id: str,
    cited_provisions: list[str],
    retrieved_chunk_ids: list[str],
    human_review_required: bool,
    insufficient_context: bool,
    latency_ms: float,
    retrieval_latency_ms: float,
    generation_latency_ms: float,
    usage: dict,
) -> AuditRecord:
    """Assemble an :class:`AuditRecord` from a turn's pieces.

    ``raw_question`` is hashed, never stored. ``masked_output`` and
    ``masked_review_reason`` must already be pseudonymised by the caller.
    """
    return AuditRecord(
        timestamp=datetime.now(UTC).isoformat(),
        session_id=session_id,
        input_hash=hash_input(raw_question),
        input_detections=input_detections,
        output_detections=output_detections,
        output=masked_output,
        model_id=model_id,
        cited_provisions=cited_provisions,
        retrieved_chunk_ids=retrieved_chunk_ids,
        human_review_required=human_review_required,
        human_review_reason=masked_review_reason,
        insufficient_context=insufficient_context,
        latency_ms=latency_ms,
        retrieval_latency_ms=retrieval_latency_ms,
        generation_latency_ms=generation_latency_ms,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
    )


def write_audit_record(record: AuditRecord, *, path: Path | None = None) -> None:
    """Append one record as a JSON line. Thread-safe."""
    target = path or DEFAULT_AUDIT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    line = record.model_dump_json() + "\n"
    with _WRITE_LOCK, target.open("a", encoding="utf-8") as f:
        f.write(line)
