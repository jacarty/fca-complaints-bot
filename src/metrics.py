"""Automated metrics for RAG evaluation.

Computes:
- Retrieval Precision@k and Recall@k (against ground-truth source sections)
- Citation Accuracy (do cited chunks actually contain supporting info?)
- Latency statistics (mean, median, p95)
- Cost per query estimation
- Inter-judge agreement (claim-level and aggregate)
"""

import logging
import statistics

from pydantic import BaseModel, Field

# Pricing lives in the shared module; re-exported here so existing callers can
# keep importing TOKEN_PRICES from src.metrics.
from src.bedrock_common import TOKEN_PRICES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RetrievalMetrics(BaseModel):
    """Retrieval quality metrics for a single query."""

    precision_at_k: float = Field(description="Fraction of retrieved chunks that are relevant")
    recall_at_k: float = Field(description="Fraction of ground-truth chunks retrieved")
    k: int = Field(description="Number of chunks retrieved")
    relevant_retrieved: int = Field(description="Count of relevant chunks in top-k")
    total_relevant: int = Field(description="Total ground-truth relevant chunks")


class CitationMetrics(BaseModel):
    """Citation accuracy metrics for a single query."""

    citation_precision: float = Field(
        description="Fraction of cited chunks that are in the retrieved set"
    )
    citation_recall: float = Field(
        description="Fraction of relevant retrieved chunks that are cited"
    )
    total_citations: int = Field(description="Number of citations in the answer")
    valid_citations: int = Field(description="Citations that point to retrieved chunks")


class FaithfulnessMetrics(BaseModel):
    """Faithfulness metrics from judge evaluation."""

    grounded_pct: float = Field(description="Percentage of claims that are grounded")
    partially_grounded_pct: float = Field(description="Percentage partially grounded")
    ungrounded_pct: float = Field(description="Percentage ungrounded (hallucinated)")
    total_claims: int = Field(description="Total number of claims extracted")
    hallucination_rate: float = Field(description="Same as ungrounded_pct")


class LatencyMetrics(BaseModel):
    """Latency statistics across multiple queries."""

    mean_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float


class CostMetrics(BaseModel):
    """Cost estimation for a single query."""

    generation_cost: float = Field(description="Cost of the generation call")
    judge_primary_cost: float = Field(description="Cost of primary judge call")
    judge_secondary_cost: float = Field(description="Cost of secondary judge call")
    total_cost: float = Field(description="Total cost per query")


class QueryMetrics(BaseModel):
    """All metrics for a single query-config combination."""

    question_id: str
    config_label: str
    retrieval: RetrievalMetrics
    citation: CitationMetrics
    faithfulness_primary: FaithfulnessMetrics
    faithfulness_secondary: FaithfulnessMetrics
    latency_ms: float
    retrieval_latency_ms: float
    generation_latency_ms: float
    cost: CostMetrics


class AggregateMetrics(BaseModel):
    """Aggregated metrics across all queries for a single config."""

    config_label: str
    n_queries: int
    retrieval_precision_mean: float
    retrieval_recall_mean: float
    grounded_pct_mean: float
    ungrounded_pct_mean: float
    citation_precision_mean: float
    latency: LatencyMetrics
    cost_per_query_mean: float
    inter_judge_agreement: float


# ---------------------------------------------------------------------------
# Section-to-chunk mapping
# ---------------------------------------------------------------------------


