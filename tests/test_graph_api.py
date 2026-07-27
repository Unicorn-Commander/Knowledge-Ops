"""Tests for the Constellation endpoints (/kb.graph, /kb.stats, /kb.me).

Fully offline: Qdrant is a fake httpx client, FalkorDB is a fake redis module
(or stays disabled), and the principal is constructed directly. Covers node id
format, visibility-filter pass-through (the exact object search_qdrant would
use), the link-doc node guarantee (and its visibility merge), the 600s cache,
per-collection failure tolerance, FalkorDB typed-layer sampling, the /kb.me
shape, and the 403 for a principal without read:knowledge.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import knowledge_ops.config as config
import knowledge_ops.graph_api as graph_api
from knowledge_ops.authorization import Principal, system_principal
from tests.test_federation import _seed_tmp_env


@pytest.fixture(autouse=True)
def _clear_graph_cache(monkeypatch):
    graph_api._CACHE.clear()
    graph_api._LINKS_CACHE.clear()
    # Hermetic default: kb_graph consults the wiki link graph (SQLite). Tests
    # that need links monkeypatch _link_graph themselves; everyone else sees
    # an empty graph instead of whatever lives in the developer's real store.
    monkeypatch.setattr(
        graph_api, "_link_graph",
        lambda principal, scope: {"links": [], "missing": [], "orphans": []},
    )
    yield
    graph_api._CACHE.clear()
    graph_api._LINKS_CACHE.clear()


# ---------------------------------------------------------------- fake qdrant
class _Resp:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, handler, calls, **_kwargs):
        self._handler = handler
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        self._calls.append(("POST", url, json))
        return self._handler("POST", url, json)

    async def get(self, url):
        self._calls.append(("GET", url, None))
        return self._handler("GET", url, None)


def _install_fake_httpx(monkeypatch, handler):
    calls: list[tuple] = []
    fake = SimpleNamespace(AsyncClient=lambda **kw: _FakeClient(handler, calls, **kw))
    monkeypatch.setattr(graph_api, "httpx", fake)
    return calls


def _qdrant_settings(monkeypatch, collections):
    monkeypatch.setattr(config.settings, "enable_qdrant", True)
    monkeypatch.setattr(config.settings, "enable_falkordb", False)
    monkeypatch.setattr(config.settings, "qdrant_collections", list(collections))
    monkeypatch.setattr(config.settings, "qdrant_url", "http://qdrant.test:6333")


def _happy_handler(method, url, body):
    """Two points per collection; recommend links 1->2; cross-search hits id 1."""
    if url.endswith("/points/scroll"):
        coll = url.split("/collections/")[1].split("/")[0]
        return _Resp({"result": {"points": [
            {"id": 1, "payload": {
                "title": f"{coll} doc one", "text": "x" * 900,
                "scope": "global", "path": f"{coll}/one.md",
            }},
            {"id": 2, "payload": {"text": "a short evidence chunk", "scope": "global"}},
        ]}})
    if url.endswith("/points/recommend"):
        return _Resp({"result": [{"id": 2, "score": 0.87}, {"id": body["positive"][0], "score": 1.0}]})
    if url.endswith("/points/search"):
        return _Resp({"result": [{"id": 1, "score": 0.71}]})
    if url.endswith("/points"):  # retrieve-with-vectors
        return _Resp({"result": [{"id": i, "vector": [0.1, 0.2, 0.3]} for i in body["ids"]]})
    return _Resp(status=404)


# ---------------------------------------------------------------- kb.graph
def test_graph_node_id_format_shape_and_edges(monkeypatch):
    _qdrant_settings(monkeypatch, ["knowledge_ops_wiki", "org_memory"])
    _install_fake_httpx(monkeypatch, _happy_handler)

    res = asyncio.run(graph_api.kb_graph(system_principal(), scope=None, limit_per_coll=50))

    ids = {n["id"] for n in res["nodes"]}
    assert ids == {"knowledge_ops_wiki:1", "knowledge_ops_wiki:2", "org_memory:1", "org_memory:2"}
    for node in res["nodes"]:
        assert set(node) == {"id", "label", "source", "collection", "scope", "size", "ref"}
        assert node["id"] == f"{node['collection']}:{node['id'].split(':', 1)[1]}"
        assert 1 <= node["size"] <= 5
        assert node["scope"] == "global"
    by_id = {n["id"]: n for n in res["nodes"]}
    assert by_id["knowledge_ops_wiki:1"]["label"] == "knowledge_ops_wiki doc one"
    assert by_id["knowledge_ops_wiki:1"]["ref"] == "knowledge_ops_wiki/one.md"
    assert by_id["knowledge_ops_wiki:2"]["label"] == "a short evidence chunk"  # first-chars fallback
    assert by_id["knowledge_ops_wiki:1"]["size"] == 3 and by_id["knowledge_ops_wiki:2"]["size"] == 1

    pairs = {(e["a"], e["b"]) for e in res["edges"]}
    # intra-collection recommend edge (self-hit id==positive is dropped)
    assert ("knowledge_ops_wiki:1", "knowledge_ops_wiki:2") in pairs
    # cross-collection wiki -> org_memory edge above the 0.55 threshold
    assert ("knowledge_ops_wiki:1", "org_memory:1") in pairs
    for edge in res["edges"]:
        assert edge["a"] in ids and edge["b"] in ids and edge["a"] != edge["b"]
        assert 0 < edge["w"] <= 1
    assert res["stats"]["node_count"] == 4
    assert res["stats"]["edge_count"] == len(res["edges"])
    assert res["stats"]["falkordb"] == []  # disabled in this test


def test_graph_reuses_search_visibility_filter(monkeypatch):
    """The scroll/recommend/search bodies carry the exact filter search_qdrant uses."""
    from knowledge_ops.adapters.qdrant import _visibility_filter

    _qdrant_settings(monkeypatch, ["org_memory"])
    calls = _install_fake_httpx(monkeypatch, _happy_handler)
    principal = Principal(
        subject="aaron", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"aaron", "aaron@magicunicorn.tech"}),
    )
    asyncio.run(graph_api.kb_graph(principal, scope=None, limit_per_coll=10))

    expected = _visibility_filter(principal, None)
    assert expected is not None  # non-admin: a real restriction, not a no-op
    filtered_calls = [
        (method, url, body) for method, url, body in calls
        if url.endswith(("/points/scroll", "/points/recommend", "/points/search"))
    ]
    assert filtered_calls, "expected qdrant calls"
    for _method, _url, body in filtered_calls:
        assert body["filter"] == expected


def test_graph_response_is_cached_per_visibility_key(monkeypatch):
    _qdrant_settings(monkeypatch, ["org_memory"])
    calls = _install_fake_httpx(monkeypatch, _happy_handler)
    principal = system_principal()

    first = asyncio.run(graph_api.kb_graph(principal, scope=None, limit_per_coll=10))
    n_calls = len(calls)
    assert n_calls > 0
    second = asyncio.run(graph_api.kb_graph(principal, scope=None, limit_per_coll=10))
    assert second is first  # served from cache
    assert len(calls) == n_calls  # no additional backend traffic

    # A different visibility identity misses the cache.
    other = Principal(
        subject="jason", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"jason"}),
    )
    asyncio.run(graph_api.kb_graph(other, scope=None, limit_per_coll=10))
    assert len(calls) > n_calls


def test_graph_tolerates_a_broken_collection(monkeypatch):
    _qdrant_settings(monkeypatch, ["exploding_coll", "org_memory"])

    def handler(method, url, body):
        if "/collections/exploding_coll/" in url:
            raise ConnectionError("collection is on fire")
        return _happy_handler(method, url, body)

    _install_fake_httpx(monkeypatch, handler)
    res = asyncio.run(graph_api.kb_graph(system_principal(), scope=None, limit_per_coll=10))
    assert {n["collection"] for n in res["nodes"]} == {"org_memory"}
    assert [c["key"] for c in res["stats"]["collections"]] == ["org_memory"]


def test_graph_skips_qdrant_entirely_when_disabled(monkeypatch):
    monkeypatch.setattr(config.settings, "enable_qdrant", False)
    monkeypatch.setattr(config.settings, "enable_falkordb", False)
    res = asyncio.run(graph_api.kb_graph(system_principal(), scope=None, limit_per_coll=10))
    assert res["nodes"] == [] and res["edges"] == []


# ---------------------------------------------------------------- link-doc guarantee
def _links(*pairs):
    return {
        "links": [{"from_path": a, "to_path": b, "text": b} for a, b in pairs],
        "missing": [], "orphans": [],
    }


def _wikipath_clauses(body):
    must = ((body or {}).get("filter") or {}).get("must") or []
    return [c for c in must if isinstance(c, dict) and c.get("key") == "wikipath"]


def test_graph_guarantees_link_participating_docs(monkeypatch):
    """A doc in the link graph but outside the sample window still gains a node
    (one representative chunk per path), so its kind='link' edge renders."""
    _qdrant_settings(monkeypatch, ["knowledge_ops_wiki"])
    monkeypatch.setattr(
        graph_api, "_link_graph",
        lambda principal, scope: _links(("infrastructure/net.md", "infrastructure/dns.md")),
    )

    def handler(method, url, body):
        if url.endswith("/points/scroll"):
            if _wikipath_clauses(body):
                # Guarantee fetch: two chunks of net.md (the duplicate must
                # collapse to one representative) plus one chunk of dns.md.
                return _Resp({"result": {"points": [
                    {"id": 7, "payload": {"wikipath": "infrastructure/net.md",
                                          "title": "Net", "text": "n", "scope": "global"}},
                    {"id": 8, "payload": {"wikipath": "infrastructure/net.md",
                                          "title": "Net 2", "text": "n2", "scope": "global"}},
                    {"id": 9, "payload": {"wikipath": "infrastructure/dns.md",
                                          "title": "DNS", "text": "d", "scope": "global"}},
                ]}})
            # The plain sample window is dominated by an unrelated chunk.
            return _Resp({"result": {"points": [
                {"id": 1, "payload": {"wikipath": "legal/contract.md",
                                      "text": "legalese", "scope": "global"}},
            ]}})
        if url.endswith("/points/recommend"):
            return _Resp({"result": []})
        return _Resp(status=404)

    calls = _install_fake_httpx(monkeypatch, handler)
    res = asyncio.run(graph_api.kb_graph(system_principal(), scope=None, limit_per_coll=50))

    refs = {n["id"]: n["ref"] for n in res["nodes"]}
    assert refs == {
        "knowledge_ops_wiki:1": "legal/contract.md",
        "knowledge_ops_wiki:7": "infrastructure/net.md",
        "knowledge_ops_wiki:9": "infrastructure/dns.md",  # id 8 = duplicate chunk, skipped
    }
    assert {"a": "knowledge_ops_wiki:7", "b": "knowledge_ops_wiki:9",
            "w": 1, "kind": "link"} in res["edges"]
    assert res["stats"]["node_count"] == 3

    guarantee = [body for _m, url, body in calls
                 if url.endswith("/points/scroll") and _wikipath_clauses(body)]
    assert len(guarantee) == 1  # one batch call
    assert guarantee[0]["limit"] == 2  # scroll limit = len(missing)
    assert guarantee[0]["filter"] == {"must": [
        {"key": "wikipath",
         "match": {"any": ["infrastructure/dns.md", "infrastructure/net.md"]}},
    ]}  # admin: no visibility conditions to merge
    # Inserted before the edge passes: the recommend loop sees them too.
    recommended = {body["positive"][0] for _m, url, body in calls
                   if url.endswith("/points/recommend")}
    assert {7, 9} <= recommended


def test_graph_link_guarantee_respects_visibility_filter(monkeypatch):
    """The guarantee fetch merges the caller's visibility filter, so a
    scope-filtered principal cannot gain nodes their filter would hide."""
    from knowledge_ops.adapters.qdrant import _visibility_filter

    _qdrant_settings(monkeypatch, ["knowledge_ops_wiki"])
    monkeypatch.setattr(
        graph_api, "_link_graph",
        lambda principal, scope: _links(("infrastructure/net.md", "infrastructure/dns.md")),
    )
    principal = Principal(
        subject="aaron", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"aaron"}),
    )

    def handler(method, url, body):
        if url.endswith("/points/scroll"):
            if _wikipath_clauses(body):
                # Qdrant honoring the merged filter: the linked docs exist but
                # sit outside this caller's visibility -> nothing comes back.
                return _Resp({"result": {"points": []}})
            return _Resp({"result": {"points": [
                {"id": 1, "payload": {"wikipath": "legal/contract.md",
                                      "text": "legalese", "scope": "global"}},
            ]}})
        if url.endswith("/points/recommend"):
            return _Resp({"result": []})
        return _Resp(status=404)

    calls = _install_fake_httpx(monkeypatch, handler)
    res = asyncio.run(graph_api.kb_graph(principal, scope=None, limit_per_coll=10))

    guarantee = [body for _m, url, body in calls
                 if url.endswith("/points/scroll") and _wikipath_clauses(body)]
    assert len(guarantee) == 1
    vis = _visibility_filter(principal, None)
    assert set(vis) == {"should"}  # the default non-admin form
    # The should-form nests intact as a single must entry beside the wikipath
    # clause — the caller's visibility conditions stay mandatory.
    assert guarantee[0]["filter"] == {"must": [
        vis,
        {"key": "wikipath",
         "match": {"any": ["infrastructure/dns.md", "infrastructure/net.md"]}},
    ]}
    # No node materialized outside the caller's visibility, no phantom edges.
    assert {n["ref"] for n in res["nodes"]} == {"legal/contract.md"}
    assert res["edges"] == []


def test_merge_visibility_filter_forms():
    clause = {"key": "wikipath", "match": {"any": ["a.md"]}}
    must_form = {"must": [{"key": "scope", "match": {"value": "global"}}]}
    should_form = {"should": [{"key": "scope", "match": {"value": "global"}}]}
    assert graph_api._merge_visibility_filter(None, clause) == {"must": [clause]}
    assert graph_api._merge_visibility_filter(must_form, clause) == {
        "must": [{"key": "scope", "match": {"value": "global"}}, clause]
    }
    assert graph_api._merge_visibility_filter(should_form, clause) == {
        "must": [should_form, clause]
    }


# ---------------------------------------------------------------- falkordb sample
def test_falkor_sample_prefers_typed_layer_and_falls_back(monkeypatch):
    """Graphs with a name_norm entity layer sample it (carrying entity_type);
    graphs without one keep today's arbitrary sample exactly."""
    monkeypatch.setattr(
        config.settings, "falkordb_graphs",
        ["brigade_global", "agent_meeting_ops_canonical"],
    )
    monkeypatch.setattr(config.settings, "falkordb_url", "redis://falkor.test:6379")

    executed: list[tuple[str, str]] = []

    class _FakeRedis:
        def execute_command(self, _cmd, graph, query):
            executed.append((graph, query))
            if "count(n)" in query:
                return [["count(n)"], [[3]], []]
            if "name_norm" in query:
                if graph == "brigade_global":
                    return [["hdr"], [
                        [11, "Aaron Stransky", "Entity", "Person", 5],
                        [12, "Magic Unicorn", "Entity", "Organization", 3],
                    ], []]
                return [["hdr"], [], []]  # this graph has no typed layer
            return [["hdr"], [[21, "PDF memos (not Slack messages)", "Episodic"]], []]

        def close(self):
            pass

    fake = SimpleNamespace(Redis=SimpleNamespace(from_url=lambda url, **kw: _FakeRedis()))
    monkeypatch.setitem(sys.modules, "redis", fake)

    stats, nodes = graph_api._falkor_sample_sync(
        {"brigade_global", "agent_meeting_ops_canonical"}
    )

    assert stats == [
        {"key": "brigade_global", "count": 3},
        {"key": "agent_meeting_ops_canonical", "count": 3},
    ]
    by_id = {n["id"]: n for n in nodes}
    typed = by_id["brigade_global:11"]
    assert typed["label"] == "Aaron Stransky"
    assert typed["entity_type"] == "Person"
    assert typed["scope"] == "global"
    assert by_id["brigade_global:12"]["entity_type"] == "Organization"
    fallback = by_id["agent_meeting_ops_canonical:21"]
    assert fallback["label"] == "PDF memos (not Slack messages)"
    assert "entity_type" not in fallback  # the arbitrary layer carries no type

    per_graph: dict[str, list[str]] = {}
    for graph, query in executed:
        per_graph.setdefault(graph, []).append(query)
    # Typed-layer success never runs the arbitrary sample for that graph...
    assert len(per_graph["brigade_global"]) == 2  # count + typed only
    assert "name_norm" in per_graph["brigade_global"][1]
    # ...while a graph without one falls back (count + typed + arbitrary).
    assert len(per_graph["agent_meeting_ops_canonical"]) == 3
    assert "name_norm" not in per_graph["agent_meeting_ops_canonical"][-1]


