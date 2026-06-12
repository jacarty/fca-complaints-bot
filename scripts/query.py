"""Run the retrieval + generation pipeline from the command line.

Usage:
    # Single query with default config (structure-titan)
    uv run python scripts/query.py "What are the FCA Principles for Business?"

    # Specify config
    uv run python scripts/query.py "What governance arrangements must a firm have?" \\
        --chunking fixed --embedding cohere

    # Run across all 4 configs for comparison
    uv run python scripts/query.py "What are the FCA Principles for Business?" --all

    # Show retrieved chunks without generation
    uv run python scripts/query.py "SYSC 10.1 conflicts of interest" --retrieve-only

    # Adjust top-k
    uv run python scripts/query.py "complaints handling" --top-k 5
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path so src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import PipelineConfig, PipelineResult, query_pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ALL_CONFIGS = [
    ("fixed", "titan"),
    ("fixed", "cohere"),
    ("structure", "titan"),
    ("structure", "cohere"),
]


def print_result(result: PipelineResult, *, verbose: bool = False) -> None:
    """Print a pipeline result in a readable format."""
    config = result.config
    config_label = f"{config['chunking']}-{config['embedding']}"

    print(f"\n{'=' * 70}")
    print(f"Config: {config_label}")
    print(f"{'=' * 70}")
    print(f"\nAnswer:\n{result.answer}")
    print(f"\nCitations: {result.citations}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Insufficient context: {result.insufficient_context}")
    print(
        f"\nLatency: {result.latency_ms:.0f}ms total "
        f"(retrieval: {result.retrieval_latency_ms:.0f}ms, "
        f"generation: {result.generation_latency_ms:.0f}ms)"
    )

    if result.usage:
        print(
            f"Tokens: {result.usage.get('input_tokens', '?')} in, "
            f"{result.usage.get('output_tokens', '?')} out"
        )

    print(f"\nRetrieved chunks ({len(result.retrieved_chunks)}):")
    for i, chunk in enumerate(result.retrieved_chunks, 1):
        section = chunk.metadata.get("section", "?")
        cited = " [CITED]" if chunk.chunk_id in result.citations else ""
        print(f"  [{i}] score={chunk.score:.4f}  section={section}{cited}")
        if verbose:
            preview = chunk.content[:200].replace("\n", " ")
            print(f"       {preview}...")
            print(f"       chunk_id: {chunk.chunk_id}")
    print()


def print_retrieval_only(chunks: list, config_label: str) -> None:
    """Print retrieval results without generation."""
    print(f"\n{'=' * 70}")
    print(f"Retrieval only: {config_label}")
    print(f"{'=' * 70}")
    print(f"\nRetrieved {len(chunks)} chunks:")
    for i, chunk in enumerate(chunks, 1):
        section = chunk.metadata.get("section", "?")
        print(f"  [{i}] score={chunk.score:.4f}  section={section}")
        preview = chunk.content[:150].replace("\n", " ")
        print(f"       {preview}...")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the RAG retrieval + generation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("question", help="Question to answer")
    parser.add_argument(
        "--chunking",
        choices=["fixed", "structure"],
        default="structure",
        help="Chunking strategy (default: structure)",
    )
    parser.add_argument(
        "--embedding",
        choices=["titan", "cohere"],
        default="titan",
        help="Embedding model (default: titan)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of chunks to retrieve (default: 10)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run across all 4 KB configurations and compare",
    )
    parser.add_argument(
        "--retrieve-only",
        action="store_true",
        help="Only retrieve chunks, skip generation",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show chunk text previews and IDs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="AWS profile name (overrides AWS_PROFILE in .env)",
    )
    args = parser.parse_args()

    configs = ALL_CONFIGS if args.all else [(args.chunking, args.embedding)]

    if args.retrieve_only:
        # Import retrieval function directly for retrieve-only mode
        from src.pipeline import (
            _get_agent_runtime_client,
            _resolve_kb_id,
            retrieve_chunks,
        )

        for chunking, embedding in configs:
            config = PipelineConfig(chunking=chunking, embedding=embedding, top_k=args.top_k)
            kb_id = _resolve_kb_id(config)
            client = _get_agent_runtime_client(args.profile)
            chunks = retrieve_chunks(client, kb_id, args.question, top_k=args.top_k)
            print_retrieval_only(chunks, f"{chunking}-{embedding}")
        return

    results = []
    for chunking, embedding in configs:
        try:
            result = query_pipeline(
                args.question,
                chunking=chunking,
                embedding=embedding,
                top_k=args.top_k,
                profile=args.profile,
            )
            results.append(result)

            if args.json_output:
                continue  # collect all, print at end
            print_result(result, verbose=args.verbose)

        except Exception as e:
            logger.error("Failed for %s-%s: %s", chunking, embedding, e)
            if not args.all:
                raise

    if args.json_output:
        output = [r.model_dump() for r in results]
        print(json.dumps(output, indent=2, default=str))

    # Summary comparison table if --all
    if args.all and not args.json_output and len(results) > 1:
        print(f"\n{'=' * 70}")
        print("Comparison Summary")
        print(f"{'=' * 70}")
        print(f"{'Config':<25} {'Confidence':>10} {'Cited':>6} {'Latency':>10} {'Tokens':>8}")
        print("-" * 70)
        for r in results:
            cfg = r.config
            label = f"{cfg['chunking']}-{cfg['embedding']}"
            tokens = r.usage.get("total_tokens", "?")
            print(
                f"{label:<25} {r.confidence:>10.2f} {len(r.citations):>6} "
                f"{r.latency_ms:>8.0f}ms {tokens:>8}"
            )
        print()


if __name__ == "__main__":
    main()
