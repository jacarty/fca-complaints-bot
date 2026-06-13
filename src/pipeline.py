"""Retrieval and drafting pipeline for the FCA complaints handling bot.

Takes a complaint scenario (and any prior conversation turns), queries the
structure-titan Bedrock Knowledge Base, unions the newly retrieved provisions
with those already in scope for the conversation, and feeds them to Claude
Sonnet 4.6 via the converse API with structured outputs. Returns a typed result:
a drafted, compliant response, the specific provisions it cites, a human-review
flag, and latency/usage metadata.

Usage as a library:
    from src.pipeline import draft_pipeline, ConversationTurn

    result = draft_pipeline(
        "A customer says we took six weeks to respond and never mentioned the FOS.",
    )
    print(result.drafted_response)
    for p in result.cited_provisions:
        print(p.provision, p.provision_type)

    # Follow-up, carrying state forward:
    result2 = draft_pipeline(
        "What's the time limit for this type of complaint?",
        history=[
            ConversationTurn(role="user", content="<the scenario above>"),
            ConversationTurn(role="assistant", content=result.drafted_response),
        ],
        prior_chunks=result.retrieved_chunks,
    )
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

import boto3
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KB_CONFIG_PATH = Path("config/knowledge_bases.json")
PROMPT_PATH = Path("data/prompts/drafting_system_prompt.txt")

GENERATION_MODEL = "global.anthropic.claude-sonnet-4-6"

# Cap on how many provisions ride in the context block for a single turn, after
# unioning the current turn's retrieval with those already in scope for the
# conversation. Keeps the prompt bounded across a long follow-up thread.
MAX_CONTEXT_CHUNKS = 15

# Map (chunking, embedding) -> KB config key. Titan only — the Cohere configs
# from the eval framework are not built in this project.
_KB_KEY_MAP = {
    ("fixed", "titan"): "fixed-titan",
    ("structure", "titan"): "structure-titan",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PipelineConfig(BaseModel):
    """Configuration for a single pipeline run."""

    chunking: str = Field(
        default="structure", description="Chunking strategy: 'structure' or 'fixed'"
    )
    embedding: str = Field(default="titan", description="Embedding model: 'titan'")
    retrieval: str = Field(
        default="SEMANTIC",
        description="Retrieval method: 'SEMANTIC' (only option for S3 Vectors)",
    )
    top_k: int = Field(default=10, description="Number of chunks to retrieve per turn")
    max_context_chunks: int = Field(
        default=MAX_CONTEXT_CHUNKS,
        description="Cap on provisions in the context block after unioning across turns",
    )
    generation_model: str = Field(
        default=GENERATION_MODEL, description="Bedrock model ID for drafting"
    )


class ConversationTurn(BaseModel):
    """One prior turn in the conversation, replayed into the messages array.

    Assistant turns hold the previously drafted response text (not the full JSON).
    History must alternate roles and end on an assistant turn so that appending
    the new user turn keeps the converse messages array valid.
    """

    role: Literal["user", "assistant"]
    content: str


class RetrievedChunk(BaseModel):
    """A single chunk returned by the Bedrock KB Retrieve API."""

    chunk_id: str = Field(description="S3 URI or unique identifier from the KB")
    content: str = Field(description="Chunk text content")
    score: float = Field(description="Relevance score from retrieval")
    metadata: dict = Field(
        default_factory=dict, description="Chunk metadata (section, module, etc.)"
    )


class CitedProvision(BaseModel):
    """A specific FCA provision the draft relies on."""

    provision: str = Field(description="The exact provision reference, e.g. 'DISP 1.6.2R'")
    provision_type: Literal["Rule", "Guidance", "Evidential"] = Field(
        description="Rule = binding requirement; Guidance = non-binding; Evidential = evidential"
    )
    relevance: str = Field(description="One sentence on why this provision applies")
    chunk_id: str = Field(description="S3 URI of the retrieved chunk this provision came from")


class ComplaintResponse(BaseModel):
    """Schema enforced on Claude's drafting output via Bedrock structured outputs."""

    handler_answer: str = Field(
        description="Reply to the complaints handler: the regulatory position, recommended action, and any assumptions or uncertainties to confirm. Always populated."
    )
    customer_draft: str = Field(
        description="Text to send to the customer, in compliant language. Empty string if the handler's message is a question that does not call for new customer-facing text."
    )
    cited_provisions: list[CitedProvision] = Field(description="The specific FCA provisions relied on")
    human_review_required: bool = Field(description="True if a human must review before sending")
    human_review_reason: str = Field(description="Why review is needed; empty string if not required")
    insufficient_context: bool = Field(description="True if retrieved provisions do not adequately cover the complaint")


