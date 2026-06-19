"""Retrieval and citation metrics for the FCA complaints bot evaluation (JAM-275).

Three metrics, each answering a different question, all sitting on the provision
normaliser in ``src.provisions``:

1. Section-level retrieval precision/recall — of the chunks retrieved, how many
   belong to a section the scenario needs, and how many of the needed sections
   were retrieved? This isolates the Knowledge Base (same model and prompt across
   configs), and is the direct successor to the Q&A framework's retrieval
   precision — the structure-titan vs fixed-titan comparison.

2. Provision-level citation precision/recall — of the provisions the bot *cited*,
   how many were expected, and of the expected provisions, how many did it cite?
   This is a joint retrieval-plus-drafting signal: the model selected this set.

3. Retrieved-text provision recall — of the expected provisions, how many had
   their actual rule text retrieved (present as a section header in a retrieved
   chunk, not merely cross-referenced)? The gap between this and citation recall
   localises a miss: rule retrieved but not cited is a drafting miss; rule never
   retrieved is a Knowledge Base miss.

Sibling to ``metrics.py`` (the Q&A faithfulness/retrieval metrics); the two are
independent so either evaluation can be cloned and run on its own.
"""

from __future__ import annotations

import re
import statistics

from pydantic import BaseModel, Field

from src.bedrock_common import TOKEN_PRICES  # single source of truth for Bedrock pricing
from src.provisions import normalise_provision, normalise_provisions

GENERATION_MODEL = "eu.anthropic.claude-sonnet-4-6"

# A provision *definition* reads "MODULE NUMBER (TypeWord)" — e.g. "DISP 2.8.2A
# (Rules)" or "DISP 2.8.3 (Guidance)". Structure chunks place these on their own
# markdown header line; fixed chunks flatten newlines so the same text sits inline
# mid-paragraph. The parenthesised TYPE LABEL only ever appears on definitions, so
# matching it anywhere (no line anchor, no dependence on the markdown "#") works
# for both chunk formats. Inline cross-references ("DISP 2.8.7 R", "DISP 1.6.2A
# R(2)(a)", "DISP 2.8.2R(2)") carry the bare status suffix and a numeric/alpha
# sub-paragraph, never a type word, so they are excluded — only rule text actually
# *present* as a definition is counted.
_PROVISION_DEF_RE = re.compile(
    r"([A-Z]{2,})[ \t]+([0-9][0-9A-Za-z.]*)[ \t]*"
    r"\((?:Rules?|Guidance|Evidential[^)]*|Directions?)\)"
)

# Fallback for the section id when metadata lacks one: the filename stem with the
# trailing chunk index removed, e.g. ".../disp2s8-001.md" -> "disp2s8".
_URI_SECTION_RE = re.compile(r"/([^/]+?)-\d+\.md$")


# ---------------------------------------------------------------------------
# Duck-typed accessors — work with pydantic RetrievedChunk/CitedProvision or
# their serialised dict forms, so the runner can pass either.
# ---------------------------------------------------------------------------


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _chunk_content(chunk) -> str:
    return _get(chunk, "content", "") or ""


def _chunk_metadata(chunk) -> dict:
    return _get(chunk, "metadata", {}) or {}


def _chunk_id(chunk) -> str:
    return _get(chunk, "chunk_id", "") or ""


def _provision_ref(provision) -> str:
    return _get(provision, "provision", "") or ""


def section_of(chunk) -> str | None:
    """The section id for a retrieved chunk.

    Prefers the ``section`` metadata attribute; falls back to parsing it from the
    chunk's S3 URI. Returns None if neither yields one.
    """
    section = _chunk_metadata(chunk).get("section")
    if section:
        return str(section)
    m = _URI_SECTION_RE.search(_chunk_id(chunk))
    return m.group(1) if m else None


def extract_provisions_from_text(content: str) -> set[str]:
    """Normalised provision keys whose rule text is present in ``content``.

    Provision definitions (``MODULE NUMBER (TypeWord)``) are extracted regardless
    of chunk formatting; inline cross-references are not. See ``_PROVISION_DEF_RE``.
    """
    out: set[str] = set()
    for module, number in _PROVISION_DEF_RE.findall(content):
        key = normalise_provision(f"{module} {number}")
        if key:
            out.add(key)
    return out


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _safe_div(num: int, denom: int) -> float:
    return num / denom if denom else 0.0


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class ProvisionCitationMetrics(BaseModel):
    """Provision-level precision/recall over the bot's cited provisions."""

    precision: float = Field(description="matched / cited (0.0 if nothing cited)")
    recall: float = Field(description="matched / expected")
    f1: float
    n_cited: int
    n_expected: int
    n_matched: int
    matched: list[str] = Field(default_factory=list)
    cited_not_expected: list[str] = Field(
        default_factory=list, description="Cited but not expected — precision errors"
    )
    expected_not_cited: list[str] = Field(
        default_factory=list, description="Expected but not cited — recall misses"
    )


