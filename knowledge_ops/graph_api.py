"""Constellation endpoints: /kb.graph, /kb.stats, /kb.me.

Read-only sampling of the same stores federated search uses. Qdrant sampling is
gated by the exact same ``_visibility_filter`` that ``search_qdrant`` applies, so
the constellation can never show a point that search would hide. Every per-store
step degrades to "skip" — an unreachable backend thins the graph, it never 500s.

The full graph response is cached in-process for 10 minutes per visibility
identity (scope, admin flag, scope-set, identity-set — identities are part of
the key so two users who share scopes but own different personal records never
share a cached graph).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request

from .adapters.falkordb import classify_graph
from .adapters.qdrant import _visibility_filter
from .authorization import Principal, can_read_record, validate_requested_scope
from .config import settings
from .models import utcnow
from .storage import connect
from .wiki import list_docs

logger = logging.getLogger("knowledge_ops.graph")

router = APIRouter()


def _current_principal(request: Request) -> Principal:
    # Late import: api.py includes this router, so a module-level import of the
    # dependency would be circular. The lookup also honors test monkeypatching.
    from .api import current_principal

    return current_principal(request)


GraphPrincipal = Annotated[Principal, Depends(_current_principal)]

# ---------------------------------------------------------------- cache
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 600.0

_INTRA_EDGE_NODES = 60  # recommend() fan-out per collection
_INTRA_EDGE_K = 4
_CROSS_EDGE_NODES = 30  # wiki vectors searched against the other collections
_CROSS_K = 2
_CROSS_THRESHOLD = 0.55
_FALKOR_SAMPLE = 50
_SIZE_BUCKETS = (200, 500, 1000, 2000)  # text length -> node size 1..5


def _cache_key(principal: Principal, scope: str | None) -> tuple:
    return (
        scope or "",
        principal.is_admin,
        frozenset(principal.scopes),
        frozenset(principal.identities),
    )


def _cache_get(key: tuple) -> dict | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            _CACHE.pop(key, None)
            return None
        return value


def _cache_put(key: tuple, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + _CACHE_TTL_SECONDS, value)


# ---------------------------------------------------------------- qdrant helpers
def _qdrant_base() -> str:
    return settings.qdrant_url.rstrip("/")


def _size_bucket(text: str) -> int:
    length = len(text)
    for bucket, threshold in enumerate(_SIZE_BUCKETS, start=1):
        if length <= threshold:
            return bucket
    return 5


def _node_from_point(coll: str, point: dict) -> dict:
    payload = point.get("payload") or {}
    pid = point.get("id")
    text = str(payload.get("text") or payload.get("content") or "")
    label = str(
        payload.get("title") or payload.get("filename") or text[:60].strip() or pid
    )
    ref = (
        payload.get("wikipath")
        or payload.get("path")
        or payload.get("source_file")
        or payload.get("uid")
        or str(pid)
    )
    return {
        "id": f"{coll}:{pid}",
        "label": label,
        "source": coll,
        "collection": coll,
        "scope": str(payload.get("scope") or "restricted"),
        "size": _size_bucket(text),
        "ref": str(ref),
    }


def _plain_vector(vector: Any) -> list | None:
    if isinstance(vector, dict):
        vector = vector.get("dense") or next(iter(vector.values()), None)
    return vector if isinstance(vector, list) else None


async def _scroll_points(client, coll: str, flt: dict | None, limit: int) -> list[dict]:
    res = await client.post(
        f"{_qdrant_base()}/collections/{coll}/points/scroll",
        json={"limit": limit, "with_payload": True, "with_vector": False, "filter": flt},
    )
    res.raise_for_status()
    return (res.json().get("result") or {}).get("points") or []


async def _recommend(client, coll: str, pid, flt: dict | None) -> list[dict]:
    body = {"positive": [pid], "limit": _INTRA_EDGE_K, "with_payload": False, "filter": flt}
    res = await client.post(f"{_qdrant_base()}/collections/{coll}/points/recommend", json=body)
    if res.status_code in (400, 422):  # named-vector collections
        body["using"] = "dense"
        res = await client.post(f"{_qdrant_base()}/collections/{coll}/points/recommend", json=body)
    res.raise_for_status()
    return res.json().get("result") or []


async def _vector_search(client, coll: str, vector: list, flt: dict | None) -> list[dict]:
    body = {
        "vector": vector,
        "limit": _CROSS_K,
        "with_payload": False,
        "score_threshold": _CROSS_THRESHOLD,
        "filter": flt,
    }
    res = await client.post(f"{_qdrant_base()}/collections/{coll}/points/search", json=body)
    if res.status_code in (400, 422):  # named-vector collections
        body["vector"] = {"name": "dense", "vector": vector}
        res = await client.post(f"{_qdrant_base()}/collections/{coll}/points/search", json=body)
    res.raise_for_status()
    return res.json().get("result") or []


def _add_edges(
    edges: list[dict],
    seen: set[frozenset],
    known_ids: set[str],
    a_id: str,
    hit_coll: str,
    hits: list[dict],
) -> None:
    for hit in hits:
        b_id = f"{hit_coll}:{hit.get('id')}"
        if b_id == a_id or b_id not in known_ids:
            continue
        pair = frozenset((a_id, b_id))
        if pair in seen:
            continue
        seen.add(pair)
        edges.append({"a": a_id, "b": b_id, "w": round(float(hit.get("score") or 0), 4)})


# ---------------------------------------------------------------- falkordb helpers
def _readable_graphs(principal: Principal, scope: str | None) -> set[str]:
    readable: set[str] = set()
    for graph in settings.falkordb_graphs:
        graph_scope, graph_owner = classify_graph(graph)
        if can_read_record(principal, {"scope": graph_scope, "owner": graph_owner}, scope):
            readable.add(graph)
    return readable


def _falkor_sample_sync(readable: set[str]) -> tuple[list[dict], list[dict]]:
    """Per-graph node counts (+ a small node sample when non-empty).

    Today every configured graph is empty, so the common path is one cheap
    ``count(n)`` per graph; each graph degrades independently to count=0.
    """
    stats = [{"key": graph, "count": 0} for graph in settings.falkordb_graphs]
    nodes: list[dict] = []
    try:
        import redis  # lazy: optional dependency, mirrors adapters.falkordb
    except ModuleNotFoundError:
        return stats, nodes
    stats = []
    for graph in settings.falkordb_graphs:
        count = 0
        client = None
        try:
            client = redis.Redis.from_url(
                settings.falkordb_url, socket_timeout=5, socket_connect_timeout=5,
                decode_responses=True,
            )
            reply = client.execute_command("GRAPH.RO_QUERY", graph, "MATCH (n) RETURN count(n)")
            count = int(reply[1][0][0])
            if count > 0 and graph in readable:
                sample = client.execute_command(
                    "GRAPH.RO_QUERY", graph,
                    "MATCH (n) RETURN id(n), "
                    "coalesce(n.name, n.title, n.label, n.summary), labels(n)[0] "
                    f"LIMIT {_FALKOR_SAMPLE}",
                )
                graph_scope, _ = classify_graph(graph)
                for row in (sample[1] if len(sample) >= 2 else []):
                    nid, label, kind = (list(row) + [None] * 3)[:3]
                    if nid is None:
                        continue
                    nodes.append({
                        "id": f"{graph}:{nid}",
                        "label": str(label or kind or nid)[:80],
                        "source": graph,
                        "collection": graph,
                        "scope": graph_scope,
                        "size": 1,
                        "ref": str(nid),
                    })
        except Exception:
            pass
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
        stats.append({"key": graph, "count": count})
    return stats, nodes


# ---------------------------------------------------------------- routes
@router.get("/kb.graph")
async def kb_graph(
    principal: GraphPrincipal, scope: str | None = None, limit_per_coll: int = 100
) -> dict:
    principal.require("read:knowledge")
    validate_requested_scope(principal, scope)
    limit = max(1, min(int(limit_per_coll), 200))
    key = _cache_key(principal, scope)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    nodes: list[dict] = []
    edges: list[dict] = []
    coll_stats: list[dict] = []
    points_by_coll: dict[str, list[dict]] = {}

    if settings.enable_qdrant and settings.qdrant_collections:
        async with httpx.AsyncClient(timeout=20) as client:
            for coll in settings.qdrant_collections:
                try:
                    flt = _visibility_filter(principal, scope)
                    points = await _scroll_points(client, coll, flt, limit)
                except Exception:
                    continue  # a broken collection thins the graph, never 500s
                points_by_coll[coll] = points
                nodes.extend(_node_from_point(coll, point) for point in points)
                coll_stats.append({"key": coll, "sampled": len(points)})

            known_ids = {node["id"] for node in nodes}
            seen_pairs: set[frozenset] = set()

            # Intra-collection neighbourhoods via recommend().
            for coll, points in points_by_coll.items():
                flt = _visibility_filter(principal, scope)
                for point in points[:_INTRA_EDGE_NODES]:
                    pid = point.get("id")
                    try:
                        hits = await _recommend(client, coll, pid, flt)
                    except Exception:
                        continue
                    _add_edges(edges, seen_pairs, known_ids, f"{coll}:{pid}", coll, hits)

            # Cross-collection: wiki vectors searched against the other collections.
            wiki_colls = [c for c in points_by_coll if "wiki" in c.lower()]
            other_colls = [c for c in points_by_coll if c not in wiki_colls]
            for coll in wiki_colls:
                sample_ids = [p.get("id") for p in points_by_coll[coll][:_CROSS_EDGE_NODES]]
                if not sample_ids or not other_colls:
                    continue
                try:
                    res = await client.post(
                        f"{_qdrant_base()}/collections/{coll}/points",
                        json={"ids": sample_ids, "with_payload": False, "with_vector": True},
                    )
                    res.raise_for_status()
                    vectors = {
                        row.get("id"): _plain_vector(row.get("vector"))
                        for row in (res.json().get("result") or [])
                    }
                except Exception:
                    continue
                for pid, vector in vectors.items():
                    if not vector:
                        continue
                    for other in other_colls:
                        flt = _visibility_filter(principal, scope)
                        try:
                            hits = await _vector_search(client, other, vector, flt)
                        except Exception:
                            continue
                        _add_edges(edges, seen_pairs, known_ids, f"{coll}:{pid}", other, hits)

    falkor_stats: list[dict] = []
    if settings.enable_falkordb:
        try:
            falkor_stats, falkor_nodes = await asyncio.to_thread(
                _falkor_sample_sync, _readable_graphs(principal, scope)
            )
            nodes.extend(falkor_nodes)
        except Exception:
            falkor_stats = [{"key": graph, "count": 0} for graph in settings.falkordb_graphs]

    result = {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "collections": coll_stats,
            "falkordb": falkor_stats,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "scope": scope or "",
            "cache_ttl_seconds": int(_CACHE_TTL_SECONDS),
            "generated_at": utcnow(),
        },
    }
    _cache_put(key, result)
    return result


def _pretty(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").strip().title()


@router.get("/kb.stats")
async def kb_stats(principal: GraphPrincipal) -> dict:
    principal.require("read:knowledge")
    sources: list[dict] = []

    if settings.enable_qdrant:
        async with httpx.AsyncClient(timeout=10) as client:
            for coll in settings.qdrant_collections:
                try:
                    res = await client.get(f"{_qdrant_base()}/collections/{coll}")
                    res.raise_for_status()
                    count = int((res.json().get("result") or {}).get("points_count") or 0)
                except Exception:
                    continue
                sources.append(
                    {"key": f"qdrant:{coll}", "label": _pretty(coll), "count": count, "kind": "semantic"}
                )

    try:
        sources.append({"key": "wiki", "label": "Wiki", "count": len(list_docs()), "kind": "canonical"})
    except Exception:
        pass

    for key, label, table, kind in (
        ("file_index", "Files", "file_chunks", "files"),
        ("metrics", "Metrics", "metrics", "metrics"),
    ):
        try:
            with connect() as db:
                count = int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        except Exception:
            continue
        sources.append({"key": key, "label": label, "count": count, "kind": kind})

    if settings.enable_falkordb:
        try:
            falkor_stats, _ = await asyncio.to_thread(_falkor_sample_sync, set())
        except Exception:
            falkor_stats = [{"key": graph, "count": 0} for graph in settings.falkordb_graphs]
        sources.extend(
            {
                "key": f"falkordb:{row['key']}",
                "label": _pretty(str(row["key"])),
                "count": int(row["count"]),
                "kind": "graph",
            }
            for row in falkor_stats
        )

    return {"sources": sources}


@router.get("/kb.me")
def kb_me(principal: GraphPrincipal) -> dict:
    # Any authenticated principal may introspect itself; no scope required.
    return {
        "name": principal.subject,
        "scopes": sorted(principal.scopes),
        "is_admin": principal.is_admin,
    }
