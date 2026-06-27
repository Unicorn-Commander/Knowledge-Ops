from __future__ import annotations

import asyncio
from collections import defaultdict
import re

from .adapters.ai_memory import search_ai_memory
from .adapters.center_deep import search_center_deep
from .adapters.falkordb import search_falkordb
from .adapters.qdrant import search_qdrant
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


async def search(query: str, scope: str | None = None, sources: list[str] | None = None, k: int = 8) -> SearchResponse:
    selected = set(sources or ["org_memory", "wiki", "file_index", "qdrant", "falkordb", "center_deep"])
    sync_hits: list[Passage] = []
    if "org_memory" in selected:
        sync_hits.extend(search_ai_memory(query, k=k, scope=scope))
    if "wiki" in selected:
        sync_hits.extend(search_wiki(query, k=k, scope=scope))
    if "file_index" in selected:
        sync_hits.extend(file_find(query, k=k, scope=scope))

    async_tasks = []
    if "qdrant" in selected:
        async_tasks.append(search_qdrant(query, k=k, scope=scope))
    if "falkordb" in selected:
        async_tasks.append(search_falkordb(query, k=k, scope=scope))
    if "center_deep" in selected:
        async_tasks.append(search_center_deep(query, k=k))
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
    return [
        SourceStatus("org_memory", settings.ai_memory_chunks.exists(), "local-jsonl", str(settings.ai_memory_chunks)),
        SourceStatus("wiki", True, "sqlite+markdown", str(settings.wiki_dir)),
        SourceStatus("file_index", True, "sqlite-fts", str(settings.db_path)),
        SourceStatus("metrics", True, "sqlite+svg", str(settings.db_path)),
        SourceStatus("qdrant", settings.enable_qdrant, "configured" if settings.enable_qdrant else "disabled", settings.qdrant_url),
        SourceStatus("falkordb", settings.enable_falkordb, "configured" if settings.enable_falkordb else "disabled", f"{settings.falkordb_url} ({settings.falkordb_graph})"),
        SourceStatus("center_deep", settings.enable_center_deep, "configured" if settings.enable_center_deep else "disabled", settings.center_deep_url),
    ]
