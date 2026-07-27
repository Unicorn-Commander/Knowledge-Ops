"""Wiki-link semantics: parser, resolver, link graph, and the HTTP surface.

The pure-function tests (parse_links / resolver / build_link_graph) need no
backend at all. The endpoint tests reuse the offline seams the rest of the
suite uses: a tmp-dir SQLite env from tests.test_federation and the fake
Qdrant httpx client from tests.test_graph_api.
"""
from __future__ import annotations

import asyncio

import pytest

import knowledge_ops.graph_api as graph_api
from knowledge_ops.authorization import Principal, system_principal
from knowledge_ops.wikilinks import build_link_graph, parse_links, resolver


@pytest.fixture(autouse=True)
def _clear_graph_caches():
    graph_api._CACHE.clear()
    graph_api._LINKS_CACHE.clear()
    yield
    graph_api._CACHE.clear()
    graph_api._LINKS_CACHE.clear()


# ================================================================ parse_links
def test_wikilink_basic():
    assert parse_links("See [[Target]].") == [
        {"target": "Target", "label": "Target", "kind": "wikilink"}
    ]


def test_wikilink_with_label():
    assert parse_links("[[Target|the label]]") == [
        {"target": "Target", "label": "the label", "kind": "wikilink"}
    ]


def test_wikilink_label_keeps_extra_pipes():
    assert parse_links("[[a|b|c]]") == [{"target": "a", "label": "b|c", "kind": "wikilink"}]


def test_wikilink_whitespace_trimmed():
    assert parse_links("[[  Spaced Target  |  lbl ]]") == [
        {"target": "Spaced Target", "label": "lbl", "kind": "wikilink"}
    ]


def test_empty_or_blank_targets_ignored():
    assert parse_links("[[]] [[   ]] [[|label-only]]") == []


def test_unclosed_wikilink_ignored():
    assert parse_links("dangling [[never closed") == []


def test_mdlink_to_md_file():
    assert parse_links("[guide](apps/guide.md)") == [
        {"target": "apps/guide.md", "label": "guide", "kind": "mdlink"}
    ]


def test_mdlink_with_title_and_angle_brackets():
    assert parse_links('[a](<b.md> "tooltip")') == [
        {"target": "b.md", "label": "a", "kind": "mdlink"}
    ]


def test_mdlink_fragment_and_query_stripped():
    assert parse_links("[a](docs/x.md#section) [b](y.md?v=2)") == [
        {"target": "docs/x.md", "label": "a", "kind": "mdlink"},
        {"target": "y.md", "label": "b", "kind": "mdlink"},
    ]


def test_mdlink_non_md_hrefs_ignored():
    assert parse_links("[img](x.png) [site](https://example.com) [mdish](x.mdx)") == []


def test_external_md_href_parses_but_never_resolves():
    links = parse_links("[ext](https://evil.example/x.md)")
    assert links == [{"target": "https://evil.example/x.md", "label": "ext", "kind": "mdlink"}]
    resolve = resolver([{"path": "x.md", "title": "X"}])
    assert resolve(links[0]["target"]) is None


def test_image_is_not_a_link():
    assert parse_links("![alt text](diagram.md)") == []


def test_fenced_code_not_parsed():
    md = "before\n```\n[[not-a-link]] [also](not.md)\n```\n[[real]]"
    assert parse_links(md) == [{"target": "real", "label": "real", "kind": "wikilink"}]


def test_tilde_fence_and_longer_closing_fence():
    md = "~~~text\n[[nope]]\n~~~~\n[[yes]]\n````\n[[hidden by unclosed fence]]"
    assert parse_links(md) == [{"target": "yes", "label": "yes", "kind": "wikilink"}]


def test_fence_of_other_char_does_not_close():
    md = "```\n~~~\n[[still inside backtick fence]]\n```\n[[out]]"
    assert parse_links(md) == [{"target": "out", "label": "out", "kind": "wikilink"}]


