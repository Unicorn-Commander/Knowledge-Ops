from __future__ import annotations

import hashlib

import httpx

from ..config import settings
from ..models import Passage


async def embed(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(f"{settings.embed_base_url}/embeddings", json={"model": settings.embed_model, "input": texts})
        res.raise_for_status()
        data = res.json()["data"]
    return [d["embedding"] for d in data]


async def search_qdrant(query: str, k: int = 5, scope: str | None = None, collections: list[str] | None = None) -> list[Passage]:
    if not settings.enable_qdrant:
        return []
    try:
        vector = (await embed([query]))[0]
    except Exception:
        return []
    hits: list[Passage] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for coll in (collections or settings.qdrant_collections):
            flt = None
            if scope and scope != "all":
                flt = {"must": [{"key": "scope", "match": {"value": scope}}]}
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
                item_scope = p.get("scope", "global")
                hits.append(Passage(
                    source=f"qdrant:{coll}",
                    source_id=sid,
                    title=title,
                    text=text,
                    score=float(row.get("score") or 0),
                    scope=item_scope,
                    owner=p.get("owner", "magic-unicorn" if item_scope == "global" else "aaron"),
                    uri=p.get("source_file") or p.get("path") or "",
                    metadata=p,
                ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