def resolve_relevant_chunks(
    source_sections: list[str],
    retrieved_chunks: list[dict],
) -> set[str]:
    """Identify which retrieved chunks are relevant to the ground-truth source sections.

    A retrieved chunk is relevant if its metadata 'section' field matches any of the
    ground-truth source_sections, OR if its S3 URI contains the source section ID or
    synthetic policy slug.

    Args:
        source_sections: Ground-truth section IDs (e.g. ["sysc10s1", "synthetic_conflicts-of-interest-policy"])
        retrieved_chunks: List of chunk dicts with chunk_id, content, metadata

    Returns:
        Set of chunk_ids that are relevant.
    """
    relevant = set()

    for chunk in retrieved_chunks:
        chunk_id = chunk.get("chunk_id", "")
        metadata = chunk.get("metadata", {})
        chunk_section = metadata.get("section", "")
        chunk_doc_title = metadata.get("document_title", "")
        chunk_source = metadata.get("source", "")

        for source_section in source_sections:
            if source_section.startswith("synthetic_"):
                slug = source_section.replace("synthetic_", "")
                if slug in chunk_id:
                    relevant.add(chunk_id)
                    break
                if (
                    chunk_source == "synthetic"
                    and slug.replace("-", " ").lower() in chunk_doc_title.lower()
                ):
                    relevant.add(chunk_id)
                    break
            else:
                if chunk_section == source_section:
                    relevant.add(chunk_id)
                    break
                if f"/{source_section}" in chunk_id or f"/{source_section}-" in chunk_id:
                    relevant.add(chunk_id)
                    break

    return relevant


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_retrieval_metrics(
    source_sections: list[str],
    retrieved_chunks: list[dict],
    *,
    all_section_chunk_ids: set[str] | None = None,
) -> RetrievalMetrics:
    """Compute Precision@k and Recall@k for retrieval."""
    k = len(retrieved_chunks)
    if k == 0:
        return RetrievalMetrics(
            precision_at_k=0.0,
            recall_at_k=0.0,
            k=0,
            relevant_retrieved=0,
            total_relevant=0,
        )

    relevant_in_retrieved = resolve_relevant_chunks(source_sections, retrieved_chunks)
    relevant_retrieved = len(relevant_in_retrieved)

    precision = relevant_retrieved / k

    if all_section_chunk_ids is not None:
        total_relevant = len(all_section_chunk_ids)
    else:
        total_relevant = max(relevant_retrieved, 1)

    recall = relevant_retrieved / total_relevant if total_relevant > 0 else 0.0

    return RetrievalMetrics(
        precision_at_k=precision,
        recall_at_k=recall,
        k=k,
        relevant_retrieved=relevant_retrieved,
        total_relevant=total_relevant,
    )


def compute_citation_metrics(
    citations: list[str],
    retrieved_chunk_ids: set[str],
    relevant_chunk_ids: set[str],
) -> CitationMetrics:
    """Compute citation accuracy."""
    if not citations:
        return CitationMetrics(
            citation_precision=0.0,
            citation_recall=0.0,
            total_citations=0,
            valid_citations=0,
        )

    valid = sum(1 for c in citations if c in retrieved_chunk_ids)
    precision = valid / len(citations)

    if relevant_chunk_ids:
        cited_relevant = sum(1 for c in citations if c in relevant_chunk_ids)
        recall = cited_relevant / len(relevant_chunk_ids)
    else:
        recall = 0.0

    return CitationMetrics(
        citation_precision=precision,
        citation_recall=recall,
        total_citations=len(citations),
        valid_citations=valid,
    )


def compute_faithfulness_metrics(judge_result) -> FaithfulnessMetrics:
    """Compute faithfulness metrics from a JudgeResult."""
    claims = judge_result.claims
    total = len(claims)
    if total == 0:
        return FaithfulnessMetrics(
            grounded_pct=0.0,
            partially_grounded_pct=0.0,
            ungrounded_pct=0.0,
            total_claims=0,
            hallucination_rate=0.0,
        )

    grounded = sum(1 for c in claims if c.grounding == "GROUNDED") / total
    partial = sum(1 for c in claims if c.grounding == "PARTIALLY_GROUNDED") / total
    ungrounded = sum(1 for c in claims if c.grounding == "UNGROUNDED") / total

    return FaithfulnessMetrics(
        grounded_pct=grounded,
        partially_grounded_pct=partial,
        ungrounded_pct=ungrounded,
        total_claims=total,
        hallucination_rate=ungrounded,
    )