def test_inline_code_not_parsed():
    assert parse_links("use `[[not-a-link]]` and ``[x](y.md)`` but [[real]]") == [
        {"target": "real", "label": "real", "kind": "wikilink"}
    ]


def test_adversarial_html_target_stays_plain_data():
    links = parse_links("[[<img src=x onerror=alert(1)>]]")
    assert links == [{
        "target": "<img src=x onerror=alert(1)>",
        "label": "<img src=x onerror=alert(1)>",
        "kind": "wikilink",
    }]
    # Parsing never invents markup — the target is a plain str for md.js to escape.
    assert isinstance(links[0]["target"], str)


def test_nested_brackets_only_innermost_parses():
    assert parse_links("[[outer [[inner]] tail]]") == [
        {"target": "inner", "label": "inner", "kind": "wikilink"}
    ]


def test_links_returned_in_document_order():
    md = "[md first](a.md) then [[Wiki Second]] then [md third](b.md)"
    assert [l["target"] for l in parse_links(md)] == ["a.md", "Wiki Second", "b.md"]


# ================================================================ resolver
_DOCS = [
    {"path": "apps/Majiks-Launch-Runbook.md", "title": "Majiks Launch Runbook"},
    {"path": "index.md", "title": "Home"},
    {"path": "ops/notes.md", "title": "Ops Notes"},
    {"path": "archive/notes.md", "title": "Old Notes"},
]


def test_resolve_exact_title_case_insensitive():
    resolve = resolver(_DOCS)
    assert resolve("majiks launch runbook") == "apps/Majiks-Launch-Runbook.md"
    assert resolve("HOME") == "index.md"


def test_resolve_exact_path_with_and_without_extension():
    resolve = resolver(_DOCS)
    assert resolve("apps/Majiks-Launch-Runbook.md") == "apps/Majiks-Launch-Runbook.md"
    assert resolve("apps/Majiks-Launch-Runbook") == "apps/Majiks-Launch-Runbook.md"
    assert resolve("APPS/majiks-launch-runbook.MD") == "apps/Majiks-Launch-Runbook.md"


def test_resolve_unique_path_suffix():
    resolve = resolver(_DOCS)
    assert resolve("Majiks-Launch-Runbook") == "apps/Majiks-Launch-Runbook.md"


def test_ambiguous_suffix_is_missing():
    resolve = resolver(_DOCS)
    assert resolve("notes") is None  # ops/notes.md vs archive/notes.md
    assert resolve("notes.md") is None


def test_ambiguous_title_falls_through_to_path_tiers():
    docs = [
        {"path": "notes/Runbook.md", "title": "Runbook"},
        {"path": "other/Runbook-2.md", "title": "Runbook"},
    ]
    resolve = resolver(docs)
    # Title tier is ambiguous, but the suffix tier is unique for Runbook.md.
    assert resolve("Runbook") == "notes/Runbook.md"


def test_title_match_wins_over_suffix_match():
    docs = [
        {"path": "guides/target.md", "title": "Other"},
        {"path": "x.md", "title": "target"},
    ]
    assert resolver(docs)("target") == "x.md"


def test_resolve_normalizes_separators_and_prefixes():
    resolve = resolver(_DOCS)
    assert resolve("apps\\Majiks-Launch-Runbook.md") == "apps/Majiks-Launch-Runbook.md"
    assert resolve("./index.md") == "index.md"
    assert resolve("/index.md") == "index.md"


def test_resolve_rejects_schemes_and_blank():
    resolve = resolver(_DOCS)
    for target in ("https://x/index.md", "mailto:a@b.md", "javascript:alert(1)//.md",
                   "//host/index.md", "", "   ", "./"):
        assert resolve(target) is None


