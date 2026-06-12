"""Retrieval and generation pipeline for the RAG evaluation framework.

Takes a question, queries the appropriate Bedrock Knowledge Base, feeds retrieved
chunks to Claude Sonnet 4.6 via the converse API with structured outputs, and
returns a typed result with citations, confidence, and latency.

Usage as a library:
    from src.pipeline import query_pipeline, PipelineConfig

    result = query_pipeline(
        question="What are the FCA Principles for Business?",
        config=PipelineConfig(chunking="structure", embedding="titan"),
    )
    print(result.answer)
    print(result.citations)
"""

import json
import logging
import os
import time
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KB_CONFIG_PATH = Path("config/knowledge_bases.json")
PROMPT_PATH = Path("data/prompts/generation_system_prompt.txt")

GENERATION_MODEL = "global.anthropic.claude-sonnet-4-6"

# Map (chunking, embedding) -> KB config key
_KB_KEY_MAP = {
    ("fixed", "titan"): "fixed-titan",
    ("fixed", "cohere"): "fixed-cohere",
    ("structure", "titan"): "structure-titan",
    ("structure", "cohere"): "structure-cohere",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PipelineConfig(BaseModel):
    """Configuration for a single pipeline run."""

    chunking: str = Field(description="Chunking strategy: 'fixed' or 'structure'")
    embedding: str = Field(description="Embedding model: 'titan' or 'cohere'")
    retrieval: str = Field(
        default="SEMANTIC",
        description="Retrieval method: 'SEMANTIC' (only option for S3 Vectors)",
    )
    top_k: int = Field(default=10, description="Number of chunks to retrieve")
    generation_model: str = Field(
        default=GENERATION_MODEL,
        description="Bedrock model ID for answer generation",
    )


class RetrievedChunk(BaseModel):
    """A single chunk returned by the Bedrock KB Retrieve API."""

    chunk_id: str = Field(description="S3 URI or unique identifier from the KB")
    content: str = Field(description="Chunk text content")
    score: float = Field(description="Relevance score from retrieval")
    metadata: dict = Field(
        default_factory=dict, description="Chunk metadata (section, module, etc.)"
    )


class GenerationResponse(BaseModel):
    """Schema enforced on Claude's response via Bedrock structured outputs.

    This model's JSON schema is passed to the converse API's outputConfig.textFormat
    to constrain the model's output at the grammar level.
    """

    answer: str = Field(description="The answer to the question, grounded in retrieved context")
    citations: list[str] = Field(description="List of chunk_ids that support claims in the answer")
    confidence: float = Field(
        description="Confidence score 0.0-1.0 based on how well context supports the answer"
    )
    insufficient_context: bool = Field(
        description="True if the retrieved context does not fully answer the question"
    )


class PipelineResult(BaseModel):
    """Full result from a pipeline run, including retrieval + generation + metadata."""

    answer: str = Field(description="Generated answer")
    citations: list[str] = Field(description="Chunk IDs cited in the answer")
    confidence: float = Field(description="Model confidence score")
    insufficient_context: bool = Field(description="Whether context was insufficient")
    retrieved_chunks: list[RetrievedChunk] = Field(description="Chunks from retrieval")
    latency_ms: float = Field(description="End-to-end wall-clock time in milliseconds")
    retrieval_latency_ms: float = Field(description="Retrieval step latency in milliseconds")
    generation_latency_ms: float = Field(description="Generation step latency in milliseconds")
    config: dict = Field(description="The pipeline config used for this run")
    usage: dict = Field(default_factory=dict, description="Token usage from generation")


# ---------------------------------------------------------------------------
# KB config loader
# ---------------------------------------------------------------------------


def _load_kb_config() -> dict:
    """Load Knowledge Base configuration from config/knowledge_bases.json."""
    if not KB_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"KB config not found at {KB_CONFIG_PATH} — run setup_knowledge_bases.py first"
        )
    with open(KB_CONFIG_PATH) as f:
        return json.load(f)


