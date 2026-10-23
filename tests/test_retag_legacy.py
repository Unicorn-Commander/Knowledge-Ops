from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _settings(tmp_path: Path, monkeypatch) -> None:
    import knowledge_ops.config as config

    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(
        config.settings, "db_path", tmp_path / "data" / "knowledge.sqlite3"
    )
    monkeypatch.setattr(config.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(
        config.settings,
        "openwiki_staging_dir",
        tmp_path / "data" / "openwiki" / "staging",
    )
    monkeypatch.setattr(config.settings, "enable_qdrant", False)


def _insert_legacy_rows() -> None:
    from knowledge_ops.storage import connect, init_db

    init_db()
    rows = [
        ("g1", "global-one.md", "global", "magic-unicorn"),
        ("g2", "global-two.md", "global", "magic-unicorn"),
        ("g3", "global-three.md", "global", "21460000-753b-46b7-9f33-d4d9609077ed"),
        ("l1", "legal-one.md", "legal", "magic-unicorn"),
        ("l2", "legal-two.md", "legal", "aaron"),
        ("i1", "infrastructure-one.md", "infrastructure", "magic-unicorn"),
        ("p1", "personal-one.md", "personal", "aaron"),
    ]
    with connect() as db:
        for doc_id, path, scope, owner in rows:
            db.execute(
                """INSERT INTO wiki_docs(
                     id, path, title, body, scope, owner, created_at, updated_at,
                     valid_from, valid_until, supersedes_id, lifecycle_state,
                     tombstoned_at, tombstone_reason, review_due_at,
                     visibility_workspace_id, visibility_project_id
                   ) VALUES (?, ?, ?, ?, ?, ?, 'now', 'now', 'now', NULL, NULL,
                             'active', NULL, NULL, NULL, NULL, NULL)""",
                (doc_id, path, path, f"body for {path}", scope, owner),
            )


def test_dry_run_maps_scope_owner_groups_without_writing(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    _insert_legacy_rows()
    from knowledge_ops.retag_legacy import retag_legacy
    from knowledge_ops.storage import connect

    result = retag_legacy("magic-unicorn", "legal-ops")

    assert result["mode"] == "dry-run"
    assert result["legacy_rows"] == 7
    assert result["would_update"] == 7
    assert result["groups"] == [
        {
            "scope": "global",
            "owner": "21460000-753b-46b7-9f33-d4d9609077ed",
            "workspace": "magic-unicorn",
            "project": None,
            "count": 1,
        },
        {
            "scope": "global",
            "owner": "magic-unicorn",
            "workspace": "magic-unicorn",
            "project": None,
            "count": 2,
        },
        {
            "scope": "infrastructure",
            "owner": "magic-unicorn",
            "workspace": "magic-unicorn",
            "project": None,
            "count": 1,
        },
        {
            "scope": "legal",
            "owner": "aaron",
            "workspace": "magic-unicorn",
            "project": "legal-ops",
            "count": 1,
        },
        {
            "scope": "legal",
            "owner": "magic-unicorn",
            "workspace": "magic-unicorn",
            "project": "legal-ops",
            "count": 1,
        },
        {
            "scope": "personal",
            "owner": "aaron",
            "workspace": "magic-unicorn",
            "project": None,
            "count": 1,
        },
    ]
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM wiki_docs WHERE visibility_workspace_id IS NULL AND visibility_project_id IS NULL"
            ).fetchone()[0]
            == 7
        )


def test_dry_run_does_not_create_a_missing_database(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.retag_legacy import retag_legacy
    import knowledge_ops.config as config

    with pytest.raises(ValueError, match="database does not exist"):
        retag_legacy("magic-unicorn", "legal-ops")
    assert not config.settings.db_path.exists()


def test_apply_requires_a_project_for_legal_rows_and_does_not_write(
    tmp_path, monkeypatch
):
    _settings(tmp_path, monkeypatch)
    _insert_legacy_rows()
    from knowledge_ops.retag_legacy import retag_legacy
    from knowledge_ops.storage import connect

    with pytest.raises(ValueError, match="legal-project"):
        retag_legacy("magic-unicorn", apply=True)
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM wiki_docs WHERE visibility_workspace_id IS NULL AND visibility_project_id IS NULL"
            ).fetchone()[0]
            == 7
        )


def test_apply_is_idempotent_and_reindexes_canonical_wiki(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    _insert_legacy_rows()
    from knowledge_ops.retag_legacy import retag_legacy
    from knowledge_ops.storage import connect

    first = retag_legacy("magic-unicorn", "legal-ops", apply=True)
    second = retag_legacy("magic-unicorn", "legal-ops", apply=True)

    assert first["updated"] == 7
    assert first["reindex"] == {"indexed": 7, "eligible": 7}
    assert second["legacy_rows"] == 0
    assert second["updated"] == 0
    with connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM wiki_docs WHERE visibility_workspace_id IS NULL AND visibility_project_id IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM wiki_docs WHERE visibility_workspace_id='magic-unicorn' AND visibility_project_id='legal-ops'"
            ).fetchone()[0]
            == 2
        )


def test_qdrant_sync_updates_visibility_payloads_for_each_touched_row(
    tmp_path, monkeypatch
):
    _settings(tmp_path, monkeypatch)
    _insert_legacy_rows()
    import knowledge_ops.config as config
    import knowledge_ops.retag_legacy as retag

    monkeypatch.setattr(config.settings, "enable_qdrant", True)
    monkeypatch.setattr(config.settings, "qdrant_collections", ["knowledge_ops_wiki"])
    monkeypatch.setattr(config.settings, "qdrant_url", "http://qdrant.test:6333")
    calls: list[tuple[str, str, dict]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            calls.append(("POST", url, json))
            return _Response()

    monkeypatch.setattr(retag.httpx, "AsyncClient", lambda **kwargs: _Client(**kwargs))
    plan = retag.build_plan("magic-unicorn", "legal-ops")
    result = asyncio.run(retag.sync_qdrant_payloads(plan.rows, plan.workspace_id))

    assert result == {
        "enabled": True,
        "collections": ["knowledge_ops_wiki"],
        "updated": 7,
        "deleted_project_keys": 5,
    }
    set_calls = [call for call in calls if "/set_payload?" in call[1]]
    delete_calls = [call for call in calls if "/delete_payload?" in call[1]]
    assert len(set_calls) == 7
    assert len(delete_calls) == 5
    legal_call = next(
        call
        for call in set_calls
        if call[2]["payload"].get("project_id") == "legal-ops"
    )
    assert legal_call[2]["filter"]["should"]
    assert delete_calls[0][2]["keys"] == ["project_id", "visibility_project_id"]
