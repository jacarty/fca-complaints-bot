"""Shared Bedrock model infrastructure.

Single source of truth for which Bedrock models the project uses, how preset
names resolve to model IDs, and what each model costs. Both evaluation tracks —
the Q&A framework (judge.py, metrics.py) and the drafting evaluation
(judge_drafting.py, metrics_drafting.py) — import from here, so neither depends
on the other for this infrastructure.
"""

# ---------------------------------------------------------------------------
# Model presets
# ---------------------------------------------------------------------------

JUDGE_PRESETS = {
    "opus": "global.anthropic.claude-opus-4-6-v1",
    "haiku": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "sonnet": "global.anthropic.claude-sonnet-4-6",
    "gpt-oss": "openai.gpt-oss-120b-1:0",
}


def resolve_judge_model(name_or_id: str) -> str:
    """Resolve a preset name or full model ID to a Bedrock model ID."""
    return JUDGE_PRESETS.get(name_or_id, name_or_id)


# ---------------------------------------------------------------------------
# Pricing (on-demand, per 1k tokens)
# ---------------------------------------------------------------------------

# Approximate Bedrock on-demand pricing as of June 2026
TOKEN_PRICES = {
    # Generation model
    "global.anthropic.claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    # Judge models
    "global.anthropic.claude-opus-4-6-v1": {"input": 0.015, "output": 0.075},
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 0.001, "output": 0.005},
    "openai.gpt-oss-120b-1:0": {"input": 0.003, "output": 0.012},
    # Embedding models (per 1k tokens, used during KB sync not per-query)
    "amazon.titan-embed-text-v2:0": {"input": 0.00002, "output": 0.0},
    "cohere.embed-english-v3": {"input": 0.0001, "output": 0.0},
}
