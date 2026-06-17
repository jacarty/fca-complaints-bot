"""FastAPI + HTMX app for the FCA complaints handling bot.

GET /      renders the chat shell.
POST /ask  pseudonymises the handler's message, runs the drafting pipeline for
           the latest turn, masks any PII the model emitted, persists the masked
           turn, and returns the response partial re-hydrated for the handler.

Conversation state (including each session's PII Vault) is held server-side in
``app.session``, keyed by a session cookie -- in-memory, single-user; see that
module's docstring for the production caveats.

PII handling (JAM-277): the model and the stored history only ever see
pseudonymised text (``[NAME_1]`` etc.); the token<->value Vault never leaves the
server. The handler, as the authorised viewer, sees real values because the
render is re-hydrated at the very end. See ``src/pii.py``.

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
from src.pii import redact, rehydrate  # noqa: E402
from src.pipeline import ConversationTurn, draft_pipeline  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

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

    logger.info("redacted question: %s", ctx.clean_question)

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
    # not in the (already pseudonymised) input -- e.g. a name it wrote into the
    # customer draft. Mask it for everything we persist; the handler still sees
    # real values after re-hydration below.
    masked_answer = redact(result.handler_answer, vault).clean_text
    masked_draft = redact(result.customer_draft, vault).clean_text if result.customer_draft else ""

    # Persist the MASKED turn: the model never re-sees raw PII on later turns,
    # and the trail stays clean for the audit logging that lands next in JAM-277.
    assistant_content = masked_answer
    if masked_draft:
        assistant_content += f"\n\n[Customer draft]\n{masked_draft}"
    session["history"].append(ConversationTurn(role="user", content=ctx.clean_question))
    session["history"].append(ConversationTurn(role="assistant", content=assistant_content))
    session["chunks"] = result.retrieved_chunks

    # Re-hydrate for the handler's render only -- the authorised viewer. Tokens
    # map back via the session Vault, which never left the server.
    render_result = result.model_copy(
        update={
            "handler_answer": rehydrate(masked_answer, vault),
            "customer_draft": (
                rehydrate(masked_draft, vault) if masked_draft else result.customer_draft
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
