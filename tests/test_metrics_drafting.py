"""Tests for src.metrics_drafting.

The retrieved-text recall hinges on extracting provision *definitions* (markdown
headers) while ignoring inline cross-references. These three chunks are the real
disp2s8 retrieval output, so the extraction is tested against the exact text the
bot sees — including the inline "DISP 2.8.7 R" / "DISP 1.6.2A R(2)(a)" mentions
that must NOT be counted as present.
"""

from src.metrics_drafting import (
    compute_citation_metrics,
    compute_drafting_retrieval_metrics,
    compute_retrieval_section_metrics,
    compute_retrieved_provision_recall,
    extract_provisions_from_text,
    section_of,
)

# --- real retrieved chunks (structure-titan, disp2s8) -----------------------

CHUNK_001 = """# DISP — DISP 2.8 Was the complaint referred to the Financial Ombudsman Service in time?

## DISP 2.8.2 (Rules)

The Ombudsman cannot consider a complaint if the complainant refers it to the Financial Ombudsman Service:

(1) more than six months after the date on which the respondent sent the complainant its final response, redress determination or summary resolution communication; or

(2) more than:

(a) six years after the event complained of; or (if later)

(b) three years from the date on which the complainant became aware (or ought reasonably to have become aware) that he had cause for complaint;

unless the complainant referred the complaint to the respondent or to the Ombudsman within that period and has a written acknowledgement or some other record of the complaint having been received;

unless:

(3) in the view of the Ombudsman, the failure to comply with the time limits in DISP 2.8.2 R or DISP 2.8.7 R was as a result of exceptional circumstances; or

(4) the Ombudsman is required to do so by the Ombudsman Transitional Order; or

(5) the respondent has consented to the Ombudsman considering the complaint where the time limits in DISP 2.8.2 R or DISP 2.8.7 R have expired (but this does not apply to a "relevant complaint" within the meaning of section 404B(3) of the Act ).

## DISP 2.8.2A (Rules)

If a respondent consents to the Ombudsman considering a complaint in accordance with DISP 2.8.2 R (5), the respondent may not withdraw consent.

## DISP 2.8.3 (Guidance)

The six-month time limit is only triggered by a response which is a final response, redress determination or summary resolution communication . The response must tell the complainant about the six-month time limit that the complainant has to refer a complaint to the Financial Ombudsman Service.

## DISP 2.8.4 (Guidance)

An example of exceptional circumstances might be where the complainant has been or is incapacitated.
"""

CHUNK_000 = """# DISP — DISP 2.8 Was the complaint referred to the Financial Ombudsman Service in time?

## DISP 2.8.1 (Rules)

The Ombudsman can only consider a complaint if:

(1) the respondent has already sent the complainant its final response or summary resolution communication; or

(2) in relation to a complaint that is not an EMD complaint or a PSD complaint, eight weeks have elapsed since the respondent received the complaint; or

(2A) in relation to a complaint that is an EMD complaint or a PSD complaint:

(a) 15 business days have elapsed since the respondent received the complaint and the complainant has not received a holding response as described in DISP 1.6.2A R(2)(a); or

(b) where the complainant has received a holding response, 35 business days have elapsed since the respondent received the complaint; or

unless:

(4) the respondent consents and:

(a) the Ombudsman has informed the complainant that the respondent must deal with the complaint within eight weeks.

## DISP 2.8.1A (Rules)

Where a respondent has chosen to treat a complaint in its entirety in accordance with DISP 1.6.2AR, notwithstanding that parts of it fall outside DISP 1.6.2AR, DISP 2.8 will apply as if the whole complaint were an EMD complaint or a PSD complaint.
"""

CHUNK_003 = """# DISP — DISP 2.8 Was the complaint referred to the Financial Ombudsman Service in time?

## DISP 2.8.7 (Rules)

(1) If a complaint relates to the sale of an endowment policy ... subject to (2), (3), (4) and (5):

(5) Paragraph (1) does not apply if the Ombudsman is of the opinion that, in the circumstances of the case, it is appropriate for DISP 2.8.2R (2) to apply.
"""


