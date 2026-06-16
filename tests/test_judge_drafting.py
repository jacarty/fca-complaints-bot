"""Tests for src.judge_drafting — drafting rubric + grounding judges.

No Bedrock calls: a FakeClient returns canned converse responses so the parsing
and scoring contracts are pinned without network or model cost.

The behaviours most worth locking:

- coverage is computed over the elements the scenario *required*, so a judge that
  omits an element scores it MISSING rather than shrinking the denominator;
- per-field grounding separates handler reasoning from customer-facing drift;
- malformed judge output degrades to an empty result instead of raising.
"""

import json

import pytest

from src.judge_drafting import (
    GroundedClaim,
    GroundingJudgeResult,
    RubricElementVerdict,
    RubricJudgeResult,
    load_rubric,
    run_grounding_judge,
    run_rubric_judge,
    select_required_elements,
    _coerce_chunk_id,
    _extract_converse_text,
    _load_json_array,
    _norm_grounding,
    _norm_source_field,
    _norm_status,
    _parse_grounded_claims,
    _parse_rubric_verdicts,
)


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class FakeClient:
    """Returns a canned Bedrock converse response, or raises if asked to."""

    def __init__(self, *, payload=None, raw=None, content=None, raise_exc=False):
        self._payload = payload
        self._raw = raw
        self._content = content
        self._raise = raise_exc

    def converse(self, **kwargs):
        if self._raise:
            raise RuntimeError("boom")
        if self._content is not None:
            content = self._content
        elif self._raw is not None:
            content = [{"text": self._raw}]
        else:
            content = [{"text": json.dumps(self._payload)}]
        return {
            "output": {"message": {"content": content}},
            "usage": {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150},
        }


def make_elements(keys, audience="customer"):
    return [
        {
            "key": k,
            "audience": audience,
            "definition": f"def {k}",
            "present_if": "present band",
            "partial_if": "partial band",
            "missing_if": "missing band",
        }
        for k in keys
    ]


def rubric_call(client, required_keys):
    return run_rubric_judge(
        client,
        scenario_text="complaint",
        response_stage="final",
        handler_answer="handler note",
        customer_draft="customer text",
        required_elements=make_elements(required_keys),
    )


# ---------------------------------------------------------------------------
# JSON parsing robustness
# ---------------------------------------------------------------------------


def test_load_json_array_plain():
    assert _load_json_array('[{"a": 1}]') == [{"a": 1}]


def test_load_json_array_fenced():
    assert _load_json_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]


def test_load_json_array_with_preamble_and_trailing():
    raw = 'Here you go:\n[{"a": 1}, {"a": 2}]\nHope that helps.'
    assert _load_json_array(raw) == [{"a": 1}, {"a": 2}]


def test_load_json_array_garbage_returns_empty():
    assert _load_json_array("not json at all") == []


def test_parse_rubric_skips_non_dicts():
    verdicts = _parse_rubric_verdicts('[{"element": "x", "status": "PRESENT"}, "junk", 42]')
    assert len(verdicts) == 1
    assert verdicts[0].element == "x"


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("present", "PRESENT"), ("Partial", "PARTIAL"), ("MISSING", "MISSING"), ("weird", "MISSING")],
)
def test_norm_status(raw, expected):
    assert _norm_status(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("grounded", "GROUNDED"),
        ("partially grounded", "PARTIALLY_GROUNDED"),
        ("UNGROUNDED", "UNGROUNDED"),
        ("nonsense", "UNGROUNDED"),
    ],
)
def test_norm_grounding(raw, expected):
    assert _norm_grounding(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("handler_answer", "handler_answer"), ("customer_draft", "customer_draft"), ("both", "unknown")],
)
def test_norm_source_field(raw, expected):
    assert _norm_source_field(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [(["s3://a", "s3://b"], "s3://a"), ([], None), (None, None), ("s3://c", "s3://c")],
)
def test_coerce_chunk_id(raw, expected):
    assert _coerce_chunk_id(raw) == expected


# ---------------------------------------------------------------------------
# Rubric coverage scoring — the denominator rule
# ---------------------------------------------------------------------------


def test_coverage_weighting():
    payload = [
        {"element": "a", "status": "PRESENT"},
        {"element": "b", "status": "PARTIAL"},
        {"element": "c", "status": "MISSING"},
    ]
    res = rubric_call(FakeClient(payload=payload), ["a", "b", "c"])
    assert res.present_count == 1 and res.partial_count == 1 and res.missing_count == 1
    assert abs(res.coverage - (1.0 + 0.5 + 0.0) / 3) < 1e-9


def test_omitted_required_element_counts_missing():
    # judge returns only two of three required elements
    payload = [
        {"element": "a", "status": "PRESENT"},
        {"element": "b", "status": "PRESENT"},
    ]
    res = rubric_call(FakeClient(payload=payload), ["a", "b", "c"])
    assert res.status_for("c") == "MISSING"
    assert res.missing_count == 1
    assert abs(res.coverage - (2 / 3)) < 1e-9


def test_extra_returned_element_does_not_inflate_coverage():
    # judge returns an element that was not required — must be ignored
    payload = [
        {"element": "a", "status": "PRESENT"},
        {"element": "z", "status": "PRESENT"},  # not required
    ]
    res = rubric_call(FakeClient(payload=payload), ["a", "b"])
    assert res.coverage == 0.5  # only a counts; b missing; z ignored


def test_empty_required_elements_no_crash():
    res = rubric_call(FakeClient(payload=[]), [])
    assert res.coverage == 0.0


def test_status_for_unknown_defaults_missing():
    res = RubricJudgeResult(verdicts=[], required_elements=["a"], judge_model="m", latency_ms=1.0)
    assert res.status_for("a") == "MISSING"


