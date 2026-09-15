from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, federation, graph_api, mcp_http
from .authorization import (
    AuthorizationError,
    Principal,
    authorized_write_target,
    can_read_record,
    filter_passages,
    principal_from_authorization,
)
from .config import settings
from .citations import inspect_citation
from .file_index import deep_read, find_file, index_root
from .http_middleware import RequestGuardMiddleware
from .metrics import metric_rows, prometheus_metrics, record_metric, render_metric_chart
from .memory_quality import (
    complete_refresh_run,
    list_refresh_jobs,
    list_refresh_runs,
    list_review_items,
    resolve_review_item,
    run_due_refresh_jobs,
    run_refresh_job,
    scan_review_queue,
    upsert_refresh_job,
)
from .openwiki_pipeline import approve_import, get_import, list_imports, reject_import, stage_bundle
from .operations import (
    list_deletion_tasks,
    operations_snapshot,
    redrive_deletion_task,
    sanitize_error,
    worker_readiness,
)
from .storage import (
    SCHEMA_VERSION,
    audit_rows,
    connect,
    init_db,
    migrate_verified_email_ownership,
    record_audit,
)
from .wiki import (
    get_doc,
    history,
    ingest_marketing_docs,
    list_docs,
    reconcile_pending_deletions,
    seed_wiki_from_disk,
    sync_wiki_to_disk,
    tombstone_doc,
    upsert_doc,
)

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
            logger.info(
                "seeded %d wiki docs from %s (KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS=1)",
                len(seeded),
                settings.wiki_dir,
            )
        elif not settings.seed_internal_corpus:
            logger.info(
                "internal seed corpus ingestion is OFF "
                "(KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS unset/0); %s not ingested",
                settings.wiki_dir,
            )
        repaired = sync_wiki_to_disk()
        if repaired:
            logger.warning("repaired %d wiki markdown projections from canonical storage", repaired)
        deletions = reconcile_pending_deletions(limit=settings.worker_batch_size)
        if deletions["backlog"]:
            logger.error(
                "%d deletion receipts still have pending projections; %d candidates deferred",
                deletions["backlog"],
                deletions["remaining"],
            )
    except Exception as exc:  # never let seeding crash startup
        logger.error(
            "wiki seed-on-startup failed code=%s",
            sanitize_error(exc, category="startup_reconciliation_failed"),
        )
    yield


app = FastAPI(title="Knowledge-Ops", version=__version__, lifespan=lifespan)
app.add_middleware(
    RequestGuardMiddleware,
    max_bytes=settings.max_request_bytes,
    per_minute=settings.rate_limit_per_minute,
)


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"error": {"type": "validation_error", "detail": exc.errors()}}, status_code=422)


@app.exception_handler(AuthorizationError)
async def _on_forbidden(request: Request, exc: AuthorizationError) -> JSONResponse:
    try:
        actor = getattr(request.state, "authenticated_subject", "anonymous")
        record_audit(
            actor, "auth.api.authorization", request.url.path, "denied",
            {"reason_code": "permission_denied"},
        )
    except Exception as audit_exc:
        logger.error(
            "authorization audit persistence failed code=%s",
            sanitize_error(audit_exc, category="audit_persistence_failed"),
        )
    return JSONResponse({"error": {"type": "forbidden", "message": str(exc)}}, status_code=403)


@app.exception_handler(ValueError)
async def _on_bad_request(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"error": {"type": "bad_request", "message": str(exc)}}, status_code=400)


@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "request failed code=%s",
        sanitize_error(exc, category="request_failed", exception_type=type(exc).__name__),
    )
    return JSONResponse({"error": {"type": "internal_error", "message": "request could not be completed"}}, status_code=500)


def current_principal(request: Request) -> Principal:
    try:
        principal = principal_from_authorization(
            request.headers.get("authorization"),
            require_auth=settings.api_require_auth,
        )
    except AuthorizationError as exc:
        try:
            record_audit(
                "anonymous", "auth.api.authentication", request.url.path, "denied",
                {"reason_code": "authorization_failed"},
            )
        except Exception as audit_exc:
            logger.error(
                "authentication audit persistence failed code=%s",
                sanitize_error(audit_exc, category="audit_persistence_failed"),
            )
        raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    request.state.authenticated_subject = principal.subject
    if settings.api_require_auth and principal.issuer in settings.api_trusted_issuers:
        for identity in sorted(principal.identities - {principal.subject.casefold()}):
            if "@" in identity:
                migrate_verified_email_ownership(
                    issuer=principal.issuer,
                    subject=principal.subject,
                    verified_email=identity,
                )
    return principal


ApiPrincipal = Annotated[Principal, Depends(current_principal)]


