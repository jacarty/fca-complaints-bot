"""LLM-as-judge module for claim-level faithfulness evaluation.

Supports configurable judge models via presets or full model IDs:
- opus: Claude Opus 4.6 via Bedrock (highest quality, most expensive)
- haiku: Claude Haiku 4.5 via Bedrock (fast, cheap, good enough for most evals)
- gpt-oss: GPT-oss-120b via Bedrock converse API (reasoning model, cross-provider)

Each judge decomposes the answer into individual claims and classifies each as:
- GROUNDED: traceable to a specific retrieved chunk
- PARTIALLY_GROUNDED: related chunk exists but claim extends beyond it
- UNGROUNDED: no supporting chunk in the retrieved context
"""

import json
import logging
import time

from pydantic import BaseModel, Field

# Model presets and resolution live in the shared module; re-exported here so
# existing callers (run_eval.py) can keep importing them from src.judge.
from src.bedrock_common import JUDGE_PRESETS, resolve_judge_model

logger = logging.getLogger(__name__)

# Defaults
JUDGE_PRIMARY = JUDGE_PRESETS["opus"]
JUDGE_SECONDARY = JUDGE_PRESETS["gpt-oss"]


class Claim(BaseModel):
    """A single factual claim extracted from an answer."""

    claim: str = Field(description="The factual claim text")
    grounding: str = Field(description="GROUNDED, PARTIALLY_GROUNDED, or UNGROUNDED")
    supporting_chunk_id: str | None = Field(
        default=None,
        description="chunk_id of the supporting chunk, if grounded",
    )
    reasoning: str = Field(description="Why this grounding classification was chosen")


class JudgeResult(BaseModel):
    """Result from a single judge evaluation."""

    claims: list[Claim] = Field(description="All claims with grounding status")
    judge_model: str = Field(description="Model ID of the judge")
    latency_ms: float = Field(description="Judge call latency")
    usage: dict = Field(default_factory=dict, description="Token usage")

    @property
    def grounded_pct(self) -> float:
        if not self.claims:
            return 0.0
        return sum(1 for c in self.claims if c.grounding == "GROUNDED") / len(self.claims)

    @property
    def partially_grounded_pct(self) -> float:
        if not self.claims:
            return 0.0
        return sum(1 for c in self.claims if c.grounding == "PARTIALLY_GROUNDED") / len(self.claims)

    @property
    def ungrounded_pct(self) -> float:
        if not self.claims:
            return 0.0
        return sum(1 for c in self.claims if c.grounding == "UNGROUNDED") / len(self.claims)

    @property
    def hallucination_rate(self) -> float:
        """Percentage of claims that are ungrounded (hallucinated)."""
        return self.ungrounded_pct


# ---------------------------------------------------------------------------
# Judge prompt
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator assessing whether an AI-generated answer is faithfully grounded in the retrieved context chunks.

Your task:
1. Decompose the ANSWER into individual factual claims. A claim is a single assertion of fact.
2. For each claim, classify its grounding status:
   - GROUNDED: The claim is directly and fully supported by one or more retrieved chunks. Cite the supporting chunk_id.
   - PARTIALLY_GROUNDED: A related chunk exists but the claim goes beyond what the chunk states (e.g. adds specificity, draws inferences, or combines information in ways not directly supported).
   - UNGROUNDED: No retrieved chunk supports this claim. The claim may be fabricated, hallucinated, or drawn from knowledge outside the provided context.
3. Provide brief reasoning for each classification.

Rules:
- Be strict. A claim is only GROUNDED if the chunk *directly* states or clearly implies it.
- Procedural/formatting claims (e.g. "the answer lists 12 principles") are GROUNDED if the context supports the underlying content.
- Citations to specific regulatory provisions (e.g. "SYSC 10.1.3R") are GROUNDED only if that provision appears in a retrieved chunk.
- Do not give benefit of the doubt — if the chunk doesn't clearly support the claim, classify as PARTIALLY_GROUNDED or UNGROUNDED.