# ================================================================ build_link_graph
def _graph_docs():
    return [
        {"path": "index.md", "title": "Home",
         "body": "See [[Majiks Launch Runbook]] and [notes](ops/notes.md) and [[Nowhere]]."},
        {"path": "apps/Majiks-Launch-Runbook.md", "title": "Majiks Launch Runbook",
         "body": "Back to [[Home|the index]]. Again [[Home]]. Self: [[Majiks Launch Runbook]]."},
        {"path": "ops/notes.md", "title": "Ops Notes",
         "body": "```\n[[Home]]\n```\nno live links, and a dead md link [x](gone/away.md)."},
    ]


def test_link_graph_links_missing_and_orphans():
    graph = build_link_graph(_graph_docs())
    assert {(l["from_path"], l["to_path"]) for l in graph["links"]} == {
        ("index.md", "apps/Majiks-Launch-Runbook.md"),
        ("index.md", "ops/notes.md"),
        ("apps/Majiks-Launch-Runbook.md", "index.md"),
    }
    by_pair = {(l["from_path"], l["to_path"]): l["text"] for l in graph["links"]}
    assert by_pair[("apps/Majiks-Launch-Runbook.md", "index.md")] == "the index"  # first wins
    # Only unresolved WIKILINKS are missing; the dead md link is dropped silently.
    assert graph["missing"] == [{"from_path": "index.md", "target": "Nowhere"}]
    # Never linked-to; a self-link does not rescue orphanhood.
    assert graph["orphans"] == []


def test_link_graph_orphans_and_code_fence_suppression():
    docs = _graph_docs()
    docs[1]["body"] = "no links here at all"  # runbook no longer links back
    graph = build_link_graph(docs)
    assert graph["orphans"] == ["index.md"]
    # The fenced [[Home]] inside ops/notes.md stayed dead.
    assert all(l["from_path"] != "ops/notes.md" for l in graph["links"])


def test_link_graph_dedupes_and_skips_self_links():
    graph = build_link_graph(_graph_docs())
    pairs = [(l["from_path"], l["to_path"]) for l in graph["links"]]
    assert len(pairs) == len(set(pairs))  # deduped
    assert all(a != b for a, b in pairs)  # no self edges


def test_link_graph_accepts_md_body_alias_and_skips_pathless():
    graph = build_link_graph([
        {"path": "a.md", "title": "A", "md": "[[B]]"},
        {"path": "b.md", "title": "B", "md": ""},
        {"title": "no path", "md": "[[A]]"},
    ])
    assert graph["links"] == [{"from_path": "a.md", "to_path": "b.md", "text": "B"}]
    assert graph["orphans"] == ["a.md"]


# ================================================================ /kb.links
def _seed_docs():
    from knowledge_ops.wiki import upsert_doc

    upsert_doc("index.md", "# Home\n\nSee [[Majiks-Launch-Runbook]] and [[Nowhere]].")
    upsert_doc("apps/Majiks-Launch-Runbook.md", "# Majiks Launch Runbook\n\nSteps.")
    upsert_doc("lonely.md", "# Lonely\n\nNo links.")


def test_kb_links_endpoint_shape(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    _seed_docs()
    from fastapi.testclient import TestClient

    from knowledge_ops.api import app

    with TestClient(app) as client:
        res = client.get("/kb.links")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"links", "missing", "orphans", "count"}
    assert {"from_path": "index.md", "to_path": "apps/Majiks-Launch-Runbook.md",
            "text": "Majiks-Launch-Runbook"} in body["links"]
    assert {"from_path": "index.md", "target": "Nowhere"} in body["missing"]
    assert "lonely.md" in body["orphans"] and "index.md" in body["orphans"]
    assert "apps/Majiks-Launch-Runbook.md" not in body["orphans"]
    assert body["count"] == len(body["links"])