@app.get("/")
def root() -> RedirectResponse:
    # The public product page is the front door: unauthenticated visitors get
    # /landing (skip-auth at the proxy); "Open the Observatory" leads to /ui,
    # which the gateway gates. Machine metadata stays at /info + /.well-known.
    return RedirectResponse(url="/landing/", status_code=307)


@app.get("/info")
def info() -> dict:
    return {
        "service": "knowledge-ops",
        "version": __version__,
        "ui": "/ui/",
        "docs": "/docs",
        "health": "/health",
        "readiness": "/ready",
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
    query: str = Field(min_length=1, max_length=10_000)
    scope: str | None = None
    sources: list[str] | None = None
    k: int = Field(default=8, ge=1, le=50)
    as_of: str | None = Field(default=None, max_length=80)


class DocRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    md: str = Field(min_length=1, max_length=900_000)
    scope: Literal["personal", "global"] = "personal"
    owner: str | None = None
    valid_from: str | None = Field(default=None, max_length=80)
    valid_until: str | None = Field(default=None, max_length=80)
    supersedes_id: str | None = Field(default=None, max_length=120)
    review_due_at: str | None = Field(default=None, max_length=80)


class DeleteRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CitationInspectRequest(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=500)
    claim: str = Field(min_length=1, max_length=10_000)


class ReviewResolveRequest(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


class RefreshJobRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=120)
    kind: Literal["review_scan", "connector_refresh"]
    source_ref: str = Field(default="wiki", max_length=500)
    interval_seconds: int = Field(ge=60, le=31_536_000)
    enabled: bool = True


class RefreshRunRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    scheduled_for: str | None = Field(default=None, max_length=80)


class RefreshCompleteRequest(BaseModel):
    status: Literal["completed", "failed"]
    result: dict = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=2000)


class MetricRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: float
    tags: dict = Field(default_factory=dict)


class OpenWikiStageRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=1000)
    producer_version: str = Field(min_length=1, max_length=40)


class OpenWikiReviewRequest(BaseModel):
    note: str = Field(min_length=3, max_length=2000)
    accept_pii: bool = False


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "knowledge-ops"}


@app.get("/ready")
def ready() -> JSONResponse:
    checks = {
        "database": False,
        "schema": False,
        "wiki": settings.wiki_dir.is_dir(),
        "staging": settings.openwiki_staging_dir.is_dir(),
    }
    worker = worker_readiness()
    try:
        with connect() as db:
            checks["database"] = db.execute("SELECT 1").fetchone()[0] == 1
            checks["schema"] = int(db.execute(
                "SELECT coalesce(max(version), 0) FROM schema_migrations"
            ).fetchone()[0]) == SCHEMA_VERSION
    except Exception as exc:
        logger.error(
            "readiness database check failed code=%s",
            sanitize_error(exc, category="readiness_database_failed"),
        )
    checks["worker"] = worker["ready"] if worker["required"] else True
    ok = all(checks.values())
    return JSONResponse(
        {"ok": ok, "service": "knowledge-ops", "checks": checks, "worker": worker},
        status_code=200 if ok else 503,
    )


@app.get("/metrics")
def openmetrics(principal: ApiPrincipal) -> Response:
    if not principal.is_admin:
        raise AuthorizationError("operational metrics require knowledge-admin")
    return Response(
        prometheus_metrics(),
        media_type="application/openmetrics-text; version=1.0.0; charset=utf-8",
    )


@app.post("/kb.search")
async def kb_search(req: SearchRequest, principal: ApiPrincipal) -> dict:
    res = await federation.search(
        req.query, scope=req.scope, sources=req.sources, k=req.k,
        principal=principal, as_of=req.as_of,
    )
    return {"query": res.query, "answer": res.answer, "passages": [p.__dict__ for p in res.passages]}


@app.post("/kb.ground")
async def kb_ground(req: SearchRequest, principal: ApiPrincipal) -> dict:
    return await federation.ground(
        req.query, scope=req.scope, k=req.k, principal=principal,
        sources=req.sources, as_of=req.as_of,
    )


@app.get("/kb.list_sources")
def kb_list_sources(principal: ApiPrincipal) -> dict:
    principal.require("read:knowledge")
    rows = [s.__dict__ for s in federation.list_sources()]
    if not principal.is_admin:
        for row in rows:
            row["detail"] = ""
    return {"sources": rows}


@app.get("/kb.docs")
def kb_docs(principal: ApiPrincipal) -> dict:
    principal.require("read:knowledge")
    return {"docs": [doc for doc in list_docs() if can_read_record(principal, doc)]}


@app.get("/kb.get_doc")
def kb_get_doc(id: str, principal: ApiPrincipal) -> dict:
    principal.require("read:knowledge")
    doc = get_doc(id)
    if doc and not can_read_record(principal, doc):
        raise AuthorizationError("document is outside the caller's visibility")
    return {"doc": doc}