# ---------------------------------------------------------------- kb.me + auth
def test_me_shape(tmp_path, monkeypatch):
    _seed_tmp_env(tmp_path, monkeypatch)  # auth disabled -> local-system principal
    from knowledge_ops.api import app

    with TestClient(app) as client:
        body = client.get("/kb.me").json()
    assert set(body) == {"name", "scopes", "is_admin"}
    assert body["name"] == "local-system"
    assert body["is_admin"] is True
    assert body["scopes"] == sorted(body["scopes"])
    assert "read:knowledge" in body["scopes"]


def test_graph_without_read_scope_is_403_not_401(tmp_path, monkeypatch):
    _seed_tmp_env(tmp_path, monkeypatch)
    import knowledge_ops.api as api
    from knowledge_ops.api import app

    monkeypatch.setattr(api, "current_principal", lambda request: Principal(
        subject="scopeless", scopes=frozenset(), identities=frozenset({"scopeless"}),
    ))
    with TestClient(app) as client:
        res = client.get("/kb.graph")
        assert res.status_code == 403
        assert res.json()["error"]["type"] == "forbidden"
        # kb.me needs no scope: any authenticated principal may introspect itself.
        me = client.get("/kb.me")
        assert me.status_code == 200 and me.json()["is_admin"] is False


# ---------------------------------------------------------------- kb.stats
def test_stats_counts_all_source_kinds(tmp_path, monkeypatch):
    _seed_tmp_env(tmp_path, monkeypatch)
    _qdrant_settings(monkeypatch, ["org_memory"])

    def handler(method, url, body):
        if method == "GET" and url.endswith("/collections/org_memory"):
            return _Resp({"result": {"points_count": 396}})
        return _Resp(status=404)

    _install_fake_httpx(monkeypatch, handler)
    from knowledge_ops.api import app

    with TestClient(app) as client:
        sources = client.get("/kb.stats").json()["sources"]
    by_key = {s["key"]: s for s in sources}
    assert by_key["qdrant:org_memory"]["count"] == 396
    assert by_key["qdrant:org_memory"]["kind"] == "semantic"
    assert by_key["wiki"]["kind"] == "canonical" and by_key["wiki"]["count"] >= 1
    assert by_key["file_index"]["kind"] == "files"
    assert by_key["metrics"]["kind"] == "metrics"
    for source in sources:
        assert set(source) == {"key", "label", "count", "kind"}
        assert source["kind"] in {"semantic", "canonical", "files", "graph", "metrics"}