def chunk(content, idx, section="disp2s8", **meta):
    return {
        "chunk_id": f"s3://fca-complaints-bot/fca-handbook/sections-structure/{section}-{idx}.md",
        "content": content,
        "score": 0.7,
        "metadata": {"section": section, "module": "disp", **meta},
    }


CHUNKS = [chunk(CHUNK_001, "001"), chunk(CHUNK_000, "000"), chunk(CHUNK_003, "003")]


# ---------------------------------------------------------------------------
# Header extraction vs inline references
# ---------------------------------------------------------------------------


def test_extracts_definition_headers():
    found = extract_provisions_from_text(CHUNK_001)
    assert found == {"DISP 2.8.2", "DISP 2.8.2A", "DISP 2.8.3", "DISP 2.8.4"}


def test_ignores_inline_cross_references():
    # CHUNK_001 mentions "DISP 2.8.7 R" inline; CHUNK_000 mentions "DISP 1.6.2A R(2)(a)"
    # and "DISP 1.6.2AR" inline. None of these are definition headers here.
    assert "DISP 2.8.7" not in extract_provisions_from_text(CHUNK_001)
    found_000 = extract_provisions_from_text(CHUNK_000)
    assert found_000 == {"DISP 2.8.1", "DISP 2.8.1A"}
    assert "DISP 1.6.2A" not in found_000


def test_chapter_title_not_extracted():
    # "# DISP — DISP 2.8 Was the complaint..." is a title, not a provision header.
    assert "DISP 2.8" not in extract_provisions_from_text(CHUNK_001)


def test_amendment_letter_header_kept_distinct():
    found = extract_provisions_from_text(CHUNK_000)
    assert "DISP 2.8.1A" in found
    assert "DISP 2.8.1" in found
    assert found != {"DISP 2.8.1"}  # the A variant is its own key


# ---------------------------------------------------------------------------
# section_of — metadata and URI fallback
# ---------------------------------------------------------------------------


def test_section_from_metadata():
    assert section_of(CHUNKS[0]) == "disp2s8"


def test_section_from_uri_fallback():
    c = {"chunk_id": "s3://b/fca-handbook/sections-structure/disp1s6-002.md", "content": "", "metadata": {}}
    assert section_of(c) == "disp1s6"


def test_section_none_when_unrecoverable():
    assert section_of({"chunk_id": "", "content": "", "metadata": {}}) is None


# ---------------------------------------------------------------------------
# Section retrieval metrics
# ---------------------------------------------------------------------------


def test_section_metrics_all_relevant():
    m = compute_retrieval_section_metrics(CHUNKS, ["disp2s8"])
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.n_retrieved == 3 and m.n_relevant == 3
    assert m.retrieved_sections == ["disp2s8"]


def test_section_metrics_none_relevant():
    m = compute_retrieval_section_metrics(CHUNKS, ["disp1s6"])
    assert m.precision == 0.0 and m.recall == 0.0
    assert m.missing_sections == ["disp1s6"]


def test_section_metrics_partial_recall():
    # two expected sections, only one retrieved
    m = compute_retrieval_section_metrics(CHUNKS, ["disp2s8", "disp1s6"])
    assert m.precision == 1.0          # all retrieved chunks are in an expected section
    assert m.recall == 0.5             # only disp2s8 of the two expected was retrieved
    assert m.matched_sections == ["disp2s8"]
    assert m.missing_sections == ["disp1s6"]


# ---------------------------------------------------------------------------
# Retrieved-text provision recall
# ---------------------------------------------------------------------------


def test_retrieved_provision_recall_full():
    m = compute_retrieved_provision_recall(CHUNKS, ["DISP 2.8.2R", "DISP 2.8.3G"])
    assert m.recall == 1.0
    assert set(m.matched) == {"DISP 2.8.2", "DISP 2.8.3"}
    assert m.missing == []


