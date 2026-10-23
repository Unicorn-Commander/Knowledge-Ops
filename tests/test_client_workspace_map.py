from __future__ import annotations

import json
import time

import pytest


def _settings(tmp_path, monkeypatch, mapping: dict | None = None) -> None:
    from knowledge_ops import config, storage, wiki

    db_path = tmp_path / "knowledge.sqlite3"
    for module in (config, storage, wiki):
        monkeypatch.setattr(module.settings, "data_dir", tmp_path)
        monkeypatch.setattr(module.settings, "db_path", db_path)
        monkeypatch.setattr(module.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(config.settings, "openwiki_staging_dir", tmp_path / "openwiki" / "staging")
    monkeypatch.setattr(config.settings, "allow_legacy_global_visibility", False)
    monkeypatch.setattr(config.settings, "client_workspace_map", mapping or {})


def _verified_principal(monkeypatch, claims: dict):
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from knowledge_ops import authorization as auth
    from knowledge_ops import brigade_auth, config

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": "client-map-test", "alg": "RS256", "use": "sig"})
    monkeypatch.setattr(brigade_auth, "_fetch_jwks", lambda: {"client-map-test": public})
    monkeypatch.setattr(brigade_auth, "_jwks_keys", {}, raising=False)
    monkeypatch.setattr(brigade_auth, "_jwks_fetched_at", 0.0, raising=False)
    monkeypatch.setattr(config.settings, "mcp_trusted_issuers", ["https://brigade.test"])
    monkeypatch.setattr(config.settings, "mcp_expected_audience", "knowledge-ops")
    now = int(time.time())
    token_claims = {
        "iss": "https://brigade.test",
        "aud": "knowledge-ops",
        "sub": "service-subject",
        "iat": now,
        "exp": now + 300,
        "scope": "read:knowledge",
        **claims,
    }
    token = pyjwt.encode(token_claims, key, algorithm="RS256", headers={"kid": "client-map-test"})
    return auth.principal_from_authorization(f"Bearer {token}")


def test_mapped_verified_client_gets_workspace_and_reads_bound_row(tmp_path, monkeypatch):
    _settings(
        tmp_path,
        monkeypatch,
        {"ko-agent-brigade": {"workspaces": ["magic-unicorn"], "projects": ["legal-ops"]}},
    )
    from knowledge_ops.wiki import get_doc, upsert_doc

    doc_id = upsert_doc(
        "magic-unicorn/legal.md",
        "# Legal operations\n\nworkspace map evidence",
        scope="global",
        owner="project-ops",
        visibility={"workspace_id": "magic-unicorn", "project_id": "legal-ops"},
    )
    principal = _verified_principal(monkeypatch, {"client_id": "ko-agent-brigade"})

    assert principal.workspaces == frozenset({"magic-unicorn"})
    assert principal.projects == frozenset({"legal-ops"})
    assert get_doc(doc_id, principal=principal)["id"] == doc_id


def test_unmapped_verified_client_gets_no_workspace_and_cannot_read_bound_row(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch, {"ko-agent-brigade": {"workspaces": ["magic-unicorn"]}})
    from knowledge_ops.wiki import get_doc, upsert_doc

    doc_id = upsert_doc(
        "magic-unicorn/brief.md",
        "# Brief\n\nunmapped client evidence",
        scope="global",
        owner="project-ops",
        visibility={"workspace_id": "magic-unicorn"},
    )
    principal = _verified_principal(monkeypatch, {"azp": "ko-agent-unknown"})

    assert not principal.workspaces
    assert get_doc(doc_id, principal=principal) is None


def test_workspace_claim_wins_over_client_map(tmp_path, monkeypatch):
    _settings(
        tmp_path,
        monkeypatch,
        {"ko-agent-brigade": {"workspaces": ["magic-unicorn"], "projects": ["legal-ops"]}},
    )
    principal = _verified_principal(
        monkeypatch,
        {
            "azp": "ko-agent-brigade",
            "workspace_id": "other-workspace",
            "project_id": "token-project",
        },
    )

    assert principal.workspaces == frozenset({"other-workspace"})
    assert principal.projects == frozenset({"token-project"})


def test_malformed_client_workspace_map_fails_closed(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP", "{not-json")
    from knowledge_ops.config import Settings

    with pytest.raises(ValueError, match="KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP must be valid JSON"):
        Settings()


def test_client_workspace_map_defaults_empty_and_loads_projects(monkeypatch):
    from knowledge_ops.config import Settings

    monkeypatch.delenv("KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP", raising=False)
    assert Settings().client_workspace_map == {}
    monkeypatch.setenv(
        "KNOWLEDGE_OPS_CLIENT_WORKSPACE_MAP",
        '{"client-a": {"workspaces": ["workspace-a"], "projects": ["project-a"]}}',
    )
    assert Settings().client_workspace_map == {
        "client-a": {"workspaces": ["workspace-a"], "projects": ["project-a"]},
    }


def test_client_map_never_applies_to_unverified_or_anonymous_principals(monkeypatch):
    import jwt as pyjwt

    from knowledge_ops import authorization as auth
    from knowledge_ops import config

    monkeypatch.setattr(
        config.settings,
        "client_workspace_map",
        {"ko-agent-brigade": {"workspaces": ["magic-unicorn"]}},
    )
    unsigned = pyjwt.encode(
        {
            "iss": "https://brigade.magicunicorn.dev",
            "aud": "knowledge-ops",
            "sub": "unverified-subject",
            "azp": "ko-agent-brigade",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(auth.AuthorizationError):
        auth.principal_from_authorization(f"Bearer {unsigned}")
    with pytest.raises(auth.AuthorizationError):
        auth.principal_from_authorization(None)
    anonymous = auth.principal_from_authorization(None, require_auth=False)

    assert not anonymous.workspaces
