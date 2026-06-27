from __future__ import annotations

import asyncio

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit("Install MCP support first: pip install 'knowledge-ops[mcp]' or pip install mcp") from exc

from . import federation
from .file_index import deep_read, find_file
from .metrics import record_metric, render_metric_chart
from .wiki import get_doc, history, list_docs, upsert_doc

mcp = FastMCP("knowledge_ops")


@mcp.tool(name="kb.search")
def kb_search(query: str, scope: str = "", sources: list[str] | None = None, k: int = 8) -> dict:
    res = asyncio.run(federation.search(query, scope=scope or None, sources=sources, k=k))
    return {"answer": res.answer, "passages": [p.__dict__ for p in res.passages]}


@mcp.tool(name="kb.ground")
def kb_ground(claim: str, scope: str = "", k: int = 8) -> dict:
    return asyncio.run(federation.ground(claim, scope=scope or None, k=k))


@mcp.tool(name="kb.list_sources")
def kb_list_sources() -> dict:
    return {"sources": [s.__dict__ for s in federation.list_sources()]}


@mcp.tool(name="kb.get_doc")
def kb_get_doc(id: str) -> dict:
    return {"doc": get_doc(id)}


@mcp.tool(name="kb.upsert_doc")
def kb_upsert_doc(path: str, md: str, scope: str = "global", owner: str = "magic-unicorn") -> dict:
    return {"id": upsert_doc(path, md, scope, owner)}


@mcp.tool(name="kb.list_docs")
def kb_list_docs() -> dict:
    return {"docs": list_docs()}


@mcp.tool(name="kb.doc_history")
def kb_doc_history(id: str) -> dict:
    return {"history": history(id)}


@mcp.tool(name="kb.record_metric")
def kb_record_metric(name: str, value: float, tags: dict | None = None) -> dict:
    return {"id": record_metric(name, value, tags or {})}


@mcp.tool(name="kb.get_metric_chart")
def kb_get_metric_chart(name: str, limit: int = 80) -> str:
    return render_metric_chart(name, limit)


@mcp.tool(name="kb.find_file")
def kb_find_file(query: str, k: int = 8, scope: str = "") -> dict:
    return {"passages": [p.__dict__ for p in find_file(query, k, scope or None)]}


@mcp.tool(name="kb.deep_read")
def kb_deep_read(path: str, query: str = "", k: int = 8) -> dict:
    return {"passages": [p.__dict__ for p in deep_read(path, query, k)]}


if __name__ == "__main__":
    mcp.run()
