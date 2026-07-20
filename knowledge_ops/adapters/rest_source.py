from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from ..authorization import Principal, can_read_record
from ..models import Passage


def _rows(data: Any, key: str | None) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        if key and isinstance(data.get(key), list):
            return [r for r in data[key] if isinstance(r, dict)]
        for candidate in ("results", "items", "docs", "passages", "data"):
            if isinstance(data.get(candidate), list):
                return [r for r in data[candidate] if isinstance(r, dict)]
    return []


async def search_rest_sources(
    query: str,
    k: int = 5,
    scope: str | None = None,
    names: set[str] | None = None,
    *,
    principal: Principal | None = None,
) -> list[Passage]:
    if principal is None:
        return []
    hits: list[Passage] = []
    async with httpx.AsyncClient(timeout=8) as client:
        for cfg in settings.rest_sources:
            name = str(cfg.get("name") or "").strip()
            if not name or (names and name not in names):
                continue
            if not cfg.get("enforces_authorization"):
                continue
            source_scope = str(cfg.get("scope") or "restricted")
            source_owner = str(cfg.get("owner") or name)
            if not can_read_record(principal, {"scope": source_scope, "owner": source_owner}, scope):
                continue
            method = str(cfg.get("method") or "GET").upper()
            url = str(cfg.get("url") or "").strip()
            if not url:
                continue
            params = dict(cfg.get("params") or {})
            body = dict(cfg.get("json") or {})
            params[cfg.get("query_param", "q")] = query
            params[cfg.get("limit_param", "k")] = k
            params[cfg.get("owner_param", "owner")] = principal.subject
            if scope and scope != "all":
                params[cfg.get("scope_param", "scope")] = scope
            try:
                if method == "POST":
                    body.update({"query": query, "q": query, "k": k, "owner": principal.subject})
                    if scope and scope != "all":
                        body["scope"] = scope
                    res = await client.post(url, params=params if cfg.get("send_params") else None, json=body)
                else:
                    res = await client.get(url, params=params)
                res.raise_for_status()
                rows = _rows(res.json(), cfg.get("results_key"))
            except Exception:
                continue
            for i, row in enumerate(rows[:k]):
                text = row.get("text") or row.get("content") or row.get("snippet") or row.get("summary") or row.get("body") or ""
                title = row.get("title") or row.get("name") or row.get("path") or f"{name} result"
                source_id = str(row.get("id") or row.get("uid") or row.get("url") or row.get("path") or i)
                item_scope = str(row.get("scope") or "restricted")
                if item_scope not in {"global", "personal", "restricted"} and not item_scope.startswith("workspace:"):
                    item_scope = "restricted"
                hits.append(Passage(
                    source=f"rest:{name}",
                    source_id=source_id,
                    title=str(title),
                    text=str(text or title),
                    score=float(row.get("score") or (1.0 / (i + 1))),
                    scope=item_scope,
                    owner=row.get("owner") or name,
                    uri=row.get("url") or row.get("uri") or row.get("path") or "",
                    metadata=row,
                ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
