"""HTTP-MCP JSON-RPC shim (POST /mcp).

The manifest advertises ``mcp_endpoint: .../mcp``. This module makes that real:
it speaks MCP JSON-RPC (``initialize``, ``tools/list``, ``tools/call``, ``ping``)
so the Brigade broker's *brokered execution* path works, not just registration.

``tools/call`` dispatches to the very same functions the REST handlers call, and
the tool catalog is single-sourced from ``.well-known/mcps.json`` (its per-tool
``rest`` mapping + ``input_schema``), so REST, the manifest, and MCP never drift.

When ``KNOWLEDGE_OPS_MCP_REQUIRE_JWT=1`` every ``tools/call`` must carry a valid
Brigade JWT (see ``brigade_auth``); ``initialize``/``tools/list``/``ping`` stay
open because they only expose the already-public manifest metadata.
"""
from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import __version__, federation
from .authorization import (
    AuthorizationError,
    Principal,
    authorized_write_target,
    can_read_record,
    filter_passages,
    require_tool_principal,
)
from .citations import inspect_citation
from .file_index import deep_read, find_file
from .metrics import record_metric
from .operations import sanitize_error
from .storage import record_audit
from .wiki import get_doc, history, list_docs, tombstone_doc, upsert_doc

logger = logging.getLogger("knowledge_ops.mcp")

PROTOCOL_VERSION = "2025-06-18"
_MANIFEST_PATH = Path(__file__).resolve().parent.parent / ".well-known" / "mcps.json"


# ---- tool handlers: thin adapters over the same logic the REST routes use ----
async def _t_search(a: dict, principal: Principal) -> dict:
    res = await federation.search(
        a["query"], scope=a.get("scope"), sources=a.get("sources"),
        k=int(a.get("k", 8)), principal=principal, as_of=a.get("as_of"),
    )
    return {"answer": res.answer, "passages": [p.__dict__ for p in res.passages]}


async def _t_ground(a: dict, principal: Principal) -> dict:
    return await federation.ground(
        a["query"], scope=a.get("scope"), sources=a.get("sources"),
        k=int(a.get("k", 8)), principal=principal, as_of=a.get("as_of"),
    )


def _t_list_sources(a: dict, principal: Principal) -> dict:
    rows = [s.__dict__ for s in federation.list_sources()]
    if not principal.is_admin:
        for row in rows:
            row["detail"] = ""
    return {"sources": rows}


def _t_get_doc(a: dict, principal: Principal) -> dict:
    doc = get_doc(a["id"])
    if doc and not can_read_record(principal, doc):
        raise AuthorizationError("document is outside the caller's visibility")
    return {"doc": doc}


def _t_list_docs(a: dict, principal: Principal) -> dict:
    return {"docs": [doc for doc in list_docs() if can_read_record(principal, doc)]}


def _t_doc_history(a: dict, principal: Principal) -> dict:
    doc = get_doc(a["id"])
    if doc and not can_read_record(principal, doc):
        raise AuthorizationError("document is outside the caller's visibility")
    return {"history": history(a["id"])}


def _t_find_file(a: dict, principal: Principal) -> dict:
    scope = a.get("scope")
    hits = find_file(a["query"], min(int(a.get("k", 8)), 50), scope, principal=principal)
    return {"passages": [p.__dict__ for p in filter_passages(principal, hits, scope)]}


def _t_deep_read(a: dict, principal: Principal) -> dict:
    hits = deep_read(a["path"], a.get("query", ""), min(int(a.get("k", 8)), 50), principal=principal)
    return {"passages": [p.__dict__ for p in filter_passages(principal, hits)]}


def _t_upsert_doc(a: dict, principal: Principal) -> dict:
    scope, owner = authorized_write_target(principal, a.get("scope"), a.get("owner"))
    doc_id = upsert_doc(
        a["path"], a["md"], scope, owner, valid_from=a.get("valid_from"),
        valid_until=a.get("valid_until"), supersedes_id=a.get("supersedes_id"),
        review_due_at=a.get("review_due_at"),
    )
    record_audit(principal.subject, "mcp.wiki.upsert", doc_id, "success", {"scope": scope})
    # Mirror api.py kb.upsert_doc: drop the cached link + constellation graphs so
    # writes through the brokered MCP path show up immediately, not after TTL.
    from . import graph_api
    graph_api.invalidate_link_caches()
    return {"id": doc_id}


def _t_inspect_citation(a: dict, principal: Principal) -> dict:
    return {"citation": inspect_citation(
        source=a["source"], source_id=a["source_id"], claim=a["claim"], principal=principal,
    )}


def _t_delete_doc(a: dict, principal: Principal) -> dict:
    deletion = tombstone_doc(a["id"], principal=principal, reason=a["reason"])
    # Mirror api.py kb.delete_doc: invalidate so backlinks/orphans refresh at once.
    from . import graph_api
    graph_api.invalidate_link_caches()
    return {"deletion": deletion}


