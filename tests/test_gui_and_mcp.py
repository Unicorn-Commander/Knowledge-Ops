"""Tests for the web GUI mount, the / -> /ui redirect, and the /mcp JSON-RPC shim.

The shim is what makes Brigade *brokered execution* work (initialize/tools/list/
tools/call), and the JWT gate is what keeps a proxy-whitelisted /mcp from
re-opening anonymous tool calls. Both are covered here alongside the existing 12.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_federation import _seed_tmp_env  # reuse the seeded tmp env helper

MANIFEST = Path(__file__).resolve().parent.parent / ".well-known" / "mcps.json"
STATIC = Path(__file__).resolve().parent.parent / "knowledge_ops" / "static"


def _client(tmp_path, monkeypatch) -> TestClient:
    _seed_tmp_env(tmp_path, monkeypatch)
    from knowledge_ops.api import app
    return TestClient(app)


# ---------------- GUI ----------------
def test_root_redirects_to_ui(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code in (307, 308)
        assert r.headers["location"].rstrip("/").endswith("/ui")


def test_ui_static_is_served_on_brand(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        idx = c.get("/ui/")
        assert idx.status_code == 200
        assert "text/html" in idx.headers["content-type"]
        assert "Knowledge-Ops" in idx.text and "Review queue" in idx.text and "Ask knowledge" in idx.text
        css = c.get("/ui/styles.css")
        assert css.status_code == 200 and "text/css" in css.headers["content-type"]
        # brand tokens plus mobile/accessibility invariants are present.
        assert "--mu-gradient-brand" in css.text and "100dvh" in css.text
        assert "safe-area-inset-bottom" in css.text and "prefers-reduced-motion" in css.text
        assert c.get("/ui/app.js").status_code == 200


def test_static_files_present_on_disk():
    for name in ("index.html", "styles.css", "app.js"):
        assert (STATIC / name).is_file(), f"missing static asset {name}"


def test_ui_has_accessible_navigation_and_no_inline_script_handlers():
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "styles.css").read_text()
    js = (STATIC / "app.js").read_text()
    assert 'class="skip-link"' in html
    assert 'id="mainContent"' in html and 'aria-live="polite"' in html
    assert "fonts.googleapis.com" not in html
    assert "onclick=" not in html and "onclick=" not in js
    assert html.count('class="side__btn') == 6
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert ".nav__status { font-size: 0; }" not in css
    mobile_css = css.split("@media (max-width: 860px)", 1)[1]
    assert "overflow-x: auto" not in mobile_css.split("@media", 1)[0]
    assert '>Ready<' not in html and '>Checking<' in html
    assert 'textContent = "Ready"' in js and 'textContent = "Offline"' in js


def test_info_route(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        body = c.get("/info").json()
        assert body["service"] == "knowledge-ops"
        assert body["mcp_endpoint"] == "/mcp" and body["ui"] == "/ui/"


# ---------------- /mcp JSON-RPC shim ----------------
def test_mcp_initialize(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["serverInfo"]["name"] == "knowledge-ops"
        assert "protocolVersion" in res and "tools" in res["capabilities"]


def test_mcp_tools_list_matches_manifest(tmp_path, monkeypatch):
    manifest_tools = {t["name"] for t in json.loads(MANIFEST.read_text())["tools"]}
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert r.status_code == 200
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        # every advertised tool is callable, and each carries an inputSchema
        assert names == manifest_tools, (names, manifest_tools)
        assert all("inputSchema" in t for t in tools)


def test_mcp_tools_call_ground_returns_cited_evidence(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "ground", "arguments": {"query": "What is Magic Unicorn?", "scope": "global", "k": 5}},
        })
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert payload["verdict"] == "related_evidence"
        assert payload["verification_status"] == "heuristic_not_verified"
        assert payload["passages"], "expected cited passages"
        # content is also JSON text for plain MCP clients
        assert "passages" in result["content"][0]["text"]


def test_mcp_tools_call_accepts_namespaced_name(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "knowledge.list_sources", "arguments": {}},
        })
        assert r.status_code == 200 and r.json()["result"]["isError"] is False


def test_mcp_unknown_tool_is_error_not_500(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        assert r.status_code == 200 and r.json()["result"]["isError"] is True


def test_mcp_notification_returns_202_no_body(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code == 202 and r.content == b""


def test_mcp_unknown_method(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 6, "method": "frobnicate"})
        assert r.status_code == 200 and r.json()["error"]["code"] == -32601


def test_mcp_jwt_gate_blocks_anonymous_tools_call_when_enforced(tmp_path, monkeypatch):
    """With enforcement on, tools/call needs a Brigade JWT; metadata stays open."""
    import knowledge_ops.config as config

    with _client(tmp_path, monkeypatch) as c:
        monkeypatch.setattr(config.settings, "mcp_require_jwt", True)
        # tools/call without a token -> 401 (not a server error)
        r = c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {
                "name": "person@example.test?token=secret",
                "arguments": {"query": "x"},
            },
        })
        assert r.status_code == 401 and r.json()["error"]["code"] == -32001
        # initialize + tools/list remain reachable (only public manifest metadata)
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 8, "method": "tools/list"}).status_code == 200
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "initialize"}).status_code == 200
    from knowledge_ops.storage import connect

    with connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT resource, details_json FROM audit_events WHERE action='auth.mcp.authentication'"
        )]
    persisted = json.dumps(rows)
    assert rows[-1]["resource"] == "tools/call"
    assert "person@example.test" not in persisted and "token=secret" not in persisted


def test_unhandled_request_log_uses_only_a_finite_error_receipt(caplog):
    import asyncio
    import logging
    from starlette.requests import Request
    import knowledge_ops.api as api

    caplog.set_level(logging.ERROR, logger="knowledge_ops")
    request = Request({"type": "http", "method": "GET", "path": "/secret/value"})
    response = asyncio.run(api._on_unhandled(
        request,
        RuntimeError("password=hunter2 person@example.test"),
    ))
    assert response.status_code == 500
    assert "hunter2" not in caplog.text and "person@example.test" not in caplog.text
    assert "/secret/value" not in caplog.text
    assert "request_failed:RuntimeError:" in caplog.text


def test_mcp_jwt_verifies_signature_audience_and_issuer(tmp_path, monkeypatch):
    """Full verifier path: a Brigade-shaped RS256 token is accepted; a wrong
    audience / untrusted issuer is rejected — all against a mocked JWKS (no net)."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    import knowledge_ops.brigade_auth as ba
    import knowledge_ops.config as config

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    pub_jwk.update({"kid": "test-kid", "alg": "RS256", "use": "sig"})

    monkeypatch.setattr(ba, "_fetch_jwks", lambda: {"test-kid": pub_jwk})
    monkeypatch.setattr(ba, "_jwks_keys", {}, raising=False)
    monkeypatch.setattr(ba, "_jwks_fetched_at", 0.0, raising=False)
    monkeypatch.setattr(config.settings, "mcp_require_jwt", True)
    monkeypatch.setattr(config.settings, "mcp_trusted_issuers", ["https://brigade.magicunicorn.dev"])

    def mint(aud="knowledge-ops", iss="https://brigade.magicunicorn.dev", scope="read:knowledge"):
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "sub": "aaron", "iat": now, "exp": now + 300}
        if scope:
            claims["scope"] = scope
        return pyjwt.encode(
            claims,
            key, algorithm="RS256", headers={"kid": "test-kid"},
        )

    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_sources", "arguments": {}}}
    with _client(tmp_path, monkeypatch) as c:
        ok = c.post("/mcp", json=call, headers={"Authorization": f"Bearer {mint()}"})
        assert ok.status_code == 200 and ok.json()["result"]["isError"] is False
        bad_aud = c.post("/mcp", json=call, headers={"Authorization": f"Bearer {mint(aud='other-app')}"})
        assert bad_aud.status_code == 401 and "other-app" not in bad_aud.text
        bad_iss = c.post("/mcp", json=call, headers={"Authorization": f"Bearer {mint(iss='https://evil.example')}"})
        assert bad_iss.status_code == 401 and "evil.example" not in bad_iss.text
        missing_scope = c.post("/mcp", json=call, headers={"Authorization": f"Bearer {mint(scope='')}"})
        assert missing_scope.status_code == 403
        write_call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "upsert_doc", "arguments": {"path": "denied.md", "md": "# Denied"},
        }}
        denied_write = c.post("/mcp", json=write_call, headers={"Authorization": f"Bearer {mint()}"})
        assert denied_write.status_code == 403