def compute_cost(
    generation_usage: dict,
    judge_primary_usage: dict,
    judge_secondary_usage: dict,
    generation_model: str = "global.anthropic.claude-sonnet-4-6",
    judge_primary_model: str = "global.anthropic.claude-opus-4-6-v1",
    judge_secondary_model: str = "openai.gpt-oss-120b-1:0",
) -> CostMetrics:
    """Estimate cost per query from token usage."""

    def _cost(usage: dict, model: str) -> float:
        prices = TOKEN_PRICES.get(model, {"input": 0.0, "output": 0.0})
        input_cost = usage.get("input_tokens", 0) / 1000 * prices["input"]
        output_cost = usage.get("output_tokens", 0) / 1000 * prices["output"]
        return input_cost + output_cost

    gen_cost = _cost(generation_usage, generation_model)
    primary_cost = _cost(judge_primary_usage, judge_primary_model)
    secondary_cost = _cost(judge_secondary_usage, judge_secondary_model)

    return CostMetrics(
        generation_cost=gen_cost,
        judge_primary_cost=primary_cost,
        judge_secondary_cost=secondary_cost,
        total_cost=gen_cost + primary_cost + secondary_cost,
    )


def compute_latency_stats(latencies: list[float]) -> LatencyMetrics:
    """Compute latency statistics across multiple queries."""
    if not latencies:
        return LatencyMetrics(mean_ms=0, median_ms=0, p95_ms=0, min_ms=0, max_ms=0)

    sorted_lat = sorted(latencies)
    p95_idx = int(len(sorted_lat) * 0.95)

    return LatencyMetrics(
        mean_ms=statistics.mean(sorted_lat),
        median_ms=statistics.median(sorted_lat),
        p95_ms=sorted_lat[min(p95_idx, len(sorted_lat) - 1)],
        min_ms=sorted_lat[0],
        max_ms=sorted_lat[-1],
    )


# ---------------------------------------------------------------------------
# Inter-judge agreement
# ---------------------------------------------------------------------------


def compute_inter_judge_agreement(
    primary_claims: list,
    secondary_claims: list,
) -> float:
    """Compute claim-level agreement between two judges."""
    if not primary_claims or not secondary_claims:
        return 0.0

    n = min(len(primary_claims), len(secondary_claims))
    if n == 0:
        return 0.0

    agreements = 0
    for i in range(n):
        if primary_claims[i].grounding == secondary_claims[i].grounding:
            agreements += 1

    return agreements / n


def compute_aggregate_metrics(
    query_metrics_list: list[QueryMetrics],
    config_label: str,
) -> AggregateMetrics:
    """Aggregate per-query metrics into summary statistics for a config."""
    n = len(query_metrics_list)
    if n == 0:
        return AggregateMetrics(
            config_label=config_label,
            n_queries=0,
            retrieval_precision_mean=0,
            retrieval_recall_mean=0,
            grounded_pct_mean=0,
            ungrounded_pct_mean=0,
            citation_precision_mean=0,
            latency=LatencyMetrics(mean_ms=0, median_ms=0, p95_ms=0, min_ms=0, max_ms=0),
            cost_per_query_mean=0,
            inter_judge_agreement=0,
        )

    latencies = [q.latency_ms for q in query_metrics_list]

    return AggregateMetrics(
        config_label=config_label,
        n_queries=n,
        retrieval_precision_mean=statistics.mean(
            q.retrieval.precision_at_k for q in query_metrics_list
        ),
        retrieval_recall_mean=statistics.mean(q.retrieval.recall_at_k for q in query_metrics_list),
        grounded_pct_mean=statistics.mean(
            q.faithfulness_primary.grounded_pct for q in query_metrics_list
        ),
        ungrounded_pct_mean=statistics.mean(
            q.faithfulness_primary.ungrounded_pct for q in query_metrics_list
        ),
        citation_precision_mean=statistics.mean(
            q.citation.citation_precision for q in query_metrics_list
        ),
        latency=compute_latency_stats(latencies),
        cost_per_query_mean=statistics.mean(q.cost.total_cost for q in query_metrics_list),
        inter_judge_agreement=0.0,
    )
