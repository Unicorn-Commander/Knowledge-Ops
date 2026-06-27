"""Federation, live-adapter safety, manifest, and API-route tests.

These cover the productionization work: adapters must be non-crashing when their
stores are disabled/unreachable, the Brigade manifest must stay schema-valid, the
self-seeding + grounding path must return cited evidence, and the real REST routes
(dot-style, e.g. /kb.ground) must answer.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

MANIFEST = Path(__file__).resolve().parent.parent / ".well-known" / "mcps.json"


def _seed_tmp_env(tmp_path, monkeypatch):
    """Point all settings at a tmp data dir + a wiki dir with one seed doc."""
    import knowledge_ops.config as config
    import knowledge_ops.storage as storage
    import knowledge_ops.wiki as wiki

    wiki_dir = tmp_path / "wiki"
    db_path = tmp_path / "knowledge.sqlite3"
    for mod in (config, storage, wiki):
        monkeypatch.setattr(mod.settings, "data_dir", tmp_path)
        monkeypatch.setattr(mod.settings, "wiki_dir", wiki_dir)
        monkeypatch.setattr(mod.settings, "db_path", db_path)
    # ai_memory chunks file shouldn't exist in tests.
    monkeypatch.setattr(config.settings, "ai_memory_chunks", tmp_path / "nope.jsonl")
    (wiki_dir / "marketing").mkdir(parents=True, exist_ok=True)
    (wiki_dir / "marketing" / "mu.md").write_text(
        "# Magic Unicorn\n\nMagic Unicorn builds sovereign AI you actually own — "
        "infrastructure, AI employees, and private search, all on your hardware.",
        encoding="utf-8",
    )


def test_manifest_is_valid_for_broker():
    """Manifest must satisfy the live broker's federation contract."""
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key in ("name", "version", "mcp_endpoint", "tool_namespace", "auth", "tools"):
        assert key in doc, f"missing {key}"
    ns = doc["tool_namespace"]
    assert 6 <= len(ns) <= 12 and ns.islower(), ns
    fed = doc["auth"]["federated_auth"]
    assert fed["signing_algorithm"] == "RS256"
    assert fed["expected_audience"] == "knowledge-ops"
    assert doc["tools"], "tools must be non-empty"
    for t in doc["tools"]:
        for k in ("name", "input_schema", "scopes_required", "tier_required", "read_only"):
            assert k in t, f"tool {t.get('name')} missing {k}"
        assert t["tier_required"] in {"free", "pro", "enterprise"}
        assert isinstance(t["read_only"], bool)


def test_qdrant_adapter_disabled_returns_empty(monkeypatch):
    import knowledge_ops.config as config
    from knowledge_ops.adapters.qdrant import search_qdrant

    monkeypatch.setattr(config.settings, "enable_qdrant", False)
    assert asyncio.run(search_qdrant("anything", k=4)) == []


def test_falkordb_adapter_disabled_returns_empty(monkeypatch):
    import knowledge_ops.config as config
    from knowledge_ops.adapters.falkordb import search_falkordb

    monkeypatch.setattr(config.settings, "enable_falkordb", False)
    assert asyncio.run(search_falkordb("magic unicorn", k=4)) == []


def test_falkordb_unreachable_is_non_crashing(monkeypatch):
    """Enabled but pointed at a dead address must degrade to [] (no exception)."""
    import knowledge_ops.config as config
    from knowledge_ops.adapters.falkordb import search_falkordb

    monkeypatch.setattr(config.settings, "enable_falkordb", True)
    monkeypatch.setattr(config.settings, "falkordb_url", "redis://127.0.0.1:6399")
    assert asyncio.run(search_falkordb("magic unicorn entity", k=4)) == []


def test_falkordb_terms_are_sanitized():
    from knowledge_ops.adapters.falkordb import _terms

    terms = _terms("What is Magic-Unicorn's 'sovereign' AI?")
    assert all(t.isalnum() and t.islower() for t in terms)
    assert "magicunicorns" in terms or "magic" in " ".join(terms)


def test_ground_returns_cited_evidence(tmp_path, monkeypatch):
    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops import federation
    from knowledge_ops.wiki import seed_wiki_from_disk

    seed_wiki_from_disk()
    res = asyncio.run(federation.ground("What is Magic Unicorn?", scope="global", k=5))
    assert res["verdict"] == "evidence_found"
    assert res["passages"], "expected supporting passages"
    assert "Citations:" in res["answer"]
    assert any(p["source"] == "wiki" for p in res["passages"])


def test_list_sources_includes_falkordb():
    from knowledge_ops import federation

    names = {s.name for s in federation.list_sources()}
    assert {"qdrant", "falkordb", "wiki", "org_memory"} <= names


def test_api_health_and_manifest_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.api import app

    with TestClient(app) as client:  # triggers lifespan (seed-on-startup)
        assert client.get("/health").json()["ok"] is True
        man = client.get("/.well-known/mcps.json")
        assert man.status_code == 200
        assert man.json()["tool_namespace"] == "knowledge"
        # Real route is dot-style POST /kb.ground (NOT /kb/ground or /ground).
        assert client.post("/kb/ground", json={"query": "x"}).status_code == 404
        grounded = client.post("/kb.ground", json={"query": "What is Magic Unicorn?", "scope": "global"})
        assert grounded.status_code == 200
        assert grounded.json()["passages"]
