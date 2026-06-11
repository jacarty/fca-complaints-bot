# ADR-002: Structure-Aware Chunking Strategy

## Status
Accepted

## Context
The FCA Handbook has explicit hierarchical structure: modules → chapters → sections → provisions. Fixed-size chunking (the Bedrock default) splits documents into equal-length windows with overlap, which can break a regulatory provision across two chunks or merge unrelated provisions into one. The project needed an alternative strategy that preserves document structure for comparison.

## Decision
Implement a custom structure-aware chunker (`src/chunking/structure.py`) that splits FCA sections at provision boundaries, packing consecutive provisions into chunks up to a dual size cap.

## Design
Each chunk contains one or more complete provisions from the same section, constrained by:
- **Character cap: ≤2,000 characters** — keeps chunks within Bedrock's ingestion limits
- **Token cap: ≤480 Cohere tokens** — Cohere Embed English v3 hard-fails inputs over 512 tokens (with ~6% safety margin)

Token counting uses a vendored Cohere tokenizer (`src/chunking/cohere_tokenizer.json`) to avoid a runtime dependency on the Cohere SDK. The dual cap is evaluated at packing time: a provision is added to the current chunk only if both caps are satisfied.

Chunk filenames follow the pattern `{section_id}-{chunk_index:03d}.md` (e.g., `sysc10s1-000.md`, `sysc10s1-001.md`). Each chunk has a metadata sidecar (`.md.metadata.json`) inheriting the parent section's metadata plus a `chunk_index` field.

## Rationale
The fixed-size baseline is a strawman — it's what you get by default with Bedrock's `FIXED_SIZE` chunking strategy. The structure-aware alternative tests whether aligning chunks to document structure improves retrieval precision. The eval results confirmed a 3× improvement (38.2% vs 13.5% precision@k with Titan embeddings).

## Consequences
- Structure-aware chunks require pre-processing before ingestion (the `convert_fca_to_sections.py` and `structure.py` pipeline). Fixed-size chunks are handled server-side by Bedrock.
- The Cohere token cap (480) is the binding constraint, not the character cap (2,000). Regulatory text averages ~4.2 characters per Cohere token.
- The chunker is FCA-specific in its provision-detection regex but the packing logic is domain-agnostic.
