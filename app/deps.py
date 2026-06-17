"""FastAPI dependency that pseudonymises handler input before it reaches the
drafting pipeline.

Declaring redaction as a dependency (rather than burying the call in the route)
keeps the step visible in the ``/ask`` signature and keeps ``app.main`` thin.
The dependency resolves the session up front and records ``session_id`` on
``request.state`` so the route uses the *same* Vault for re-hydration; cookie
issuance stays in the route, which owns the response object.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import Form, Request
from pydantic import BaseModel, Field

from app.session import get_or_create_session
from src.pii import Detection, RedactionResult, Vault, redact


class RedactionContext(BaseModel):
    """What the route receives in place of the raw form field."""

    raw_question: str  # the handler's own input -- shown only on the authorised render
    clean_question: str  # pseudonymised text: sent to the model and stored in history
    detections: list[Detection] = Field(default_factory=list)
    session_id: str


def get_redaction_context(request: Request, message: str = Form("")) -> RedactionContext:
    """Resolve the session, pseudonymise the handler's message, and hand the
    route a :class:`RedactionContext`."""
    session_id = request.cookies.get("session_id") or str(uuid4())
    request.state.session_id = session_id

    session = get_or_create_session(session_id)
    vault: Vault = session["pii_vault"]

    raw = message.strip()
    result: RedactionResult = (
        redact(raw, vault) if raw else RedactionResult(clean_text="", detections=[])
    )

    return RedactionContext(
        raw_question=raw,
        clean_question=result.clean_text,
        detections=result.detections,
        session_id=session_id,
    )
