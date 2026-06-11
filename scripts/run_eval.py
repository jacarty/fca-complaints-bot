"""RAG evaluation runner.

Runs ground-truth Q&A pairs through pipeline configurations, scores results
using LLM-as-judge and automated metrics, and outputs comparison tables.

Saves a checkpoint after every question so progress is never lost.

Usage:
    # Run all 4 configs
    uv run python scripts/run_eval.py --all --primary-judge haiku

    # Resume after crash — picks up from exact question where it stopped
    uv run python scripts/run_eval.py --all --primary-judge haiku --resume

    # Other options
    uv run python scripts/run_eval.py --chunking structure --embedding titan
    uv run python scripts/run_eval.py --all --limit 5
    uv run python scripts/run_eval.py --all --skip-judges
    uv run python scripts/run_eval.py --all --primary-judge-only

    # Available judge presets: opus, haiku, sonnet, gpt-oss
"""

import argparse
import json
import logging
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.judge import (  # noqa: E402
    JUDGE_PRESETS,
    JudgeResult,
    resolve_judge_model,
    run_dual_judges,
    run_judge,
)
from src.metrics import (  # noqa: E402
    AggregateMetrics,
    QueryMetrics,
    compute_aggregate_metrics,
    compute_citation_metrics,
    compute_cost,
    compute_faithfulness_metrics,
    compute_inter_judge_agreement,
    compute_retrieval_metrics,
    resolve_relevant_chunks,
)
from src.pipeline import (  # noqa: E402
    PipelineConfig,
    _get_bedrock_runtime_client,
    query_pipeline,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("botocore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

QA_PATH = Path("data/qa/qa_pairs.json")
OUTPUT_DIR = Path("data/eval")
CHECKPOINT_FILE = "eval_checkpoint.json"

ALL_CONFIGS = [
    ("fixed", "titan"),
    ("fixed", "cohere"),
    ("structure", "titan"),
    ("structure", "cohere"),
]


# ---------------------------------------------------------------------------
# Q&A loading
# ---------------------------------------------------------------------------


def load_qa_pairs(path: Path = QA_PATH, *, limit: int | None = None) -> list[dict]:
    with open(path) as f:
        pairs = json.load(f)
    if limit:
        pairs = pairs[:limit]
    logger.info("Loaded %d Q&A pairs", len(pairs))
    return pairs


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def save_checkpoint(all_results: dict, output_dir: Path) -> None:
    """Save current results to checkpoint file. Called after every question."""
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / CHECKPOINT_FILE
    with open(checkpoint_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)


def load_checkpoint(output_dir: Path) -> dict:
    """Load checkpoint if it exists."""
    checkpoint_path = output_dir / CHECKPOINT_FILE
    if checkpoint_path.exists():
        with open(checkpoint_path) as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Single query evaluation
# ---------------------------------------------------------------------------


def evaluate_single(
    qa_pair: dict,
    config: PipelineConfig,
    judge_client,
    *,
    skip_judges: bool = False,
    primary_only: bool = False,
    primary_model: str = "opus",
    secondary_model: str = "gpt-oss",
    profile: str | None = None,
) -> dict:
    question = qa_pair["question"]
    question_id = qa_pair["question_id"]
    source_sections = qa_pair["source_sections"]
    config_label = f"{config.chunking}-{config.embedding}"

    logger.info("Evaluating %s on %s", question_id, config_label)

    try:
        result = query_pipeline(question, config=config, profile=profile)
    except Exception as e:
        logger.error("Pipeline failed for %s on %s: %s", question_id, config_label, e)
        return {"question_id": question_id, "config": config_label, "error": str(e)}

    chunk_dicts = [c.model_dump() for c in result.retrieved_chunks]
    retrieved_ids = {c.chunk_id for c in result.retrieved_chunks}
    retrieval_metrics = compute_retrieval_metrics(source_sections, chunk_dicts)

    relevant_ids = resolve_relevant_chunks(source_sections, chunk_dicts)
    citation_metrics = compute_citation_metrics(
        result.citations,
        retrieved_ids,
        relevant_ids,
    )

    primary_result = JudgeResult(claims=[], judge_model="skipped", latency_ms=0)
    secondary_result = JudgeResult(claims=[], judge_model="skipped", latency_ms=0)

    if not skip_judges:
        if primary_only:
            primary_result = run_judge(
                judge_client,
                question,
                result.answer,
                chunk_dicts,
                model_id=primary_model,
            )
        else:
            primary_result, secondary_result = run_dual_judges(
                judge_client,
                question,
                result.answer,
                chunk_dicts,
                primary_model=primary_model,
                secondary_model=secondary_model,
            )

    faith_primary = compute_faithfulness_metrics(primary_result)
    faith_secondary = compute_faithfulness_metrics(secondary_result)

    cost = compute_cost(
        result.usage,
        primary_result.usage,
        secondary_result.usage,
        judge_primary_model=resolve_judge_model(primary_model),
        judge_secondary_model=resolve_judge_model(secondary_model),
    )

    agreement = 0.0
    if primary_result.claims and secondary_result.claims:
        agreement = compute_inter_judge_agreement(
            primary_result.claims,
            secondary_result.claims,
        )

    query_metrics = QueryMetrics(
        question_id=question_id,
        config_label=config_label,
        retrieval=retrieval_metrics,
        citation=citation_metrics,
        faithfulness_primary=faith_primary,
        faithfulness_secondary=faith_secondary,
        latency_ms=result.latency_ms,
        retrieval_latency_ms=result.retrieval_latency_ms,
        generation_latency_ms=result.generation_latency_ms,
        cost=cost,
    )

    return {
        "question_id": question_id,
        "config": config_label,
        "question": question,
        "ground_truth_answer": qa_pair["answer"],
        "source_sections": source_sections,
        "difficulty": qa_pair.get("difficulty", "unknown"),
        "type": qa_pair.get("type", "unknown"),
        "generated_answer": result.answer,
        "citations": result.citations,
        "confidence": result.confidence,
        "insufficient_context": result.insufficient_context,
        "retrieved_chunks": [
            {
                "chunk_id": c.chunk_id,
                "score": c.score,
                "section": c.metadata.get("section", "unknown"),
            }
            for c in result.retrieved_chunks
        ],
        "metrics": query_metrics.model_dump(),
        "judge_primary": primary_result.model_dump() if primary_result.claims else None,
        "judge_secondary": secondary_result.model_dump() if secondary_result.claims else None,
        "inter_judge_agreement": agreement,
        "latency_ms": result.latency_ms,
        "usage": result.usage,
    }


# ---------------------------------------------------------------------------
# Config-level evaluation with per-question checkpointing
# ---------------------------------------------------------------------------


def evaluate_config(
    qa_pairs: list[dict],
    chunking: str,
    embedding: str,
    all_results: dict,
    output_dir: Path,
    *,
    skip_judges: bool = False,
    primary_only: bool = False,
    primary_model: str = "opus",
    secondary_model: str = "gpt-oss",
    profile: str | None = None,
) -> AggregateMetrics:
    """Evaluate all Q&A pairs against a single config with per-question checkpointing."""
    config = PipelineConfig(chunking=chunking, embedding=embedding)
    config_label = f"{chunking}-{embedding}"
    judge_client = _get_bedrock_runtime_client(profile)

    # Get or create the results list for this config
    if config_label not in all_results:
        all_results[config_label] = []

    existing_results = all_results[config_label]
    completed_ids = {r["question_id"] for r in existing_results if "question_id" in r}

    for i, qa_pair in enumerate(qa_pairs, 1):
        qid = qa_pair["question_id"]

        # Skip already-completed questions (resume support)
        if qid in completed_ids:
            logger.info(
                "[%d/%d] %s on %s — already done, skipping", i, len(qa_pairs), qid, config_label
            )
            continue

        logger.info("[%d/%d] %s on %s", i, len(qa_pairs), qid, config_label)

        result = evaluate_single(
            qa_pair,
            config,
            judge_client,
            skip_judges=skip_judges,
            primary_only=primary_only,
            primary_model=primary_model,
            secondary_model=secondary_model,
            profile=profile,
        )
        existing_results.append(result)

        # Checkpoint after every question
        save_checkpoint(all_results, output_dir)

    # Aggregate
    query_metrics_list = []
    for r in existing_results:
        if "error" not in r:
            query_metrics_list.append(QueryMetrics(**r["metrics"]))

    aggregate = compute_aggregate_metrics(query_metrics_list, config_label)

    if not skip_judges and not primary_only:
        agreements = [
            r["inter_judge_agreement"]
            for r in existing_results
            if "error" not in r and r.get("inter_judge_agreement", 0) > 0
        ]
        if agreements:
            aggregate.inter_judge_agreement = statistics.mean(agreements)

    return aggregate


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def save_final(
    all_results: dict,
    all_aggregates: dict[str, AggregateMetrics],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    detail_path = output_dir / f"eval_detail_{timestamp}.json"
    with open(detail_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info("Saved detail results to %s", detail_path)

    agg_data = {k: v.model_dump() for k, v in all_aggregates.items()}
    agg_path = output_dir / f"eval_aggregate_{timestamp}.json"
    with open(agg_path, "w") as f:
        json.dump(agg_data, f, indent=2, default=str)

    md_path = output_dir / f"eval_comparison_{timestamp}.md"
    md_content = format_comparison_table(all_aggregates)
    with open(md_path, "w") as f:
        f.write(md_content)

    # Update latest
    for name, data in [
        ("eval_detail_latest.json", all_results),
        ("eval_aggregate_latest.json", agg_data),
    ]:
        with open(output_dir / name, "w") as f:
            json.dump(data, f, indent=2, default=str)
    with open(output_dir / "eval_comparison_latest.md", "w") as f:
        f.write(md_content)


def format_comparison_table(aggregates: dict[str, AggregateMetrics]) -> str:
    lines = [
        "# RAG Evaluation Results",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Configuration Comparison",
        "",
        "| Config | Precision@k | Recall@k | % Grounded | % Ungrounded | Citation Acc | Latency (p50) | Cost/Query | Judge Agreement |",
        "|--------|-------------|----------|------------|--------------|--------------|---------------|------------|-----------------|",
    ]

    for label, agg in aggregates.items():
        lines.append(
            f"| {label} "
            f"| {agg.retrieval_precision_mean:.1%} "
            f"| {agg.retrieval_recall_mean:.1%} "
            f"| {agg.grounded_pct_mean:.1%} "
            f"| {agg.ungrounded_pct_mean:.1%} "
            f"| {agg.citation_precision_mean:.1%} "
            f"| {agg.latency.median_ms:.0f}ms "
            f"| ${agg.cost_per_query_mean:.4f} "
            f"| {agg.inter_judge_agreement:.1%} |"
        )

    lines.extend(
        [
            "",
            f"*Based on {next(iter(aggregates.values())).n_queries} Q&A pairs per config*",
            "",
            "## Metric Definitions",
            "",
            "- **Precision@k**: Fraction of retrieved chunks that match ground-truth source sections",
            "- **Recall@k**: Fraction of ground-truth source sections found in retrieved chunks",
            "- **% Grounded**: Claims fully supported by retrieved context (primary judge)",
            "- **% Ungrounded**: Claims with no supporting evidence (hallucination rate)",
            "- **Citation Acc**: Fraction of model citations pointing to actual retrieved chunks",
            "- **Latency (p50)**: Median end-to-end pipeline latency",
            "- **Cost/Query**: Estimated Bedrock API cost (generation + judge calls)",
            "- **Judge Agreement**: Claim-level agreement between primary and secondary judges",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    preset_names = ", ".join(f"'{k}'" for k in JUDGE_PRESETS)

    parser = argparse.ArgumentParser(
        description="Run RAG evaluation across pipeline configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--chunking", choices=["fixed", "structure"], default="structure")
    parser.add_argument("--embedding", choices=["titan", "cohere"], default="titan")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-judges", action="store_true")
    parser.add_argument("--primary-judge-only", action="store_true")
    parser.add_argument(
        "--primary-judge",
        type=str,
        default="opus",
        help=f"Primary judge — preset ({preset_names}) or model ID (default: opus)",
    )
    parser.add_argument(
        "--secondary-judge",
        type=str,
        default="gpt-oss",
        help=f"Secondary judge — preset ({preset_names}) or model ID (default: gpt-oss)",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR))
    parser.add_argument("--profile", type=str, default=None)
    parser.add_argument("--qa-file", type=str, default=str(QA_PATH))
    args = parser.parse_args()

    primary_resolved = resolve_judge_model(args.primary_judge)
    secondary_resolved = resolve_judge_model(args.secondary_judge)
    logger.info("Primary judge: %s (%s)", args.primary_judge, primary_resolved)
    if not args.primary_judge_only and not args.skip_judges:
        logger.info("Secondary judge: %s (%s)", args.secondary_judge, secondary_resolved)

    qa_pairs = load_qa_pairs(Path(args.qa_file), limit=args.limit)
    configs = ALL_CONFIGS if args.all else [(args.chunking, args.embedding)]
    output_dir = Path(args.output_dir)

    # Load checkpoint if resuming
    all_results = {}
    if args.resume:
        all_results = load_checkpoint(output_dir)
        if all_results:
            total_qs = sum(len(v) for v in all_results.values())
            logger.info(
                "Resuming from checkpoint: %d config(s), %d total questions completed (%s)",
                len(all_results),
                total_qs,
                ", ".join(all_results.keys()),
            )

    all_aggregates = {}
    total_start = time.perf_counter()

    for chunking, embedding in configs:
        config_label = f"{chunking}-{embedding}"

        # Check if this config is fully complete
        if config_label in all_results:
            existing = all_results[config_label]
            if len(existing) >= len(qa_pairs):
                logger.info("Skipping %s — all %d questions complete", config_label, len(existing))
                # Still compute aggregate for the table
                qm = [QueryMetrics(**r["metrics"]) for r in existing if "error" not in r]
                all_aggregates[config_label] = compute_aggregate_metrics(qm, config_label)
                continue
            else:
                logger.info(
                    "Resuming %s — %d/%d questions complete",
                    config_label,
                    len(existing),
                    len(qa_pairs),
                )

        logger.info("=" * 60)
        logger.info("Starting evaluation: %s", config_label)
        logger.info("=" * 60)

        aggregate = evaluate_config(
            qa_pairs,
            chunking,
            embedding,
            all_results,
            output_dir,
            skip_judges=args.skip_judges,
            primary_only=args.primary_judge_only,
            primary_model=args.primary_judge,
            secondary_model=args.secondary_judge,
            profile=args.profile,
        )

        all_aggregates[config_label] = aggregate

        logger.info(
            "%s: precision=%.1f%% recall=%.1f%% grounded=%.1f%% latency_p50=%.0fms",
            config_label,
            aggregate.retrieval_precision_mean * 100,
            aggregate.retrieval_recall_mean * 100,
            aggregate.grounded_pct_mean * 100,
            aggregate.latency.median_ms,
        )

    total_elapsed = (time.perf_counter() - total_start) / 60

    save_final(all_results, all_aggregates, output_dir)

    print("\n")
    print(format_comparison_table(all_aggregates))
    print(f"\nTotal evaluation time: {total_elapsed:.1f} minutes")
    print(f"Results saved to {output_dir}/")


if __name__ == "__main__":
    main()
