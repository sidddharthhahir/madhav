"""FastAPI surface over the pipeline.

The index and SQLite connection are built once at startup, not per request --
rebuilding BM25 on every call would dominate latency and defeat the point of an
in-process index.
"""

import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..pipeline import Pipeline

WEB_ROOT = Path(__file__).resolve().parents[3] / "frontend" / "web"

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # threaded=True because FastAPI dispatches these sync endpoints to a worker
    # threadpool, so the connection opened here is used from other threads.
    # Nothing on the request path actually queries SQLite -- the pipeline holds
    # the index, records, valid ids and language list in memory -- but the flag
    # keeps a stray query from raising instead of silently working.
    # use_dense=True fuses in local Ollama embeddings via RRF; it degrades to
    # BM25 alone if Ollama isn't running or embeddings haven't been built, so
    # this is safe to leave on even where that setup step was skipped.
    _state["pipeline"] = Pipeline(threaded=True, use_dense=True)
    yield
    pipeline = _state.pop("pipeline", None)
    if pipeline is not None:
        pipeline.close()


def get_pipeline() -> Pipeline:
    pipeline = _state.get("pipeline")
    if pipeline is None:
        raise HTTPException(status_code=503, detail="pipeline not initialised")
    return pipeline


app = FastAPI(
    title="Gita Wisdom API",
    version="0.1.0",
    description=(
        "Cited retrieval over the Bhagavad Gita. Every answer cites verses "
        "that were actually retrieved for the question; citations are "
        "validated before the answer is returned."
    ),
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          examples=["why do I resent people I have never met online"])
    k: int | None = Field(None, ge=1, le=20,
                          description="how many verses to ground on")


class AskResponse(BaseModel):
    ok: bool
    status: str
    question: str
    answer: str
    language: str
    citations: list[str]
    retrieved: list[dict]
    attempts: int
    detail: str = ""
    usage: dict = {}
    timings: dict = {}


@app.get("/health")
def health():
    return get_pipeline().health()


# /ask is the only endpoint that spends money -- two model calls per request,
# a few cents each. Everything else reads local data and is free. Left
# unguarded, anyone who can reach the port can drain the account attached to
# the key, and a runaway client can do it by accident.
#
# A fixed window per client, in memory: this is a single-process, single-user
# desktop app, so a shared counter is the right size of solution. It is a
# spend guard, not a security control -- it will not stop someone determined,
# and it is no substitute for authentication if this is ever exposed beyond
# localhost.
ASK_RATE_LIMIT = int(os.environ.get("MADHAV_ASK_PER_HOUR", "60"))
_ask_calls: dict[str, deque] = {}
_ask_lock = threading.Lock()


def _rate_limit_ask(request: Request) -> None:
    if ASK_RATE_LIMIT <= 0:            # 0 disables the guard entirely
        return
    who = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _ask_lock:
        seen = _ask_calls.setdefault(who, deque())
        while seen and now - seen[0] > 3600:
            seen.popleft()
        if len(seen) >= ASK_RATE_LIMIT:
            retry = int(3600 - (now - seen[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=("Rate limit reached: %d answers per hour. Retrieval and "
                        "/preview are free and unaffected. Set "
                        "MADHAV_ASK_PER_HOUR to change this." % ASK_RATE_LIMIT),
                headers={"Retry-After": str(retry)},
            )
        seen.append(now)


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest, request: Request):
    _rate_limit_ask(request)
    pipeline = get_pipeline()
    result = pipeline.ask(req.question, k=req.k)
    # Recorded regardless of ok/failed -- the frontend's history row already
    # renders a status dot for both cases, so both were always meant to be
    # logged. This was previously never called at all: the history table,
    # GET /history, and the sidebar UI all existed with nothing writing to
    # them, so a page reload had no conversation to restore.
    pipeline.record_history(result)
    out = result.to_dict()
    out.pop("plan", None)             # internal; not part of the contract
    return out


@app.post("/preview")
def preview(req: AskRequest):
    """Retrieval + grounding context with no model calls. Costs nothing."""
    return get_pipeline().preview(req.question, k=req.k)


@app.get("/verse/{verse_id}")
def verse(verse_id: str):
    record = get_pipeline().verse(verse_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such verse: %s" % verse_id)
    return record


@app.get("/search")
def search(q: str, k: int = 8):
    """Raw lexical search. Diagnostic surface, no model calls."""
    pipeline = get_pipeline()
    return {
        "query": q,
        "hits": [
            {"verse_id": h.doc_id, "rank": h.rank, "score": round(h.score, 3),
             "enriched": h.meta.get("enriched", False),
             "terms": [t for t, _ in pipeline.index.explain(q, h.doc_id)[:6]]}
            for h in pipeline.retrieve(q, k)
        ],
    }


# -- sidebar state ---------------------------------------------------------

@app.get("/chapters")
def chapters():
    return get_pipeline().chapters()


@app.get("/chapters/{chapter}")
def chapter_verses(chapter: int):
    if not 1 <= chapter <= 18:
        raise HTTPException(status_code=404, detail="chapters run 1-18")
    return get_pipeline().chapter_verses(chapter)


@app.get("/history")
def history(limit: int = 30):
    return get_pipeline().history(limit)


@app.get("/saved")
def saved():
    return get_pipeline().saved()


class SaveRequest(BaseModel):
    verse_id: str
    note: str | None = None


@app.post("/saved")
def save_verse(req: SaveRequest):
    if not get_pipeline().save_verse(req.verse_id, req.note):
        raise HTTPException(status_code=404, detail="no such verse: %s" % req.verse_id)
    return {"saved": req.verse_id}


@app.delete("/saved/{verse_id}")
def unsave_verse(verse_id: str):
    get_pipeline().unsave_verse(verse_id)
    return {"removed": verse_id}


# -- static UI -------------------------------------------------------------
# The app is served from the same origin as the API, so there is no CORS
# preflight on the normal path. The middleware below exists only for the case
# where the UI is served separately during development.

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

if WEB_ROOT.is_dir():
    # StaticFiles sends ETag/Last-Modified, but browsers apply heuristic
    # caching to HTML and JS when no explicit policy is given -- which meant
    # a returning visitor could keep running yesterday's app.js against
    # today's markup, with no error to explain the mismatch. There is no
    # build step here to hash filenames, so the frontend is served
    # must-revalidate instead: the conditional request still 304s when
    # nothing changed, so this costs a round trip, not bandwidth.
    class NoCacheStatic(StaticFiles):
        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

    app.mount("/static", NoCacheStatic(directory=WEB_ROOT), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(
            WEB_ROOT / "index.html",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