@app.post("/kb.upsert_doc")
def kb_upsert_doc(req: DocRequest, principal: ApiPrincipal) -> dict:
    scope, owner = authorized_write_target(principal, req.scope, req.owner)
    doc_id = upsert_doc(
        req.path, req.md, scope, owner, valid_from=req.valid_from,
        valid_until=req.valid_until, supersedes_id=req.supersedes_id,
        review_due_at=req.review_due_at,
    )
    record_audit(principal.subject, "wiki.upsert", doc_id, "success", {"scope": scope, "path": req.path})
    graph_api.invalidate_link_caches()
    return {"id": doc_id}


@app.post("/kb.delete_doc")
def kb_delete_doc(id: str, req: DeleteRequest, principal: ApiPrincipal) -> dict:
    deletion = tombstone_doc(id, principal=principal, reason=req.reason)
    graph_api.invalidate_link_caches()
    return {"deletion": deletion}


@app.post("/kb.inspect_citation")
def kb_inspect_citation(req: CitationInspectRequest, principal: ApiPrincipal) -> dict:
    return {"citation": inspect_citation(
        source=req.source, source_id=req.source_id, claim=req.claim, principal=principal,
    )}


@app.get("/kb.doc_history")
def kb_doc_history(id: str, principal: ApiPrincipal) -> dict:
    principal.require("read:knowledge")
    doc = get_doc(id)
    if doc and not can_read_record(principal, doc):
        raise AuthorizationError("document is outside the caller's visibility")
    return {"history": history(id)}