class RetrievalSectionMetrics(BaseModel):
    """Section-level retrieval precision/recall — the KB comparison metric."""

    precision: float = Field(description="relevant retrieved chunks / retrieved chunks")
    recall: float = Field(description="expected sections retrieved / expected sections")
    n_retrieved: int
    n_relevant: int = Field(description="Retrieved chunks whose section is expected")
    expected_sections: list[str] = Field(default_factory=list)
    retrieved_sections: list[str] = Field(
        default_factory=list, description="Distinct, in rank order"
    )
    matched_sections: list[str] = Field(default_factory=list)
    missing_sections: list[str] = Field(default_factory=list)


class RetrievedProvisionRecall(BaseModel):
    """Provision-level recall over rule text actually present in retrieved chunks."""

    recall: float = Field(description="expected provisions present in retrieved text / expected")
    n_expected: int
    n_present: int = Field(description="Expected provisions whose text was retrieved")
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list, description="Expected but never retrieved")
    present_in_text: list[str] = Field(
        default_factory=list, description="All provisions whose text appears in retrieved chunks"
    )


class DraftingRetrievalMetrics(BaseModel):
    """All three retrieval/citation metrics for one scenario."""

    citation: ProvisionCitationMetrics
    section: RetrievalSectionMetrics
    retrieved_provision_recall: RetrievedProvisionRecall

    @property
    def drafting_attributable_gap(self) -> float:
        """retrieved-text recall minus citation recall.

        Positive: the rule text was retrieved but not cited — a drafting miss.
        Near zero with low recall: the rule was never retrieved — a KB miss.
        """
        return self.retrieved_provision_recall.recall - self.citation.recall


# ---------------------------------------------------------------------------
# Compute functions
# ---------------------------------------------------------------------------


def compute_citation_metrics(cited_provisions, expected_provisions) -> ProvisionCitationMetrics:
    """Precision/recall of the bot's cited provisions against expected provisions."""
    cited = normalise_provisions(_provision_ref(p) for p in cited_provisions)
    expected = normalise_provisions(expected_provisions)
    matched = cited & expected

    precision = _safe_div(len(matched), len(cited))
    recall = _safe_div(len(matched), len(expected))
    return ProvisionCitationMetrics(
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        n_cited=len(cited),
        n_expected=len(expected),
        n_matched=len(matched),
        matched=sorted(matched),
        cited_not_expected=sorted(cited - expected),
        expected_not_cited=sorted(expected - cited),
    )


def compute_retrieval_section_metrics(
    retrieved_chunks, expected_sections
) -> RetrievalSectionMetrics:
    """Section-level retrieval precision/recall.

    Precision is over retrieved chunks (a chunk is relevant if its section is
    expected); recall is over the distinct expected sections retrieved.
    """
    expected = {str(s) for s in expected_sections}

    chunk_sections = [section_of(c) for c in retrieved_chunks]
    n_relevant = sum(1 for s in chunk_sections if s in expected)

    # distinct retrieved sections in rank order (chunks arrive score-descending)
    seen: list[str] = []
    for s in chunk_sections:
        if s and s not in seen:
            seen.append(s)
    matched = expected & set(seen)

    return RetrievalSectionMetrics(
        precision=_safe_div(n_relevant, len(retrieved_chunks)),
        recall=_safe_div(len(matched), len(expected)),
        n_retrieved=len(retrieved_chunks),
        n_relevant=n_relevant,
        expected_sections=sorted(expected),
        retrieved_sections=seen,
        matched_sections=sorted(matched),
        missing_sections=sorted(expected - set(seen)),
    )


def compute_retrieved_provision_recall(
    retrieved_chunks, expected_provisions
) -> RetrievedProvisionRecall:
    """Recall of expected provisions whose rule text was retrieved."""
    expected = normalise_provisions(expected_provisions)

    present: set[str] = set()
    for chunk in retrieved_chunks:
        present |= extract_provisions_from_text(_chunk_content(chunk))

    matched = expected & present
    return RetrievedProvisionRecall(
        recall=_safe_div(len(matched), len(expected)),
        n_expected=len(expected),
        n_present=len(matched),
        matched=sorted(matched),
        missing=sorted(expected - present),
        present_in_text=sorted(present),
    )


def compute_drafting_retrieval_metrics(
    *,
    cited_provisions,
    retrieved_chunks,
    expected_provisions,
    expected_sections,
) -> DraftingRetrievalMetrics:
    """Compute all three retrieval/citation metrics for one scenario."""
    return DraftingRetrievalMetrics(
        citation=compute_citation_metrics(cited_provisions, expected_provisions),
        section=compute_retrieval_section_metrics(retrieved_chunks, expected_sections),
        retrieved_provision_recall=compute_retrieved_provision_recall(
            retrieved_chunks, expected_provisions
        ),
    )


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


