from __future__ import annotations

import asyncio
from collections import defaultdict
import re

from .adapters.ai_memory import search_ai_memory
from .adapters.center_deep import search_center_deep
from .adapters.falkordb import search_falkordb
from .adapters.postgres_source import search_postgres_sources
from .adapters.qdrant import search_qdrant
from .adapters.rest_source import search_rest_sources
from .config import settings
from .file_index import deep_read as file_deep_read
from .file_index import find_file as file_find
from .models import Passage, SearchResponse, SourceStatus
from .wiki import get_doc, history, list_docs, search_wiki, upsert_doc
from .metrics import record_metric, render_metric_chart


def enforce_rls(passages: list[Passage], scope: str | None) -> list[Passage]:
    if not scope or scope == "all":
        return passages
    return [p for p in passages if p.scope == scope]


def merge_rerank(passages: list[Passage], k: int) -> list[Passage]:
    by_key: dict[tuple[str, str], Passage] = {}
    source_seen: defaultdict[str, int] = defaultdict(int)
    for p in sorted(passages, key=lambda h: h.score, reverse=True):
        key = (p.source, p.source_id)
        if key not in by_key:
            p.score -= min(source_seen[p.source] * 0.15, 0.75)
            source_seen[p.source] += 1
            by_key[key] = p
    return sorted(by_key.values(), key=lambda h: h.score, reverse=True)[:k]


def synthesize_answer(query: str, hits: list[Passage]) -> str:
    if not hits:
        return "No grounded evidence found."
    sources = []
    for i, h in enumerate(hits[:6], 1):
        sources.append(f"[{i}] {h.citation()} ({h.scope}, owner={h.owner})")
    lead_hit = hits[0]
    for h in hits:
        lower = h.text.lower()
        if "magic unicorn builds" in lower or "one-sentence story" in lower or "master one-liner" in lower:
            lead_hit = h
            break
    lead = lead_hit.text.strip().replace("\n", " ")
    for marker in ("Magic Unicorn builds", "Magic Unicorn turns", "Sovereign AI you actually own"):
        pos = lead.lower().find(marker.lower())
        if pos >= 0:
            tail = lead[pos:]
            end = re.search(r"(?<=[.!?])\s+", tail)
            lead = tail[: end.start() if end else min(len(tail), 420)]
            break
    if len(lead) > 360:
        lead = lead[:357].rstrip() + "..."
    return f"Grounded summary: {lead}\n\nCitations:\n" + "\n".join(sources)


def _source_parts(sources: list[str] | None) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    selected = set(sources or ["org_memory", "wiki", "file_index", "qdrant", "falkordb", "center_deep", "rest", "postgres"])
    qdrant = {s.split(":", 1)[1] for s in selected if s.startswith("qdrant:")}
    falkor = {s.split(":", 1)[1] for s in selected if s.startswith("falkordb:")}
    rest = {s.split(":", 1)[1] for s in selected if s.startswith("rest:")}
    postgres = {s.split(":", 1)[1] for s in selected if s.startswith("postgres:")}
    aliases = {s for s in selected if ":" not in s}
    for cfg in settings.rest_sources:
        name = str(cfg.get("name") or "")
        if name in aliases:
            rest.add(name)
    for cfg in settings.postgres_sources:
        name = str(cfg.get("name") or "")
        if name in aliases:
            postgres.add(name)
    for coll in settings.qdrant_collections:
        if coll in aliases:
            qdrant.add(coll)
    for graph in settings.falkordb_graphs:
        if graph in aliases:
            falkor.add(graph)
    return selected, qdrant, falkor, rest, postgres


async def search(query: str, scope: str | None = None, sources: list[str] | None = None, k: int = 8) -> SearchResponse:
    selected, qdrant_sources, falkor_sources, rest_sources, postgres_sources = _source_parts(sources)
    sync_hits: list[Passage] = []
    if "org_memory" in selected:
        sync_hits.extend(search_ai_memory(query, k=k, scope=scope))
    if "wiki" in selected:
        sync_hits.extend(search_wiki(query, k=k, scope=scope))
    if "file_index" in selected:
        sync_hits.extend(file_find(query, k=k, scope=scope))

    async_tasks = []
    if "qdrant" in selected or qdrant_sources:
        async_tasks.append(search_qdrant(query, k=k, scope=scope, collections=sorted(qdrant_sources) or None))
    if "falkordb" in selected or falkor_sources:
        async_tasks.append(search_falkordb(query, k=k, scope=scope, graphs=sorted(falkor_sources) or None))
    if "center_deep" in selected:
        async_tasks.append(search_center_deep(query, k=k))
    if "rest" in selected or rest_sources:
        async_tasks.append(search_rest_sources(query, k=k, scope=scope, names=rest_sources or None))
    if "postgres" in selected or postgres_sources:
        async_tasks.append(search_postgres_sources(query, k=k, scope=scope, names=postgres_sources or None))
    if async_tasks:
        for result in await asyncio.gather(*async_tasks, return_exceptions=True):
            if isinstance(result, list):
                sync_hits.extend(result)
    hits = merge_rerank(enforce_rls(sync_hits, scope), k)
    return SearchResponse(query=query, passages=hits, answer=synthesize_answer(query, hits))


async def ground(claim: str, scope: str | None = None, k: int = 8) -> dict:
    resp = await search(claim, scope=scope, k=k)
    return {
        "claim": claim,
        "verdict": "evidence_found" if resp.passages else "not_enough_evidence",
        "answer": resp.answer,
        "passages": [p.__dict__ for p in resp.passages],
    }


def list_sources() -> list[SourceStatus]:
    sources = [
        SourceStatus("org_memory", settings.ai_memory_chunks.exists(), "local-jsonl", str(settings.ai_memory_chunks)),
        SourceStatus("wiki", True, "sqlite+markdown", str(settings.wiki_dir)),
        SourceStatus("file_index", True, "sqlite-fts", str(settings.db_path)),
        SourceStatus("metrics", True, "sqlite+svg", str(settings.db_path)),
        SourceStatus("qdrant", settings.enable_qdrant, "configured" if settings.enable_qdrant else "disabled", settings.qdrant_url),
        SourceStatus("falkordb", settings.enable_falkordb, "configured" if settings.enable_falkordb else "disabled", f"{settings.falkordb_url} ({settings.falkordb_graph})"),
        SourceStatus("center_deep", settings.enable_center_deep, "configured" if settings.enable_center_deep else "disabled", settings.center_deep_url),
    ]
    sources.extend(
        SourceStatus(f"qdrant:{coll}", settings.enable_qdrant, "configured" if settings.enable_qdrant else "disabled", settings.qdrant_url)
        for coll in settings.qdrant_collections
    )
    sources.extend(
        SourceStatus(f"falkordb:{graph}", settings.enable_falkordb, "configured" if settings.enable_falkordb else "disabled", settings.falkordb_url)
        for graph in settings.falkordb_graphs
    )
    sources.extend(
        SourceStatus(f"rest:{cfg.get('name')}", bool(cfg.get("url")), "configured" if cfg.get("url") else "disabled", str(cfg.get("url") or ""))
        for cfg in settings.rest_sources
        if cfg.get("name")
    )
    sources.extend(
        SourceStatus(f"postgres:{cfg.get('name')}", bool(cfg.get("dsn") and cfg.get("sql")), "configured" if cfg.get("dsn") and cfg.get("sql") else "disabled", "read-only SQL")
        for cfg in settings.postgres_sources
        if cfg.get("name")
    )
    return sources
