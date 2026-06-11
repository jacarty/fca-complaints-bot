"""Structure-aware chunking for the NONE-strategy (whole-file) Knowledge Bases.

Both `structure-titan` and `structure-cohere` ingest their files with
`chunkingStrategy=NONE`, so Bedrock embeds each file's full text as a single
vector. The binding constraint is Cohere Embed v3's **512-token** per-input
limit. Bedrock invokes Cohere with no truncation, so an over-length text fails
ingestion with `Invalid parameter combination` (a 400) rather than being
truncated. Cohere's 2048-character cap is a looser secondary limit.

The catch: token count is NOT proportional to character count. Within a fixed
2000-char budget, true Cohere token counts observed on the FCA corpus ranged
from 15 to 881 — HTML entities (`&nbsp;`), tables and dense rule text inflate
tokens far beyond what the character length suggests. So chunks MUST be capped
by Cohere tokens, measured with Cohere's own tokenizer (vendored alongside this
module), not by characters.

Strategy: respect natural document boundaries (FCA provisions / policy
headings), greedily packing consecutive units from the *same parent* into one
chunk up to MAX_TOKENS, never crossing the parent boundary. A single unit over
the cap is split by paragraph, then sentence, then a hard token-window as a last
resort so nothing can ever exceed the cap.
"""

import functools
import re
from pathlib import Path

# Cohere Embed v3 hard limit is 512 tokens; leave margin for any special tokens
# Bedrock adds and for the entity cleanup that runs upstream.
MAX_TOKENS = 480
# Secondary guard: Cohere's 2048-char cap. Rarely binds once tokens are capped,
# but enforced so a token-light-but-very-long run can't slip through.
MAX_CHARS = 2000

_TOKENIZER_PATH = Path(__file__).parent / "cohere_tokenizer.json"


@functools.lru_cache(maxsize=1)
def _tokenizer():
    """Load Cohere's tokenizer once (vendored, so no network at runtime)."""
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(_TOKENIZER_PATH))
    tok.no_truncation()  # we need TRUE token counts, not silently capped ones
    return tok


def count_tokens(text: str) -> int:
    """Number of Cohere tokens in `text`."""
    return len(_tokenizer().encode(text).ids)


def _within(text: str) -> bool:
    """True if `text` is within both the token and character caps."""
    return len(text) <= MAX_CHARS and count_tokens(text) <= MAX_TOKENS


def _hard_split_tokens(text: str, max_tokens: int) -> list[str]:
    """Last-resort split of an unbreakable run into <=max_tokens-token pieces."""
    ids = _tokenizer().encode(text).ids
    tok = _tokenizer()
    return [tok.decode(ids[i : i + max_tokens]).strip() for i in range(0, len(ids), max_tokens)]


def _split_sentences(text: str, max_tokens: int) -> list[str]:
    """Pack sentences into <=max_tokens pieces; hard-split any oversized sentence."""
    pieces: list[str] = []
    buf = ""
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        sent = sent.strip()
        if not sent:
            continue
        if count_tokens(sent) > max_tokens:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.extend(_hard_split_tokens(sent, max_tokens))
            continue
        candidate = f"{buf} {sent}" if buf else sent
        if count_tokens(candidate) <= max_tokens:
            buf = candidate
        else:
            pieces.append(buf)
            buf = sent
    if buf:
        pieces.append(buf)
    return pieces


def _split_oversized(text: str, max_tokens: int) -> list[str]:
    """Split a block exceeding max_tokens into <=max_tokens-token pieces.

    Prefers paragraph boundaries, then sentences, then a hard token window.
    """
    text = text.strip()
    if count_tokens(text) <= max_tokens:
        return [text] if text else []

    pieces: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if count_tokens(para) > max_tokens:
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.extend(_split_sentences(para, max_tokens))
            continue
        candidate = f"{buf}\n\n{para}" if buf else para
        if count_tokens(candidate) <= max_tokens:
            buf = candidate
        else:
            pieces.append(buf)
            buf = para
    if buf:
        pieces.append(buf)
    return pieces


def _enforce_char_cap(pieces: list[str], max_chars: int) -> list[str]:
    """Char-hard-split any piece still over the secondary character cap."""
    out: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            out.append(piece)
        else:
            out.extend(piece[i : i + max_chars] for i in range(0, len(piece), max_chars))
    return out


def pack_units(
    units: list[tuple[str, str]],
    header: str = "",
    max_tokens: int = MAX_TOKENS,
    max_chars: int = MAX_CHARS,
) -> list[dict]:
    """Pack ``(label, text)`` units from ONE parent into capped chunks.

    Consecutive units are greedily combined until adding the next would exceed
    ``max_tokens`` (or ``max_chars``). A unit whose own text exceeds the budget
    is emitted as its own run of fallback-split chunks. ``header`` (e.g. a
    section or document heading) is prepended to every chunk and counts against
    the budget, so each chunk stays self-describing at retrieval time.

    Returns a list of ``{"text": str, "labels": [unit labels in this chunk]}``.
    Every returned chunk is guaranteed within both caps.
    """
    header = header.strip()
    prefix = f"{header}\n\n" if header else ""
    header_tokens = count_tokens(prefix) if prefix else 0
    body_token_budget = max_tokens - header_tokens
    body_char_budget = max_chars - len(prefix)
    if body_token_budget <= 0 or body_char_budget <= 0:
        raise ValueError(f"header too large ({header_tokens} tokens) for cap {max_tokens}")

    chunks: list[dict] = []
    cur_text = ""
    cur_labels: list[str] = []

    def flush() -> None:
        nonlocal cur_text, cur_labels
        if cur_text.strip():
            chunks.append({"text": prefix + cur_text.strip(), "labels": cur_labels})
        cur_text = ""
        cur_labels = []

    def body_fits(body: str) -> bool:
        return len(body) <= body_char_budget and count_tokens(body) <= body_token_budget

    for label, text in units:
        block = (text or "").strip()
        if not block:
            continue
        if not body_fits(block):
            flush()
            pieces = _enforce_char_cap(_split_oversized(block, body_token_budget), body_char_budget)
            for piece in pieces:
                chunks.append({"text": prefix + piece, "labels": [label]})
            continue
        candidate = f"{cur_text}\n\n{block}" if cur_text else block
        if body_fits(candidate):
            cur_text = candidate
            if label not in cur_labels:
                cur_labels.append(label)
        else:
            flush()
            cur_text = block
            cur_labels = [label]
    flush()
    return chunks
