"""FastAPI + HTMX app for the FCA complaints handling bot.

GET /      renders the chat shell.
POST /ask  pseudonymises the handler's message, runs the drafting pipeline,
           masks any PII the model emitted, persists the masked turn, writes an
           audit record, and returns the response partial re-hydrated for the
           handler.

Conversation state (including each session's PII Vault) is held server-side in
``app.session``, keyed by a session cookie -- in-memory, single-user; see that
module's docstring for the production caveats.

PII handling (JAM-277): the model and the stored history only ever see
pseudonymised text (``[NAME_1]`` etc.); the token<->value Vault never leaves the
server. The handler, as the authorised viewer, sees real values because the
render is re-hydrated at the very end. See ``src/pii.py``.

Audit logging (JAM-277): every turn appends a PII-free record (HMAC-hashed
input, masked output, detection counts, model/usage/latency) to the trail. See
``src/audit.py`` and ``scripts/query_audit.py``.

Run from the repo root:
    uv run uvicorn app.main:app --reload
"""

import logging
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Make src/ importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.deps import RedactionContext, get_redaction_context  # noqa: E402
from app.session import get_or_create_session  # noqa: E402
from src.audit import build_audit_record, merge_detections, write_audit_record  # noqa: E402
from src.pii import redact, rehydrate  # noqa: E402
from src.pipeline import ConversationTurn, draft_pipeline  # noqa: E402

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="FCA Complaints Handling Bot")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request=request, name="index.html")
    session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = str(uuid4())
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    get_or_create_session(session_id)
    return response


@app.post("/ask", response_class=HTMLResponse)
def ask(
    request: Request,
    ctx: RedactionContext = Depends(get_redaction_context),
) -> HTMLResponse:
    if not ctx.clean_question:
        return HTMLResponse("")

    session = get_or_create_session(ctx.session_id)
    vault = session["pii_vault"]

    try:
        # Sync route -> FastAPI runs it in a threadpool, so the blocking boto3
        # pipeline call doesn't stall the event loop. The model only ever sees
        # the pseudonymised question and the (already masked) history.
        result = draft_pipeline(
            ctx.clean_question,
            history=session["history"],
            prior_chunks=session["chunks"],
        )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully in the UI
        logger.exception("Drafting failed")
        return templates.TemplateResponse(
            request=request,
            name="_error.html",
            context={"question": ctx.raw_question, "error": str(exc)[:300]},
        )

    # Output side of defence-in-depth: catch any PII the model emitted that was
    # not in the (already pseudonymised) input. redact() handles empty strings,
    # so these are safe to call unconditionally. We keep the detections this time
    # to record proof-of-masking in the audit trail.
    answer_red = redact(result.handler_answer, vault)
    draft_red = redact(result.customer_draft, vault)
    reason_red = redact(result.human_review_reason, vault)
    output_detections = merge_detections(
        answer_red.detections, draft_red.detections, reason_red.detections
    )

    masked_output = answer_red.clean_text
    if draft_red.clean_text:
        masked_output += f"\n\n[Customer draft]\n{draft_red.clean_text}"

    # Persist the MASKED turn: the model never re-sees raw PII on later turns.
    session["history"].append(ConversationTurn(role="user", content=ctx.clean_question))
    session["history"].append(ConversationTurn(role="assistant", content=masked_output))
    session["chunks"] = result.retrieved_chunks

    # Audit: a PII-free record of the turn. Fail-open -- a write failure logs but
    # does not break the handler's response. A regulated production deployment
    # might instead fail-closed (withhold the answer if it cannot be audited).
    try:
        write_audit_record(
            build_audit_record(
                session_id=ctx.session_id,
                raw_question=ctx.raw_question,
                masked_output=masked_output,
                masked_review_reason=reason_red.clean_text,
                input_detections=ctx.detections,
                output_detections=output_detections,
                model_id=result.config.get("generation_model", ""),
                cited_provisions=[p.provision for p in result.cited_provisions],
                retrieved_chunk_ids=[c.chunk_id for c in result.retrieved_chunks],
                human_review_required=result.human_review_required,
                insufficient_context=result.insufficient_context,
                latency_ms=result.latency_ms,
                retrieval_latency_ms=result.retrieval_latency_ms,
                generation_latency_ms=result.generation_latency_ms,
                usage=result.usage,
            )
        )
    except Exception:  # noqa: BLE001 - audit must not break the user response
        logger.exception("Audit write failed")

    # Re-hydrate for the handler's render only -- the authorised viewer. Tokens
    # map back via the session Vault, which never left the server. The review
    # reason is shown to the handler in full (authorised); only its audit copy
    # is masked.
    render_result = result.model_copy(
        update={
            "handler_answer": rehydrate(answer_red.clean_text, vault),
            "customer_draft": (
                rehydrate(draft_red.clean_text, vault)
                if draft_red.clean_text
                else result.customer_draft
            ),
        }
    )

    response = templates.TemplateResponse(
        request=request,
        name="_message.html",
        context={"question": ctx.raw_question, "result": render_result},
    )
    if not request.cookies.get("session_id"):
        response.set_cookie("session_id", ctx.session_id, httponly=True, samesite="lax")
    return response