def test_mcp_jwks_cache_has_a_bounded_stale_grace(monkeypatch):
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    import knowledge_ops.brigade_auth as ba
    import knowledge_ops.config as config

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update({"kid": "cached", "alg": "RS256", "use": "sig"})
    monkeypatch.setattr(ba, "_jwks_keys", {"cached": public})
    monkeypatch.setattr(ba, "_jwks_fetched_at", 100.0)
    monkeypatch.setattr(ba, "_jwks_stale_fallback_total", 0)
    monkeypatch.setattr(ba, "_fetch_jwks", lambda: (_ for _ in ()).throw(OSError("secret")))
    monkeypatch.setattr(config.settings, "mcp_jwks_ttl", 60)
    monkeypatch.setattr(config.settings, "mcp_jwks_max_stale_seconds", 30)

    monkeypatch.setattr(ba.time, "time", lambda: 189.0)
    assert ba._signing_key_for("cached") is not None
    assert ba.jwks_stale_fallback_total() == 1
    monkeypatch.setattr(ba.time, "time", lambda: 191.0)
    with pytest.raises(ba.AuthError, match="jwks_unavailable"):
        ba._signing_key_for("cached")

    clock = iter((189.0, 191.0))
    monkeypatch.setattr(ba.time, "time", lambda: next(clock))
    with pytest.raises(ba.AuthError, match="jwks_unavailable"):
        ba._signing_key_for("cached")
    assert ba.jwks_stale_fallback_total() == 1