def _t_record_metric(a: dict, principal: Principal) -> dict:
    metric_id = record_metric(a["name"], float(a["value"]), a.get("tags") or {})
    record_audit(principal.subject, "mcp.metric.record", a["name"], "success")
    return {"id": metric_id}


# manifest tool name -> handler (manifest is the single source of truth for the
# names/schemas; this table is the execution half of each tool's `rest` mapping).
_HANDLERS: dict[str, Callable[[dict, Principal], Any | Awaitable[Any]]] = {
    "search": _t_search,
    "ground": _t_ground,
    "list_sources": _t_list_sources,
    "get_doc": _t_get_doc,
    "list_docs": _t_list_docs,
    "doc_history": _t_doc_history,
    "find_file": _t_find_file,
    "deep_read": _t_deep_read,
    "upsert_doc": _t_upsert_doc,
    "inspect_citation": _t_inspect_citation,
    "delete_doc": _t_delete_doc,
    "record_metric": _t_record_metric,
}


def _load_manifest_tools() -> list[dict]:
    try:
        data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except OSError:
        return []
    return data.get("tools", [])


def list_tools() -> list[dict]:
    """MCP tool descriptors (name + description + inputSchema) from the manifest."""
    out = []
    for t in _load_manifest_tools():
        if t.get("name") in _HANDLERS:
            out.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "inputSchema": t.get("input_schema", {"type": "object"}),
            })
    return out


def _rpc_result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _tool_content(payload: Any, is_error: bool = False) -> dict:
    """An MCP CallToolResult: text content (+ structuredContent for clients that use it)."""
    res = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}], "isError": is_error}
    if not is_error and isinstance(payload, dict):
        res["structuredContent"] = payload
    return res


async def _call_tool(name: str, arguments: dict, principal: Principal) -> dict:
    bare = (name or "").split(".")[-1]  # tolerate a `knowledge.` namespace prefix
    handler = _HANDLERS.get(bare)
    if handler is None:
        return _tool_content({"error": "unknown tool"}, is_error=True)
    try:
        result = handler(arguments or {}, principal)
        if inspect.isawaitable(result):
            result = await result
        return _tool_content(result)
    except KeyError:
        return _tool_content({"error": "missing required argument"}, is_error=True)
    except Exception as exc:  # never 500 the broker; surface as an MCP tool error
        receipt = sanitize_error(
            exc, category="mcp_tool_failed", exception_type=type(exc).__name__
        )
        logger.error("tools/call failed tool=%s code=%s", bare, receipt)
        return _tool_content({"error": receipt}, is_error=True)


def _audit_tool_name(name: object) -> str:
    if not isinstance(name, str):
        return "unknown"
    bare = name.split(".")[-1]
    return bare if bare in _HANDLERS else "unknown"


def _required_scopes(name: str) -> list[str]:
    bare = (name or "").split(".")[-1]
    for tool in _load_manifest_tools():
        if tool.get("name") == bare:
            return [str(scope) for scope in tool.get("scopes_required") or []]
    return []


async def handle(message: dict, authorization: str | None = None) -> tuple[int, dict | None]:
    """Process one JSON-RPC message. Returns (http_status, response | None).

    A None response body means "no content" (202) — used for notifications.
    """
    if not isinstance(message, dict):
        return 400, _rpc_error(None, -32700, "parse error")
    req_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        return 200, _rpc_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "knowledge-ops", "version": __version__},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return 202, None
    if method == "ping":
        return 200, _rpc_result(req_id, {})
    if method == "tools/list":
        return 200, _rpc_result(req_id, {"tools": list_tools()})
    if method == "tools/call":
        if not isinstance(params, dict):
            return 400, _rpc_error(req_id, -32602, "invalid params")
        raw_name = params.get("name", "")
        name = raw_name if isinstance(raw_name, str) else ""
        audit_name = _audit_tool_name(raw_name)
        try:
            principal = require_tool_principal(authorization)
        except AuthorizationError:
            record_audit("anonymous", "auth.mcp.authentication", "tools/call", "denied")
            return 401, _rpc_error(req_id, -32001, "unauthorized")
        try:
            principal.require(*_required_scopes(name))
        except AuthorizationError:
            record_audit(
                principal.subject, "mcp.tool", audit_name, "denied",
                {"reason_code": "permission_denied"},
            )
            return 403, _rpc_error(req_id, -32003, "forbidden")
        arguments = params.get("arguments") or {}
        result = await _call_tool(name, arguments, principal)
        record_audit(
            principal.subject, "mcp.tool", audit_name,
            "success" if not result.get("isError") else "error",
        )
        return 200, _rpc_result(req_id, result)

    return 200, _rpc_error(req_id, -32601, f"method not found: {method}")
