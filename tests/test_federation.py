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
    monkeypatch.setattr(config.settings, "openwiki_staging_dir", tmp_path / "openwiki" / "staging")
    monkeypatch.setattr(config.settings, "default_file_roots", [tmp_path])
    monkeypatch.setattr(config.settings, "default_personal_owner", "local-system")
    monkeypatch.setattr(config.settings, "api_require_auth", False)
    # This helper exists to produce a wiki dir that HAS a seed corpus, so the
    # internal-corpus gate belongs on for anything built on it. Automatic seeding
    # defaults off in production (P-00099 finding 3) — see
    # test_seed_corpus_is_off_unless_explicitly_enabled for that half.
    monkeypatch.setattr(config.settings, "seed_internal_corpus", True)
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
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.wiki import seed_wiki_from_disk

    # Explicit: this test IS the internal-corpus case (P-00099 finding 3 gate).
    seed_wiki_from_disk(allow_internal_corpus=True)
    res = asyncio.run(federation.ground(
        "What is Magic Unicorn?", scope="global", k=5, principal=system_principal()
    ))
    assert res["verdict"] == "related_evidence"
    assert res["verification_status"] == "heuristic_not_verified"
    assert res["passages"], "expected supporting passages"
    assert "Evidence excerpts" in res["answer"] and "Source:" in res["answer"]
    assert any(p["source"] == "wiki" for p in res["passages"])


def test_list_sources_includes_falkordb():
    from knowledge_ops import federation

    names = {s.name for s in federation.list_sources()}
    assert {"qdrant", "falkordb", "wiki", "org_memory"} <= names


def test_list_sources_expands_configured_live_sources(monkeypatch):
    import knowledge_ops.config as config
    from knowledge_ops import federation

    monkeypatch.setattr(config.settings, "enable_qdrant", True)
    monkeypatch.setattr(config.settings, "qdrant_collections", ["meeting_transcripts", "crisis_evidence"])
    monkeypatch.setattr(config.settings, "enable_falkordb", True)
    monkeypatch.setattr(config.settings, "falkordb_graphs", ["brigade_global", "contact_ops__magic_unicorn_inc"])
    monkeypatch.setattr(config.settings, "rest_sources", [{"name": "center_deep_catalogue", "url": "https://verify.centerdeep.online/api/v1/catalogue/quick-search"}])
    monkeypatch.setattr(config.settings, "postgres_sources", [{"name": "accounting_ops", "dsn": "postgresql://example/db", "sql": "select 1"}])

    names = {s.name for s in federation.list_sources()}
    assert {
        "qdrant:meeting_transcripts",
        "qdrant:crisis_evidence",
        "falkordb:contact_ops__magic_unicorn_inc",
        "rest:center_deep_catalogue",
        "postgres:accounting_ops",
    } <= names


def test_postgres_adapter_disabled_by_missing_driver_or_dead_dsn(monkeypatch):
    import knowledge_ops.config as config
    from knowledge_ops.adapters.postgres_source import search_postgres_sources

    monkeypatch.setattr(config.settings, "postgres_sources", [{
        "name": "accounting_ops",
        "dsn": "postgresql://127.0.0.1:1/nope",
        "sql": "select '1' as id, 'title' as title, 'body' as text",
    }])
    assert asyncio.run(search_postgres_sources("anything", names={"accounting_ops"})) == []


def test_postgres_authorization_contract_requires_identity_bound_select():
    from knowledge_ops.adapters.postgres_source import has_authorization_contract

    base = {
        "name": "accounting_ops",
        "dsn": "postgresql://example/db",
        "enforces_authorization": True,
    }
    assert not has_authorization_contract({**base, "sql": "select * from knowledge_docs"})
    assert not has_authorization_contract({**base, "sql": "delete from knowledge_docs where owner = %(owner)s"})
    assert has_authorization_contract({
        **base,
        "sql": "select * from knowledge_docs where owner = %(owner)s",
    })


def test_api_health_and_manifest_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.api import app

    with TestClient(app) as client:  # triggers lifespan (seed-on-startup)
        assert client.get("/health").json()["ok"] is True
        assert client.get("/ready").json()["ok"] is True
        man = client.get("/.well-known/mcps.json")
        assert man.status_code == 200
        assert man.json()["tool_namespace"] == "knowledge"
        # Real route is dot-style POST /kb.ground (NOT /kb/ground or /ground).
        assert client.post("/kb/ground", json={"query": "x"}).status_code == 404
        grounded = client.post("/kb.ground", json={"query": "What is Magic Unicorn?", "scope": "global"})
        assert grounded.status_code == 200
        assert grounded.json()["passages"]


def test_seed_corpus_is_off_unless_explicitly_enabled(tmp_path, monkeypatch):
    """P-00099 finding 3 regression: automatic seeding must default OFF.

    seed_wiki_from_disk() writes scope=global owner=magic-unicorn rows, readable by
    every principal on the instance. On 2026-09-15 the customer-facing deployment was
    measured holding two Magic Unicorn internal documents put there by this path with
    no human involved. The gate, not the emptiness of wiki/, is what makes that
    unrepeatable — an empty wiki/ is one careless `cp` away from being full again.
    """
    import knowledge_ops.config as config
    from knowledge_ops.wiki import list_docs, seed_wiki_from_disk

    _seed_tmp_env(tmp_path, monkeypatch)

    # Default: the flag is off, so a corpus sitting on disk is NOT ingested.
    monkeypatch.setattr(config.settings, "seed_internal_corpus", False)
    assert seed_wiki_from_disk() == []
    assert list_docs() == [], "seed corpus was ingested with the gate off"

    # Internal ring: the flag is on, so it is.
    monkeypatch.setattr(config.settings, "seed_internal_corpus", True)
    assert seed_wiki_from_disk(), "gate on but nothing seeded"
    assert any(d["path"] == "marketing/mu.md" for d in list_docs())


def test_seed_corpus_flag_defaults_off_in_settings():
    """The DEFAULT must be off, not just off in this environment.

    KNOWLEDGE_OPS_MCP_REQUIRE_JWT defaulting OPEN is how the same knowledge base
    ended up unauthenticated on a customer host. A security default belongs in an
    assertion, not in a deploy checklist.
    """
    import os

    from knowledge_ops.config import Settings

    for value in (None, "", "0", "false", "no"):
        env = {k: v for k, v in os.environ.items() if k != "KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS"}
        if value is not None:
            env["KNOWLEDGE_OPS_SEED_INTERNAL_CORPUS"] = value
        real = os.environ
        try:
            os.environ = env  # type: ignore[assignment]
            assert Settings().seed_internal_corpus is False, f"default not off for {value!r}"
        finally:
            os.environ = real  # type: ignore[assignment]
