from __future__ import annotations

import hashlib

import httpx

from ..config import settings
from ..authorization import Principal, validate_requested_scope
from ..models import Passage


async def embed(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(f"{settings.embed_base_url}/embeddings", json={"model": settings.embed_model, "input": texts})
        res.raise_for_status()
        data = res.json()["data"]
    return [d["embedding"] for d in data]


def _visibility_filter(principal: Principal, scope: str | None) -> dict | None:
    principal.require("read:knowledge")
    validate_requested_scope(principal, scope)
    if principal.is_admin and scope in (None, "", "all"):
        return None
    if scope == "global":
        return {"must": [{"key": "scope", "match": {"value": "global"}}]}
    owners = sorted(principal.identities)
    personal = {"must": [
        {"key": "scope", "match": {"value": "personal"}},
        {"key": "owner", "match": {"any": owners}},
    ]}
    if scope == "personal":
        return personal
    if scope and scope.startswith("workspace:"):
        return {"must": [{"key": "scope", "match": {"value": scope}}]}
    visible: list[dict] = [{"key": "scope", "match": {"value": "global"}}, personal]
    if principal.has_scope("read:knowledge:restricted"):
        visible.append({"key": "scope", "match": {"value": "restricted"}})
    return {"should": visible}


async def search_qdrant(
    query: str,
    k: int = 5,
    scope: str | None = None,
    collections: list[str] | None = None,
    *,
    principal: Principal | None = None,
) -> list[Passage]:
    if not settings.enable_qdrant:
        return []
    if principal is None:
        return []
    try:
        vector = (await embed([query]))[0]
    except Exception:
        return []
    hits: list[Passage] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for coll in (collections or settings.qdrant_collections):
            flt = _visibility_filter(principal, scope)
            try:
                payload = {"vector": vector, "limit": k, "with_payload": True, "filter": flt}
                res = await client.post(f"{settings.qdrant_url.rstrip('/')}/collections/{coll}/points/search", json=payload)
                if res.status_code in (400, 422):
                    payload["vector"] = {"name": "dense", "vector": vector}
                    res = await client.post(f"{settings.qdrant_url.rstrip('/')}/collections/{coll}/points/search", json=payload)
                res.raise_for_status()
            except Exception:
                continue
            for row in res.json().get("result", []):
                p = row.get("payload") or {}
                text = p.get("text") or p.get("content") or p.get("chunk") or ""
                title = p.get("title") or p.get("filename") or coll
                sid = str(p.get("uid") or p.get("id") or row.get("id") or hashlib.sha1(text.encode()).hexdigest())
                item_scope = str(p.get("scope") or "restricted")
                if item_scope not in {"global", "personal", "restricted"} and not item_scope.startswith("workspace:"):
                    item_scope = "restricted"
                hits.append(Passage(
                    source=f"qdrant:{coll}",
                    source_id=sid,
                    title=title,
                    text=text,
                    score=float(row.get("score") or 0),
                    scope=item_scope,
                    owner=str(p.get("owner") or ("magic-unicorn" if item_scope == "global" else "")),
                    uri=p.get("source_file") or p.get("path") or "",
                    metadata=p,
                ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
