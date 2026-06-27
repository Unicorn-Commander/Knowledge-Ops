from __future__ import annotations

import httpx

from ..config import settings
from ..models import Passage


async def search_center_deep(query: str, k: int = 5) -> list[Passage]:
    if not settings.enable_center_deep:
        return []
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            res = await client.get(f"{settings.center_deep_url.rstrip('/')}/search", params={"q": query, "format": "json"})
            res.raise_for_status()
            data = res.json()
    except Exception:
        return []
    rows = data.get("results") if isinstance(data, dict) else data
    hits: list[Passage] = []
    for i, r in enumerate((rows or [])[:k]):
        title = r.get("title") or r.get("url") or "Center Deep result"
        text = r.get("content") or r.get("snippet") or title
        hits.append(Passage(
            source="center_deep",
            source_id=r.get("url") or str(i),
            title=title,
            text=text,
            score=float(r.get("score") or (1.0 / (i + 1))),
            scope="global",
            owner="web",
            uri=r.get("url") or "",
        ))
    return hits
