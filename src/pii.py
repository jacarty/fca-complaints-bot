"""PII redaction for the FCA complaints handling bot.

Reversible, session-scoped pseudonymisation. Detected personal data is replaced
with stable placeholder tokens (e.g. ``[NAME_1]``) *before* any text is sent to
Bedrock or stored in conversation history. The token-to-value map -- the
``Vault`` -- lives server-side, per session, and is never sent to the model or
written to the audit log. The handler-facing render re-hydrates the tokens, so
the model never sees raw PII but the authorised human does.

This is a *demonstration* of the pattern, not a production PII engine. Detection
is deliberately lightweight regex/format matching:

  * Structured identifiers -- email, NI number, card (PAN, Luhn-checked), sort
    code, keyword-anchored account number, postcode -- detect fairly reliably.
  * Free-text **names and street addresses are best-effort**. Regex name
    detection both misses (unusual formats) and false-positives (Title-Case
    domain phrases), which is why a stop-word guard is applied and why the
    output-side redaction in the route exists as a second net.

The production upgrade path is Amazon Comprehend ``DetectPiiEntities`` or
Bedrock Guardrails sensitive-information filters, slotted in behind the same
``Detector`` interface without touching the Vault / redact / rehydrate contract.

British English throughout.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Validators -- cut false positives on structured identifiers
# ---------------------------------------------------------------------------


def _luhn_ok(candidate: str) -> bool:
    """Confirm a candidate card PAN with the Luhn checksum."""
    nums = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    checksum = 0
    parity = len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def _nino_ok(candidate: str) -> bool:
    """Validate a UK National Insurance number's structure.

    Two prefix letters (excluding the combinations HMRC never issues), six
    digits, a final suffix letter A-D. Applying the prefix rules removes a lot
    of false positives like ordinary two-letter-plus-digits tokens.
    """
    s = candidate.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{6}[A-D]", s):
        return False
    first, second = s[0], s[1]
    if first in "DFIQUV" or second in "DFIQUVO":
        return False
    return s[:2] not in {"BG", "GB", "NK", "KN", "TN", "NT", "ZZ"}


_NAME_STOPWORDS = {
    "financial",
    "conduct",
    "authority",
    "ombudsman",
    "service",
    "services",
    "complaint",
    "complaints",
    "handling",
    "handler",
    "handbook",
    "customer",
    "dear",
    "sir",
    "madam",
    "sirs",
    "kind",
    "best",
    "yours",
    "sincerely",
    "faithfully",
    "regards",
    "section",
    "rule",
    "guidance",
    "evidential",
    "response",
    "draft",
    "bank",
    "account",
    "reference",
    "number",
}


def _looks_like_name(candidate: str) -> bool:
    """Best-effort guard for the generic Title-Case name detector.

    Rejects the candidate if any token is a common domain/letter-closing word,
    which keeps phrases like 'Financial Ombudsman' or 'Kind Regards' from being
    pseudonymised as names. It will still miss real names and accept some
    non-names -- regex NER is inherently lossy; see module docstring.
    """
    return all(word.strip(".").casefold() not in _NAME_STOPWORDS for word in candidate.split())


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Detection(BaseModel):
    """A redaction summary entry. Carries the entity *type* and a count only --
    never the raw value -- so it is safe to log or write to an audit record."""

    entity_type: str
    count: int


class RedactionResult(BaseModel):
    """Output of a redaction pass."""

    clean_text: str
    detections: list[Detection] = Field(default_factory=list)


class Detector(BaseModel):
    """One PII detector: a compiled pattern, the capture group to replace, and
    an optional validator that vetoes a match (e.g. Luhn for cards)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    entity_type: str
    pattern: re.Pattern
    group: int = 0
    validator: Callable[[str], bool] | None = None


class Vault(BaseModel):
    """Session-scoped, reversible PII map -- the de-anonymisation key for a
    conversation.

    Holds ``token -> value`` (for re-hydration) and a casefolded
    ``value -> token`` map (for referential integrity, so the same person maps
    to the same token across turns). NEVER serialise this into logs, audit
    records, or anything sent to Bedrock; in a production port it must live in
    encrypted, access-controlled, TTL'd storage rather than process memory.
    """

    token_to_value: dict[str, str] = Field(default_factory=dict)
    value_to_token: dict[str, str] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)

    def token_for(self, entity_type: str, value: str) -> str:
        """Return a stable token for ``value``, allocating one on first sight."""
        key = value.strip().casefold()
        existing = self.value_to_token.get(key)
        if existing is not None:
            return existing
        self.counters[entity_type] = self.counters.get(entity_type, 0) + 1
        token = f"[{entity_type}_{self.counters[entity_type]}]"
        self.token_to_value[token] = value.strip()
        self.value_to_token[key] = token
        return token


# ---------------------------------------------------------------------------
# Detector registry -- ordered most-specific first
# ---------------------------------------------------------------------------

