from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import replace
import re

from .adapters.ai_memory import search_ai_memory
from .adapters.center_deep import search_center_deep
from .adapters.falkordb import search_falkordb
from .adapters.postgres_source import has_authorization_contract, search_postgres_sources
from .adapters.qdrant import search_qdrant
from .adapters.rest_source import search_rest_sources
from .config import settings
from .authorization import Principal, filter_passages, validate_requested_scope
from .file_index import find_file as file_find
from .models import Passage, SearchResponse, SourceStatus
from .wiki import search_wiki
from .text import STOPWORDS, stable_id, tokenize


def enforce_rls(passages: list[Passage], principal: Principal, scope: str | None) -> list[Passage]:
    """Apply the same identity-derived policy after every source adapter."""
    return filter_passages(principal, passages, scope)


def merge_rerank(passages: list[Passage], k: int) -> list[Passage]:
    by_key: dict[tuple[str, str], Passage] = {}
    source_seen: defaultdict[str, int] = defaultdict(int)
    for p in sorted(passages, key=lambda h: h.score, reverse=True):
        key = (p.source, p.source_id)
        if key not in by_key:
            adjusted = p.score - min(source_seen[p.source] * 0.15, 0.75)
            source_seen[p.source] += 1
            by_key[key] = replace(p, score=adjusted)
    return sorted(by_key.values(), key=lambda h: h.score, reverse=True)[:k]


def synthesize_answer(query: str, hits: list[Passage]) -> str:
    if not hits:
        return "No accessible evidence was found."
    excerpts = []
    for i, h in enumerate(hits[:6], 1):
        excerpt = re.sub(r"\s+", " ", h.text).strip()
        if len(excerpt) > 280:
            excerpt = excerpt[:277].rstrip() + "..."
        excerpts.append(f"[{i}] {excerpt}\n    Source: {h.citation()}")
    return "Evidence excerpts (not a generated factual conclusion):\n\n" + "\n\n".join(excerpts)


def _grounding_assessment(claim: str, hits: list[Passage]) -> tuple[str, str, list[Passage]]:
    """Conservative lexical/polarity triage, explicitly not semantic entailment."""
    terms = {t for t in tokenize(claim) if t not in STOPWORDS and len(t) > 2}
    is_question = claim.rstrip().endswith("?") or bool(re.match(r"^(who|what|when|where|why|how)\b", claim.strip(), re.I))
    claim_negative = bool(re.search(r"\b(no|not|never|without|cannot|can't|doesn't|isn't|aren't|won't)\b", claim, re.I))
    relations: list[str] = []
    annotated: list[Passage] = []
    for hit in hits:
        hit_terms = set(tokenize(hit.text))
        coverage = len(terms & hit_terms) / max(1, len(terms))
        passage_negative = bool(re.search(r"\b(no|not|never|without|cannot|can't|doesn't|isn't|aren't|won't)\b", hit.text, re.I))
        if coverage < 0.35:
            relation = "context_only"
        elif is_question or coverage < 0.65:
            relation = "related"
        elif claim_negative == passage_negative:
            relation = "possible_support"
        else:
            relation = "possible_contradiction"
        relations.append(relation)
        metadata = dict(hit.metadata)
        metadata.update({"evidence_relation": relation, "claim_term_coverage": round(coverage, 3)})
        annotated.append(replace(hit, metadata=metadata))
    relation_set = set(relations)
    if {"possible_support", "possible_contradiction"} <= relation_set:
        verdict = "possible_conflict"
    elif "possible_contradiction" in relation_set:
        verdict = "possible_contradiction"
    elif "possible_support" in relation_set:
        verdict = "possible_support"
    elif "related" in relation_set:
        verdict = "related_evidence"
    else:
        verdict = "not_enough_evidence"
    return verdict, "lexical_polarity_triage_v1", annotated


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