Respond ONLY with a JSON array of claim objects. No preamble, no markdown fences."""


def _build_judge_user_message(
    question: str,
    answer: str,
    chunks: list[dict],
) -> str:
    """Build the user message for the judge prompt."""
    context_blocks = []
    for i, chunk in enumerate(chunks, 1):
        section = chunk.get("metadata", {}).get("section", "unknown")
        context_blocks.append(
            f"--- Chunk {i} (chunk_id: {chunk['chunk_id']}) ---\n"
            f"Section: {section}\n\n"
            f"{chunk['content']}"
        )
    context = "\n\n".join(context_blocks)

    return (
        f"QUESTION:\n{question}\n\n"
        f"ANSWER:\n{answer}\n\n"
        f"RETRIEVED CONTEXT:\n\n{context}\n\n"
        f"---\n\n"
        f"Decompose the ANSWER into individual factual claims and classify each "
        f"claim's grounding status. Respond with a JSON array of objects, each with "
        f"keys: claim, grounding, supporting_chunk_id, reasoning."
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_text_from_converse(response: dict) -> str:
    """Extract text from a Bedrock converse response, handling both
    standard text blocks and reasoning model (reasoningContent) blocks.
    """
    content = response["output"]["message"]["content"]
    parts = []
    for block in content:
        if block.get("type") == "text" or "text" in block:
            parts.append(block.get("text", ""))
        elif "reasoningContent" in block:
            rc = block["reasoningContent"]
            if "reasoningText" in rc:
                continue
    if not parts:
        for block in content:
            if "reasoningContent" in block:
                rc = block["reasoningContent"]
                if "reasoningText" in rc and "text" in rc["reasoningText"]:
                    parts.append(rc["reasoningText"]["text"])
    if not parts:
        for block in content:
            if isinstance(block, dict):
                for _key, val in block.items():
                    if isinstance(val, str) and val.strip().startswith("["):
                        parts.append(val)
                        break
    return "\n".join(parts)


def _parse_claims(raw_text: str) -> list[Claim]:
    """Parse the judge's JSON response into Claim objects."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()

    try:
        claims_data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                claims_data = json.loads(text[start:end])
            except json.JSONDecodeError:
                logger.error("Failed to parse judge response as JSON: %s", text[:200])
                return []
        else:
            logger.error("No JSON array found in judge response: %s", text[:200])
            return []

    claims = []
    for item in claims_data:
        grounding = item.get("grounding", "UNGROUNDED").upper().replace(" ", "_")
        if grounding not in ("GROUNDED", "PARTIALLY_GROUNDED", "UNGROUNDED"):
            grounding = "UNGROUNDED"

        chunk_id = item.get("supporting_chunk_id")
        if isinstance(chunk_id, list):
            chunk_id = chunk_id[0] if chunk_id else None
        elif not isinstance(chunk_id, (str, type(None))):
            chunk_id = str(chunk_id)

        claims.append(
            Claim(
                claim=item.get("claim", ""),
                grounding=grounding,
                supporting_chunk_id=chunk_id,
                reasoning=item.get("reasoning", ""),
            )
        )
    return claims


# ---------------------------------------------------------------------------
# Judge invocation
# ---------------------------------------------------------------------------


def run_judge(
    client,
    question: str,
    answer: str,
    chunks: list[dict],
    *,
    model_id: str = JUDGE_PRIMARY,
) -> JudgeResult:
    """Run a single judge evaluation.

    Args:
        client: bedrock-runtime boto3 client
        question: The original question
        answer: The generated answer to evaluate
        chunks: List of retrieved chunk dicts (chunk_id, content, metadata)
        model_id: Judge model ID or preset name ('opus', 'haiku', 'sonnet', 'gpt-oss')

    Returns:
        JudgeResult with per-claim grounding classifications.
    """
    model_id = resolve_judge_model(model_id)
    user_message = _build_judge_user_message(question, answer, chunks)

    start = time.perf_counter()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": JUDGE_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 8192, "temperature": 0.0},
        )
    except Exception as e:
        logger.error("Judge call failed for model %s: %s", model_id, e)
        return JudgeResult(
            claims=[],
            judge_model=model_id,
            latency_ms=(time.perf_counter() - start) * 1000,
            usage={},
        )

    latency_ms = (time.perf_counter() - start) * 1000

    raw_text = _extract_text_from_converse(response)
    claims = _parse_claims(raw_text)

    usage = {}
    if "usage" in response:
        usage = {
            "input_tokens": response["usage"].get("inputTokens", 0),
            "output_tokens": response["usage"].get("outputTokens", 0),
            "total_tokens": response["usage"].get("totalTokens", 0),
        }

    logger.info(
        "Judge %s: %d claims (%.0f%% grounded, %.0f%% ungrounded) in %.0fms",
        model_id.split(".")[-1],
        len(claims),
        sum(1 for c in claims if c.grounding == "GROUNDED") / max(len(claims), 1) * 100,
        sum(1 for c in claims if c.grounding == "UNGROUNDED") / max(len(claims), 1) * 100,
        latency_ms,
    )

    return JudgeResult(
        claims=claims,
        judge_model=model_id,
        latency_ms=latency_ms,
        usage=usage,
    )


def run_dual_judges(
    client,
    question: str,
    answer: str,
    chunks: list[dict],
    *,
    primary_model: str = JUDGE_PRIMARY,
    secondary_model: str = JUDGE_SECONDARY,
) -> tuple[JudgeResult, JudgeResult]:
    """Run both primary and secondary judges.

    Args:
        client: bedrock-runtime boto3 client
        question: The original question
        answer: The generated answer to evaluate
        chunks: List of retrieved chunk dicts
        primary_model: Primary judge model ID or preset name
        secondary_model: Secondary judge model ID or preset name

    Returns:
        Tuple of (primary_result, secondary_result)
    """
    primary = run_judge(client, question, answer, chunks, model_id=primary_model)
    secondary = run_judge(client, question, answer, chunks, model_id=secondary_model)
    return primary, secondary