def test_inline_only_provision_is_a_miss():
    # DISP 1.6.2A appears only inline (never as a header) -> not retrieved
    m = compute_retrieved_provision_recall(CHUNKS, ["DISP 1.6.2AR"])
    assert m.recall == 0.0
    assert m.missing == ["DISP 1.6.2A"]


def test_present_in_text_is_union_of_headers():
    m = compute_retrieved_provision_recall(CHUNKS, ["DISP 2.8.2R"])
    assert set(m.present_in_text) == {
        "DISP 2.8.1", "DISP 2.8.1A", "DISP 2.8.2", "DISP 2.8.2A",
        "DISP 2.8.3", "DISP 2.8.4", "DISP 2.8.7",
    }


# ---------------------------------------------------------------------------
# Citation metrics
# ---------------------------------------------------------------------------


def test_citation_metrics():
    cited = [{"provision": "DISP 2.8.2R"}, {"provision": "DISP 1.6.2R"}]
    m = compute_citation_metrics(cited, ["DISP 2.8.2R", "DISP 2.8.3G"])
    assert m.precision == 0.5          # 1 of 2 cited was expected
    assert m.recall == 0.5             # 1 of 2 expected was cited
    assert m.matched == ["DISP 2.8.2"]
    assert m.cited_not_expected == ["DISP 1.6.2"]
    assert m.expected_not_cited == ["DISP 2.8.3"]


def test_citation_metrics_normalises_inconsistent_spellings():
    # ground truth bare form vs bot suffixed form must match
    cited = [{"provision": "DISP 1.2.1R"}]
    m = compute_citation_metrics(cited, ["DISP 1.2.1"])
    assert m.precision == 1.0 and m.recall == 1.0


def test_citation_metrics_nothing_cited():
    m = compute_citation_metrics([], ["DISP 2.8.2R"])
    assert m.precision == 0.0 and m.recall == 0.0
    assert m.n_cited == 0


# ---------------------------------------------------------------------------
# Bundle + the drafting-attributable gap
# ---------------------------------------------------------------------------


def test_gap_signals_drafting_miss():
    # 2.8.3 retrieved (text present) but only 2.8.2 cited -> rule was there, not cited
    cited = [{"provision": "DISP 2.8.2R"}]
    m = compute_drafting_retrieval_metrics(
        cited_provisions=cited,
        retrieved_chunks=CHUNKS,
        expected_provisions=["DISP 2.8.2R", "DISP 2.8.3G"],
        expected_sections=["disp2s8"],
    )
    assert m.retrieved_provision_recall.recall == 1.0
    assert m.citation.recall == 0.5
    assert m.drafting_attributable_gap == 0.5   # positive -> drafting miss, not KB miss


def test_object_accessor_path():
    # accessors must also work on attribute-style objects, not just dicts
    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    cited = [Obj(provision="DISP 2.8.2R")]
    chunks = [Obj(chunk_id="s3://b/sections-structure/disp2s8-001.md", content=CHUNK_001,
                  metadata={"section": "disp2s8"})]
    m = compute_drafting_retrieval_metrics(
        cited_provisions=cited,
        retrieved_chunks=chunks,
        expected_provisions=["DISP 2.8.2R"],
        expected_sections=["disp2s8"],
    )
    assert m.citation.recall == 1.0
    assert m.section.precision == 1.0


# ---------------------------------------------------------------------------
# Cost, agreement, aggregate
# ---------------------------------------------------------------------------

from src.judge_drafting import RubricElementVerdict, RubricJudgeResult
from src.metrics_drafting import (
    DraftingScenarioMetrics,
    compute_drafting_aggregate,
    compute_drafting_cost,
    compute_rubric_agreement,
)


