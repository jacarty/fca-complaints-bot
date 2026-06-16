"""Drafting-aware judges for the FCA complaints bot evaluation (JAM-275).

Two axes, kept separate because they measure different things:

1. Rubric coverage (primary, dual-model). For each element a scenario requires
   (its ``required_response_elements``), score the drafted response PRESENT /
   PARTIAL / MISSING against the canonical definitions in
   ``data/eval/rubric_elements.json``. Customer-facing elements are scored
   against the customer draft; the one "either" element (vulnerability) may be
   met in either part. Run with two models for inter-judge agreement.

2. Grounding (secondary, single-model). Extract only the regulatory/factual
   claims from the combined draft, tag each with the part it came from
   (handler_answer / customer_draft), and classify each as GROUNDED /
   PARTIALLY_GROUNDED / UNGROUNDED against the retrieved provisions. Tagging by
   field surfaces the drift case where the handler reasoning is sound but the
   customer-facing text misstates a right or a time limit.

This module is a sibling to ``judge.py`` (the Q&A faithfulness judge); the two
are independent so either evaluation can be cloned and run on its own. The only
shared import is the model-preset table, so model IDs have a single source of
truth.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.bedrock_common import JUDGE_PRESETS, resolve_judge_model

logger = logging.getLogger(__name__)

RUBRIC_PATH = Path("data/eval/rubric_elements.json")

# Defaults: dual-model on the rubric axis, single strong model on grounding.
RUBRIC_JUDGE_PRIMARY = JUDGE_PRESETS["gpt-oss"]
RUBRIC_JUDGE_SECONDARY = JUDGE_PRESETS["haiku"]
GROUNDING_JUDGE_MODEL = JUDGE_PRESETS["gpt-oss"]

COVERAGE_WEIGHTS = {"PRESENT": 1.0, "PARTIAL": 0.5, "MISSING": 0.0}
_RUBRIC_STATUSES = frozenset(COVERAGE_WEIGHTS)
_GROUNDING_LABELS = frozenset({"GROUNDED", "PARTIALLY_GROUNDED", "UNGROUNDED"})
_SOURCE_FIELDS = frozenset({"handler_answer", "customer_draft"})


# ---------------------------------------------------------------------------
# Rubric loading
# ---------------------------------------------------------------------------


def load_rubric(path: Path = RUBRIC_PATH) -> dict:
    """Load the rubric element definitions."""
    if not path.exists():
        raise FileNotFoundError(f"Rubric not found at {path}")
    with open(path) as f:
        return json.load(f)


def select_required_elements(rubric: dict, required_keys: list[str]) -> list[dict]:
    """Return the definition blocks for a scenario's required elements, in order.

    Each returned dict carries the element key plus its definition fields. Keys
    not present in the rubric are skipped with a warning rather than failing the
    whole scenario.
    """
    elements = rubric.get("elements", {})
    selected = []
    for key in required_keys:
        block = elements.get(key)
        if block is None:
            logger.warning("Required element %r has no rubric definition; skipping", key)
            continue
        selected.append({"key": key, **block})
    return selected


# ---------------------------------------------------------------------------
# Result models — rubric axis
# ---------------------------------------------------------------------------


class RubricElementVerdict(BaseModel):
    """One element's verdict from the rubric judge."""

    element: str = Field(description="The rubric element key")
    status: str = Field(description="PRESENT, PARTIAL, or MISSING")
    evidence: str = Field(default="", description="Short span quoted from the draft; empty if MISSING")
    reasoning: str = Field(default="", description="Why this status was chosen")


