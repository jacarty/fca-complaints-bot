import json
import logging
import os
import random
import re
from pathlib import Path

import boto3
from dotenv import load_dotenv

from src.scraper.fca_client import FCAHandbookClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TARGET_MODULES = [
    "prin",
    "sysc",
    "cocon",
    "cond",
    "aper",
    "fit",
    "finmar",
    "tc",
    "gen",
    "fees",
    "cobs",
    "icobs",
    "mcob",
    "bcobs",
    "cass",
    "mar",
    "prod",
    "esg",
    "disp",
    "conc",
]

OUTPUT_DIR = Path("data/fca")


def _parse_cross_references(html: str) -> list[str]:
    """Extract handbook cross-references from provision HTML."""
    if not html:
        return []
    refs = re.findall(r'href="(/handbook/[^"]+)"', html)
    seen = set()
    result = []
    for ref in refs:
        clean = ref.replace("/handbook/", "").strip("/")
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def extract_section_pairs(tree: list[dict], target_modules: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for block in tree:
        for sourcebook in block.get("parts", []):
            if sourcebook["entityId"] in target_modules:
                for chapter in sourcebook.get("parts", []):
                    for section in chapter.get("parts", []):
                        pairs.append((chapter["entityId"], section["entityId"]))
    return pairs


def build_chapter_to_module(tree: list[dict], target_modules: list[str]) -> dict[str, str]:
    """Map every chapter entityId to its parent sourcebook entityId."""
    mapping = {}
    for block in tree:
        for sourcebook in block.get("parts", []):
            if sourcebook["entityId"] in target_modules:
                for chapter in sourcebook.get("parts", []):
                    mapping[chapter["entityId"]] = sourcebook["entityId"]
    return mapping


def scrape(
    client: FCAHandbookClient,
    pairs: list[tuple[str, str]],
    chapter_to_module: dict[str, str],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(pairs)
    provision_count = 0

    with open(output_path, "w") as f:
        for i, (chapter_id, section_id) in enumerate(pairs, start=1):
            module = chapter_to_module.get(chapter_id, chapter_id)
            logger.info(f"[{i}/{total}] {module.upper()} — {chapter_id} / {section_id}")

            try:
                provisions = client.get_section_provisions(chapter_id, section_id)
            except Exception as e:
                logger.error(f"Failed to fetch {chapter_id}/{section_id}: {e}")
                continue

            for provision in provisions:
                html = provision.get("contentType", "")
                record = {
                    "provision_id": provision.get("provisionName", ""),
                    "entity_id": provision.get("entityId", ""),
                    "module": module,
                    "chapter": chapter_id,
                    "section": section_id,
                    "section_name": provision.get("sectionName", ""),
                    "provision_type": provision.get("provisionType", ""),
                    "text": provision.get("contentText", ""),
                    "html": html,
                    "cross_references": _parse_cross_references(html),
                    "last_modified": provision.get("timeline", ""),
                }
                f.write(json.dumps(record) + "\n")
                provision_count += 1

    logger.info(f"Done. {provision_count} provisions written to {output_path}")


def get_aws_session() -> boto3.Session:
    """Create a boto3 session using the profile from .env."""
    return boto3.Session(
        profile_name=os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "eu-west-1"),
    )


def ensure_s3_bucket(session: boto3.Session, bucket: str) -> None:
    """Create the S3 bucket if it doesn't exist."""
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
        logger.info(f"Bucket s3://{bucket} already exists")
    except s3.exceptions.ClientError:
        region = session.region_name
        logger.info(f"Creating bucket s3://{bucket} in {region}")
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
        logger.info(f"Bucket s3://{bucket} created")


def upload_to_s3(session: boto3.Session, file_path: Path, bucket: str, prefix: str) -> None:
    """Upload the JSONL file to S3."""
    s3 = session.client("s3")
    key = f"{prefix}/{file_path.name}"
    logger.info(f"Uploading {file_path} to s3://{bucket}/{key}")
    s3.upload_file(str(file_path), bucket, key)
    logger.info(f"Upload complete: s3://{bucket}/{key}")


def spot_check(file_path: Path, n: int = 10) -> None:
    """Print n random provisions for manual verification against the handbook website."""
    with open(file_path) as f:
        lines = f.readlines()

    samples = random.sample(lines, min(n, len(lines)))

    print(f"\n{'=' * 80}")
    print(f"SPOT CHECK — {n} random provisions")
    print(f"{'=' * 80}\n")

    for i, line in enumerate(samples, 1):
        record = json.loads(line)
        text_preview = record["text"][:120].replace("\n", " ")
        url = f"https://handbook.fca.org.uk/handbook/{record['chapter']}/{record['section']}"
        print(f"  [{i}] {record['provision_id']} ({record['provision_type']})")
        print(f"      {text_preview}...")
        print(f"      Verify: {url}")
        print()


if __name__ == "__main__":
    load_dotenv()

    client = FCAHandbookClient()

    logger.info("Fetching handbook tree...")
    tree = client.get_handbook_tree()

    pairs = extract_section_pairs(tree, TARGET_MODULES)
    chapter_to_module = build_chapter_to_module(tree, TARGET_MODULES)
    logger.info(f"Found {len(pairs)} sections to scrape across {len(TARGET_MODULES)} modules")

    output_path = OUTPUT_DIR / "fca_handbook.jsonl"
    scrape(client, pairs, chapter_to_module, output_path)

    spot_check(output_path)

    bucket = os.getenv("S3_BUCKET")
    prefix = os.getenv("S3_PREFIX", "fca-handbook")
    if bucket:
        session = get_aws_session()
        ensure_s3_bucket(session, bucket)
        upload_to_s3(session, output_path, bucket, prefix)
    else:
        logger.warning("S3_BUCKET not set in .env — skipping upload")