# ---------------------------------------------------------------------------
# Grounding scoring — per-field drift
# ---------------------------------------------------------------------------


def grounding_result(claims):
    return GroundingJudgeResult(claims=claims, judge_model="m", latency_ms=1.0)


def GroundedClaim_(claim="c", source_field="customer_draft", grounding="GROUNDED", supporting_chunk_id=None):
    return GroundedClaim(
        claim=claim, source_field=source_field, grounding=grounding, supporting_chunk_id=supporting_chunk_id
    )


def test_grounding_overall_rates():
    g = grounding_result(
        [
            GroundedClaim_(grounding="GROUNDED"),
            GroundedClaim_(grounding="GROUNDED"),
            GroundedClaim_(grounding="UNGROUNDED"),
        ]
    )
    assert abs(g.grounded_pct - 2 / 3) < 1e-9
    assert abs(g.ungrounded_pct - 1 / 3) < 1e-9
    assert g.hallucination_rate == g.ungrounded_pct


def test_grounding_per_field_drift():
    g = grounding_result(
        [
            GroundedClaim_(source_field="handler_answer", grounding="GROUNDED"),
            GroundedClaim_(source_field="customer_draft", grounding="GROUNDED"),
            GroundedClaim_(source_field="customer_draft", grounding="UNGROUNDED"),
        ]
    )
    assert g.grounded_pct_for("handler_answer") == 1.0
    assert g.grounded_pct_for("customer_draft") == 0.5
    assert len(g.claims_for("customer_draft")) == 2


def test_grounding_empty_claims_no_div_by_zero():
    g = grounding_result([])
    assert g.grounded_pct == 0.0 and g.ungrounded_pct == 0.0
    assert g.grounded_pct_for("customer_draft") == 0.0


# ---------------------------------------------------------------------------
# run_* end to end with the fake client
# ---------------------------------------------------------------------------


def test_run_rubric_judge_populates_metadata():
    res = rubric_call(FakeClient(payload=[{"element": "a", "status": "PRESENT"}]), ["a"])
    assert res.judge_model.endswith("gpt-oss-120b-1:0")  # preset resolved
    assert res.usage["total_tokens"] == 150
    assert res.latency_ms >= 0
    assert res.required_elements == ["a"]


def test_run_grounding_judge_parses_claims():
    payload = [
        {"claim": "x", "source_field": "handler_answer", "grounding": "GROUNDED",
         "supporting_chunk_id": "s3://k.md"},
    ]
    g = run_grounding_judge(
        FakeClient(payload=payload),
        handler_answer="h",
        customer_draft="c",
        chunks=[{"chunk_id": "s3://k.md", "content": "DISP 1.6", "metadata": {"section": "disp1s6"}}],
    )
    assert len(g.claims) == 1
    assert g.claims[0].supporting_chunk_id == "s3://k.md"


def test_judge_call_failure_degrades_gracefully():
    res = rubric_call(FakeClient(raise_exc=True), ["a", "b"])
    assert res.verdicts == []
    assert res.coverage == 0.0
    assert res.missing_count == 2  # both required, both unmet


def test_grounding_call_failure_degrades_gracefully():
    g = run_grounding_judge(
        FakeClient(raise_exc=True), handler_answer="h", customer_draft="c", chunks=[]
    )
    assert g.claims == []
    assert g.grounded_pct == 0.0


def test_reasoning_model_content_extraction():
    # gpt-oss style: text block empty, answer carried in reasoningContent
    payload = [{"element": "a", "status": "PRESENT"}]
    content = [{"reasoningContent": {"reasoningText": {"text": json.dumps(payload)}}}]
    res = rubric_call(FakeClient(content=content), ["a"])
    assert res.present_count == 1


def test_extract_converse_text_prefers_text_block():
    resp = {"output": {"message": {"content": [{"text": "hello"}]}}}
    assert _extract_converse_text(resp) == "hello"


# ---------------------------------------------------------------------------
# Rubric file integrity
# ---------------------------------------------------------------------------

EXPECTED_ELEMENT_KEYS = {
    "acknowledgement",
    "decision_and_reasons",
    "fos_referral_rights",
    "six_month_referral_limit",
    "eight_week_timeline",
    "fair_treatment",
    "redress_consideration",
    "vulnerability_consideration",
}


def test_rubric_file_has_all_elements():
    rubric = load_rubric()
    assert set(rubric["elements"]) == EXPECTED_ELEMENT_KEYS


def test_rubric_elements_have_required_fields():
    rubric = load_rubric()
    for key, block in rubric["elements"].items():
        for field in ("definition", "present_if", "partial_if", "missing_if", "audience"):
            assert block.get(field), f"{key} missing {field}"


def test_timing_elements_fence_off_the_others():
    rubric = load_rubric()
    six_month = rubric["elements"]["six_month_referral_limit"]["not_satisfied_by"].lower()
    eight_week = rubric["elements"]["eight_week_timeline"]["not_satisfied_by"].lower()
    # six-month must warn against the 8-week clock and the eligibility time-bar
    assert "eight" in six_month or "8" in six_month
    assert "year" in six_month  # the 6-year/3-year time-bar
    # eight-week must warn against the six-month referral window
    assert "six month" in eight_week or "six-month" in eight_week


def test_vulnerability_is_the_only_either_element():
    rubric = load_rubric()
    either = {k for k, b in rubric["elements"].items() if b.get("audience") == "either"}
    assert either == {"vulnerability_consideration"}


def test_select_required_elements_skips_unknown_keys():
    rubric = load_rubric()
    selected = select_required_elements(rubric, ["acknowledgement", "not_a_real_element"])
    assert [e["key"] for e in selected] == ["acknowledgement"]
