from __future__ import annotations

from pathlib import Path

from knowledge_ops.authorization import Principal, principal_from_claims


def _settings(tmp_path: Path, monkeypatch) -> None:
    import knowledge_ops.config as config

    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "data" / "knowledge.sqlite3")
    monkeypatch.setattr(config.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(config.settings, "openwiki_staging_dir", tmp_path / "data" / "openwiki" / "staging")
    monkeypatch.setattr(config.settings, "allow_legacy_global_visibility", False)


def _principal(subject: str, workspace: str, *projects: str) -> Principal:
    return Principal(
        subject=subject,
        scopes=frozenset({"read:knowledge"}),
        identities=frozenset({subject}),
        workspaces=frozenset({workspace}),
        projects=frozenset(projects),
    )


def test_claims_preserve_workspace_and_project_authority():
    principal = principal_from_claims({
        "sub": "user-a",
        "workspace_id": "workspace-a",
        "project_id": "project-a",
        "scope": "read:knowledge",
    })
    assert principal.workspaces == frozenset({"workspace-a"})
    assert principal.projects == frozenset({"project-a"})


def test_bound_document_is_filtered_from_list_get_and_search(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.wiki import get_doc, list_docs, search_wiki, upsert_doc

    doc_a = upsert_doc(
        "projects/a.md", "# Shared project\n\nreceiver visibility needle",
        scope="global", owner="project-ops",
        metadata={"workspace_id": "workspace-a", "project_id": "project-a"},
    )
    doc_b = upsert_doc(
        "projects/b.md", "# Foreign project\n\nreceiver visibility needle",
        scope="global", owner="project-ops",
        metadata={"workspace_id": "workspace-b", "project_id": "project-b"},
    )
    alice = _principal("alice", "workspace-a", "project-a")

    assert {doc["id"] for doc in list_docs(principal=alice)} == {doc_a}
    assert get_doc(doc_a, principal=alice)["id"] == doc_a
    assert get_doc(doc_b, principal=alice) is None
    assert {hit.metadata["document_id"] for hit in search_wiki(
        "receiver visibility needle", k=10, principal=alice,
    )} == {doc_a}


def test_same_workspace_and_project_can_read_bound_document(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.wiki import get_doc, list_docs, search_wiki, upsert_doc

    doc_id = upsert_doc(
        "projects/a.md", "# Project A\n\nworkspace project evidence",
        scope="global", owner="project-ops",
        visibility={"workspace_id": "workspace-a", "project_id": "project-a"},
    )
    principal = _principal("alice", "workspace-a", "project-a")
    assert list_docs(principal=principal)[0]["id"] == doc_id
    assert get_doc(doc_id, principal=principal)["id"] == doc_id
    assert search_wiki("workspace project evidence", principal=principal)[0].metadata["document_id"] == doc_id
    assert not search_wiki(
        "workspace project evidence", principal=_principal("bob", "workspace-a", "project-b")
    )


def test_untagged_global_legacy_compatibility_is_explicit(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.wiki import get_doc, list_docs, search_wiki, upsert_doc

    doc_id = upsert_doc("legacy.md", "# Legacy\n\nlegacy visibility needle", scope="global", owner="org")
    principal = _principal("alice", "workspace-a")

    assert list_docs(principal=principal) == []
    assert get_doc(doc_id, principal=principal) is None
    assert not search_wiki("legacy visibility needle", principal=principal)

    monkeypatch.setattr(config.settings, "allow_legacy_global_visibility", True)
    assert {doc["id"] for doc in list_docs(principal=principal)} == {doc_id}
    assert get_doc(doc_id, principal=principal)["id"] == doc_id
    assert search_wiki("legacy visibility needle", principal=principal)[0].metadata["document_id"] == doc_id


def test_project_binding_is_stored_on_canonical_document(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.storage import connect
    from knowledge_ops.wiki import get_doc, upsert_doc

    doc_id = upsert_doc(
        "projects/stored.md", "# Stored binding", scope="global", owner="project-ops",
        metadata={"workspace_id": "workspace-a", "project_id": "project-a"},
    )
    doc = get_doc(doc_id)
    assert doc["visibility"] == {"workspace_id": "workspace-a", "project_id": "project-a"}
    with connect() as db:
        row = db.execute(
            "SELECT visibility_workspace_id, visibility_project_id FROM wiki_docs WHERE id=?",
            (doc_id,),
        ).fetchone()
    assert tuple(row) == ("workspace-a", "project-a")
