from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest

from knowledge_ops.authorization import AuthorizationError, Principal, system_principal


def _settings(tmp_path: Path, monkeypatch) -> None:
    import knowledge_ops.config as config

    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "data" / "knowledge.sqlite3")
    monkeypatch.setattr(config.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(config.settings, "openwiki_staging_dir", tmp_path / "data" / "openwiki" / "staging")
    monkeypatch.setattr(config.settings, "default_file_roots", [tmp_path])
    monkeypatch.setattr(config.settings, "default_personal_owner", "local-system")
    monkeypatch.setattr(config.settings, "api_require_auth", False)


def test_legacy_database_upgrades_in_place(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.storage import connect, init_db

    config.settings.data_dir.mkdir(parents=True)
    with sqlite3.connect(config.settings.db_path) as db:
        db.execute(
            """CREATE TABLE wiki_docs(
              id TEXT PRIMARY KEY, path TEXT UNIQUE, title TEXT, body TEXT, scope TEXT,
              owner TEXT, created_at TEXT, updated_at TEXT
            )"""
        )
        db.execute(
            """INSERT INTO wiki_docs VALUES(
              'legacy', 'legacy.md', 'Legacy', 'body', 'global', 'org',
              '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00'
            )"""
        )
    init_db()
    with connect() as db:
        columns = {row["name"] for row in db.execute("PRAGMA table_info(wiki_docs)")}
        row = db.execute("SELECT valid_from, lifecycle_state, body FROM wiki_docs WHERE id='legacy'").fetchone()
        version = db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
    assert {"valid_from", "valid_until", "supersedes_id", "lifecycle_state", "tombstoned_at"} <= columns
    assert row["valid_from"] == "2025-01-01T00:00:00+00:00" and row["body"] == "body"
    assert row["lifecycle_state"] == "active" and version == 3


def test_supersession_supports_current_and_as_of_retrieval(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.wiki import get_doc, search_wiki, upsert_doc

    admin = system_principal()
    old_id = upsert_doc(
        "facts/old.md", "# Policy\n\nThe policy token is ALPHA-OLD.",
        valid_from="2025-01-01T00:00:00Z",
    )
    new_id = upsert_doc(
        "facts/new.md", "# Policy\n\nThe policy token is ALPHA-NEW.",
        valid_from="2025-02-01T00:00:00Z", supersedes_id=old_id,
    )
    assert not search_wiki("ALPHA-OLD", k=5, principal=admin)
    assert search_wiki("ALPHA-NEW", k=5, principal=admin)[0].metadata["document_id"] == new_id
    historical = search_wiki(
        "ALPHA-OLD", k=5, principal=admin, as_of="2025-01-15T00:00:00Z",
    )
    assert historical[0].metadata["document_id"] == old_id
    assert get_doc(old_id)["lifecycle_state"] == "superseded"
    with pytest.raises(ValueError):
        upsert_doc(
            "facts/bad.md", "# Bad", valid_from="2024-01-01T00:00:00Z",
            supersedes_id=new_id,
        )


def test_tombstone_purges_every_local_and_openwiki_projection(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.file_index import index_root
    from knowledge_ops.openwiki_pipeline import approve_import, get_import, stage_bundle
    from knowledge_ops.storage import connect
    from knowledge_ops.wiki import get_doc, search_wiki, tombstone_doc, upsert_doc

    source = tmp_path / "openwiki-output"
    source.mkdir()
    (source / "project.md").write_text(
        "---\ntype: knowledge\nsource_uri: connector://project/1\nscope: global\n---\n"
        "# Project\n\nThe purge token is DELETE-ME-9922.\n"
    )
    monkeypatch.setattr(config.settings, "openwiki_import_roots", [source])
    admin = system_principal()
    staged = stage_bundle(str(source), "0.2.0", admin)
    first_import_id = staged["import_id"]
    promoted = approve_import(staged["import_id"], admin, "Reviewed")
    doc_id = promoted["promoted_doc_ids"][0]
    governed = get_doc(doc_id)
    upsert_doc(
        "openwiki/project.md", governed["body"] + "\nRevision.",
        governed["scope"], governed["owner"],
    )
    # Restore governed provenance through a second reviewed snapshot.
    (source / "project.md").write_text((source / "project.md").read_text() + "\nRevision.\n")
    staged = stage_bundle(str(source), "0.2.0", admin)
    second_import_id = staged["import_id"]
    approve_import(staged["import_id"], admin, "Reviewed update")

    monkeypatch.setattr(config.settings, "default_file_roots", [config.settings.wiki_dir])
    assert index_root(config.settings.wiki_dir, scope="global", owner="org") > 0
    projection = config.settings.wiki_dir / "openwiki" / "project.md"
    assert projection.exists() and search_wiki("DELETE-ME-9922", k=5, principal=admin)

    receipt = tombstone_doc(doc_id, principal=admin, reason="source requested deletion")
    assert receipt["complete"] is True
    assert receipt["projections"]["openwiki_staging"] == "success"
    assert not projection.exists() and not search_wiki("DELETE-ME-9922", k=5, principal=admin)
    assert get_doc(doc_id)["body"] == "" and get_doc(doc_id)["title"] == "[deleted]"
    assert get_doc(doc_id)["lifecycle_state"] == "tombstoned"
    for import_id in (first_import_id, second_import_id):
        manifest = json.loads((config.settings.openwiki_staging_dir / import_id / "manifest.json").read_text())
        assert manifest["documents"] == [] and manifest["deleted_documents"] == ["project.md"]
        assert not (config.settings.openwiki_staging_dir / import_id / "documents" / "project.md").exists()
    imported = get_import(staged["import_id"], admin)
    assert imported["document_count"] == 0 and imported["deleted_document_count"] == 1
    (source / "project.md").write_text(
        "---\ntype: knowledge\nsource_uri: connector://project/1\nscope: global\n---\n"
        "# Project\n\nA producer attempted to restore DELETE-ME-9922.\n"
    )
    blocked = stage_bundle(str(source), "0.2.0", admin)
    assert blocked["state"] == "blocked" and blocked["documents"] == []
    assert any(item["category"] == "deletion" for item in blocked["findings"])
    assert not (
        config.settings.openwiki_staging_dir / blocked["import_id"] / "documents" / "project.md"
    ).exists()
    with connect() as db:
        assert db.execute("SELECT count(*) FROM wiki_fts WHERE doc_id=?", (doc_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM wiki_versions WHERE doc_id=?", (doc_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM wiki_provenance WHERE doc_id=?", (doc_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM file_chunks WHERE path=?", (str(projection.resolve()),)).fetchone()[0] == 0


def test_tombstone_projection_cleanup_retries_after_interruption(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    import knowledge_ops.openwiki_pipeline as pipeline
    from knowledge_ops.openwiki_pipeline import approve_import, stage_bundle
    from knowledge_ops.wiki import reconcile_pending_deletions, tombstone_doc

    source = tmp_path / "openwiki-output"
    source.mkdir()
    (source / "retry.md").write_text(
        "---\ntype: knowledge\nsource_uri: connector://retry/1\nscope: global\n---\n"
        "# Retry\n\nThe retry token is CLEANUP-8821.\n"
    )
    monkeypatch.setattr(config.settings, "openwiki_import_roots", [source])
    admin = system_principal()
    staged = stage_bundle(str(source), "0.2.0", admin)
    doc_id = approve_import(staged["import_id"], admin, "Reviewed")["promoted_doc_ids"][0]

    real_purge = pipeline.purge_staging_references
    attempts = 0

    def interrupted(source_id: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated projection interruption")
        return real_purge(source_id)

    monkeypatch.setattr(pipeline, "purge_staging_references", interrupted)
    first = tombstone_doc(doc_id, principal=admin, reason="erasure request")
    assert first["complete"] is False
    assert first["projections"]["openwiki_staging"] == "pending"

    # Repeating the same governed deletion resumes the durable receipt.
    retried = tombstone_doc(doc_id, principal=admin, reason="repeat request")
    assert retried["deletion_id"] == first["deletion_id"] and retried["complete"] is True
    assert retried["projections"]["openwiki_staging"] == "success"

    # Startup reconciliation also removes a projection reintroduced after a
    # receipt was marked complete (for example, after filesystem rollback).
    from knowledge_ops.file_index import index_root
    from knowledge_ops.storage import connect

    projection = config.settings.wiki_dir / "openwiki" / "retry.md"
    projection.parent.mkdir(parents=True, exist_ok=True)
    projection.write_text("# Restored projection\n\nCLEANUP-8821\n")
    monkeypatch.setattr(config.settings, "default_file_roots", [config.settings.wiki_dir])
    assert index_root(config.settings.wiki_dir, scope="global", owner="org") > 0
    assert reconcile_pending_deletions(include_completed=True) == {
        "processed": 1,
        "completed": 1,
        "pending": 0,
        "remaining": 0,
        "backlog": 0,
    }
    assert not projection.exists()
    with connect() as db:
        assert db.execute(
            "SELECT count(*) FROM file_chunks WHERE path=?", (str(projection.resolve()),)
        ).fetchone()[0] == 0
    manifest = json.loads(
        (config.settings.openwiki_staging_dir / staged["import_id"] / "manifest.json").read_text()
    )
    assert manifest["documents"] == []


def test_superseded_and_tombstoned_documents_cannot_be_resurrected(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.wiki import get_doc, tombstone_doc, upsert_doc

    admin = system_principal()
    old_id = upsert_doc(
        "policy/old.md", "# Policy\n\nOld content.",
        valid_from="2025-01-01T00:00:00Z",
    )
    upsert_doc(
        "policy/new.md", "# Policy\n\nNew content.",
        valid_from="2025-02-01T00:00:00Z", supersedes_id=old_id,
    )
    with pytest.raises(ValueError, match="immutable"):
        upsert_doc("policy/old.md", "# Policy\n\nReactivated content.")
    assert get_doc(old_id)["lifecycle_state"] == "superseded"

    deleted_id = upsert_doc("policy/deleted.md", "# Deleted\n\nSensitive content.")
    tombstone_doc(deleted_id, principal=admin, reason="erasure")
    with pytest.raises(ValueError, match="immutable"):
        upsert_doc("policy/deleted.md", "# Deleted\n\nRestored content.")
    assert get_doc(deleted_id)["body"] == ""


def test_existing_document_identity_and_validity_start_are_immutable(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.wiki import upsert_doc

    upsert_doc(
        "policy/identity.md", "# Identity\n\nOriginal.", "global", "organization",
        valid_from="2025-01-01T00:00:00Z",
    )
    with pytest.raises(AuthorizationError, match="scope and owner"):
        upsert_doc(
            "policy/identity.md", "# Identity\n\nOwner takeover.",
            "personal", "alice@example.com",
        )
    with pytest.raises(ValueError, match="valid_from is immutable"):
        upsert_doc(
            "policy/identity.md", "# Identity\n\nRetroactive shift.",
            "global", "organization", valid_from="2025-02-01T00:00:00Z",
        )


def test_review_queue_detects_staleness_and_conflicts(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import list_review_items, resolve_review_item, scan_review_queue
    from knowledge_ops.wiki import upsert_doc

    upsert_doc(
        "facts/stale.md", "# Review Me\n\nStill active.",
        review_due_at="2025-01-01T00:00:00Z",
    )
    upsert_doc("facts/positive.md", "# Launch Status\n\nThe Helios launch is approved.")
    upsert_doc("facts/negative.md", "# Launch Status\n\nThe Helios launch is not approved.")
    scan = scan_review_queue(at="2027-01-01T00:00:00Z")
    items = list_review_items()
    assert scan["detected"] == 2 and {item["kind"] for item in items} == {"stale", "conflict"}
    resolved = resolve_review_item(items[0]["id"], actor="operator", note="Reviewed source")
    assert resolved["status"] == "resolved"


def test_resolved_conflict_reopens_when_its_content_changes(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import list_review_items, resolve_review_item, scan_review_queue
    from knowledge_ops.wiki import upsert_doc

    upsert_doc("facts/a.md", "# Launch Status\n\nThe launch is approved.")
    upsert_doc("facts/b.md", "# Launch Status\n\nThe launch is not approved.")
    scan_review_queue()
    first = list_review_items(kind="conflict")[0]
    resolve_review_item(first["id"], actor="operator", note="Reviewed original evidence")
    upsert_doc("facts/b.md", "# Launch Status\n\nThe launch is never approved without review.")
    scan_review_queue()
    open_items = list_review_items(kind="conflict")
    assert len(open_items) == 1 and open_items[0]["id"] != first["id"]


def test_refresh_run_contract_is_idempotent(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import (
        complete_refresh_run, list_refresh_runs, run_due_refresh_jobs,
        run_refresh_job, upsert_refresh_job,
    )

    upsert_refresh_job("review", kind="review_scan", source_ref="wiki", interval_seconds=3600)
    first = run_refresh_job(
        "review", actor="operator", idempotency_key="manual-1",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    duplicate = run_refresh_job(
        "review", actor="other", idempotency_key="  manual-1  ",
        scheduled_for="2026-01-02T00:00:00Z",
    )
    assert first["run_id"] == duplicate["run_id"] and first["status"] == "completed"

    upsert_refresh_job("connector", kind="connector_refresh", source_ref="crm", interval_seconds=3600)
    with pytest.raises(ValueError, match="credential-free"):
        upsert_refresh_job(
            "unsafe", kind="connector_refresh",
            source_ref="https://user:password@example.invalid", interval_seconds=3600,
        )
    pending = run_refresh_job(
        "connector", actor="operator", idempotency_key="dispatch-1",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    assert pending["status"] == "pending"
    assert complete_refresh_run(pending["run_id"], status="completed", result={"changed": 2})["result"] == {"changed": 2}
    due = run_due_refresh_jobs(actor="scheduler", at="2026-01-02T00:00:00Z")
    assert {run["job_id"] for run in due} == {"review", "connector"}
    assert len({run["run_id"] for run in list_refresh_runs()}) == 4


def test_failed_synchronous_refresh_is_durably_recorded(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.memory_quality as quality

    quality.upsert_refresh_job(
        "review", kind="review_scan", source_ref="wiki", interval_seconds=3600,
    )

    def broken_scan(*, at=None):
        raise RuntimeError("simulated scanner failure")

    monkeypatch.setattr(quality, "scan_review_queue", broken_scan)
    with pytest.raises(RuntimeError, match="scanner failure"):
        quality.run_refresh_job(
            "review", actor="operator", idempotency_key="failed-1",
            scheduled_for="2026-01-01T00:00:00Z",
        )
    run = quality.list_refresh_runs()[0]
    assert run["status"] == "failed"
    assert run["error"].startswith("operation_failed:")
    assert "scanner failure" not in run["error"]


def test_citation_span_is_exact_and_authorized(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.citations import inspect_citation
    from knowledge_ops.wiki import search_wiki, upsert_doc

    alice = Principal(
        subject="alice@example.com", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"alice@example.com"}),
    )
    bob = Principal(
        subject="bob@example.com", scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"bob@example.com"}),
    )
    upsert_doc(
        "private.md", "# Private\n\nExact source phrase COBALT-4488 appears here.\n",
        "personal", "alice@example.com",
    )
    passage = search_wiki("COBALT-4488", k=1, principal=alice)[0]
    citation = inspect_citation(
        source="wiki", source_id=passage.source_id, claim="COBALT-4488", principal=alice,
    )
    assert citation["excerpt"] == passage.text
    assert citation["source_span"] == passage.metadata["source_span"]
    assert citation["match_ranges"] and citation["matched_terms"] == ["cobalt-4488"]
    with pytest.raises(AuthorizationError):
        inspect_citation(source="wiki", source_id=passage.source_id, claim="COBALT-4488", principal=bob)

    from knowledge_ops.wiki import upsert_doc

    upsert_doc(
        "private.md", "# Private\n\nThe source has changed.",
        "personal", "alice@example.com",
    )
    with pytest.raises(ValueError, match="source changed"):
        inspect_citation(
            source="wiki", source_id=passage.source_id,
            claim="COBALT-4488", principal=alice,
        )


def test_historical_federation_rejects_current_only_sources(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.federation import search
    from knowledge_ops.wiki import upsert_doc

    admin = system_principal()
    upsert_doc(
        "facts/history.md", "# History\n\nThe historical token is PAST-5511.",
        valid_from="2025-01-01T00:00:00Z",
    )
    result = asyncio.run(search(
        "PAST-5511", principal=admin, as_of="2025-01-02T00:00:00Z",
    ))
    assert result.passages and {passage.source for passage in result.passages} == {"wiki"}
    with pytest.raises(ValueError, match="supports only"):
        asyncio.run(search(
            "PAST-5511", sources=["wiki", "file_index"], principal=admin,
            as_of="2025-01-02T00:00:00Z",
        ))


def test_mcp_exposes_temporal_citation_and_deletion_contracts(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.mcp_http import handle

    monkeypatch.setattr(config.settings, "mcp_require_jwt", False)

    async def call(name: str, arguments: dict) -> dict:
        status, body = await handle({
            "jsonrpc": "2.0", "id": name, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        assert status == 200 and body["result"]["isError"] is False
        return body["result"]["structuredContent"]

    created = asyncio.run(call("upsert_doc", {
        "path": "mcp/temporal.md",
        "md": "# MCP Temporal\n\nThe MCP citation token is AGENT-3307.",
        "scope": "global",
        "valid_from": "2026-01-01T00:00:00Z",
    }))
    found = asyncio.run(call("search", {
        "query": "AGENT-3307", "sources": ["wiki"],
        "as_of": "2026-01-02T00:00:00Z",
    }))
    passage = found["passages"][0]
    inspected = asyncio.run(call("inspect_citation", {
        "source": passage["source"], "source_id": passage["source_id"],
        "claim": "The MCP citation token is AGENT-3307.",
    }))
    assert inspected["citation"]["inspection_available"] is True

    deleted = asyncio.run(call("delete_doc", {
        "id": created["id"], "reason": "MCP governed deletion test",
    }))
    assert deleted["deletion"]["complete"] is True


def test_deterministic_memory_evaluation_passes():
    from knowledge_ops.evaluation import evaluate_memory

    result = evaluate_memory()
    assert result["passed"] is True
    assert result["metrics"] == {
        "recall_at_5": 1.0,
        "mrr": 1.0,
        "citation_precision": 1.0,
        "unauthorized_recall": 0,
        "deleted_recall": 0,
    }


def test_memory_quality_ui_has_accessible_dialog_and_mobile_controls():
    root = Path(__file__).resolve().parent.parent / "knowledge_ops" / "static"
    html = (root / "index.html").read_text()
    css = (root / "ui" / "base.css").read_text()
    ask_js = (root / "ui" / "views" / "ask.js").read_text()
    assert '<dialog class="citation-dialog"' in html and 'aria-labelledby="citationTitle"' in html
    assert 'aria-label="Close citation inspection"' in html
    # Span inspection lives in the ask view now, with match-range highlighting.
    assert "Inspect span" in ask_js and "match_ranges" in ask_js
    assert "min-height: 44px" in css and "prefers-reduced-motion" in css
    assert "onclick=" not in html and "onclick=" not in ask_js