@app.post("/kb.ingest_marketing_docs")
def kb_ingest_marketing_docs(principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("marketing ingestion requires knowledge-admin")
    ids = ingest_marketing_docs()
    record_audit(principal.subject, "wiki.ingest_marketing", "marketing", "success", {"documents": len(ids)})
    return {"ids": ids}


@app.post("/kb.record_metric")
def kb_record_metric(req: MetricRequest, principal: ApiPrincipal) -> dict:
    principal.require("write:knowledge")
    metric_id = record_metric(req.name, req.value, req.tags)
    record_audit(principal.subject, "metric.record", req.name, "success")
    return {"id": metric_id}


@app.get("/kb.get_metric_chart")
def kb_get_metric_chart(name: str, principal: ApiPrincipal, limit: int = 80) -> Response:
    principal.require("read:knowledge")
    return Response(render_metric_chart(name, limit), media_type="image/svg+xml")


@app.get("/kb.metrics")
def kb_metrics(name: str, principal: ApiPrincipal, limit: int = 200) -> dict:
    principal.require("read:knowledge")
    return {"metrics": metric_rows(name, limit)}


@app.get("/kb.audit")
def kb_audit(principal: ApiPrincipal, limit: int = 100) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("audit history requires knowledge-admin")
    return {"events": audit_rows(limit)}


@app.get("/kb.review_items")
def kb_review_items(
    principal: ApiPrincipal,
    status: Literal["open", "resolved", "all"] = "open",
    kind: Literal["stale", "conflict"] | None = None,
    limit: int = 200,
) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("memory review queue requires knowledge-admin")
    return {"items": list_review_items(status=status, kind=kind, limit=limit)}


@app.post("/kb.review_scan")
def kb_review_scan(principal: ApiPrincipal, at: str | None = None) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("memory review scan requires knowledge-admin")
    result = scan_review_queue(at=at)
    record_audit(principal.subject, "memory.review_scan", "wiki", "success", result)
    return result


@app.post("/kb.review_resolve")
def kb_review_resolve(id: int, req: ReviewResolveRequest, principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("memory review resolution requires knowledge-admin")
    item = resolve_review_item(id, actor=principal.subject, note=req.note)
    record_audit(principal.subject, "memory.review_resolve", str(id), "success")
    return {"item": item}


@app.get("/kb.refresh_jobs")
def kb_refresh_jobs(principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("refresh operations require knowledge-admin")
    return {"jobs": list_refresh_jobs(), "runs": list_refresh_runs(limit=100)}


@app.get("/kb.operations")
def kb_operations(principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("operational state requires knowledge-admin")
    return operations_snapshot()


@app.get("/kb.deletion_outbox")
def kb_deletion_outbox(
    principal: ApiPrincipal,
    status: Literal["all", "pending", "running", "failed", "succeeded"] = "all",
    limit: int = 200,
) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("deletion outbox requires knowledge-admin")
    return {"tasks": list_deletion_tasks(status=status, limit=limit)}


@app.post("/kb.deletion_outbox/{task_id}/redrive")
def kb_deletion_outbox_redrive(task_id: str, principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("deletion outbox redrive requires knowledge-admin")
    task = redrive_deletion_task(task_id)
    record_audit(principal.subject, "deletion.outbox_redrive", task_id, "success")
    return {"task": task}


@app.post("/kb.refresh_job")
def kb_refresh_job(req: RefreshJobRequest, principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("refresh job changes require knowledge-admin")
    job = upsert_refresh_job(
        req.job_id, kind=req.kind, source_ref=req.source_ref,
        interval_seconds=req.interval_seconds, enabled=req.enabled,
    )
    record_audit(principal.subject, "refresh.job_upsert", req.job_id, "success", {"kind": req.kind})
    return {"job": job}


@app.post("/kb.refresh_run")
def kb_refresh_run(id: str, req: RefreshRunRequest, principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("refresh run dispatch requires knowledge-admin")
    run = run_refresh_job(
        id, actor=principal.subject, idempotency_key=req.idempotency_key,
        scheduled_for=req.scheduled_for,
    )
    record_audit(principal.subject, "refresh.run", run["run_id"], run["status"], {"job_id": id})
    return {"run": run}


@app.post("/kb.refresh_due")
def kb_refresh_due(principal: ApiPrincipal, at: str | None = None) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("refresh scheduler dispatch requires knowledge-admin")
    runs = run_due_refresh_jobs(actor=principal.subject, at=at)
    record_audit(principal.subject, "refresh.run_due", "refresh_jobs", "success", {"runs": len(runs)})
    return {"runs": runs}


@app.post("/kb.refresh_complete")
def kb_refresh_complete(id: str, req: RefreshCompleteRequest, principal: ApiPrincipal) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("refresh completion requires knowledge-admin")
    run = complete_refresh_run(id, status=req.status, result=req.result, error=req.error)
    record_audit(principal.subject, "refresh.complete", id, req.status)
    return {"run": run}


@app.post("/kb.index_files")
def kb_index_files(root: str, principal: ApiPrincipal, limit: int | None = None) -> dict:
    if not principal.is_admin:
        raise AuthorizationError("file indexing requires knowledge-admin")
    chunks = index_root(Path(root), owner=principal.subject, limit=limit)
    record_audit(principal.subject, "files.index", root, "success", {"chunks": chunks})
    return {"chunks": chunks}


@app.get("/kb.find_file")
def kb_find_file(query: str, principal: ApiPrincipal, k: int = 8, scope: str | None = None) -> dict:
    principal.require("read:knowledge")
    hits = find_file(query, min(k, 50), scope, principal=principal)
    return {"passages": [p.__dict__ for p in filter_passages(principal, hits, scope)]}


@app.get("/kb.deep_read")
def kb_deep_read(path: str, principal: ApiPrincipal, query: str = "", k: int = 8) -> dict:
    principal.require("read:knowledge")
    hits = deep_read(path, query, min(k, 50), principal=principal)
    return {"passages": [p.__dict__ for p in filter_passages(principal, hits)]}


@app.get("/kb.openwiki_imports")
def kb_openwiki_imports(principal: ApiPrincipal) -> dict:
    return {"imports": list_imports(principal)}


@app.get("/kb.openwiki_import")
def kb_openwiki_import(id: str, principal: ApiPrincipal) -> dict:
    return {"import": get_import(id, principal)}


@app.post("/kb.openwiki_stage")
def kb_openwiki_stage(req: OpenWikiStageRequest, principal: ApiPrincipal) -> dict:
    return {"import": stage_bundle(req.source_path, req.producer_version, principal)}


@app.post("/kb.openwiki_approve")
def kb_openwiki_approve(id: str, req: OpenWikiReviewRequest, principal: ApiPrincipal) -> dict:
    return approve_import(id, principal, req.note, accept_pii=req.accept_pii)


@app.post("/kb.openwiki_reject")
def kb_openwiki_reject(id: str, req: OpenWikiReviewRequest, principal: ApiPrincipal) -> dict:
    return reject_import(id, principal, req.note)


# Constellation graph/stats/identity endpoints (kb.graph, kb.stats, kb.me).
app.include_router(graph_api.router)
try:  # the Guide (Brigade proxy) is optional — a deployment without it still boots
    from . import guide_api
    app.include_router(guide_api.router)
except Exception as _guide_exc:  # pragma: no cover - absent module / bad config
    logging.getLogger(__name__).info("guide api not mounted: %s", _guide_exc)


# Web GUI — served by this app itself (no build step, no CDN). Mounted last so it
# never shadows the API routes above. `html=True` serves index.html at /ui/.
if _STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
_LANDING_DIR = _STATIC_DIR / "landing"
if _LANDING_DIR.is_dir():
    app.mount("/landing", StaticFiles(directory=str(_LANDING_DIR), html=True), name="landing")
