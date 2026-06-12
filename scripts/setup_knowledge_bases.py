"""Set up 2 Bedrock Knowledge Bases with S3 Vectors for the FCA complaints bot.

Creates:
  - 1 S3 vector bucket with 2 vector indexes (one per config)
  - 1 IAM service role for KB
  - 2 Knowledge Bases (2 chunking strategies x 1 embedding model)
  - 2 data sources per KB (FCA sections + synthetic policies)
  - Triggers initial sync on all KBs

Usage:
    uv run python scripts/setup_knowledge_bases.py              # Full setup
    uv run python scripts/setup_knowledge_bases.py --sync-only  # Re-sync existing KBs
    uv run python scripts/setup_knowledge_bases.py --teardown   # Delete all resources
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Configuration ---

PROJECT_PREFIX = "fca-complaints"

# Knowledge Base configurations: (name_suffix, chunking_strategy, embedding_model_id)
KB_CONFIGS = [
    ("fixed-titan", "FIXED_SIZE", "amazon.titan-embed-text-v2:0"),
    ("structure-titan", "NONE", "amazon.titan-embed-text-v2:0"),
]

EMBEDDING_DIMENSION = 1024
FIXED_CHUNK_MAX_TOKENS = 512
FIXED_CHUNK_OVERLAP_PCT = 10  # ~50 tokens overlap

# S3 data source prefixes.
# The fixed-size KB re-chunks server-side, so it reads the whole-document
# prefixes. The structure (NONE-chunking) KB embeds each pre-split file whole,
# so it reads the "-structure" prefixes. Those files are capped at 480 Cohere
# tokens — not because Cohere is used here (embedding is Titan-only), but to
# keep chunk boundaries identical to the Post 16 baseline so the structure-titan
# results stay comparable.
FCA_SECTIONS_PREFIX = "fca-handbook/sections/"
FCA_SECTIONS_STRUCTURE_PREFIX = "fca-handbook/sections-structure/"
SYNTHETIC_PREFIX = "synthetic/policies/"
SYNTHETIC_STRUCTURE_PREFIX = "synthetic/policies-structure/"


def prefixes_for(chunking_strategy: str) -> tuple[str, str]:
    """Return (fca_prefix, synthetic_prefix) for a chunking strategy."""
    if chunking_strategy == "FIXED_SIZE":
        return FCA_SECTIONS_PREFIX, SYNTHETIC_PREFIX
    return FCA_SECTIONS_STRUCTURE_PREFIX, SYNTHETIC_STRUCTURE_PREFIX


# Output config file for downstream scripts
CONFIG_OUTPUT = Path("config/knowledge_bases.json")


def get_session() -> boto3.Session:
    return boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "AdministratorAccess-982099554067"),
        region_name=os.getenv("AWS_REGION", "eu-west-1"),
    )


def get_account_id(session: boto3.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


# --- S3 Vectors ---


def create_vector_bucket(session: boto3.Session, bucket_name: str) -> str:
    """Create an S3 vector bucket. Returns the bucket ARN."""
    s3v = session.client("s3vectors")
    try:
        resp = s3v.create_vector_bucket(vectorBucketName=bucket_name)
        resp = s3v.get_vector_bucket(vectorBucketName=bucket_name)
        arn = resp.get("vectorBucketArn") or resp["vectorBucket"]["vectorBucketArn"]
        logger.info(f"Created S3 vector bucket: {bucket_name}")
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            resp = s3v.get_vector_bucket(vectorBucketName=bucket_name)
            arn = resp["vectorBucket"]["vectorBucketArn"]
            logger.info(f"S3 vector bucket already exists: {bucket_name}")
            return arn
        raise


def create_vector_index(session: boto3.Session, bucket_name: str, index_name: str) -> str:
    """Create a vector index with non-filterable metadata keys. Returns index ARN.

    IMPORTANT: AMAZON_BEDROCK_TEXT and AMAZON_BEDROCK_METADATA must be declared
    as non-filterable. Bedrock KB stores the full chunk text under
    AMAZON_BEDROCK_TEXT, which routinely exceeds the S3 Vectors 2048-byte
    filterable metadata cap. Without this declaration, every ingestion record
    fails with "Filterable metadata must have at most 2048 bytes".

    Note: the key name is AMAZON_BEDROCK_TEXT (for S3 Vectors), NOT
    AMAZON_BEDROCK_TEXT_CHUNK (which is the OpenSearch variant).
    """
    s3v = session.client("s3vectors")
    try:
        resp = s3v.create_index(
            vectorBucketName=bucket_name,
            indexName=index_name,
            dataType="float32",
            dimension=EMBEDDING_DIMENSION,
            distanceMetric="cosine",
            metadataConfiguration={
                "nonFilterableMetadataKeys": [
                    "AMAZON_BEDROCK_TEXT",
                    "AMAZON_BEDROCK_METADATA",
                ]
            },
        )
        arn = resp.get("indexArn") or resp["index"]["indexArn"]
        logger.info(f"Created vector index: {index_name}")
        return arn
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            resp = s3v.get_index(vectorBucketName=bucket_name, indexName=index_name)
            arn = resp["index"]["indexArn"]
            logger.info(f"Vector index already exists: {index_name}")
            return arn
        raise


# --- IAM ---


def create_kb_role(
    session: boto3.Session,
    role_name: str,
    account_id: str,
    data_bucket_arn: str,
    vector_bucket_arn: str,
    index_arns: list[str],
    region: str,
) -> str:
    """Create the IAM service role for Bedrock KB. Returns the role ARN."""
    iam = session.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Service role for FCA complaints bot Knowledge Bases",
        )
        role_arn = resp["Role"]["Arn"]
        logger.info(f"Created IAM role: {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
            logger.info(f"IAM role already exists: {role_name}")
        else:
            raise

    # S3 read policy — access the data source bucket
    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
                "Resource": [data_bucket_arn],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"{data_bucket_arn}/*"],
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{PROJECT_PREFIX}-kb-s3-read",
        PolicyDocument=json.dumps(s3_policy),
    )

    # S3 Vectors policy — read/write vector indexes
    s3v_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3vectors:GetVectors",
                    "s3vectors:PutVectors",
                    "s3vectors:DeleteVectors",
                    "s3vectors:QueryVectors",
                    "s3vectors:ListVectors",
                    "s3vectors:GetIndex",
                    "s3vectors:ListIndexes",
                ],
                "Resource": [vector_bucket_arn] + index_arns,
            }
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{PROJECT_PREFIX}-kb-s3vectors",
        PolicyDocument=json.dumps(s3v_policy),
    )

    # Bedrock InvokeModel policy — embedding models
    bedrock_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [
                    f"arn:aws:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0",
                ],
            }
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{PROJECT_PREFIX}-kb-bedrock",
        PolicyDocument=json.dumps(bedrock_policy),
    )

    logger.info("IAM policies attached")
    # IAM propagation delay — policies need time to become effective
    logger.info("Waiting 10s for IAM propagation...")
    time.sleep(10)

    return role_arn


# --- Knowledge Bases ---


def create_knowledge_base(
    session: boto3.Session,
    name: str,
    role_arn: str,
    embedding_model_id: str,
    index_arn: str,
    region: str,
) -> str:
    """Create a Bedrock Knowledge Base. Returns the KB ID."""
    bedrock_agent = session.client("bedrock-agent", config=Config(read_timeout=120))

    embedding_model_arn = f"arn:aws:bedrock:{region}::foundation-model/{embedding_model_id}"

    vector_kb_config = {
        "embeddingModelArn": embedding_model_arn,
        "embeddingModelConfiguration": {
            "bedrockEmbeddingModelConfiguration": {
                "dimensions": EMBEDDING_DIMENSION,
                "embeddingDataType": "FLOAT32",
            }
        },
    }

    try:
        resp = bedrock_agent.create_knowledge_base(
            name=name,
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": vector_kb_config,
            },
            storageConfiguration={
                "type": "S3_VECTORS",
                "s3VectorsConfiguration": {
                    "indexArn": index_arn,
                },
            },
        )
        kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
        logger.info(f"Created Knowledge Base: {name} ({kb_id})")
        return kb_id
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            kb_id = find_knowledge_base_by_name(bedrock_agent, name)
            if kb_id:
                logger.info(f"Knowledge Base already exists: {name} ({kb_id})")
                return kb_id
        raise


def find_knowledge_base_by_name(bedrock_agent, name: str) -> str | None:
    """Find a KB by name. Returns KB ID or None."""
    paginator = bedrock_agent.get_paginator("list_knowledge_bases")
    for page in paginator.paginate():
        for kb in page["knowledgeBaseSummaries"]:
            if kb["name"] == name:
                return kb["knowledgeBaseId"]
    return None


def create_data_source(
    session: boto3.Session,
    kb_id: str,
    name: str,
    bucket_arn: str,
    inclusion_prefix: str,
    chunking_strategy: str,
) -> str:
    """Create a data source on a KB. Returns data source ID."""
    bedrock_agent = session.client("bedrock-agent", config=Config(read_timeout=120))

    chunking_config = {"chunkingStrategy": chunking_strategy}
    if chunking_strategy == "FIXED_SIZE":
        chunking_config["fixedSizeChunkingConfiguration"] = {
            "maxTokens": FIXED_CHUNK_MAX_TOKENS,
            "overlapPercentage": FIXED_CHUNK_OVERLAP_PCT,
        }

    try:
        resp = bedrock_agent.create_data_source(
            knowledgeBaseId=kb_id,
            name=name,
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": bucket_arn,
                    "inclusionPrefixes": [inclusion_prefix],
                },
            },
            vectorIngestionConfiguration={
                "chunkingConfiguration": chunking_config,
            },
            dataDeletionPolicy="DELETE",
        )
        ds_id = resp["dataSource"]["dataSourceId"]
        logger.info(f"  Created data source: {name} ({ds_id})")
        return ds_id
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            logger.info(f"  Data source already exists: {name}")
            existing = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
            for ds in existing["dataSourceSummaries"]:
                if ds["name"] == name:
                    return ds["dataSourceId"]
        raise


# --- Sync ---


TERMINAL_STATES = {"COMPLETE", "FAILED", "STOPPED"}


def start_ingestion(session: boto3.Session, kb_id: str, ds_id: str) -> str:
    """Start an ingestion job. Returns job ID."""
    bedrock_agent = session.client("bedrock-agent")
    resp = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    job_id = resp["ingestionJob"]["ingestionJobId"]
    logger.info(f"  Started ingestion: {job_id}")
    return job_id


def wait_for_ingestion(
    session: boto3.Session,
    kb_id: str,
    ds_id: str,
    job_id: str,
    poll_seconds: int = 15,
    timeout_seconds: int = 3600,
) -> dict:
    """Poll an ingestion job until it reaches a terminal state. Returns the job."""
    bedrock_agent = session.client("bedrock-agent")
    deadline = time.time() + timeout_seconds
    while True:
        job = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )["ingestionJob"]
        status = job["status"]
        if status in TERMINAL_STATES:
            return job
        if time.time() > deadline:
            logger.warning(f"  Timed out waiting for {job_id} (last status: {status})")
            return job
        time.sleep(poll_seconds)


def sync_all(session: boto3.Session, kb_config: dict) -> None:
    """Sync every data source, waiting for each job before starting the next.

    Bedrock allows only one ingestion job per KB at a time, so data sources on
    the same KB MUST run sequentially — start one, wait for it to finish, then
    start the next. KBs are independent, but we keep the whole pass sequential
    for simple, legible logs.
    """
    results: list[tuple[str, str, str]] = []
    for config_name, info in kb_config.items():
        logger.info(f"Syncing {config_name}...")
        for ds_name, ds_id in info["data_sources"].items():
            try:
                job_id = start_ingestion(session, info["kb_id"], ds_id)
            except ClientError as e:
                logger.error(f"  Failed to start {ds_name}: {e}")
                results.append((config_name, ds_name, "START_FAILED"))
                continue
            job = wait_for_ingestion(session, info["kb_id"], ds_id, job_id)
            stats = job.get("statistics", {})
            status = job["status"]
            indexed = stats.get("numberOfNewDocumentsIndexed", "?")
            failed = stats.get("numberOfDocumentsFailed", "?")
            log = logger.info if status == "COMPLETE" and not failed else logger.warning
            log(f"  {ds_name}: {status} (indexed={indexed}, failed={failed})")
            if status == "FAILED":
                for reason in job.get("failureReasons", [])[:3]:
                    logger.warning(f"    reason: {reason[:200]}")
            results.append((config_name, ds_name, status))

    print(f"\n{'=' * 60}\nSync summary")
    for config_name, ds_name, status in results:
        print(f"  {config_name:18s} {ds_name:10s} {status}")
    print(f"{'=' * 60}\n")


# --- Teardown ---


def _list_project_kbs(bedrock_agent) -> list[dict]:
    """Return all KB summaries whose name starts with PROJECT_PREFIX."""
    kbs = []
    for page in bedrock_agent.get_paginator("list_knowledge_bases").paginate():
        kbs.extend(
            kb for kb in page["knowledgeBaseSummaries"] if kb["name"].startswith(PROJECT_PREFIX)
        )
    return kbs


def delete_all_kbs(bedrock_agent, poll_seconds: int = 15, timeout_seconds: int = 1800) -> bool:
    """Delete every project KB and BLOCK until they are fully gone.

    Returns True if all KBs were deleted, False if it timed out with some
    remaining. KB deletion is asynchronous: delete_knowledge_base returns
    immediately while the KB sits in DELETING, and can land in
    DELETE_UNSUCCESSFUL and need re-issuing. Crucially, deleting the vector
    store/role out from under an in-flight KB delete deadlocks it (the delete
    can't clean up its vectors), so callers MUST confirm this returned True
    before removing any infrastructure the KBs depend on.
    """
    deadline = time.time() + timeout_seconds
    while True:
        kbs = _list_project_kbs(bedrock_agent)
        if not kbs:
            logger.info("All project Knowledge Bases deleted")
            return True
        for kb in kbs:
            # DELETING is in-flight; anything else (incl. DELETE_UNSUCCESSFUL) we (re)issue.
            if kb.get("status") == "DELETING":
                continue
            kb_id = kb["knowledgeBaseId"]
            try:
                ds_list = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
                for ds in ds_list["dataSourceSummaries"]:
                    logger.info(f"Deleting data source: {ds['name']}")
                    bedrock_agent.delete_data_source(
                        knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"]
                    )
            except ClientError:
                pass
            try:
                logger.info(f"Deleting KB: {kb['name']} (status {kb.get('status')})")
                bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
            except ClientError as e:
                logger.warning(f"  retry delete {kb['name']}: {e}")
        if time.time() > deadline:
            logger.warning(f"Timed out waiting for KB deletion: {[kb['name'] for kb in kbs]}")
            return False
        time.sleep(poll_seconds)


def teardown(session: boto3.Session, vector_bucket_name: str, role_name: str) -> None:
    """Delete all resources created by this script."""
    bedrock_agent = session.client("bedrock-agent")
    s3v = session.client("s3vectors")
    iam = session.client("iam")

    # Delete Knowledge Bases (and their data sources), waiting until fully gone
    # before touching the indexes/bucket they depend on. If they don't all
    # delete, STOP — removing the store now would deadlock the in-flight deletes
    # (they can no longer clean up their vectors). Re-run --teardown to retry.
    if not delete_all_kbs(bedrock_agent):
        logger.error(
            "Some KBs are not deleted yet — leaving vector store and IAM role in "
            "place to avoid deadlocking the in-flight deletes. Re-run --teardown."
        )
        return

    # Delete vector indexes
    try:
        indexes = s3v.list_indexes(vectorBucketName=vector_bucket_name)
        for idx in indexes.get("indexes", []):
            logger.info(f"Deleting vector index: {idx['indexName']}")
            s3v.delete_index(
                vectorBucketName=vector_bucket_name,
                indexName=idx["indexName"],
            )
    except ClientError:
        pass

    # Delete vector bucket
    try:
        s3v.delete_vector_bucket(vectorBucketName=vector_bucket_name)
        logger.info(f"Deleted vector bucket: {vector_bucket_name}")
    except ClientError:
        pass

    # Delete IAM role policies then role
    try:
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
        logger.info(f"Deleted IAM role: {role_name}")
    except ClientError:
        pass

    logger.info("Teardown complete")


# --- Main ---


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Bedrock Knowledge Bases for the FCA complaints bot"
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Re-sync existing KBs from config file",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="Delete all resources",
    )
    args = parser.parse_args()

    load_dotenv(override=True)

    session = get_session()
    region = session.region_name
    account_id = get_account_id(session)

    vector_bucket_name = f"{PROJECT_PREFIX}-vectors"
    role_name = f"{PROJECT_PREFIX}-kb-service-role"

    s3_bucket = os.getenv("S3_BUCKET")
    if not s3_bucket and not args.teardown:
        logger.error("S3_BUCKET not set in .env")
        raise SystemExit(1)
    data_bucket_arn = f"arn:aws:s3:::{s3_bucket}" if s3_bucket else ""

    if args.teardown:
        teardown(session, vector_bucket_name, role_name)
        return

    if args.sync_only:
        if not CONFIG_OUTPUT.exists():
            logger.error(f"Config file not found: {CONFIG_OUTPUT}")
            raise SystemExit(1)
        kb_config = json.loads(CONFIG_OUTPUT.read_text())
        sync_all(session, kb_config)
        return

    # --- Full setup ---

    # 1. S3 Vectors infrastructure
    logger.info("=== Step 1: S3 Vectors ===")
    vector_bucket_arn = create_vector_bucket(session, vector_bucket_name)

    index_arns = {}
    for suffix, _, _ in KB_CONFIGS:
        index_name = f"{PROJECT_PREFIX}-{suffix}"
        index_arns[suffix] = create_vector_index(session, vector_bucket_name, index_name)

    # 2. IAM role
    logger.info("=== Step 2: IAM Role ===")
    role_arn = create_kb_role(
        session,
        role_name,
        account_id,
        data_bucket_arn,
        vector_bucket_arn,
        list(index_arns.values()),
        region,
    )

    # 3. Knowledge Bases + data sources
    logger.info("=== Step 3: Knowledge Bases ===")
    kb_config = {}

    for suffix, chunking, embedding_model in KB_CONFIGS:
        kb_name = f"{PROJECT_PREFIX}-{suffix}"
        kb_id = create_knowledge_base(
            session,
            kb_name,
            role_arn,
            embedding_model,
            index_arns[suffix],
            region,
        )

        fca_prefix, synthetic_prefix = prefixes_for(chunking)
        fca_ds_id = create_data_source(
            session,
            kb_id,
            f"{kb_name}-fca",
            data_bucket_arn,
            fca_prefix,
            chunking,
        )
        synthetic_ds_id = create_data_source(
            session,
            kb_id,
            f"{kb_name}-synthetic",
            data_bucket_arn,
            synthetic_prefix,
            chunking,
        )

        kb_config[suffix] = {
            "kb_id": kb_id,
            "kb_name": kb_name,
            "chunking_strategy": chunking,
            "embedding_model": embedding_model,
            "index_arn": index_arns[suffix],
            "data_sources": {
                "fca": fca_ds_id,
                "synthetic": synthetic_ds_id,
            },
        }

    # 4. Save config for downstream use
    CONFIG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_OUTPUT.write_text(json.dumps(kb_config, indent=2))
    logger.info(f"Config saved to {CONFIG_OUTPUT}")

    # 5. Sync all
    logger.info("=== Step 4: Initial Sync ===")
    sync_all(session, kb_config)

    # Summary
    print(f"\n{'=' * 60}")
    print("Setup complete: 2 Knowledge Bases created")
    for suffix, info in kb_config.items():
        print(f"  {suffix:20s} -> {info['kb_id']}")
    print(f"\nConfig: {CONFIG_OUTPUT}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