# Structured identifiers run before the best-effort name/street heuristics, so
# their matches are already tokens by the time those greedy patterns run.
DETECTORS: list[Detector] = [
    Detector(
        entity_type="EMAIL",
        pattern=re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    ),
    Detector(
        entity_type="NINO",
        pattern=re.compile(r"\b[A-Za-z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-Da-d]\b"),
        validator=_nino_ok,
    ),
    Detector(
        entity_type="CARD",
        # 13-19 digits, optionally grouped with spaces/hyphens; Luhn vetoes
        # non-cards (and incidentally most long account/reference numbers).
        pattern=re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
        validator=_luhn_ok,
    ),
    Detector(
        entity_type="SORT_CODE",
        pattern=re.compile(r"\b\d{2}[- ]\d{2}[- ]\d{2}\b"),
    ),
    Detector(
        entity_type="ACCOUNT",
        # Keyword-anchored: only the 8-digit number (group 1) is masked, not the
        # surrounding 'account number' text. Anchoring keeps the false-positive
        # rate down -- a bare 8-digit run is far too common to mask blindly.
        pattern=re.compile(
            r"\baccount(?:\s+(?:number|no\.?|#))?\s*[:\-]?\s*(\d{8})\b",
            re.IGNORECASE,
        ),
        group=1,
    ),
    Detector(
        entity_type="PHONE",
        # Pragmatic UK-ish matcher; phone formats are messy, treat as best-effort.
        pattern=re.compile(r"\b(?:\+44\s?\d{2,4}|\(?0\d{2,4}\)?)[\s-]?\d{3,4}[\s-]?\d{3,4}\b"),
    ),
    Detector(
        entity_type="POSTCODE",
        pattern=re.compile(
            r"\b([A-Za-z][A-Ha-hJ-Yj-y]?[0-9][A-Za-z0-9]?\s?[0-9][A-Za-z]{2}"
            r"|[Gg][Ii][Rr]\s?0[Aa]{2})\b"
        ),
    ),
    Detector(
        entity_type="ADDRESS",
        # Best-effort street line: a building number followed by up to a few
        # Title-Case words and a street-type suffix. Misses plenty; see docstring.
        pattern=re.compile(
            r"\b\d{1,4}[A-Za-z]?\s+(?:[A-Z][a-z]+\s+){0,3}"
            r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Close|Cl|Drive|Dr|Way|"
            r"Court|Ct|Place|Pl|Terrace|Gardens|Grove|Crescent|Cres|Hill|Row|"
            r"Walk|Square|Sq)\b"
        ),
    ),
    Detector(
        entity_type="NAME",
        # Title-prefixed names are reliably names -- accept without the guard.
        pattern=re.compile(
            r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b"
        ),
    ),
    Detector(
        entity_type="NAME",
        # Generic Title-Case pair -- the weakest detector. The stop-word guard
        # keeps domain phrases out; real misses/false-positives remain expected.
        pattern=re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b"),
        validator=_looks_like_name,
    ),
]

# Matches an allocated token, for re-hydration. Entity types are upper-case and
# may contain underscores (e.g. SORT_CODE), followed by _<n>.
_TOKEN_RE = re.compile(r"\[[A-Z][A-Z_]*_\d+\]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def redact(text: str, vault: Vault) -> RedactionResult:
    """Replace detected PII in ``text`` with stable Vault tokens.

    Detectors run most-specific first, so structured identifiers are masked
    before the best-effort name/street heuristics see the text. Mutates
    ``vault`` with any newly allocated tokens. Returns the pseudonymised text
    plus per-type counts (no raw values), which are safe to log or audit.
    """
    if not text:
        return RedactionResult(clean_text="", detections=[])

    counts: dict[str, int] = {}
    clean = text

    for det in DETECTORS:

        def _replace(match: re.Match, _det: Detector = det) -> str:
            value = match.group(_det.group)
            if not value:
                return match.group(0)
            if _det.validator is not None and not _det.validator(value):
                return match.group(0)
            token = vault.token_for(_det.entity_type, value)
            counts[_det.entity_type] = counts.get(_det.entity_type, 0) + 1
            if _det.group == 0:
                return token
            # Keyword-anchored detector: swap only the captured group, keeping
            # the surrounding anchor text intact.
            full = match.group(0)
            base = match.start(0)
            start, end = match.span(_det.group)
            return full[: start - base] + token + full[end - base :]

        clean = det.pattern.sub(_replace, clean)

    detections = [Detection(entity_type=etype, count=n) for etype, n in sorted(counts.items())]
    return RedactionResult(clean_text=clean, detections=detections)


def rehydrate(text: str, vault: Vault) -> str:
    """Reverse :func:`redact`: replace Vault tokens with their real values.

    Tokens with no entry in the Vault are left untouched (defensive -- should
    not happen within a single session's lifecycle).
    """
    if not text:
        return text

    def _restore(match: re.Match) -> str:
        token = match.group(0)
        return vault.token_to_value.get(token, token)

    return _TOKEN_RE.sub(_restore, text)