class PipelineResult(BaseModel):
    """Full result from a pipeline run, including retrieval + drafting + metadata."""

    handler_answer: str = Field(description="Reply to the handler: position, action, caveats")
    customer_draft: str = Field(description="Customer-facing draft text; empty if not applicable")
    cited_provisions: list[CitedProvision] = Field(description="Provisions the draft relies on")
    human_review_required: bool = Field(description="Whether a human must review before sending")
    human_review_reason: str = Field(description="Reason for human review; empty if not required")
    insufficient_context: bool = Field(description="Whether retrieved context was insufficient")
    retrieved_chunks: list[RetrievedChunk] = Field(
        description="Provisions in scope for this turn (current retrieval unioned with prior)"
    )
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
            f"embedding={config.embedding}. Valid combinations: {list(_KB_KEY_MAP.keys())}"
        )

    kb_configs = _load_kb_config()
    if config_key not in kb_configs:
        raise ValueError(f"KB config key '{config_key}' not found in {KB_CONFIG_PATH}")

    return kb_configs[config_key]["kb_id"]


# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------


def _load_system_prompt() -> str:
    """Load the drafting system prompt from data/prompts/."""
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
    return session.client("bedrock-runtime", config=BotoConfig(read_timeout=300))


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

    Returns chunks ordered by relevance score descending.
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
        location = r.get("location", {})
        s3_uri = location.get("s3Location", {}).get("uri", "")

        metadata = {}
        for key, value in r.get("metadata", {}).items():
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


def _merge_chunks(
    prior: list[RetrievedChunk] | None,
    new: list[RetrievedChunk],
    cap: int,
) -> list[RetrievedChunk]:
    """Union prior and newly retrieved chunks, deduped by chunk_id.

    Keeps the highest score seen for each chunk, sorts by score descending, and
    caps the total so a long follow-up thread doesn't grow the context block
    without bound. This is what keeps the original scenario's provisions in
    scope when a later follow-up ("the time limit for this?") would not retrieve
    them on its own.
    """
    by_id: dict[str, RetrievedChunk] = {}
    for chunk in (prior or []) + new:
        existing = by_id.get(chunk.chunk_id)
        if existing is None or chunk.score > existing.score:
            by_id[chunk.chunk_id] = chunk
    merged = sorted(by_id.values(), key=lambda c: c.score, reverse=True)
    return merged[:cap]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _set_additional_properties_false(node: dict) -> None:
    """Recursively set additionalProperties: false on every object node.

    Bedrock structured outputs rejects any object in the schema without it.
    Pydantic emits nested models (CitedProvision) under $defs and does not add
    it, so we walk the whole tree: properties, array items, and $defs.
    """
    if node.get("type") == "object":
        node["additionalProperties"] = False
        for prop in node.get("properties", {}).values():
            _set_additional_properties_false(prop)
    if "items" in node:
        _set_additional_properties_false(node["items"])
    for definition in node.get("$defs", {}).values():
        _set_additional_properties_false(definition)


def _build_generation_schema() -> str:
    """Build the JSON schema string for Bedrock structured outputs."""
    schema = ComplaintResponse.model_json_schema()
    _set_additional_properties_false(schema)
    return json.dumps(schema)


