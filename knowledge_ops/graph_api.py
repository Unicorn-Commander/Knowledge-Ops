"""Constellation endpoints: /kb.graph, /kb.stats, /kb.me.

Read-only sampling of the same stores federated search uses. Qdrant sampling is
gated by the exact same ``_visibility_filter`` that ``search_qdrant`` applies, so
the constellation can never show a point that search would hide. Wiki docs that
participate in the parsed link graph are guaranteed a node (fetched through that
same filter) even when the sample window misses them, so ``kind='link'`` edges
never silently vanish. Every per-store step degrades to "skip" — an unreachable
backend thins the graph, it never 500s.

The full graph response is cached in-process for 10 minutes per visibility
identity (scope, admin flag, scope-set, identity-set, workspace-set, and
project-set).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from .adapters.falkordb import classify_graph
from .adapters.qdrant import _visibility_filter
from .authorization import (
    AuthorizationError,
    Principal,
    can_read_record,
    validate_requested_scope,
)
from .config import settings
from .models import utcnow
from .storage import connect, init_db
from .wiki import get_doc, list_docs
from .wikilinks import build_link_graph

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

# The wiki link graph is far cheaper to build (one SQLite scan, no vector
# stores) and must reflect edits promptly, so it lives in its own short cache.
_LINKS_CACHE: dict[tuple, tuple[float, dict]] = {}
_LINKS_TTL_SECONDS = 60.0

_INTRA_EDGE_NODES = 60  # recommend() fan-out per collection
_INTRA_EDGE_K = 4
_CROSS_EDGE_NODES = 30  # wiki vectors searched against the other collections
_CROSS_K = 2
_CROSS_THRESHOLD = 0.55
_FALKOR_SAMPLE = 50
_LINK_GUARANTEE_PATHS = 60  # link-doc node guarantee: max missing wiki paths fetched
_SIZE_BUCKETS = (200, 500, 1000, 2000)  # text length -> node size 1..5


def _cache_key(principal: Principal, scope: str | None) -> tuple:
    return (
        scope or "",
        principal.is_admin,
        frozenset(principal.scopes),
        frozenset(principal.identities),
        frozenset(principal.workspaces),
        frozenset(principal.projects),
        settings.allow_legacy_global_visibility,
    )


def _cache_get(key: tuple, cache: dict | None = None) -> dict | None:
    store = _CACHE if cache is None else cache
    with _CACHE_LOCK:
        entry = store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            store.pop(key, None)
            return None
        return value


def _cache_put(key: tuple, value: dict, cache: dict | None = None, ttl: float | None = None) -> None:
    store = _CACHE if cache is None else cache
    with _CACHE_LOCK:
        store[key] = (time.time() + (ttl if ttl is not None else _CACHE_TTL_SECONDS), value)


def invalidate_link_caches() -> None:
    """Drop cached link + constellation graphs after a wiki doc save/delete.

    The full graph cache embeds ``kind='link'`` edges, so it must fall with the
    link graph — otherwise a save would take up to 10 minutes to appear.
    """
    with _CACHE_LOCK:
        _LINKS_CACHE.clear()
        _CACHE.clear()


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
    ref = str(
        payload.get("wikipath")
        or payload.get("path")
        or payload.get("source_file")
        or payload.get("uid")
        or str(pid)
    )
    if "wiki" in coll.lower():
        # Wiki node refs must equal the doc's wiki-relative path so the
        # frontend (and the link-edge join below) can map paths -> node ids.
        base = str(settings.wiki_dir).rstrip("/") + "/"
        if ref.startswith(base):
            ref = ref[len(base):]
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
        edges.append({
            "a": a_id, "b": b_id,
            "w": round(float(hit.get("score") or 0), 4),
            "kind": "semantic",
        })


def _merge_visibility_filter(flt: dict | None, clause: dict) -> dict:
    """AND a mandatory payload clause into a visibility filter.

    A must-form filter contributes its clauses directly; a should-form (or any
    other shape) nests intact as a single must entry. Either way the caller's
    visibility conditions stay mandatory — merging can only narrow, never
    widen, what a scope-filtered principal sees.
    """
    if flt is None:
        return {"must": [clause]}
    if set(flt) == {"must"} and isinstance(flt["must"], list):
        return {"must": [*flt["must"], clause]}
    return {"must": [flt, clause]}


# ---------------------------------------------------------------- wiki links
def _visible_wiki_docs(principal: Principal, scope: str | None) -> list[dict]:
    """Active wiki docs (path, title, body) the caller may read, in-process."""
    init_db()
    with connect() as db:
        rows = db.execute(
            """SELECT path, title, body, scope, owner,
                      visibility_workspace_id, visibility_project_id
               FROM wiki_docs
               WHERE lifecycle_state='active' ORDER BY path"""
        ).fetchall()
    return [doc for doc in (dict(row) for row in rows) if can_read_record(principal, doc, scope)]


def _link_graph(principal: Principal, scope: str | None) -> dict:
    """The parsed wiki link graph for this visibility identity (60s TTL)."""
    key = ("links",) + _cache_key(principal, scope)
    cached = _cache_get(key, cache=_LINKS_CACHE)
    if cached is None:
        cached = build_link_graph(_visible_wiki_docs(principal, scope))
        _cache_put(key, cached, cache=_LINKS_CACHE, ttl=_LINKS_TTL_SECONDS)
    return cached


def _wiki_link_edges(nodes: list[dict], link_graph: dict) -> list[dict]:
    """Join link-graph doc paths onto wiki-collection node refs.

    Wiki docs are chunked into several points, so one path can map to several
    node ids; every (a, b) pair is emitted once. Link edges may coexist with a
    semantic edge over the same pair — the kinds render differently.
    """
    by_ref: dict[str, list[str]] = {}
    for node in nodes:
        if "wiki" in str(node.get("collection") or "").lower() and node.get("ref"):
            by_ref.setdefault(str(node["ref"]), []).append(node["id"])
    edges: list[dict] = []
    seen: set[frozenset] = set()
    for link in link_graph.get("links", []):
        for a_id in by_ref.get(str(link["from_path"]), ()):
            for b_id in by_ref.get(str(link["to_path"]), ()):
                pair = frozenset((a_id, b_id))
                if a_id == b_id or pair in seen:
                    continue
                seen.add(pair)
                edges.append({"a": a_id, "b": b_id, "w": 1, "kind": "link"})
    return edges


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

    Sampling prefers the typed entity layer (nodes with ``name_norm``, busiest
    first) over Graphiti's episodic noise; graphs without a typed layer fall
    back to the arbitrary sample and render exactly as before. Today every
    configured graph is empty, so the common path is one cheap ``count(n)``
    per graph; each graph degrades independently to count=0.
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
                rows: list = []
                try:
                    # Typed entity layer first: our pipeline stamps name_norm,
                    # Graphiti's episodic layer does not. Busiest entities
                    # (highest in+out degree) lead the sample.
                    typed = client.execute_command(
                        "GRAPH.RO_QUERY", graph,
                        "MATCH (n) WHERE n.name_norm IS NOT NULL "
                        "RETURN id(n), "
                        "coalesce(n.name, n.title, n.label, n.summary), "
                        "labels(n)[0], n.type, "
                        "indegree(n) + outdegree(n) AS deg "
                        f"ORDER BY deg DESC LIMIT {_FALKOR_SAMPLE}",
                    )
                    rows = typed[1] if len(typed) >= 2 else []
                except Exception:
                    rows = []
                if not rows:
                    # No typed layer (e.g. agent_meeting_ops_canonical): keep
                    # today's arbitrary sample so those graphs render unchanged.
                    sample = client.execute_command(
                        "GRAPH.RO_QUERY", graph,
                        "MATCH (n) RETURN id(n), "
                        "coalesce(n.name, n.title, n.label, n.summary), labels(n)[0] "
                        f"LIMIT {_FALKOR_SAMPLE}",
                    )
                    rows = sample[1] if len(sample) >= 2 else []
                graph_scope, _ = classify_graph(graph)
                for row in rows:
                    nid, label, kind, entity_type = (list(row) + [None] * 4)[:4]
                    if nid is None:
                        continue
                    node = {
                        "id": f"{graph}:{nid}",
                        "label": str(label or kind or nid)[:80],
                        "source": graph,
                        "collection": graph,
                        "scope": graph_scope,
                        "size": 1,
                        "ref": str(nid),
                    }
                    if entity_type:
                        node["entity_type"] = str(entity_type)
                    nodes.append(node)
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

            # Link-doc node guarantee: docs that participate in the parsed wiki
            # link graph often miss the sample window, which would silently
            # drop their kind='link' edges. Fetch one representative chunk per
            # missing path — through the caller's visibility filter, so the
            # guarantee can never surface a doc that search would hide — before
            # any edge pass runs. Every step degrades to skip, like sampling.
            wiki_colls = [c for c in points_by_coll if "wiki" in c.lower()]
            other_colls = [c for c in points_by_coll if c not in wiki_colls]
            if wiki_colls:
                try:
                    links = _link_graph(principal, scope).get("links", [])
                except Exception:
                    links = []
                linked_paths = {
                    str(link[key]) for link in links for key in ("from_path", "to_path")
                }
                present = {
                    str(node["ref"]) for node in nodes
                    if "wiki" in str(node.get("collection") or "").lower()
                }
                missing = sorted(linked_paths - present)[:_LINK_GUARANTEE_PATHS]
                for coll in wiki_colls:
                    if not missing:
                        break
                    flt = _merge_visibility_filter(
                        _visibility_filter(principal, scope),
                        {"key": "wikipath", "match": {"any": missing}},
                    )
                    try:
                        fetched = await _scroll_points(client, coll, flt, len(missing))
                    except Exception:
                        continue  # a failed guarantee fetch thins the graph
                    wanted = set(missing)
                    found: set[str] = set()
                    for point in fetched:
                        node = _node_from_point(coll, point)
                        if node["ref"] not in wanted or node["ref"] in found:
                            continue  # one representative chunk per doc path
                        found.add(node["ref"])
                        points_by_coll[coll].append(point)
                        nodes.append(node)
                    missing = [path for path in missing if path not in found]

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

    # Real wiki-link edges: doc paths from the parsed link graph joined onto
    # wiki node refs. An unreadable wiki store thins the graph, never 500s.
    try:
        edges.extend(_wiki_link_edges(nodes, _link_graph(principal, scope)))
    except Exception:
        logger.debug("wiki link edges skipped", exc_info=True)

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
        sources.append({
            "key": "wiki", "label": "Wiki", "count": len(list_docs(principal=principal)),
            "kind": "canonical",
        })
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


@router.get("/kb.links")
def kb_links(principal: GraphPrincipal, scope: str | None = None) -> dict:
    """The wiki link graph: resolved links, missing red-link targets, orphans.

    Built in-process from the caller-visible slice of the wiki store and cached
    for 60 seconds per visibility identity.
    """
    principal.require("read:knowledge")
    validate_requested_scope(principal, scope)
    graph = _link_graph(principal, scope)
    return {
        "links": graph["links"],
        "missing": graph["missing"],
        "orphans": graph["orphans"],
        "count": len(graph["links"]),
    }


@router.get("/kb.doc_version")
def kb_doc_version(
    principal: GraphPrincipal,
    id: str,
    version_id: int | None = None,
    version: int | None = None,
) -> dict:
    """A historical version body from the wiki store (reading-room timeline).

    ``id`` accepts a doc id or path, mirroring /kb.get_doc; ``version_id``
    matches the rows /kb.doc_history returns (``version`` is an alias).
    """
    principal.require("read:knowledge")
    requested = version_id if version_id is not None else version
    if requested is None:
        raise ValueError("version_id is required")
    doc = get_doc(id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document was not found")
    if not can_read_record(principal, doc):
        raise AuthorizationError("document is outside the caller's visibility")
    with connect() as db:
        row = db.execute(
            """SELECT version_id, doc_id, body, created_at FROM wiki_versions
               WHERE doc_id=? AND version_id=?""",
            (doc["id"], int(requested)),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="version was not found")
    record = dict(row)
    record["version"] = record["version_id"]
    record["saved_at"] = record["created_at"]
    record["bytes"] = len(record["body"])
    return {"version": record}
