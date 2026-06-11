"""Convert the FCA JSONL corpus into per-section markdown files for Bedrock KB ingestion.

Reads data/fca/fca_handbook.jsonl (one record per provision), groups provisions
by section, and writes one markdown file per section with a metadata sidecar.

Usage:
    uv run python scripts/convert_fca_to_sections.py              # Convert all
    uv run python scripts/convert_fca_to_sections.py --dry-run     # Preview counts
    uv run python scripts/convert_fca_to_sections.py --upload       # Convert and upload to S3
"""

import argparse
import html
import json
import logging
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.chunking.structure import pack_units  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

JSONL_PATH = Path("data/fca/fca_handbook.jsonl")
OUTPUT_DIR = Path("data/fca/sections")
# Structure-aware (NONE-chunking) output: provision-packed files under the Cohere
# 512-token cap, read by the structure-titan and structure-cohere KBs.
STRUCTURE_DIR = Path("data/fca/sections-structure")


def clean_text(text: str) -> str:
    """Decode HTML entities and normalise whitespace from FCA contentText.

    The scraped text carries literal HTML entities (`&nbsp;`, `&amp;`, ...). Left
    in, `&nbsp;` alone inflates Cohere token counts enough to push otherwise-small
    chunks over the 512-token limit, and it degrades embedding quality. Decode to
    real characters, fold non-breaking spaces to spaces, and collapse runs of
    spaces/tabs (newlines preserved for paragraph structure).
    """
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def load_provisions(jsonl_path: Path) -> dict[str, list[dict]]:
    """Read JSONL and group provisions by section, preserving insertion order."""
    sections: dict[str, list[dict]] = defaultdict(list)
    with open(jsonl_path) as f:
        for line in f:
            record = json.loads(line)
            sections[record["section"]].append(record)
    return dict(sections)


def section_heading(provisions: list[dict]) -> str:
    """Build the section heading line, e.g. '# PRIN 2.1 — The Principles'."""
    first = provisions[0]
    module = first["module"].upper()
    section_name = first.get("section_name", "")
    section_id = (
        first["provision_id"].rsplit(".", 1)[0]
        if "." in first["provision_id"]
        else first["section"]
    )
    return f"# {module} — {section_name}" if section_name else f"# {module} — {section_id}"


def provision_block(prov: dict) -> str:
    """Format a single provision as a markdown subheading block (no section heading)."""
    prov_type = prov.get("provision_type", "")
    prov_id = prov.get("provision_id", "")
    text = clean_text(prov.get("text") or "")
    if not text:
        return ""
    type_label = f" ({prov_type})" if prov_type else ""
    return f"## {prov_id}{type_label}\n\n{text}"


def format_section_markdown(provisions: list[dict]) -> str:
    """Format a whole section's provisions as one markdown document.

    Used for the fixed-size KBs, which re-chunk server-side and so want the
    full section as input.
    """
    lines = [section_heading(provisions), ""]
    for prov in provisions:
        block = provision_block(prov)
        if block:
            lines.append(block)
            lines.append("")
    return "\n".join(lines)


def build_metadata(section_id: str, provisions: list[dict]) -> dict:
    """Build the (lean, scalar-only) metadata sidecar for a section.

    Kept to short scalars so the combined filterable metadata stays well under
    the S3 Vectors 2048-byte cap. Provision IDs and cross-references are
    intentionally omitted: they are already present in the chunk text (the
    '## PRIN 2.1.1R' headings), and a whole section's lists can be large enough
    to breach the cap and fail ingestion.
    """
    first = provisions[0]
    return {
        "document_type": "fca_regulation",
        "source": "fca_handbook_api",
        "synthetic": False,
        "module": first["module"],
        "chapter": first["chapter"],
        "section": section_id,
        "section_name": first.get("section_name", ""),
        "provision_count": len(provisions),
        "doc_type": "fca_regulation",
    }


def convert(sections: dict[str, list[dict]], output_dir: Path) -> int:
    """Write whole-section markdown + metadata files. Returns file count.

    Bedrock requires the metadata sidecar to be named '<source-filename>.metadata.json'
    (i.e. INCLUDING the .md extension). A sidecar named '<slug>.metadata.json' is
    silently ignored, leaving chunks with no custom metadata.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for section_id, provisions in sections.items():
        slug = section_id  # Already a clean slug like "prin-2-1"
        md_path = output_dir / f"{slug}.md"
        meta_path = output_dir / f"{slug}.md.metadata.json"

        md_content = format_section_markdown(provisions)
        md_path.write_text(md_content)

        metadata = build_metadata(section_id, provisions)
        meta_path.write_text(json.dumps({"metadataAttributes": metadata}, indent=2))

        count += 1

    return count


def build_structure_metadata(
    section_id: str, provisions: list[dict], labels: list[str], chunk_index: int
) -> dict:
    """Lean, scalar-only metadata for a single structure-aware chunk.

    Mirrors build_metadata; the section key is what the eval maps ground-truth
    references onto. Provision IDs stay in the chunk text, not the sidecar.
    """
    first = provisions[0]
    return {
        "document_type": "fca_regulation",
        "source": "fca_handbook_api",
        "synthetic": False,
        "module": first["module"],
        "chapter": first["chapter"],
        "section": section_id,
        "section_name": first.get("section_name", ""),
        "provision_count": len(labels),
        "chunk_index": chunk_index,
        "doc_type": "fca_regulation",
    }


def convert_structure(sections: dict[str, list[dict]], output_dir: Path) -> tuple[int, int]:
    """Write provision-packed chunk files (<= Cohere 512-token cap) for structure KBs.

    Returns (chunk_file_count, source_section_count). Each chunk stays within one
    section; provisions are packed up to the token cap, and any provision larger
    than the cap is fallback-split. The section heading is prepended to every chunk.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    chunk_count = 0

    for section_id, provisions in sections.items():
        heading = section_heading(provisions)
        units = [(p["provision_id"], provision_block(p)) for p in provisions if provision_block(p)]
        if not units:
            continue

        chunks = pack_units(units, header=heading)
        for i, chunk in enumerate(chunks):
            slug = f"{section_id}-{i:03d}"
            md_path = output_dir / f"{slug}.md"
            meta_path = output_dir / f"{slug}.md.metadata.json"
            md_path.write_text(chunk["text"])
            metadata = build_structure_metadata(section_id, provisions, chunk["labels"], i)
            meta_path.write_text(json.dumps({"metadataAttributes": metadata}, indent=2))
            chunk_count += 1

    return chunk_count, len(sections)