class RubricJudgeResult(BaseModel):
    """Result of one rubric-coverage judge run for a single scenario."""

    verdicts: list[RubricElementVerdict] = Field(description="Per-element verdicts returned")
    required_elements: list[str] = Field(
        default_factory=list,
        description="Elements asked for — the coverage denominator. Any required element the "
        "judge omitted is treated as MISSING.",
    )
    judge_model: str = Field(description="Model ID of the judge")
    latency_ms: float = Field(description="Judge call latency")
    usage: dict = Field(default_factory=dict, description="Token usage")

    def _status_map(self) -> dict[str, str]:
        return {v.element: v.status for v in self.verdicts}

    def status_for(self, element: str) -> str:
        """Status for a required element, defaulting to MISSING if not returned."""
        return self._status_map().get(element, "MISSING")

    @property
    def present_count(self) -> int:
        return sum(1 for e in self.required_elements if self.status_for(e) == "PRESENT")

    @property
    def partial_count(self) -> int:
        return sum(1 for e in self.required_elements if self.status_for(e) == "PARTIAL")

    @property
    def missing_count(self) -> int:
        return sum(1 for e in self.required_elements if self.status_for(e) == "MISSING")

    @property
    def coverage(self) -> float:
        """Weighted coverage over required elements (PRESENT 1.0, PARTIAL 0.5)."""
        if not self.required_elements:
            return 0.0
        total = sum(COVERAGE_WEIGHTS[self.status_for(e)] for e in self.required_elements)
        return total / len(self.required_elements)


# ---------------------------------------------------------------------------
# Result models — grounding axis
# ---------------------------------------------------------------------------


class GroundedClaim(BaseModel):
    """A regulatory/factual claim extracted from the draft and graded."""

    claim: str = Field(description="The claim text")
    source_field: str = Field(description="handler_answer or customer_draft")
    grounding: str = Field(description="GROUNDED, PARTIALLY_GROUNDED, or UNGROUNDED")
    supporting_chunk_id: str | None = Field(default=None, description="Supporting chunk_id if grounded")
    reasoning: str = Field(default="", description="Why this grounding was chosen")


class GroundingJudgeResult(BaseModel):
    """Result of one grounding judge run for a single scenario."""

    claims: list[GroundedClaim] = Field(description="Regulatory/factual claims with grounding")
    judge_model: str = Field(description="Model ID of the judge")
    latency_ms: float = Field(description="Judge call latency")
    usage: dict = Field(default_factory=dict, description="Token usage")

    def _rate(self, label: str, claims: list[GroundedClaim]) -> float:
        if not claims:
            return 0.0
        return sum(1 for c in claims if c.grounding == label) / len(claims)

    @property
    def grounded_pct(self) -> float:
        return self._rate("GROUNDED", self.claims)

    @property
    def partially_grounded_pct(self) -> float:
        return self._rate("PARTIALLY_GROUNDED", self.claims)

    @property
    def ungrounded_pct(self) -> float:
        return self._rate("UNGROUNDED", self.claims)

    @property
    def hallucination_rate(self) -> float:
        return self.ungrounded_pct

    def claims_for(self, field: str) -> list[GroundedClaim]:
        return [c for c in self.claims if c.source_field == field]

    def grounded_pct_for(self, field: str) -> float:
        """Grounded rate restricted to one source field — the drift diagnostic."""
        return self._rate("GROUNDED", self.claims_for(field))

    def ungrounded_pct_for(self, field: str) -> float:
        return self._rate("UNGROUNDED", self.claims_for(field))


# ---------------------------------------------------------------------------
# Prompts — rubric axis
# ---------------------------------------------------------------------------

