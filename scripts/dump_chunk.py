"""One-off diagnostic (JAM-275): dump a few retrieved chunks for a given chunking
strategy, to see the provision-reference format in the chunk text and confirm
whether provision headers survive that strategy.

Run from the repo root, passing the chunking strategy (default: structure):

    uv run python scripts/dump_chunk.py fixed
    uv run python scripts/dump_chunk.py structure
"""

import json
import sys

from src.pipeline import (
    PipelineConfig,
    _get_agent_runtime_client,
    _resolve_kb_id,
    retrieve_chunks,
)

# A DISP-heavy query (final response + FOS referral) to surface DISP sections.
QUERY = (
    "We took six weeks to respond to the customer's complaint and did not tell "
    "them about their right to refer it to the Financial Ombudsman Service."
)

chunking = sys.argv[1] if len(sys.argv) > 1 else "structure"
if chunking not in ("fixed", "structure"):
    sys.exit(f"chunking must be 'fixed' or 'structure', got {chunking!r}")

config = PipelineConfig(chunking=chunking, embedding="titan")
kb_id = _resolve_kb_id(config)
print(f"{chunking}-titan KB: {kb_id}\nquery: {QUERY}\n")

client = _get_agent_runtime_client()
chunks = retrieve_chunks(client, kb_id, QUERY, top_k=3)

for i, c in enumerate(chunks):
    print(f"\n===== CHUNK {i} (score {c.score:.4f}) =====")
    print(f"chunk_id: {c.chunk_id}")
    print(f"metadata: {json.dumps(c.metadata, indent=2)}")
    print("--- content ---")
    print(c.content)
    print("--- end content ---")