class DraftingCostMetrics(BaseModel):
    """Cost for one scenario: generation plus all judge calls."""

    generation_cost: float
    judge_cost: float
    total_cost: float
    breakdown: dict = Field(default_factory=dict, description="Per-call cost by label")


def _cost(usage: dict, model: str) -> float:
    """Cost of one call from token usage (prices are per 1k tokens)."""
    prices = TOKEN_PRICES.get(model, {"input": 0.0, "output": 0.0})
    return (
        usage.get("input_tokens", 0) / 1000 * prices["input"]
        + usage.get("output_tokens", 0) / 1000 * prices["output"]
    )


def compute_drafting_cost(
    generation_usage: dict,
    judge_calls: list[tuple[str, str, dict]],
    *,
    generation_model: str = GENERATION_MODEL,
) -> DraftingCostMetrics:
    """Estimate scenario cost.

    Args:
        generation_usage: Usage dict from the drafting call.
        judge_calls: List of (label, model_id, usage) for each judge call made —
            e.g. ("rubric_primary", opus_id, usage), ("grounding", opus_id, usage).
    """
    gen_cost = _cost(generation_usage, generation_model)
    breakdown = {"generation": gen_cost}
    judge_cost = 0.0
    for label, model_id, usage in judge_calls:
        c = _cost(usage, model_id)
        breakdown[label] = c
        judge_cost += c
    return DraftingCostMetrics(
        generation_cost=gen_cost,
        judge_cost=judge_cost,
        total_cost=gen_cost + judge_cost,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Inter-judge agreement (rubric axis, element-level)
# ---------------------------------------------------------------------------


def compute_rubric_agreement(primary, secondary) -> float:
    """Element-level agreement between two rubric judges over the same required set.

    Both judges were asked for the same required elements, so agreement is the
    fraction of those elements on which they returned the same status. Duck-typed
    on .required_elements and .status_for so it does not import the judge module.
    """
    elements = getattr(primary, "required_elements", None) or []
    if not elements:
        return 0.0
    agree = sum(1 for e in elements if primary.status_for(e) == secondary.status_for(e))
    return agree / len(elements)


# ---------------------------------------------------------------------------
# Per-scenario summary + aggregate
# ---------------------------------------------------------------------------


class DraftingScenarioMetrics(BaseModel):
    """Flat per-scenario scalars, stored in the record and re-hydrated to aggregate."""

    scenario_id: str
    config_label: str
    coverage: float
    present_count: int
    n_required: int
    section_precision: float
    section_recall: float
    citation_precision: float
    citation_recall: float
    retrieved_provision_recall: float
    drafting_gap: float
    grounded_pct: float = 0.0
    grounded_pct_customer: float = 0.0
    grounded_pct_handler: float = 0.0
    ungrounded_pct: float = 0.0
    inter_judge_agreement: float = 0.0
    latency_ms: float = 0.0
    total_cost: float = 0.0


class DraftingAggregateMetrics(BaseModel):
    """Means across scenarios for one config (median for latency)."""

    config_label: str
    n_scenarios: int
    coverage_mean: float = 0.0
    section_precision_mean: float = 0.0
    section_recall_mean: float = 0.0
    citation_precision_mean: float = 0.0
    citation_recall_mean: float = 0.0
    retrieved_provision_recall_mean: float = 0.0
    drafting_gap_mean: float = 0.0
    grounded_pct_mean: float = 0.0
    grounded_pct_customer_mean: float = 0.0
    ungrounded_pct_mean: float = 0.0
    inter_judge_agreement_mean: float = 0.0
    latency_p50_ms: float = 0.0
    cost_per_scenario_mean: float = 0.0


def compute_drafting_aggregate(
    scenario_metrics: list[DraftingScenarioMetrics],
    config_label: str,
) -> DraftingAggregateMetrics:
    """Aggregate per-scenario metrics for one config."""
    n = len(scenario_metrics)
    if n == 0:
        return DraftingAggregateMetrics(config_label=config_label, n_scenarios=0)

    def mean(attr: str) -> float:
        return statistics.mean(getattr(s, attr) for s in scenario_metrics)

    return DraftingAggregateMetrics(
        config_label=config_label,
        n_scenarios=n,
        coverage_mean=mean("coverage"),
        section_precision_mean=mean("section_precision"),
        section_recall_mean=mean("section_recall"),
        citation_precision_mean=mean("citation_precision"),
        citation_recall_mean=mean("citation_recall"),
        retrieved_provision_recall_mean=mean("retrieved_provision_recall"),
        drafting_gap_mean=mean("drafting_gap"),
        grounded_pct_mean=mean("grounded_pct"),
        grounded_pct_customer_mean=mean("grounded_pct_customer"),
        ungrounded_pct_mean=mean("ungrounded_pct"),
        inter_judge_agreement_mean=mean("inter_judge_agreement"),
        latency_p50_ms=statistics.median(s.latency_ms for s in scenario_metrics),
        cost_per_scenario_mean=mean("total_cost"),
    )