RUBRIC_SYSTEM_PROMPT = """You are a compliance reviewer assessing whether a drafted response to a financial services complaint contains the elements the FCA's DISP rules require.

You are given the customer's complaint, the response stage (final or holding), the drafted response in two parts, and a rubric of required elements. The drafted response is split into:
- HANDLER ANSWER: an internal note to the complaints handler (regulatory position and recommended action);
- CUSTOMER DRAFT: the text intended to be sent to the customer.

For each rubric element provided, decide PRESENT, PARTIAL, or MISSING using only the bands given for that element.

Audience rule:
- An element marked audience "customer" is satisfied only by the CUSTOMER DRAFT. The handler answer may explain the reasoning, but a duty owed to the complainant (such as informing them of their rights) is met only if it appears in the text the customer receives. Where an element's bands say handler reasoning may be read as context (redress), the position communicated to the customer is still what is scored.
- An element marked audience "either" may be satisfied in the handler answer or the customer draft.

Keep these three timing concepts distinct and never treat one as satisfying another:
- the firm's eight-week clock to issue a final response (eight_week_timeline);
- the complainant's six months from the final response to refer the complaint to the Financial Ombudsman Service (six_month_referral_limit);
- the FOS eligibility time-bar of six years from the event or three years from awareness. This last one is NOT a rubric element; a draft discussing the six-year/three-year limits must never be credited as six_month_referral_limit.

Score only the elements provided. Be strict: if the PRESENT band is not clearly met, choose PARTIAL or MISSING. Give a short evidence span quoted from the draft (empty if MISSING) and brief reasoning.

Respond ONLY with a JSON array of objects, each with keys: element, status, evidence, reasoning. No preamble, no markdown fences."""


def _format_rubric_elements(elements: list[dict]) -> str:
    blocks = []
    for el in elements:
        lines = [
            f"### {el['key']} (audience: {el.get('audience', 'customer')})",
            f"Definition: {el.get('definition', '')}",
            f"PRESENT if: {el.get('present_if', '')}",
            f"PARTIAL if: {el.get('partial_if', '')}",
            f"MISSING if: {el.get('missing_if', '')}",
        ]
        if el.get("not_satisfied_by"):
            lines.append(f"Not satisfied by: {el['not_satisfied_by']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _build_rubric_user_message(
    scenario_text: str,
    response_stage: str,
    handler_answer: str,
    customer_draft: str,
    elements: list[dict],
) -> str:
    return (
        f"COMPLAINT (scenario):\n{scenario_text}\n\n"
        f"RESPONSE STAGE: {response_stage}\n\n"
        f"DRAFTED RESPONSE\n"
        f"--- HANDLER ANSWER ---\n{handler_answer or '(empty)'}\n\n"
        f"--- CUSTOMER DRAFT ---\n{customer_draft or '(empty)'}\n\n"
        f"REQUIRED ELEMENTS TO SCORE:\n\n{_format_rubric_elements(elements)}\n\n"
        f"---\n\n"
        f"Score each element above. Respond with a JSON array of objects with keys: "
        f"element, status, evidence, reasoning."
    )


# ---------------------------------------------------------------------------
# Prompts — grounding axis
# ---------------------------------------------------------------------------

GROUNDING_SYSTEM_PROMPT = """You are assessing whether the regulatory content of a drafted complaint response is faithfully grounded in a set of retrieved FCA Handbook provisions.

Extract only REGULATORY or FACTUAL claims from the draft: the firm's position on the complaint, the customer's rights, time limits, the firm's obligations, and any statement of what a rule requires or permits. Ignore empathic, relational, and routine procedural filler (apologies, thanks, "we value your custom", contact pleasantries) — these are not claims to be grounded and must not be returned.

The draft has two parts. Tag each claim with the part it came from:
- "handler_answer": the internal regulatory note;
- "customer_draft": the customer-facing text.

For each extracted claim, classify its grounding against the retrieved provisions:
- GROUNDED: directly and fully supported by a retrieved provision; cite its chunk_id.
- PARTIALLY_GROUNDED: a related provision exists but the claim goes beyond what it states.
- UNGROUNDED: no retrieved provision supports the claim.

A plain-language customer claim can be GROUNDED against a provision even if it does not name it, as long as the provision's text supports it. Be strict: a misstated right or time limit is not GROUNDED merely because the topic appears in a provision.

Respond ONLY with a JSON array of objects, each with keys: claim, source_field, grounding, supporting_chunk_id, reasoning. No preamble, no markdown fences."""


def _format_provisions(chunks: list[dict]) -> str:
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        section = chunk.get("metadata", {}).get("section", "unknown")
        blocks.append(
            f"--- Provision {i} (chunk_id: {chunk['chunk_id']}) ---\n"
            f"Section: {section}\n\n"
            f"{chunk['content']}"
        )
    return "\n\n".join(blocks)