async def search(
    query: str,
    scope: str | None = None,
    sources: list[str] | None = None,
    k: int = 8,
    *,
    principal: Principal,
    as_of: str | None = None,
) -> SearchResponse:
    principal.require("read:knowledge")
    validate_requested_scope(principal, scope)
    if not query.strip():
        raise ValueError("query must not be empty")
    k = max(1, min(int(k), 50))
    if as_of:
        if not sources:
            sources = ["wiki"]
        unsupported = sorted(set(sources) - {"wiki"})
        if unsupported:
            raise ValueError(
                "as_of retrieval supports only the versioned wiki source; "
                f"unsupported sources: {', '.join(unsupported)}"
            )
    selected, qdrant_sources, falkor_sources, rest_sources, postgres_sources = _source_parts(sources)
    sync_hits: list[Passage] = []
    if "org_memory" in selected:
        sync_hits.extend(search_ai_memory(query, k=k, scope=scope, principal=principal))
    if "wiki" in selected:
        sync_hits.extend(search_wiki(query, k=k, scope=scope, principal=principal, as_of=as_of))
    if "file_index" in selected:
        sync_hits.extend(file_find(query, k=k, scope=scope, principal=principal))

    async_tasks = []
    if "qdrant" in selected or qdrant_sources:
        async_tasks.append(search_qdrant(
            query, k=k, scope=scope, collections=sorted(qdrant_sources) or None, principal=principal
        ))
    if "falkordb" in selected or falkor_sources:
        async_tasks.append(search_falkordb(
            query, k=k, scope=scope, graphs=sorted(falkor_sources) or None, principal=principal
        ))
    if "center_deep" in selected:
        async_tasks.append(search_center_deep(
            query,
            k=k,
            allow_catalogue=principal.is_admin or principal.has_scope("read:knowledge:restricted"),
        ))
    if "rest" in selected or rest_sources:
        async_tasks.append(search_rest_sources(
            query, k=k, scope=scope, names=rest_sources or None, principal=principal
        ))
    if "postgres" in selected or postgres_sources:
        async_tasks.append(search_postgres_sources(
            query, k=k, scope=scope, names=postgres_sources or None, principal=principal
        ))
    if async_tasks:
        for result in await asyncio.gather(*async_tasks, return_exceptions=True):
            if isinstance(result, list):
                sync_hits.extend(result)
    hits = merge_rerank(enforce_rls(sync_hits, principal, scope), k)
    return SearchResponse(query=query, passages=hits, answer=synthesize_answer(query, hits))


async def ground(
    claim: str,
    scope: str | None = None,
    k: int = 8,
    *,
    principal: Principal,
    sources: list[str] | None = None,
    as_of: str | None = None,
) -> dict:
    resp = await search(claim, scope=scope, sources=sources, k=k, principal=principal, as_of=as_of)
    verdict, method, passages = _grounding_assessment(claim, resp.passages)
    claim_id = stable_id("claim:" + " ".join(tokenize(claim)))
    linked: list[Passage] = []
    claim_terms = {term for term in tokenize(claim) if term not in STOPWORDS and len(term) > 2}
    for passage in passages:
        metadata = dict(passage.metadata)
        metadata["claim_id"] = claim_id
        metadata["citation_id"] = stable_id(f"citation:{claim_id}:{passage.source}:{passage.source_id}")
        metadata["matched_claim_terms"] = sorted(claim_terms & set(tokenize(passage.text)))
        linked.append(replace(passage, metadata=metadata))
    return {
        "claim": claim,
        "claim_id": claim_id,
        "verdict": verdict,
        "verification_status": "heuristic_not_verified",
        "assessment_method": method,
        "answer": synthesize_answer(claim, linked),
        "passages": [p.__dict__ for p in linked],
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
        SourceStatus(
            f"rest:{cfg.get('name')}",
            bool(cfg.get("url") and cfg.get("enforces_authorization")),
            "configured" if cfg.get("url") and cfg.get("enforces_authorization") else "blocked-missing-auth-contract",
            str(cfg.get("url") or ""),
        )
        for cfg in settings.rest_sources
        if cfg.get("name")
    )
    sources.extend(
        SourceStatus(
            f"postgres:{cfg.get('name')}",
            has_authorization_contract(cfg),
            "configured" if has_authorization_contract(cfg) else "blocked-missing-auth-contract",
            "read-only SQL",
        )
        for cfg in settings.postgres_sources
        if cfg.get("name")
    )
    return sources
