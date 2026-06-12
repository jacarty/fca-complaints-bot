# ADR-003: Dual LLM-as-Judge with Claim-Level Scoring

## Status
Accepted

## Context
RAG evaluation requires assessing whether generated answers are faithful to the retrieved context. Traditional metrics (BLEU, ROUGE) measure surface-level text overlap and miss semantic faithfulness. Holistic LLM scoring (e.g., "rate this answer 1-5") suffers from score clustering around 3-4. The project needed a faithfulness metric that is granular, interpretable, and resistant to self-enhancement bias.

## Decision
Use claim-level decomposition with dual judges from different model families.

### Claim-level scoring
Each judge decomposes the generated answer into individual factual claims, then classifies each as:
- **GROUNDED** — directly supported by a retrieved chunk
- **PARTIALLY_GROUNDED** — related chunk exists but claim extends beyond it
- **UNGROUNDED** — no supporting chunk (hallucinated)

This produces a percentage breakdown (e.g., "87% grounded, 10% partially grounded, 3% ungrounded") rather than a single holistic score.

### Dual judges
- **Primary:** Claude Haiku 4.5 (fast, cheap, same-family as generator)
- **Secondary:** GPT-oss-120b (different model family, reasoning model)

Inter-judge agreement is computed at the claim level — the fraction of claims where both judges assign the same grounding classification.

## Rationale
**Claim-level over holistic:** "87% of claims grounded" is more actionable than "3.8/5". It tells you exactly how much of the answer is supported and lets you drill into which claims are problematic.

**Dual judges over single:** Using Claude to judge Claude's output risks self-enhancement bias. A cross-provider judge (OpenAI via Bedrock) provides an independent check. The inter-judge agreement metric (~77%) makes this transparent — where the judges diverge is typically the GROUNDED/PARTIALLY_GROUNDED boundary, not wholesale disagreement.

**Haiku over Opus as primary:** The full eval runs 312 judge calls (78 questions × 4 configs). Opus at $0.015/$0.075 per 1k tokens would cost ~$50 for judge calls alone. Haiku at $0.001/$0.005 costs ~$3. The 2-question calibration test showed Haiku and Opus produce identical results on well-grounded answers, with Haiku slightly more lenient on edge cases. The cost difference justifies Haiku as the default, with Opus available via `--primary-judge opus`.

## Consequences
- Judge model is configurable via CLI (`--primary-judge haiku|opus|sonnet|gpt-oss`). The framework is not locked to any specific judge.
- Claim decomposition is non-deterministic — the same answer may be split into different numbers of claims across runs. The inter-judge agreement metric accounts for this by comparing up to the shorter claim list.
- GPT-oss-120b is a reasoning model that returns `reasoningContent` blocks instead of plain `text`. The judge response parser handles both formats.
