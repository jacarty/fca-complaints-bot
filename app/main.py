"""FastAPI + HTMX skeleton for the FCA complaints handling bot.

This is the JAM-271 scaffold: it boots, serves the chat shell, and round-trips
through HTMX. The /ask route is a deliberate stub — retrieval and complaint
drafting (wiring src/pipeline.py, the drafting prompt, structured outputs, and
citation rendering) land in JAM-273.

Run:
    uv run uvicorn app.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="FCA Complaints Handling Bot")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

STUB_ANSWER = (
    "The retrieval and drafting pipeline isn't connected yet — this is the UI "
    "skeleton (JAM-271). Once JAM-273 wires in the knowledge base, this is where "
    "the cited, compliant draft response will appear."
)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request, message: str = Form("")) -> HTMLResponse:
    question = message.strip()
    if not question:
        return HTMLResponse("")
    return templates.TemplateResponse(
        request=request,
        name="_message.html",
        context={"question": question, "answer": STUB_ANSWER},
    )
