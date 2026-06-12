"""Unit tests for the structure-aware splitter (src/chunking/structure.py).

The invariant that matters for ingestion: no emitted chunk may exceed Cohere's
512-token limit, because Bedrock invokes Cohere with no truncation and an
over-length text fails the ingestion record. Counts use Cohere's own tokenizer.
"""

from src.chunking import structure
from src.chunking.structure import count_tokens, pack_units


def _all_within(chunks, max_tokens, max_chars):
    return all(
        count_tokens(c["text"]) <= max_tokens and len(c["text"]) <= max_chars for c in chunks
    )


def test_small_units_pack_into_one_chunk():
    units = [("a", "alpha"), ("b", "bravo"), ("c", "charlie")]
    chunks = pack_units(units, header="# Heading", max_tokens=100, max_chars=500)
    assert len(chunks) == 1
    assert chunks[0]["labels"] == ["a", "b", "c"]
    assert chunks[0]["text"].startswith("# Heading")


def test_packing_respects_token_budget():
    # Several sentence-sized units with a tight token budget must span chunks.
    units = [(str(i), f"Sentence number {i} with a little extra text here.") for i in range(8)]
    chunks = pack_units(units, max_tokens=20, max_chars=2000)
    assert len(chunks) > 1
    assert _all_within(chunks, 20, 2000)


def test_oversized_single_unit_is_split():
    body = "\n\n".join("This is a sentence. " * 8 for _ in range(8))
    chunks = pack_units([("big", body)], max_tokens=30, max_chars=2000)
    assert len(chunks) > 1
    assert _all_within(chunks, 30, 2000)
    # Every fragment of an oversized unit is attributed to that unit.
    assert all(c["labels"] == ["big"] for c in chunks)


def test_unsplittable_run_falls_back_to_hard_token_window():
    # One very long word: only the hard token window can cap it.
    chunks = pack_units([("blob", "supercalifragilistic" * 80)], max_tokens=25, max_chars=2000)
    assert _all_within(chunks, 25, 2000)


def test_secondary_char_cap_enforced():
    # Token-light but very long run must still be split by the char guard.
    chunks = pack_units([("blob", "ha " * 1500)], max_tokens=100000, max_chars=200)
    assert all(len(c["text"]) <= 200 for c in chunks)


def test_header_counts_against_budget():
    chunks = pack_units(
        [("u", "some body text here")],
        header="A fairly wordy heading line",
        max_tokens=18,
        max_chars=2000,
    )
    assert _all_within(chunks, 18, 2000)


def test_default_cap_is_under_cohere_limit():
    assert structure.MAX_TOKENS < 512
    assert structure.MAX_CHARS <= 2048