def _format_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a numbered context block for the prompt."""
    blocks = []
    for i, chunk in enumerate(chunks, 1):
        section = chunk.metadata.get("section", "unknown")
        blocks.append(
            f"--- Provision {i} (chunk_id: {chunk.chunk_id}) ---\n"
            f"Section: {section}\n"
            f"Score: {chunk.score:.4f}\n\n"
            f"{chunk.content}"
        )
    return "\n\n".join(blocks)


def generate_draft(
    client,
    question: str,
    chunks: list[RetrievedChunk],
    *,
    history: list[ConversationTurn] | None = None,
    model_id: str = GENERATION_MODEL,
    system_prompt: str | None = None,
) -> tuple[ComplaintResponse, dict]:
    """Draft a complaint response from retrieved provisions using Claude.

    Uses Bedrock structured outputs (outputConfig.textFormat) to enforce the
    ComplaintResponse schema. Prior turns are replayed as alternating user/
    assistant messages; the current turn carries the retrieved-provisions block
    plus the handler's latest message.

    Returns (ComplaintResponse, usage_dict).
    """
    if system_prompt is None:
        system_prompt = _load_system_prompt()

    messages: list[dict] = []
    for turn in history or []:
        messages.append({"role": turn.role, "content": [{"text": turn.content}]})

    context = _format_context(chunks)
    user_message = (
        f"Retrieved FCA provisions:\n\n{context}\n\n"
        f"---\n\n"
        f"Handler's message:\n{question}\n\n"
        f"Using only the retrieved provisions above, draft or update the response "
        f"following your instructions. Record every provision you rely on — with its "
        f"exact reference, type, and chunk_id — in cited_provisions."
    )
    messages.append({"role": "user", "content": [{"text": user_message}]})

    response = client.converse(
        modelId=model_id,
        system=[{"text": system_prompt}],
        messages=messages,
        inferenceConfig={"maxTokens": 4096, "temperature": 0.1},
        outputConfig={
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": _build_generation_schema(),
                        "name": "complaint_response",
                        "description": "Drafted complaint response with cited provisions",
                    }
                },
            }
        },
    )

    raw_text = response["output"]["message"]["content"][0]["text"]
    draft = ComplaintResponse.model_validate_json(raw_text)

    usage = {}
    if "usage" in response:
        usage = {
            "input_tokens": response["usage"].get("inputTokens", 0),
            "output_tokens": response["usage"].get("outputTokens", 0),
            "total_tokens": response["usage"].get("totalTokens", 0),
        }

    return draft, usage


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


def draft_pipeline(
    question: str,
    *,
    config: PipelineConfig | None = None,
    chunking: str = "structure",
    embedding: str = "titan",
    retrieval: str = "SEMANTIC",
    top_k: int = 10,
    history: list[ConversationTurn] | None = None,
    prior_chunks: list[RetrievedChunk] | None = None,
    profile: str | None = None,
) -> PipelineResult:
    """Run the full retrieval + drafting pipeline for one conversation turn.

    Args:
        question: The handler's latest message (scenario or follow-up).
        config: Pipeline configuration (overrides individual params if provided).
        chunking/embedding/retrieval/top_k: Config shortcuts if no config passed.
        history: Prior conversation turns, alternating and ending on an assistant
            turn (the caller is responsible for appending each drafted response).
        prior_chunks: Provisions already in scope for this conversation; unioned
            with this turn's retrieval. Pass result.retrieved_chunks back in.
        profile: AWS profile name (overrides AWS_PROFILE env var).

    Returns:
        PipelineResult. retrieved_chunks is the unioned, capped set in scope for
        this turn — store it and pass it back as prior_chunks on the next turn.
    """
    load_dotenv()

    if config is None:
        config = PipelineConfig(
            chunking=chunking, embedding=embedding, retrieval=retrieval, top_k=top_k
        )

    start_time = time.perf_counter()

    kb_id = _resolve_kb_id(config)
    logger.info(
        "Pipeline: %s-%s, KB=%s, top_k=%d", config.chunking, config.embedding, kb_id, config.top_k
    )

    # --- Retrieval (current turn) unioned with provisions already in scope ---
    retrieval_start = time.perf_counter()
    agent_client = _get_agent_runtime_client(profile)
    new_chunks = retrieve_chunks(
        agent_client, kb_id, question, search_type=config.retrieval, top_k=config.top_k
    )
    chunks = _merge_chunks(prior_chunks, new_chunks, config.max_context_chunks)
    retrieval_ms = (time.perf_counter() - retrieval_start) * 1000
    logger.info(
        "Retrieved %d new, %d in scope after union (%.0fms)",
        len(new_chunks),
        len(chunks),
        retrieval_ms,
    )

    # --- Generation ---
    generation_start = time.perf_counter()
    bedrock_client = _get_bedrock_runtime_client(profile)
    draft, usage = generate_draft(
        bedrock_client, question, chunks, history=history, model_id=config.generation_model
    )
    generation_ms = (time.perf_counter() - generation_start) * 1000
    logger.info(
        "Drafted response in %.0fms (human_review=%s, insufficient_context=%s)",
        generation_ms,
        draft.human_review_required,
        draft.insufficient_context,
    )

    total_ms = (time.perf_counter() - start_time) * 1000

    return PipelineResult(
        handler_answer=draft.handler_answer,
        customer_draft=draft.customer_draft,
        cited_provisions=draft.cited_provisions,
        human_review_required=draft.human_review_required,
        human_review_reason=draft.human_review_reason,
        insufficient_context=draft.insufficient_context,
        retrieved_chunks=chunks,
        latency_ms=total_ms,
        retrieval_latency_ms=retrieval_ms,
        generation_latency_ms=generation_ms,
        config=config.model_dump(),
        usage=usage,
    )