def _clean_dir(output_dir: Path) -> None:
    """Remove stale .md / .metadata.json files so reruns don't leave orphans.

    Earlier runs wrote sidecars under the wrong name ('<slug>.metadata.json');
    clearing avoids uploading those dead files alongside the corrected ones.
    """
    if output_dir.exists():
        for f in output_dir.glob("*.json"):
            f.unlink()
        for f in output_dir.glob("*.md"):
            f.unlink()


def upload_to_s3(
    session: boto3.Session,
    output_dir: Path,
    bucket: str,
    prefix: str,
    max_workers: int = 32,
) -> None:
    """Upload all section files to S3 concurrently, logging progress."""
    s3 = session.client("s3", config=Config(max_pool_connections=max_workers))
    files = sorted(output_dir.glob("*"))
    total = len(files)
    logger.info(f"Uploading {total} files to s3://{bucket}/{prefix}/ ...")

    def _put(file_path: Path) -> None:
        s3.upload_file(str(file_path), bucket, f"{prefix}/{file_path.name}")

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_put, f): f for f in files}
        for future in as_completed(futures):
            future.result()  # surface upload errors
            done += 1
            if done % 500 == 0 or done == total:
                logger.info(f"  {done}/{total} uploaded to {prefix}/")
    logger.info(f"Uploaded {total} files to s3://{bucket}/{prefix}/")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert FCA JSONL to per-section markdown")
    parser.add_argument("--dry-run", action="store_true", help="Show counts without writing")
    parser.add_argument("--upload", action="store_true", help="Upload to S3 after conversion")
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="AWS profile name (overrides AWS_PROFILE in .env)",
    )
    args = parser.parse_args()

    load_dotenv()

    if not JSONL_PATH.exists():
        logger.error(f"JSONL not found at {JSONL_PATH} — run scrape_fca.py first")
        raise SystemExit(1)

    logger.info(f"Reading {JSONL_PATH}...")
    sections = load_provisions(JSONL_PATH)

    # Summary stats
    modules = defaultdict(int)
    for provisions in sections.values():
        modules[provisions[0]["module"]] += 1

    if args.dry_run:
        provision_total = sum(len(p) for p in sections.values())
        print(f"\nWould create {len(sections)} section files from {provision_total} provisions:\n")
        for module, count in sorted(modules.items()):
            print(f"  {module.upper():12s} {count:4d} sections")
        print(f"  {'TOTAL':12s} {len(sections):4d} sections")
        print(f"\nOutput: {OUTPUT_DIR}/")
        return

    logger.info(f"Converting {len(sections)} sections (whole-section, fixed arm)...")
    _clean_dir(OUTPUT_DIR)
    count = convert(sections, OUTPUT_DIR)

    logger.info("Converting structure-aware chunks (<= 2000 chars, structure arm)...")
    _clean_dir(STRUCTURE_DIR)
    chunk_count, _ = convert_structure(sections, STRUCTURE_DIR)

    print(f"\n{'=' * 60}")
    print(f"Whole-section:    {count} sections -> {OUTPUT_DIR}/")
    print(f"Structure chunks: {chunk_count} chunks from {count} sections -> {STRUCTURE_DIR}/")
    for module, sec_count in sorted(modules.items()):
        print(f"  {module.upper():12s} {sec_count:4d} sections")
    print(f"{'=' * 60}\n")

    if args.upload:
        bucket = os.getenv("S3_BUCKET", "rag-eval-framework-project")
        whole_prefix = os.getenv("S3_FCA_SECTIONS_PREFIX", "fca-handbook/sections")
        structure_prefix = os.getenv(
            "S3_FCA_SECTIONS_STRUCTURE_PREFIX", "fca-handbook/sections-structure"
        )
        if bucket:
            profile = args.profile or os.getenv("AWS_PROFILE")
            session = boto3.Session(
                profile_name=profile,
                region_name=os.getenv("AWS_REGION", "eu-west-1"),
            )
            upload_to_s3(session, OUTPUT_DIR, bucket, whole_prefix)
            upload_to_s3(session, STRUCTURE_DIR, bucket, structure_prefix)
        else:
            logger.warning("S3_BUCKET not set — skipping upload")


if __name__ == "__main__":
    main()