def _build_grounding_user_message(
    handler_answer: str,
    customer_draft: str,
    chunks: list[dict],
) -> str:
    return (
        f"DRAFTED RESPONSE\n"
        f"--- HANDLER ANSWER (source_field: handler_answer) ---\n{handler_answer or '(empty)'}\n\n"
        f"--- CUSTOMER DRAFT (source_field: customer_draft) ---\n{customer_draft or '(empty)'}\n\n"
        f"RETRIEVED PROVISIONS:\n\n{_format_provisions(chunks)}\n\n"
        f"---\n\n"
        f"Extract the regulatory/factual claims, tag each with its source_field, and classify "
        f"grounding. Respond with a JSON array of objects with keys: claim, source_field, "
        f"grounding, supporting_chunk_id, reasoning."
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_converse_text(response: dict) -> str:
    """Extract text from a Bedrock converse response, handling standard text
    blocks and reasoning-model (reasoningContent) blocks (e.g. gpt-oss).
    """
    content = response["output"]["message"]["content"]
    parts = [b.get("text", "") for b in content if "text" in b]
    if any(parts):
        return "\n".join(p for p in parts if p)

    # reasoning models may carry the answer inside reasoningContent
    for block in content:
        rc = block.get("reasoningContent") if isinstance(block, dict) else None
        if rc and "reasoningText" in rc and "text" in rc["reasoningText"]:
            parts.append(rc["reasoningText"]["text"])
    if any(parts):
        return "\n".join(p for p in parts if p)

    # last resort: any string value that looks like a JSON array
    for block in content:
        if isinstance(block, dict):
            for val in block.values():
                if isinstance(val, str) and val.strip().startswith("["):
                    return val
    return ""


def _load_json_array(raw_text: str) -> list:
    """Parse a JSON array from a judge response, tolerating fences and preamble."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]") + 1
        if 0 <= start < end:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    logger.error("Failed to parse judge response as JSON array: %s", raw_text[:200])
    return []


def _norm_status(value: str) -> str:
    s = str(value).upper().replace(" ", "_").strip()
    return s if s in _RUBRIC_STATUSES else "MISSING"


def _norm_grounding(value: str) -> str:
    s = str(value).upper().replace(" ", "_").strip()
    return s if s in _GROUNDING_LABELS else "UNGROUNDED"


def _norm_source_field(value) -> str:
    s = str(value).strip()
    return s if s in _SOURCE_FIELDS else "unknown"


def _coerce_chunk_id(value) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _parse_rubric_verdicts(raw_text: str) -> list[RubricElementVerdict]:
    out = []
    for item in _load_json_array(raw_text):
        if not isinstance(item, dict):
            continue
        out.append(
            RubricElementVerdict(
                element=str(item.get("element", "")),
                status=_norm_status(item.get("status", "MISSING")),
                evidence=str(item.get("evidence", "")),
                reasoning=str(item.get("reasoning", "")),
            )
        )
    return out


def _parse_grounded_claims(raw_text: str) -> list[GroundedClaim]:
    out = []
    for item in _load_json_array(raw_text):
        if not isinstance(item, dict):
            continue
        out.append(
            GroundedClaim(
                claim=str(item.get("claim", "")),
                source_field=_norm_source_field(item.get("source_field")),
                grounding=_norm_grounding(item.get("grounding", "UNGROUNDED")),
                supporting_chunk_id=_coerce_chunk_id(item.get("supporting_chunk_id")),
                reasoning=str(item.get("reasoning", "")),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Judge invocation
# ---------------------------------------------------------------------------


def _converse_usage(response: dict) -> dict:
    if "usage" not in response:
        return {}
    u = response["usage"]
    return {
        "input_tokens": u.get("inputTokens", 0),
        "output_tokens": u.get("outputTokens", 0),
        "total_tokens": u.get("totalTokens", 0),
    }


def run_rubric_judge(
    client,
    *,
    scenario_text: str,
    response_stage: str,
    handler_answer: str,
    customer_draft: str,
    required_elements: list[dict],
    model_id: str = RUBRIC_JUDGE_PRIMARY,
) -> RubricJudgeResult:
    """Score a drafted response against a scenario's required rubric elements.

    Args:
        client: bedrock-runtime boto3 client.
        scenario_text: The complaint text.
        response_stage: 'final' or 'holding'.
        handler_answer / customer_draft: The two parts of the drafted response.
        required_elements: Output of select_required_elements() — the element
            definition blocks to score (each has a 'key').
        model_id: Judge model ID or preset name.
    """
    model_id = resolve_judge_model(model_id)
    required_keys = [el["key"] for el in required_elements]
    user_message = _build_rubric_user_message(
        scenario_text, response_stage, handler_answer, customer_draft, required_elements
    )

    start = time.perf_counter()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": RUBRIC_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
        )
    except Exception as e:
        logger.error("Rubric judge call failed for %s: %s", model_id, e)
        return RubricJudgeResult(
            verdicts=[],
            required_elements=required_keys,
            judge_model=model_id,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    latency_ms = (time.perf_counter() - start) * 1000
    verdicts = _parse_rubric_verdicts(_extract_converse_text(response))
    result = RubricJudgeResult(
        verdicts=verdicts,
        required_elements=required_keys,
        judge_model=model_id,
        latency_ms=latency_ms,
        usage=_converse_usage(response),
    )
    logger.info(
        "Rubric judge %s: coverage=%.0f%% (%d/%d present) in %.0fms",
        model_id.split(".")[-1],
        result.coverage * 100,
        result.present_count,
        len(required_keys),
        latency_ms,
    )
    return result


def run_dual_rubric_judges(
    client,
    *,
    scenario_text: str,
    response_stage: str,
    handler_answer: str,
    customer_draft: str,
    required_elements: list[dict],
    primary_model: str = RUBRIC_JUDGE_PRIMARY,
    secondary_model: str = RUBRIC_JUDGE_SECONDARY,
) -> tuple[RubricJudgeResult, RubricJudgeResult]:
    """Run two models on the rubric axis for inter-judge agreement."""
    common = dict(
        scenario_text=scenario_text,
        response_stage=response_stage,
        handler_answer=handler_answer,
        customer_draft=customer_draft,
        required_elements=required_elements,
    )
    primary = run_rubric_judge(client, model_id=primary_model, **common)
    secondary = run_rubric_judge(client, model_id=secondary_model, **common)
    return primary, secondary


def run_grounding_judge(
    client,
    *,
    handler_answer: str,
    customer_draft: str,
    chunks: list[dict],
    model_id: str = GROUNDING_JUDGE_MODEL,
) -> GroundingJudgeResult:
    """Grade the regulatory claims in the combined draft against retrieved provisions.

    Args:
        client: bedrock-runtime boto3 client.
        handler_answer / customer_draft: The two parts of the drafted response.
        chunks: Retrieved chunk dicts (chunk_id, content, metadata).
        model_id: Judge model ID or preset name.
    """
    model_id = resolve_judge_model(model_id)
    user_message = _build_grounding_user_message(handler_answer, customer_draft, chunks)

    start = time.perf_counter()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": GROUNDING_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 8192, "temperature": 0.0},
        )
    except Exception as e:
        logger.error("Grounding judge call failed for %s: %s", model_id, e)
        return GroundingJudgeResult(
            claims=[], judge_model=model_id, latency_ms=(time.perf_counter() - start) * 1000
        )

    latency_ms = (time.perf_counter() - start) * 1000
    claims = _parse_grounded_claims(_extract_converse_text(response))
    result = GroundingJudgeResult(
        claims=claims, judge_model=model_id, latency_ms=latency_ms, usage=_converse_usage(response)
    )
    logger.info(
        "Grounding judge %s: %d claims (%.0f%% grounded, %.0f%% ungrounded) in %.0fms",
        model_id.split(".")[-1],
        len(claims),
        result.grounded_pct * 100,
        result.ungrounded_pct * 100,
        latency_ms,
    )
    return result
