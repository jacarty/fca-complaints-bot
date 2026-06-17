"""Server-side conversation state for the FCA complaints bot.

Extracted from ``app.main`` so the ``/ask`` route and the PII redaction
dependency (``app.deps``) share one source of truth for a session's ``Vault``
without a circular import.

In-memory, single-process: fine for a single-user local tool, but not
persistent across restarts and not concurrency-hardened. Multi-user/auth is out
of scope per the brief. Note that each session holds its conversation ``Vault``,
which is the re-identification key for that conversation -- a production port
must move this store into encrypted, access-controlled, TTL'd storage.
"""

from __future__ import annotations

from src.pii import Vault

# session_id -> {"history": [ConversationTurn], "chunks": [RetrievedChunk],
#                "pii_vault": Vault}
SESSIONS: dict[str, dict] = {}


def get_or_create_session(session_id: str) -> dict:
    """Return the session dict for ``session_id``, creating it (with an empty
    Vault) on first use."""
    return SESSIONS.setdefault(session_id, {"history": [], "chunks": [], "pii_vault": Vault()})
