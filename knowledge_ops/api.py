from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from pydantic import BaseModel

from . import federation
from .config import settings
from .file_index import deep_read, find_file, index_root
from .metrics import metric_rows, record_metric, render_metric_chart
from .storage import init_db
from .wiki import get_doc, history, ingest_marketing_docs, list_docs, seed_wiki_from_disk, upsert_doc

logger = logging.getLogger("knowledge_ops")

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / ".well-known" / "mcps.json"


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


@app.get("/")
def root() -> dict:
    return {
        "service": "knowledge-ops",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "manifest": "/.well-known/mcps.json",
    }


@app.get("/.well-known/mcps.json")
def mcps_manifest() -> Response:
    try:
        data = _MANIFEST_PATH.read_text(encoding="utf-8")
        return Response(data, media_type="application/json")
    except OSError:
        return Response(json.dumps({"error": "manifest not found"}), status_code=404, media_type="application/json")


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