def test_mcp_jwks_errors_do_not_echo_fetch_or_key_details(monkeypatch):
    import knowledge_ops.brigade_auth as ba
    import knowledge_ops.config as config

    monkeypatch.setattr(ba, "_jwks_keys", {})
    monkeypatch.setattr(ba, "_jwks_fetched_at", 0.0)
    monkeypatch.setattr(config.settings, "mcp_jwks_ttl", 60)
    monkeypatch.setattr(config.settings, "mcp_jwks_max_stale_seconds", 30)
    monkeypatch.setattr(
        ba,
        "_fetch_jwks",
        lambda: (_ for _ in ()).throw(OSError("private-jwks-host?token=secret")),
    )
    with pytest.raises(ba.AuthError) as error:
        ba._signing_key_for("private-kid")
    assert str(error.value) == "jwks_unavailable"
    assert "private-kid" not in str(error.value)
    assert "private-jwks-host" not in str(error.value)


def test_successful_mcp_auth_log_omits_subject_and_token_id(monkeypatch, caplog):
    import logging
    import knowledge_ops.brigade_auth as ba
    import knowledge_ops.config as config

    monkeypatch.setattr(config.settings, "mcp_require_jwt", True)
    monkeypatch.setattr(
        ba,
        "verify_brigade_jwt",
        lambda token: {"sub": "person@example.test", "jti": "secret-token-id"},
    )
    caplog.set_level(logging.INFO, logger="knowledge_ops.auth")
    assert ba.require_for_tool_call("Bearer token") is not None
    assert "person@example.test" not in caplog.text
    assert "secret-token-id" not in caplog.text