def test_kb_links_respects_visibility(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.wiki import upsert_doc

    upsert_doc("shared.md", "# Shared\n\nPublic.")
    upsert_doc("mine.md", "# Mine\n\n[[Shared]]", scope="personal", owner="aaron")
    upsert_doc("theirs.md", "# Theirs\n\n[[Shared]]", scope="personal", owner="someone-else")

    import knowledge_ops.api as api
    from fastapi.testclient import TestClient
    from knowledge_ops.api import app

    monkeypatch.setattr(api, "current_principal", lambda request: Principal(
        subject="aaron", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"aaron"}),
    ))
    with TestClient(app) as client:
        body = client.get("/kb.links").json()
    froms = {l["from_path"] for l in body["links"]}
    assert froms == {"mine.md"}  # someone-else's personal doc never leaks
    assert "theirs.md" not in body["orphans"]
    assert "mine.md" in body["orphans"]


def test_kb_links_cached_and_invalidated(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    _seed_docs()
    from fastapi.testclient import TestClient

    from knowledge_ops.api import app

    builds = []
    real = graph_api.build_link_graph
    monkeypatch.setattr(graph_api, "build_link_graph", lambda docs: builds.append(1) or real(docs))
    with TestClient(app) as client:
        first = client.get("/kb.links").json()
        second = client.get("/kb.links").json()
        assert first == second
        assert len(builds) == 1  # 60s TTL cache: one build for two reads

        # A save through the API invalidates both the links and graph caches.
        res = client.post("/kb.upsert_doc", json={
            "path": "lonely.md",
            "md": "# Lonely\n\nNow links [[Majiks-Launch-Runbook]].",
            "scope": "global", "owner": "magic-unicorn",
        })
        assert res.status_code == 200
        third = client.get("/kb.links").json()
    assert len(builds) == 2
    assert {"from_path": "lonely.md", "to_path": "apps/Majiks-Launch-Runbook.md",
            "text": "Majiks-Launch-Runbook"} in third["links"]


def test_kb_links_requires_read_scope(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    import knowledge_ops.api as api
    from fastapi.testclient import TestClient
    from knowledge_ops.api import app

    monkeypatch.setattr(api, "current_principal", lambda request: Principal(
        subject="scopeless", scopes=frozenset(), identities=frozenset({"scopeless"}),
    ))
    with TestClient(app) as client:
        assert client.get("/kb.links").status_code == 403


# ================================================================ /kb.doc_version
def test_doc_version_returns_historical_body(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.wiki import upsert_doc

    doc_id = upsert_doc("guide.md", "# Guide\n\nfirst body")
    upsert_doc("guide.md", "# Guide\n\nsecond body")
    from fastapi.testclient import TestClient

    from knowledge_ops.api import app

    with TestClient(app) as client:
        history = client.get("/kb.doc_history", params={"id": doc_id}).json()["history"]
        assert len(history) == 1
        vid = history[0]["version_id"]
        res = client.get("/kb.doc_version", params={"id": doc_id, "version_id": vid})
        assert res.status_code == 200
        version = res.json()["version"]
        assert version["body"] == "# Guide\n\nfirst body"
        assert version["version"] == vid and version["version_id"] == vid
        assert version["saved_at"] == version["created_at"]
        assert version["bytes"] == len(version["body"])
        # The path form of ``id`` and the ``version`` alias both work.
        alias = client.get("/kb.doc_version", params={"id": "guide.md", "version": vid})
        assert alias.status_code == 200
        assert alias.json()["version"]["body"] == "# Guide\n\nfirst body"


def test_doc_version_errors(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.wiki import upsert_doc

    doc_id = upsert_doc("guide.md", "# Guide\n\nonly body")
    from fastapi.testclient import TestClient

    from knowledge_ops.api import app

    with TestClient(app) as client:
        assert client.get("/kb.doc_version", params={"id": "missing.md", "version_id": 1}).status_code == 404
        assert client.get("/kb.doc_version", params={"id": doc_id, "version_id": 999}).status_code == 404
        assert client.get("/kb.doc_version", params={"id": doc_id}).status_code == 400


def test_doc_version_foreign_personal_doc_is_403(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.wiki import upsert_doc

    doc_id = upsert_doc("private.md", "# P\n\nv1", scope="personal", owner="someone-else")
    upsert_doc("private.md", "# P\n\nv2", scope="personal", owner="someone-else")

    import knowledge_ops.api as api
    from fastapi.testclient import TestClient
    from knowledge_ops.api import app

    monkeypatch.setattr(api, "current_principal", lambda request: Principal(
        subject="aaron", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"aaron"}),
    ))
    with TestClient(app) as client:
        res = client.get("/kb.doc_version", params={"id": doc_id, "version_id": 1})
    assert res.status_code == 403


# ================================================================ /kb.graph link edges
def _wiki_points_handler(wiki_dir):
    """Fake Qdrant: two wiki points whose payload paths map to seeded docs."""
    from tests.test_graph_api import _Resp

    def handler(method, url, body):
        if url.endswith("/points/scroll"):
            return _Resp({"result": {"points": [
                {"id": 1, "payload": {"title": "One", "path": "one.md", "scope": "global",
                                      "text": "short"}},
                # Absolute payload path: node ref must normalize to the doc path.
                {"id": 2, "payload": {"title": "Two", "path": f"{wiki_dir}/two.md",
                                      "scope": "global", "text": "short"}},
            ]}})
        if url.endswith("/points/recommend"):
            return _Resp({"result": [{"id": 2, "score": 0.87}]})
        if url.endswith("/points/search"):
            return _Resp({"result": []})
        if url.endswith("/points"):
            return _Resp({"result": []})
        return _Resp(status=404)

    return handler


def test_graph_gains_wiki_link_edges_with_kinds(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env
    from tests.test_graph_api import _install_fake_httpx, _qdrant_settings

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.config import settings
    from knowledge_ops.wiki import upsert_doc

    upsert_doc("one.md", "# One\n\nSee [[Two]].")
    upsert_doc("two.md", "# Two\n\nLeaf.")
    _qdrant_settings(monkeypatch, ["knowledge_ops_wiki"])
    _install_fake_httpx(monkeypatch, _wiki_points_handler(str(settings.wiki_dir).rstrip("/")))

    res = asyncio.run(graph_api.kb_graph(system_principal(), scope=None, limit_per_coll=10))

    refs = {n["id"]: n["ref"] for n in res["nodes"]}
    assert refs == {"knowledge_ops_wiki:1": "one.md", "knowledge_ops_wiki:2": "two.md"}
    by_kind = {}
    for edge in res["edges"]:
        by_kind.setdefault(edge["kind"], []).append(edge)
    assert {"a": "knowledge_ops_wiki:1", "b": "knowledge_ops_wiki:2", "w": 1,
            "kind": "link"} in by_kind["link"]
    assert all(e["kind"] == "semantic" and 0 < e["w"] <= 1 for e in by_kind["semantic"])
    assert res["stats"]["edge_count"] == len(res["edges"])


def test_graph_survives_broken_wiki_store(tmp_path, monkeypatch):
    from tests.test_federation import _seed_tmp_env
    from tests.test_graph_api import _install_fake_httpx, _qdrant_settings

    _seed_tmp_env(tmp_path, monkeypatch)
    _qdrant_settings(monkeypatch, ["knowledge_ops_wiki"])
    _install_fake_httpx(monkeypatch, _wiki_points_handler("/nowhere"))

    def boom(principal, scope):
        raise RuntimeError("sqlite is on fire")

    monkeypatch.setattr(graph_api, "_link_graph", boom)
    res = asyncio.run(graph_api.kb_graph(system_principal(), scope=None, limit_per_coll=10))
    assert res["nodes"]  # graph thinned, not 500ed
    assert all(e["kind"] == "semantic" for e in res["edges"])
