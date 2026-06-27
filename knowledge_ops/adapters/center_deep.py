from __future__ import annotations

import httpx

from ..config import settings
from ..models import Passage


async def search_center_deep(query: str, k: int = 5) -> list[Passage]:
    if not settings.enable_center_deep:
        return []
    data = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            res = await client.get(f"{settings.center_deep_url.rstrip('/')}/search", params={"q": query, "format": "json"})
            if res.headers.get("content-type", "").startswith("application/json"):
                res.raise_for_status()
                data = res.json()
    except Exception:
        data = None
    if data is None:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                res = await client.get(f"{settings.center_deep_url.rstrip('/')}/api/v1/catalogue/quick-search", params={"q": query, "limit": k})
                res.raise_for_status()
                data = res.json()
        except Exception:
            return []
    if isinstance(data, dict) and ("contacts" in data or "companies" in data):
        rows = []
        for kind in ("companies", "contacts"):
            for item in data.get(kind) or []:
                item = dict(item)
                item["_kind"] = kind[:-1]
                rows.append(item)
    else:
        rows = data.get("results") if isinstance(data, dict) else data
    hits: list[Passage] = []
    for i, r in enumerate((rows or [])[:k]):
        title = r.get("title") or r.get("company_name") or r.get("name") or r.get("email") or r.get("url") or "Center Deep result"
        text = r.get("content") or r.get("snippet") or r.get("description") or r.get("summary") or title
        hits.append(Passage(
            source="center_deep",
            source_id=str(r.get("id") or r.get("url") or r.get("email") or title or i),
            title=title,
            text=text,
            score=float(r.get("score") or (1.0 / (i + 1))),
            scope="global",
            owner="web",
            uri=r.get("url") or r.get("website") or "",
            metadata=r,
        ))
    return hits
