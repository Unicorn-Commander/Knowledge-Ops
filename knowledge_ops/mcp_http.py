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

from . import federation
from .brigade_auth import AuthError, require_for_tool_call
from .file_index import deep_read, find_file
from .metrics import record_metric
from .wiki import get_doc, history, list_docs, upsert_doc

logger = logging.getLogger("knowledge_ops.mcp")

PROTOCOL_VERSION = "2025-06-18"
_MANIFEST_PATH = Path(__file__).resolve().parent.parent / ".well-known" / "mcps.json"


# ---- tool handlers: thin adapters over the same logic the REST routes use ----
async def _t_search(a: dict) -> dict:
    res = await federation.search(a["query"], scope=a.get("scope"), sources=a.get("sources"), k=int(a.get("k", 8)))
    return {"answer": res.answer, "passages": [p.__dict__ for p in res.passages]}


async def _t_ground(a: dict) -> dict:
    return await federation.ground(a["query"], scope=a.get("scope"), k=int(a.get("k", 8)))


def _t_list_sources(a: dict) -> dict:
    return {"sources": [s.__dict__ for s in federation.list_sources()]}


def _t_get_doc(a: dict) -> dict:
    return {"doc": get_doc(a["id"])}


def _t_list_docs(a: dict) -> dict:
    return {"docs": list_docs()}


def _t_doc_history(a: dict) -> dict:
    return {"history": history(a["id"])}


def _t_find_file(a: dict) -> dict:
    return {"passages": [p.__dict__ for p in find_file(a["query"], int(a.get("k", 8)), a.get("scope"))]}


def _t_deep_read(a: dict) -> dict:
    return {"passages": [p.__dict__ for p in deep_read(a["path"], a.get("query", ""), int(a.get("k", 8)))]}


def _t_upsert_doc(a: dict) -> dict:
    return {"id": upsert_doc(a["path"], a["md"], a.get("scope", "global"), a.get("owner", "magic-unicorn"))}


def _t_record_metric(a: dict) -> dict:
    return {"id": record_metric(a["name"], float(a["value"]), a.get("tags") or {})}


# manifest tool name -> handler (manifest is the single source of truth for the
# names/schemas; this table is the execution half of each tool's `rest` mapping).
_HANDLERS: dict[str, Callable[[dict], Any | Awaitable[Any]]] = {
    "search": _t_search,
    "ground": _t_ground,
    "list_sources": _t_list_sources,
    "get_doc": _t_get_doc,
    "list_docs": _t_list_docs,
    "doc_history": _t_doc_history,
    "find_file": _t_find_file,
    "deep_read": _t_deep_read,
    "upsert_doc": _t_upsert_doc,
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


async def _call_tool(name: str, arguments: dict) -> dict:
    bare = (name or "").split(".")[-1]  # tolerate a `knowledge.` namespace prefix
    handler = _HANDLERS.get(bare)
    if handler is None:
        return _tool_content({"error": f"unknown tool: {name}"}, is_error=True)
    try:
        result = handler(arguments or {})
        if inspect.isawaitable(result):
            result = await result
        return _tool_content(result)
    except KeyError as exc:
        return _tool_content({"error": f"missing required argument: {exc}"}, is_error=True)
    except Exception as exc:  # never 500 the broker; surface as an MCP tool error
        logger.exception("tools/call %s failed", name)
        return _tool_content({"error": f"{type(exc).__name__}: {exc}"}, is_error=True)


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
            "serverInfo": {"name": "knowledge-ops", "version": "0.1.0"},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return 202, None
    if method == "ping":
        return 200, _rpc_result(req_id, {})
    if method == "tools/list":
        return 200, _rpc_result(req_id, {"tools": list_tools()})
    if method == "tools/call":
        try:
            require_for_tool_call(authorization)
        except AuthError as exc:
            return 401, _rpc_error(req_id, -32001, f"unauthorized: {exc}")
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        result = await _call_tool(name, arguments)
        return 200, _rpc_result(req_id, result)

    return 200, _rpc_error(req_id, -32601, f"method not found: {method}")
