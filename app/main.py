"""FastAPI + HTMX app for the FCA complaints handling bot.

GET /      renders the chat shell.
POST /ask  runs the drafting pipeline for the latest turn and returns the
           rendered response partial.

Conversation state is held server-side, keyed by a session cookie. It's an
in-memory dict — fine for a single-user local tool, but not persistent across
restarts and not concurrency-hardened (multi-user/auth is out of scope per the
brief).

Run from the repo root:
    uv run uvicorn app.main:app --reload
"""

import logging
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Make src/ importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import ConversationTurn, draft_pipeline  # noqa: E402

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="FCA Complaints Handling Bot")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# session_id -> {"history": [ConversationTurn], "chunks": [RetrievedChunk]}
# In-memory, single-process; see module docstring.
SESSIONS: dict[str, dict] = {}


def _get_session(session_id: str) -> dict:
    return SESSIONS.setdefault(session_id, {"history": [], "chunks": []})


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    response = templates.TemplateResponse(request=request, name="index.html")
    session_id = request.cookies.get("session_id")
    if not session_id:
        session_id = str(uuid4())
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    _get_session(session_id)
    return response


@app.post("/ask", response_class=HTMLResponse)
def ask(request: Request, message: str = Form("")) -> HTMLResponse:
    question = message.strip()
    if not question:
        return HTMLResponse("")

    session_id = request.cookies.get("session_id") or str(uuid4())
    session = _get_session(session_id)

    try:
        # Sync route -> FastAPI runs it in a threadpool, so the blocking boto3
        # pipeline call doesn't stall the event loop.
        result = draft_pipeline(
            question,
            history=session["history"],
            prior_chunks=session["chunks"],
        )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully in the UI
        logger.exception("Drafting failed")
        return templates.TemplateResponse(
            request=request,
            name="_error.html",
            context={"question": question, "error": str(exc)[:300]},
        )

    assistant_content = result.handler_answer
    if result.customer_draft:
        assistant_content += f"\n\n[Customer draft]\n{result.customer_draft}"

    session["history"].append(ConversationTurn(role="user", content=question))
    session["history"].append(
        ConversationTurn(role="assistant", content=assistant_content)
    )
    session["chunks"] = result.retrieved_chunks

    response = templates.TemplateResponse(
        request=request,
        name="_message.html",
        context={"question": question, "result": result},
    )
    if not request.cookies.get("session_id"):
        response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    return response