def test_compute_drafting_cost_breakdown():
    gen = {"input_tokens": 1000, "output_tokens": 1000}
    judges = [
        ("rubric_primary", "global.anthropic.claude-opus-4-6-v1", {"input_tokens": 2000, "output_tokens": 500}),
        ("grounding", "global.anthropic.claude-opus-4-6-v1", {"input_tokens": 2000, "output_tokens": 500}),
    ]
    c = compute_drafting_cost(gen, judges)
    # generation: 1*0.003 + 1*0.015 = 0.018
    assert abs(c.generation_cost - 0.018) < 1e-9
    # each judge: 2*0.015 + 0.5*0.075 = 0.0675; two of them = 0.135
    assert abs(c.judge_cost - 0.135) < 1e-9
    assert abs(c.total_cost - 0.153) < 1e-9
    assert set(c.breakdown) == {"generation", "rubric_primary", "grounding"}


def _rubric_result(status_map, required):
    verdicts = [RubricElementVerdict(element=k, status=v) for k, v in status_map.items()]
    return RubricJudgeResult(
        verdicts=verdicts, required_elements=required, judge_model="m", latency_ms=1.0
    )


def test_rubric_agreement_full_and_partial():
    required = ["a", "b", "c"]
    p = _rubric_result({"a": "PRESENT", "b": "PARTIAL", "c": "MISSING"}, required)
    s_full = _rubric_result({"a": "PRESENT", "b": "PARTIAL", "c": "MISSING"}, required)
    assert compute_rubric_agreement(p, s_full) == 1.0

    s_part = _rubric_result({"a": "PRESENT", "b": "MISSING", "c": "MISSING"}, required)
    assert abs(compute_rubric_agreement(p, s_part) - 2 / 3) < 1e-9


def test_rubric_agreement_counts_omitted_as_missing():
    required = ["a", "b"]
    p = _rubric_result({"a": "PRESENT", "b": "MISSING"}, required)
    # secondary omitted b entirely -> status_for defaults MISSING -> agrees on b
    s = _rubric_result({"a": "PRESENT"}, required)
    assert compute_rubric_agreement(p, s) == 1.0


def test_rubric_agreement_empty_required():
    p = _rubric_result({}, [])
    assert compute_rubric_agreement(p, p) == 0.0


def test_drafting_aggregate_means_and_median_latency():
    sms = [
        DraftingScenarioMetrics(
            scenario_id="c001", config_label="structure-titan", coverage=1.0, present_count=3,
            n_required=3, section_precision=1.0, section_recall=1.0, citation_precision=0.8,
            citation_recall=0.6, retrieved_provision_recall=1.0, drafting_gap=0.4,
            grounded_pct=0.9, grounded_pct_customer=0.8, ungrounded_pct=0.1,
            inter_judge_agreement=1.0, latency_ms=1000, total_cost=0.10,
        ),
        DraftingScenarioMetrics(
            scenario_id="c002", config_label="structure-titan", coverage=0.5, present_count=1,
            n_required=2, section_precision=0.5, section_recall=0.5, citation_precision=0.4,
            citation_recall=0.4, retrieved_provision_recall=0.5, drafting_gap=0.1,
            grounded_pct=0.7, grounded_pct_customer=0.6, ungrounded_pct=0.3,
            inter_judge_agreement=0.8, latency_ms=3000, total_cost=0.20,
        ),
    ]
    agg = compute_drafting_aggregate(sms, "structure-titan")
    assert agg.n_scenarios == 2
    assert agg.coverage_mean == 0.75
    assert abs(agg.citation_precision_mean - 0.6) < 1e-9
    assert agg.latency_p50_ms == 2000  # median of 1000, 3000
    assert abs(agg.cost_per_scenario_mean - 0.15) < 1e-9


def test_drafting_aggregate_empty():
    agg = compute_drafting_aggregate([], "structure-titan")
    assert agg.n_scenarios == 0 and agg.coverage_mean == 0.0


