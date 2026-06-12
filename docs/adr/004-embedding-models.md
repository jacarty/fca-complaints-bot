# ADR-004: Titan vs Cohere Embedding Models

## Status
Accepted

## Context
Bedrock Knowledge Bases support multiple embedding models. The two most common for English text are Amazon Titan Text Embeddings V2 and Cohere Embed English v3. The project tests both to quantify their impact on retrieval quality.

## Decision
Include both Titan and Cohere as embedding dimensions in the evaluation matrix, creating two KBs per chunking strategy (4 KBs total).

## Comparison

| Attribute | Titan Embeddings V2 | Cohere Embed English v3 |
|-----------|-------------------|----------------------|
| Dimensions | 1,024 (configurable) | 1,024 |
| Max input | 8,192 tokens | 512 tokens |
| Cost (per 1k tokens) | $0.00002 | $0.0001 |
| Input limit effect | No practical constraint on chunk size | Binding constraint — chunks must be ≤480 tokens |

## Results
Titan slightly outperforms Cohere on retrieval precision within each chunking strategy (38.2% vs 33.1% for structure-aware, 13.5% vs 12.1% for fixed). The gap is small relative to the chunking effect (3× difference). Faithfulness and recall metrics are comparable.

## Consequences
- Cohere's 512-token hard limit is the binding constraint on structure-aware chunk size. The dual character/token cap in `src/chunking/structure.py` exists because of Cohere.
- Titan's larger input window means fixed-size chunks with Titan could be made larger without hitting a limit, but we kept chunk sizes consistent across embedding models for a fair comparison.
- At 5× higher per-token cost, Cohere does not justify its price for this corpus. For other domains (e.g., multilingual, code), the relative performance may differ.