def _resolve_kb_id(config: PipelineConfig) -> str:
    """Map a PipelineConfig to the correct KB ID."""
    key = (config.chunking, config.embedding)
    config_key = _KB_KEY_MAP.get(key)
    if not config_key:
        raise ValueError(
            f"Unknown config combination: chunking={config.chunking}, "
            f"embedding={config.embedding}. "
            f"Valid combinations: {list(_KB_KEY_MAP.keys())}"
        )

    kb_configs = _load_kb_config()
    if config_key not in kb_configs:
        raise ValueError(f"KB config key '{config_key}' not found in {KB_CONFIG_PATH}")

    return kb_configs[config_key]["kb_id"]


# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """Load the generation system prompt from data/prompts/."""
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"System prompt not found at {PROMPT_PATH}")
    return PROMPT_PATH.read_text().strip()


# ---------------------------------------------------------------------------
# Bedrock client helpers
# ---------------------------------------------------------------------------


def _get_agent_runtime_client(profile: str | None = None):
    """Create a bedrock-agent-runtime client for KB retrieval."""
    load_dotenv()
    session = boto3.Session(
        profile_name=profile or os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "eu-west-1"),
    )
    return session.client("bedrock-agent-runtime")


def _get_bedrock_runtime_client(profile: str | None = None):
    """Create a bedrock-runtime client for model invocation."""
    load_dotenv()
    session = boto3.Session(
        profile_name=profile or os.getenv("AWS_PROFILE"),
        region_name=os.getenv("AWS_REGION", "eu-west-1"),
    )
    return session.client(
        "bedrock-runtime",
        config=BotoConfig(read_timeout=300),
    )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def retrieve_chunks(
    client,
    kb_id: str,
    question: str,
    *,
    search_type: str = "SEMANTIC",
    top_k: int = 10,
) -> list[RetrievedChunk]:
    """Call Bedrock KB Retrieve API and return typed chunks.

    Args:
        client: bedrock-agent-runtime boto3 client
        kb_id: Knowledge Base ID
        question: User query
        search_type: "SEMANTIC" (only option for S3 Vectors)
        top_k: Number of results to return

    Returns:
        List of RetrievedChunk objects, ordered by relevance score descending.
    """
    if search_type == "HYBRID":
        raise ValueError(
            "HYBRID search is not supported on S3 Vectors-backed Knowledge Bases. "
            "Use 'SEMANTIC' instead."
        )

    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": question},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": top_k,
                "overrideSearchType": search_type,
            }
        },
    )

    chunks = []
    for r in response.get("retrievalResults", []):
        # Extract S3 URI as chunk ID
        location = r.get("location", {})
        s3_uri = location.get("s3Location", {}).get("uri", "")

        # Extract metadata — Bedrock returns it flat in the result
        metadata = {}
        for key, value in r.get("metadata", {}).items():
            # Skip internal Bedrock metadata keys
            if not key.startswith("x-amz-bedrock-kb-"):
                metadata[key] = value

        chunks.append(
            RetrievedChunk(
                chunk_id=s3_uri,
                content=r.get("content", {}).get("text", ""),
                score=r.get("score", 0.0),
                metadata=metadata,
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _build_generation_schema() -> str:
    """Build the JSON schema string for Bedrock structured outputs.

    Bedrock requires:
    - Schema passed as a JSON string (not a dict)
    - additionalProperties: false on every object
    """
    schema = GenerationResponse.model_json_schema()

    # Bedrock structured outputs requires additionalProperties: false
    # on the root object. Pydantic doesn't add this by default.
    schema["additionalProperties"] = False

    return json.dumps(schema)


def _format_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a numbered context block for the generation prompt."""
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        section = chunk.metadata.get("section", "unknown")
        blocks.append(
            f"--- Chunk {i} (chunk_id: {chunk.chunk_id}) ---\n"
            f"Section: {section}\n"
            f"Score: {chunk.score:.4f}\n\n"
            f"{chunk.content}"
        )
    return "\n\n".join(blocks)


def generate_answer(
    client,
    question: str,
    chunks: list[RetrievedChunk],
    *,
    model_id: str = GENERATION_MODEL,
    system_prompt: str | None = None,
) -> tuple[GenerationResponse, dict]:
    """Generate a grounded answer from retrieved chunks using Claude.

    Uses Bedrock structured outputs (outputConfig.textFormat) to enforce
    the GenerationResponse schema on Claude's output.

    Args:
        client: bedrock-runtime boto3 client
        question: User question
        chunks: Retrieved chunks to ground the answer in
        model_id: Bedrock model ID
        system_prompt: Override system prompt (uses default if None)

    Returns:
        Tuple of (GenerationResponse, usage_dict)
    """
    if system_prompt is None:
        system_prompt = _load_system_prompt()

    context = _format_context(chunks)

    user_message = (
        f"Retrieved context:\n\n{context}\n\n"
        f"---\n\n"
        f"Question: {question}\n\n"
        f"Answer the question using only the retrieved context above."
    )

    response = client.converse(
        modelId=model_id,
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        inferenceConfig={
            "maxTokens": 4096,
            "temperature": 0.1,
        },
        outputConfig={
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": _build_generation_schema(),
                        "name": "rag_generation_response",
                        "description": "Structured answer with citations and confidence",
                    }
                },
            }
        },
    )

    # Parse the structured response
    raw_text = response["output"]["message"]["content"][0]["text"]
    generation = GenerationResponse.model_validate_json(raw_text)

    # Extract usage metadata
    usage = {}
    if "usage" in response:
        usage = {
            "input_tokens": response["usage"].get("inputTokens", 0),
            "output_tokens": response["usage"].get("outputTokens", 0),
            "total_tokens": response["usage"].get("totalTokens", 0),
        }

    return generation, usage


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def query_pipeline(
    question: str,
    *,
    config: PipelineConfig | None = None,
    chunking: str = "structure",
    embedding: str = "titan",
    retrieval: str = "SEMANTIC",
    top_k: int = 10,
    profile: str | None = None,
) -> PipelineResult:
    """Run the full retrieval + generation pipeline.

    Can be called with a PipelineConfig object or with individual parameters:

        # Option 1: PipelineConfig
        result = query_pipeline(
        "question", config=PipelineConfig(chunking="fixed", embedding="cohere")
        )

        # Option 2: keyword arguments
        result = query_pipeline("question", chunking="fixed", embedding="cohere")

    Args:
        question: The user question to answer
        config: Pipeline configuration (overrides individual params if provided)
        chunking: Chunking strategy ("fixed" or "structure")
        embedding: Embedding model ("titan" or "cohere")
        retrieval: Retrieval method ("SEMANTIC" only for S3 Vectors)
        top_k: Number of chunks to retrieve
        profile: AWS profile name (overrides AWS_PROFILE env var)

    Returns:
        PipelineResult with answer, citations, chunks, latency, and usage metadata.
    """
    load_dotenv()

    if config is None:
        config = PipelineConfig(
            chunking=chunking,
            embedding=embedding,
            retrieval=retrieval,
            top_k=top_k,
        )

    start_time = time.perf_counter()

    # Resolve KB ID
    kb_id = _resolve_kb_id(config)
    logger.info(
        "Pipeline: %s-%s, KB=%s, top_k=%d",
        config.chunking,
        config.embedding,
        kb_id,
        config.top_k,
    )

    # --- Retrieval ---
    retrieval_start = time.perf_counter()
    agent_client = _get_agent_runtime_client(profile)
    chunks = retrieve_chunks(
        agent_client,
        kb_id,
        question,
        search_type=config.retrieval,
        top_k=config.top_k,
    )
    retrieval_ms = (time.perf_counter() - retrieval_start) * 1000
    logger.info("Retrieved %d chunks in %.0fms", len(chunks), retrieval_ms)

    # --- Generation ---
    generation_start = time.perf_counter()
    bedrock_client = _get_bedrock_runtime_client(profile)
    generation, usage = generate_answer(
        bedrock_client,
        question,
        chunks,
        model_id=config.generation_model,
    )
    generation_ms = (time.perf_counter() - generation_start) * 1000
    logger.info(
        "Generated answer in %.0fms (confidence=%.2f)", generation_ms, generation.confidence
    )

    total_ms = (time.perf_counter() - start_time) * 1000

    return PipelineResult(
        answer=generation.answer,
        citations=generation.citations,
        confidence=generation.confidence,
        insufficient_context=generation.insufficient_context,
        retrieved_chunks=chunks,
        latency_ms=total_ms,
        retrieval_latency_ms=retrieval_ms,
        generation_latency_ms=generation_ms,
        config=config.model_dump(),
        usage=usage,
    )
