"""Drafting evaluation runner (JAM-275).

Runs ground-truth complaint scenarios through the drafting pipeline for each KB
config, scores each drafted response with the rubric-coverage judge (dual-model),
the grounding judge (single-model), and the retrieval/citation metrics, then
outputs a comparison table. Checkpoints after every scenario so progress survives
a crash.

Sibling to run_eval.py (the Q&A runner); the two are independent so either
evaluation can be cloned and run on its own.

Usage:
    # Both titan configs, full run (grounding on by default)
    uv run python scripts/run_drafting_eval.py --all

    # Resume after a crash — picks up at the exact scenario where it stopped
    uv run python scripts/run_drafting_eval.py --all --resume

    # Cheaper iteration
    uv run python scripts/run_drafting_eval.py --all --no-grounding
    uv run python scripts/run_drafting_eval.py --all --skip-judges
    uv run python scripts/run_drafting_eval.py --all --primary-judge-only --limit 5

    # Single config
    uv run python scripts/run_drafting_eval.py --chunking structure --embedding titan

    # Judge presets: opus, haiku, sonnet, gpt-oss
"""

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bedrock_common import JUDGE_PRESETS, resolve_judge_model  # noqa: E402
from src.judge_drafting import (  # noqa: E402
    GroundingJudgeResult,
    RubricJudgeResult,
    load_rubric,
    run_dual_rubric_judges,
    run_grounding_judge,
    run_rubric_judge,
    select_required_elements,
)
from src.metrics_drafting import (  # noqa: E402
    DraftingScenarioMetrics,
    compute_drafting_aggregate,
    compute_drafting_cost,
    compute_drafting_retrieval_metrics,
    compute_rubric_agreement,
)
from src.pipeline import (  # noqa: E402
    PipelineConfig,
    _get_bedrock_runtime_client,
    draft_pipeline,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("botocore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

SCENARIOS_PATH = Path("data/qa/complaint_scenarios.json")
OUTPUT_DIR = Path("data/eval")
CHECKPOINT_FILE = "drafting_eval_checkpoint.json"

# Titan only — the bot builds no Cohere KBs.
ALL_CONFIGS = [
    ("fixed", "titan"),
    ("structure", "titan"),
]


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------


def load_scenarios(path: Path = SCENARIOS_PATH, *, limit: int | None = None) -> list[dict]:
    with open(path) as f:
        scenarios = json.load(f)
    if limit:
        scenarios = scenarios[:limit]
    logger.info("Loaded %d scenarios", len(scenarios))
    return scenarios


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def save_checkpoint(all_results: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / CHECKPOINT_FILE, "w") as f:
        json.dump(all_results, f, indent=2, default=str)


def load_checkpoint(output_dir: Path) -> dict:
    checkpoint_path = output_dir / CHECKPOINT_FILE
    if checkpoint_path.exists():
        with open(checkpoint_path) as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Single scenario evaluation
# ---------------------------------------------------------------------------


def evaluate_single(
    scenario: dict,
    config: PipelineConfig,
    judge_client,
    rubric: dict,
    *,
    skip_judges: bool = False,
    primary_only: bool = False,
    grounding: bool = True,
    primary_model: str = "gpt-oss",
    secondary_model: str = "haiku",
    grounding_model: str = "gpt-oss",
    profile: str | None = None,
) -> dict:
    scenario_id = scenario["scenario_id"]
    scenario_text = scenario["scenario_text"]
    response_stage = scenario.get("response_stage", "final")
    required_keys = scenario.get("required_response_elements", [])
    expected_provisions = scenario.get("expected_provisions", [])
    expected_sections = scenario.get("expected_provision_sections", [])
    config_label = f"{config.chunking}-{config.embedding}"

    logger.info("Evaluating %s on %s", scenario_id, config_label)

    try:
        result = draft_pipeline(scenario_text, config=config, profile=profile)
    except Exception as e:
        logger.error("Pipeline failed for %s on %s: %s", scenario_id, config_label, e)
        return {"scenario_id": scenario_id, "config": config_label, "error": str(e)}

    chunk_dicts = [c.model_dump() for c in result.retrieved_chunks]

    # --- automated retrieval / citation metrics ---
    retrieval = compute_drafting_retrieval_metrics(
        cited_provisions=result.cited_provisions,
        retrieved_chunks=chunk_dicts,
        expected_provisions=expected_provisions,
        expected_sections=expected_sections,
    )

    # --- rubric-coverage judge (dual unless skipped / primary-only) ---
    elements = select_required_elements(rubric, required_keys)
    primary = RubricJudgeResult(verdicts=[], required_elements=required_keys, judge_model="skipped", latency_ms=0)
    secondary = RubricJudgeResult(verdicts=[], required_elements=required_keys, judge_model="skipped", latency_ms=0)

    if not skip_judges:
        if primary_only:
            primary = run_rubric_judge(
                judge_client,
                scenario_text=scenario_text,
                response_stage=response_stage,
                handler_answer=result.handler_answer,
                customer_draft=result.customer_draft,
                required_elements=elements,
                model_id=primary_model,
            )
        else:
            primary, secondary = run_dual_rubric_judges(
                judge_client,
                scenario_text=scenario_text,
                response_stage=response_stage,
                handler_answer=result.handler_answer,
                customer_draft=result.customer_draft,
                required_elements=elements,
                primary_model=primary_model,
                secondary_model=secondary_model,
            )

    # --- grounding judge (single model, on by default) ---
    grounding_result = GroundingJudgeResult(claims=[], judge_model="skipped", latency_ms=0)
    if grounding and not skip_judges:
        grounding_result = run_grounding_judge(
            judge_client,
            handler_answer=result.handler_answer,
            customer_draft=result.customer_draft,
            chunks=chunk_dicts,
            model_id=grounding_model,
        )

    # --- agreement + cost ---
    agreement = 0.0
    if not skip_judges and not primary_only and primary.verdicts and secondary.verdicts:
        agreement = compute_rubric_agreement(primary, secondary)

    judge_calls = []
    if not skip_judges:
        judge_calls.append(("rubric_primary", resolve_judge_model(primary_model), primary.usage))
        if not primary_only:
            judge_calls.append(("rubric_secondary", resolve_judge_model(secondary_model), secondary.usage))
        if grounding:
            judge_calls.append(("grounding", resolve_judge_model(grounding_model), grounding_result.usage))
    cost = compute_drafting_cost(result.usage, judge_calls)

    # --- flat per-scenario metrics (re-hydrated to aggregate) ---
    scenario_metrics = DraftingScenarioMetrics(
        scenario_id=scenario_id,
        config_label=config_label,
        coverage=primary.coverage,
        present_count=primary.present_count,
        n_required=len(required_keys),
        section_precision=retrieval.section.precision,
        section_recall=retrieval.section.recall,
        citation_precision=retrieval.citation.precision,
        citation_recall=retrieval.citation.recall,
        retrieved_provision_recall=retrieval.retrieved_provision_recall.recall,
        drafting_gap=retrieval.drafting_attributable_gap,
        grounded_pct=grounding_result.grounded_pct,
        grounded_pct_customer=grounding_result.grounded_pct_for("customer_draft"),
        grounded_pct_handler=grounding_result.grounded_pct_for("handler_answer"),
        ungrounded_pct=grounding_result.ungrounded_pct,
        inter_judge_agreement=agreement,
        latency_ms=result.latency_ms,
        total_cost=cost.total_cost,
    )

    return {
        "scenario_id": scenario_id,
        "config": config_label,
        "scenario_text": scenario_text,
        "response_stage": response_stage,
        "complaint_type": scenario.get("complaint_type", "unknown"),
        "difficulty": scenario.get("difficulty", "unknown"),
        "expected_provisions": expected_provisions,
        "expected_provision_sections": expected_sections,
        "required_response_elements": required_keys,
        "handler_answer": result.handler_answer,
        "customer_draft": result.customer_draft,
        "cited_provisions": [p.model_dump() for p in result.cited_provisions],
        "human_review_required": result.human_review_required,
        "insufficient_context": result.insufficient_context,
        # Full chunk dicts (content + metadata) so retrieval/citation metrics can be
        # recomputed offline from the detail JSON without re-calling Bedrock.
        "retrieved_chunks": chunk_dicts,
        "retrieval_metrics": retrieval.model_dump(),
        "judge_rubric_primary": primary.model_dump() if primary.verdicts else None,
        "judge_rubric_secondary": secondary.model_dump() if secondary.verdicts else None,
        "judge_grounding": grounding_result.model_dump() if grounding_result.claims else None,
        "inter_judge_agreement": agreement,
        "cost": cost.model_dump(),
        "metrics": scenario_metrics.model_dump(),
        "latency_ms": result.latency_ms,
        "usage": result.usage,
    }


# ---------------------------------------------------------------------------
# Config-level evaluation with per-scenario checkpointing
# ---------------------------------------------------------------------------


def evaluate_config(
    scenarios: list[dict],
    chunking: str,
    embedding: str,
    all_results: dict,
    output_dir: Path,
    rubric: dict,
    **kwargs,
):
    config = PipelineConfig(chunking=chunking, embedding=embedding)
    config_label = f"{chunking}-{embedding}"
    judge_client = _get_bedrock_runtime_client(kwargs.get("profile"))

    all_results.setdefault(config_label, [])
    existing_results = all_results[config_label]
    completed_ids = {r["scenario_id"] for r in existing_results if "scenario_id" in r}

    for i, scenario in enumerate(scenarios, 1):
        sid = scenario["scenario_id"]
        if sid in completed_ids:
            logger.info("[%d/%d] %s on %s — already done, skipping", i, len(scenarios), sid, config_label)
            continue

        logger.info("[%d/%d] %s on %s", i, len(scenarios), sid, config_label)
        record = evaluate_single(scenario, config, judge_client, rubric, **kwargs)
        existing_results.append(record)
        save_checkpoint(all_results, output_dir)

    scenario_metrics = [
        DraftingScenarioMetrics(**r["metrics"]) for r in existing_results if "error" not in r
    ]
    return compute_drafting_aggregate(scenario_metrics, config_label)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def format_comparison_table(aggregates: dict) -> str:
    lines = [
        "# Drafting Evaluation Results",
        "",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Configuration Comparison",
        "",
        "| Config | Coverage | Section P | Citation P/R | Retr. Recall | Gap | Grounded % | Agreement |",
        "|--------|----------|-----------|--------------|--------------|-----|------------|-----------|",
    ]
    for label, agg in aggregates.items():
        lines.append(
            f"| {label} "
            f"| {agg.coverage_mean:.1%} "
            f"| {agg.section_precision_mean:.1%} "
            f"| {agg.citation_precision_mean:.1%} / {agg.citation_recall_mean:.1%} "
            f"| {agg.retrieved_provision_recall_mean:.1%} "
            f"| {agg.drafting_gap_mean:+.1%} "
            f"| {agg.grounded_pct_mean:.1%} "
            f"| {agg.inter_judge_agreement_mean:.1%} |"
        )

    first = next(iter(aggregates.values()), None)
    lines.extend(
        [
            "",
            f"*Based on {first.n_scenarios if first else 0} scenarios per config*",
            "",
            "## Metric Definitions",
            "",
            "- **Coverage**: Weighted rubric coverage (PRESENT 1.0, PARTIAL 0.5) over each scenario's required elements (primary judge)",
            "- **Section P**: Fraction of retrieved chunks whose section is an expected section (the KB comparison)",
            "- **Citation P/R**: Of provisions the bot cited, fraction expected / of expected provisions, fraction cited (normalised)",
            "- **Retr. Recall**: Fraction of expected provisions whose rule text was actually retrieved (present as a header)",
            "- **Gap**: Retrieved-text recall minus citation recall. Positive = rule retrieved but not cited (drafting miss); near-zero with low recall = KB miss",
            "- **Grounded %**: Regulatory claims grounded in retrieved provisions (grounding judge, overall)",
            "- **Agreement**: Element-level agreement between the two rubric judges",
            "",
            "Per-field grounding (handler vs customer draft) is in the aggregate JSON; a low customer-draft figure against a high handler figure is the drift signal.",
        ]
    )
    return "\n".join(lines)


def save_final(all_results: dict, all_aggregates: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    with open(output_dir / f"drafting_eval_detail_{timestamp}.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    agg_data = {k: v.model_dump() for k, v in all_aggregates.items()}
    with open(output_dir / f"drafting_eval_aggregate_{timestamp}.json", "w") as f:
        json.dump(agg_data, f, indent=2, default=str)

    md_content = format_comparison_table(all_aggregates)
    with open(output_dir / f"drafting_eval_comparison_{timestamp}.md", "w") as f:
        f.write(md_content)

    for name, data in [
        ("drafting_eval_detail_latest.json", all_results),
        ("drafting_eval_aggregate_latest.json", agg_data),
    ]:
        with open(output_dir / name, "w") as f:
            json.dump(data, f, indent=2, default=str)
    with open(output_dir / "drafting_eval_comparison_latest.md", "w") as f:
        f.write(md_content)
    logger.info("Saved results to %s", output_dir)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    preset_names = ", ".join(f"'{k}'" for k in JUDGE_PRESETS)

    parser = argparse.ArgumentParser(
        description="Run drafting evaluation across KB configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--chunking", choices=["fixed", "structure"], default="structure")
    parser.add_argument("--embedding", choices=["titan"], default="titan")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-judges", action="store_true", help="Retrieval/citation metrics only")
    parser.add_argument("--primary-judge-only", action="store_true", help="Skip the secondary rubric judge")
    parser.add_argument("--no-grounding", action="store_true", help="Skip the grounding judge")
    parser.add_argument("--primary-judge", default="gpt-oss", help=f"Rubric primary — preset ({preset_names}) or model ID")
    parser.add_argument("--secondary-judge", default="haiku", help=f"Rubric secondary — preset ({preset_names}) or model ID")
    parser.add_argument("--grounding-judge", default="gpt-oss", help=f"Grounding judge — preset ({preset_names}) or model ID")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--scenarios-file", default=str(SCENARIOS_PATH))
    args = parser.parse_args()

    logger.info("Rubric primary judge: %s (%s)", args.primary_judge, resolve_judge_model(args.primary_judge))
    if not args.primary_judge_only and not args.skip_judges:
        logger.info("Rubric secondary judge: %s (%s)", args.secondary_judge, resolve_judge_model(args.secondary_judge))
    if not args.no_grounding and not args.skip_judges:
        logger.info("Grounding judge: %s (%s)", args.grounding_judge, resolve_judge_model(args.grounding_judge))

    scenarios = load_scenarios(Path(args.scenarios_file), limit=args.limit)
    rubric = load_rubric()
    configs = ALL_CONFIGS if args.all else [(args.chunking, args.embedding)]
    output_dir = Path(args.output_dir)

    all_results = {}
    if args.resume:
        all_results = load_checkpoint(output_dir)
        if all_results:
            total = sum(len(v) for v in all_results.values())
            logger.info("Resuming: %d config(s), %d scenarios done (%s)", len(all_results), total, ", ".join(all_results))

    eval_kwargs = dict(
        skip_judges=args.skip_judges,
        primary_only=args.primary_judge_only,
        grounding=not args.no_grounding,
        primary_model=args.primary_judge,
        secondary_model=args.secondary_judge,
        grounding_model=args.grounding_judge,
        profile=args.profile,
    )

    all_aggregates = {}
    total_start = time.perf_counter()

    for chunking, embedding in configs:
        config_label = f"{chunking}-{embedding}"

        if config_label in all_results and len(all_results[config_label]) >= len(scenarios):
            logger.info("Skipping %s — all %d scenarios complete", config_label, len(all_results[config_label]))
            sm = [DraftingScenarioMetrics(**r["metrics"]) for r in all_results[config_label] if "error" not in r]
            all_aggregates[config_label] = compute_drafting_aggregate(sm, config_label)
            continue

        logger.info("=" * 60)
        logger.info("Starting evaluation: %s", config_label)
        logger.info("=" * 60)

        all_aggregates[config_label] = evaluate_config(
            scenarios, chunking, embedding, all_results, output_dir, rubric, **eval_kwargs
        )

        agg = all_aggregates[config_label]
        logger.info(
            "%s: coverage=%.1f%% section_p=%.1f%% citation_p=%.1f%% grounded=%.1f%%",
            config_label,
            agg.coverage_mean * 100,
            agg.section_precision_mean * 100,
            agg.citation_precision_mean * 100,
            agg.grounded_pct_mean * 100,
        )

    total_elapsed = (time.perf_counter() - total_start) / 60
    save_final(all_results, all_aggregates, output_dir)

    print("\n")
    print(format_comparison_table(all_aggregates))
    print(f"\nTotal evaluation time: {total_elapsed:.1f} minutes")
    print(f"Results saved to {output_dir}/")


if __name__ == "__main__":
    main()