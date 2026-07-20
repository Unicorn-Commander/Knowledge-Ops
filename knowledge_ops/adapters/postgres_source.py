from __future__ import annotations

import asyncio
from typing import Any

from ..config import settings
from ..authorization import Principal, can_read_record
from ..models import Passage
from ..text import lexical_score


def has_authorization_contract(cfg: dict[str, Any]) -> bool:
    """Require both an explicit declaration and identity-bound SQL."""
    sql = str(cfg.get("sql") or "").strip()
    has_identity_parameter = "%(owner)s" in sql or "%(owners)s" in sql
    return bool(
        cfg.get("dsn")
        and cfg.get("enforces_authorization") is True
        and sql.lower().lstrip().startswith("select")
        and has_identity_parameter
    )


def _query_sync(cfg: dict[str, Any], query: str, k: int, scope: str | None, principal: Principal) -> list[Passage]:
    try:
        import psycopg
    except ModuleNotFoundError:
        return []

    name = str(cfg.get("name") or "").strip()
    dsn = str(cfg.get("dsn") or "").strip()
    sql = str(cfg.get("sql") or "").strip()
    if not name or not has_authorization_contract(cfg):
        return []

    source_scope = str(cfg.get("scope") or "restricted")
    source_owner = str(cfg.get("owner") or name)
    if not can_read_record(principal, {"scope": source_scope, "owner": source_owner}, scope):
        return []
    params = {
        "query": query, "q": query, "k": max(1, int(k)), "scope": scope or "",
        "owner": principal.subject, "owners": sorted(principal.identities),
    }
    try:
        with psycopg.connect(dsn, connect_timeout=4) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [d.name for d in cur.description or []]
                rows = [dict(zip(cols, row)) for row in cur.fetchmany(k)]
    except Exception:
        return []

    hits: list[Passage] = []
    for i, row in enumerate(rows):
        text = row.get("text") or row.get("content") or row.get("summary") or row.get("body") or ""
        title = row.get("title") or row.get("name") or row.get("path") or f"{name} row"
        item_scope = str(row.get("scope") or "restricted")
        if item_scope not in {"global", "personal", "restricted"} and not item_scope.startswith("workspace:"):
            item_scope = "restricted"
        if scope and scope != "all" and item_scope != scope:
            continue
        score = row.get("score")
        if score is None:
            score = lexical_score(query, str(text), str(title)) + (1.0 / (i + 1))
        hits.append(Passage(
            source=f"postgres:{name}",
            source_id=str(row.get("id") or row.get("uid") or i),
            title=str(title),
            text=str(text or title),
            score=float(score),
            scope=str(item_scope),
            owner=str(row.get("owner") or name),
            uri=str(row.get("uri") or row.get("url") or row.get("path") or ""),
            metadata={k: v for k, v in row.items() if k not in {"text", "content", "body"}},
        ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


async def search_postgres_sources(
    query: str,
    k: int = 5,
    scope: str | None = None,
    names: set[str] | None = None,
    *,
    principal: Principal | None = None,
) -> list[Passage]:
    if principal is None:
        return []
    tasks = []
    for cfg in settings.postgres_sources:
        name = str(cfg.get("name") or "").strip()
        if name and (not names or name in names):
            tasks.append(asyncio.to_thread(_query_sync, cfg, query, k, scope, principal))
    hits: list[Passage] = []
    if tasks:
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, list):
                hits.extend(result)
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
