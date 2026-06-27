from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import federation, mcp_http
from .config import settings
from .file_index import deep_read, find_file, index_root
from .metrics import metric_rows, record_metric, render_metric_chart
from .storage import init_db
from .wiki import get_doc, history, ingest_marketing_docs, list_docs, seed_wiki_from_disk, upsert_doc

logger = logging.getLogger("knowledge_ops")

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / ".well-known" / "mcps.json"
_STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Graceful, idempotent startup: ensure data dirs, init schema, self-seed the
    # wiki from any markdown shipped in wiki_dir so grounding works on a fresh volume.
    settings.ensure_dirs()
    init_db()
    try:
        seeded = seed_wiki_from_disk()
        if seeded:
            logger.info("seeded %d wiki docs from %s", len(seeded), settings.wiki_dir)
    except Exception:  # never let seeding crash startup
        logger.exception("wiki seed-on-startup failed (non-fatal)")
    yield


app = FastAPI(title="Knowledge-Ops", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"error": {"type": "validation_error", "detail": exc.errors()}}, status_code=422)


@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": {"type": exc.__class__.__name__, "message": str(exc)}}, status_code=500)


@app.get("/")
def root() -> RedirectResponse:
    # Humans land on the GUI; machine metadata stays at /info + /.well-known.
    return RedirectResponse(url="/ui/", status_code=307)


@app.get("/info")
def info() -> dict:
    return {
        "service": "knowledge-ops",
        "version": "0.1.0",
        "ui": "/ui/",
        "docs": "/docs",
        "health": "/health",
        "manifest": "/.well-known/mcps.json",
        "mcp_endpoint": "/mcp",
    }


@app.get("/.well-known/mcps.json")
def mcps_manifest() -> Response:
    try:
        data = _MANIFEST_PATH.read_text(encoding="utf-8")
        return Response(data, media_type="application/json")
    except OSError:
        return Response(json.dumps({"error": "manifest not found"}), status_code=404, media_type="application/json")


@app.post("/mcp")
async def mcp_jsonrpc(request: Request) -> Response:
    """HTTP-MCP JSON-RPC surface for the Brigade broker (initialize/tools/list/tools/call)."""
    try:
        message = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    status, body = await mcp_http.handle(message, authorization=request.headers.get("authorization"))
    if body is None:
        return Response(status_code=status)
    return JSONResponse(body, status_code=status)


class SearchRequest(BaseModel):
    query: str
    scope: str | None = None
    sources: list[str] | None = None
    k: int = 8


class DocRequest(BaseModel):
    path: str
    md: str
    scope: str = "global"
    owner: str = "magic-unicorn"


class MetricRequest(BaseModel):
    name: str
    value: float
    tags: dict = {}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "knowledge-ops"}


@app.post("/kb.search")
async def kb_search(req: SearchRequest) -> dict:
    res = await federation.search(req.query, scope=req.scope, sources=req.sources, k=req.k)
    return {"query": res.query, "answer": res.answer, "passages": [p.__dict__ for p in res.passages]}


@app.post("/kb.ground")
async def kb_ground(req: SearchRequest) -> dict:
    return await federation.ground(req.query, scope=req.scope, k=req.k)


@app.get("/kb.list_sources")
def kb_list_sources() -> dict:
    return {"sources": [s.__dict__ for s in federation.list_sources()]}


@app.get("/kb.docs")
def kb_docs() -> dict:
    return {"docs": list_docs()}


@app.get("/kb.get_doc")
def kb_get_doc(id: str) -> dict:
    return {"doc": get_doc(id)}


@app.post("/kb.upsert_doc")
def kb_upsert_doc(req: DocRequest) -> dict:
    return {"id": upsert_doc(req.path, req.md, req.scope, req.owner)}


@app.get("/kb.doc_history")
def kb_doc_history(id: str) -> dict:
    return {"history": history(id)}


@app.post("/kb.ingest_marketing_docs")
def kb_ingest_marketing_docs() -> dict:
    return {"ids": ingest_marketing_docs()}


@app.post("/kb.record_metric")
def kb_record_metric(req: MetricRequest) -> dict:
    return {"id": record_metric(req.name, req.value, req.tags)}


@app.get("/kb.get_metric_chart")
def kb_get_metric_chart(name: str, limit: int = 80) -> Response:
    return Response(render_metric_chart(name, limit), media_type="image/svg+xml")


@app.get("/kb.metrics")
def kb_metrics(name: str, limit: int = 200) -> dict:
    return {"metrics": metric_rows(name, limit)}


@app.post("/kb.index_files")
def kb_index_files(root: str, limit: int | None = None) -> dict:
    return {"chunks": index_root(Path(root).expanduser(), limit=limit)}


@app.get("/kb.find_file")
def kb_find_file(query: str, k: int = 8, scope: str | None = None) -> dict:
    return {"passages": [p.__dict__ for p in find_file(query, k, scope)]}


@app.get("/kb.deep_read")
def kb_deep_read(path: str, query: str = "", k: int = 8) -> dict:
    return {"passages": [p.__dict__ for p in deep_read(path, query, k)]}


# Web GUI — served by this app itself (no build step, no CDN). Mounted last so it
# never shadows the API routes above. `html=True` serves index.html at /ui/.
if _STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