# ---------------------------------------------------------------------------
# Format-agnostic extraction: real FIXED chunks (newlines flattened, headers
# sit inline mid-paragraph) must extract the same definitions as structure.
# ---------------------------------------------------------------------------

# Real fixed-titan chunk 0 (disp2s8): starts mid-2.8.2 (its header is in the
# previous chunk), inline cross-refs to "DISP 2.8.2 R" / "DISP 2.8.7 R", then
# four definition headers run inline.
FIXED_CHUNK_0 = (
    ": (1) more than six months after the date ... unless: (3) in the view of the "
    "Ombudsman, the failure to comply with the time limits in DISP 2.8.2 R or DISP "
    "2.8.7 R was as a result of exceptional circumstances; or (4) the Ombudsman is "
    "required to do so by the Ombudsman Transitional Order; or (5) the respondent has "
    "consented ... within the meaning of section 404B(3) of the Act ). ## DISP 2.8.2A "
    "(Rules) If a respondent consents to the Ombudsmanconsidering a complaint in "
    "accordance with DISP 2.8.2 R (5), the respondent may not withdraw consent. ## "
    "DISP 2.8.3 (Guidance) The six-month time limit is only triggered by a response "
    "which is a final response ... ## DISP 2.8.4 (Guidance) An example of exceptional "
    "circumstances might be where the complainant has been or is incapacitated. ## "
    "DISP 2.8.5 (Rules) The six-year and the three-year time limits do not apply where: "
    "(1) [deleted] (2) the complaint concerns a contract or policy ..."
)

# Real fixed-titan chunk 2 (disp1s18): an annex of sample wording. Header is
# "DISP 1 Annex 3" (not a numbered provision); body quotes "DISP 2.8.2 R (1)" and
# "DISP 2.8.2R(2)" inline. Nothing here is a definition to credit.
FIXED_ANNEX = (
    "# DISP — DISP 1 Annex 3 Appropriate wording for inclusion in a final response "
    "## DISP 1 Annex 3 (Rules) The respondent does not consent to waive the six-month "
    "time limit in DISP 2.8.2 R (1) (1) \u201cYou have the right to refer your complaint "
    "... within six months of the date of this letter ...\u201d The complaint was "
    "received outside the time limits in DISP 2.8.2R(2) and the respondent does not "
    "consent to waive those time limits or the six-month time limit in DISP 2.8.2 R (1)"
)


def test_fixed_inline_headers_extracted():
    found = extract_provisions_from_text(FIXED_CHUNK_0)
    assert found == {"DISP 2.8.2A", "DISP 2.8.3", "DISP 2.8.4", "DISP 2.8.5"}


def test_fixed_inline_cross_refs_still_excluded():
    found = extract_provisions_from_text(FIXED_CHUNK_0)
    # 2.8.2 header is in the *previous* chunk; the "DISP 2.8.2 R" / "2.8.7 R" here
    # are inline cross-references and must not be counted.
    assert "DISP 2.8.2" not in found
    assert "DISP 2.8.7" not in found


def test_fixed_annex_extracts_nothing():
    # "DISP 1 Annex 3" is not a numbered provision; the inline "DISP 2.8.2 R (1)"
    # and "DISP 2.8.2R(2)" are cross-references in sample wording.
    found = extract_provisions_from_text(FIXED_ANNEX)
    assert found == set()


def test_fixed_recall_no_longer_zero():
    # A fixed chunk now yields real retrieved-text recall instead of 0.0.
    fixed_chunk = {
        "chunk_id": "s3://b/fca-handbook/sections/disp2s8.md",
        "content": FIXED_CHUNK_0,
        "metadata": {"section": "disp2s8"},
    }
    m = compute_retrieved_provision_recall([fixed_chunk], ["DISP 2.8.3G", "DISP 2.8.5R"])
    assert m.recall == 1.0
    assert set(m.matched) == {"DISP 2.8.3", "DISP 2.8.5"}