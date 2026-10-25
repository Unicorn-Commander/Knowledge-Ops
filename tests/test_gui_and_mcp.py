"""Tests for the web GUI mount, the / -> /ui redirect, and the /mcp JSON-RPC shim.

The shim is what makes Brigade *brokered execution* work (initialize/tools/list/
tools/call), and the JWT gate is what keeps a proxy-whitelisted /mcp from
re-opening anonymous tool calls. Both are covered here alongside the 16 tools
(the original 12 plus the graph/links/stats/whoami read-parity tools that give
agents the same view the Observatory UI renders).
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


def _client(tmp_path, monkeypatch, *, setup_auth=True) -> TestClient:
    _seed_tmp_env(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    monkeypatch.setattr(config.settings, "mcp_allow_anonymous_discovery", False)
    token = None
    if setup_auth:
        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        import knowledge_ops.brigade_auth as ba

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_jwk = json.loads(pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        pub_jwk.update({"kid": "client-test-kid", "alg": "RS256", "use": "sig"})
        monkeypatch.setattr(ba, "_fetch_jwks", lambda: {"client-test-kid": pub_jwk})
        monkeypatch.setattr(ba, "_jwks_keys", {}, raising=False)
        monkeypatch.setattr(ba, "_jwks_fetched_at", 0.0, raising=False)
        monkeypatch.setattr(config.settings, "mcp_trusted_issuers", ["https://brigade.magicunicorn.dev"])
        now = int(time.time())
        token = pyjwt.encode(
            {"iss": "https://brigade.magicunicorn.dev", "aud": "knowledge-ops", "sub": "mcp-test",
             "iat": now, "exp": now + 300, "scope": "admin:knowledge"},
            key, algorithm="RS256", headers={"kid": "client-test-kid"},
        )
    from knowledge_ops.api import app
    client = TestClient(app)
    if token:
        client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ---------------- GUI ----------------
def test_root_redirects_to_landing(tmp_path, monkeypatch):
    # v3: the public landing is the front door; "Open the Observatory" leads to /ui.
    with _client(tmp_path, monkeypatch) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code in (307, 308)
        assert r.headers["location"].rstrip("/").endswith("/landing")


def test_ui_static_is_served_on_brand(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        idx = c.get("/ui/")
        assert idx.status_code == 200
        assert "text/html" in idx.headers["content-type"]
        # Constellation shell: brand, the module entrypoint, and the ui/ stylesheets.
        assert "Knowledge-Ops" in idx.text
        assert 'type="module" src="ui/shell.js"' in idx.text
        for sheet in ("ui/fonts.css", "ui/tokens.css", "ui/base.css"):
            assert f'href="{sheet}"' in idx.text
        tokens = c.get("/ui/ui/tokens.css")
        assert tokens.status_code == 200 and "text/css" in tokens.headers["content-type"]
        assert "--mu-gradient-brand" in tokens.text
        for theme in ("aurora", "daylight", "nebula", "midnight"):
            assert f'data-theme="{theme}"' in tokens.text
        base = c.get("/ui/ui/base.css")
        assert base.status_code == 200
        assert "prefers-reduced-motion" in base.text and ":focus-visible" in base.text
        assert c.get("/ui/ui/shell.js").status_code == 200
        assert c.get("/landing/").status_code == 200


def test_static_files_present_on_disk():
    for name in (
        "index.html",
        "landing/index.html",
        "ui/shell.js",
        "ui/api.js",
        "ui/bus.js",
        "ui/theme.js",
        "ui/lens.js",
        "ui/md.js",
        "ui/constellation.js",
        "ui/vendor/forcesim.js",
        "ui/tokens.css",
        "ui/base.css",
        "ui/fonts.css",
        "ui/fonts/fraunces-latin-600.woff2",
        "ui/views/ask.js",
        "ui/views/library.js",
        "ui/views/review.js",
        "ui/views/files.js",
        "ui/views/sources.js",
        "ui/views/ops.js",
        "ui/views/help.js",
    ):
        assert (STATIC / name).is_file(), f"missing static asset {name}"


def test_ui_has_accessible_navigation_and_no_inline_script_handlers():
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "ui" / "base.css").read_text()
    js = (STATIC / "ui" / "shell.js").read_text()
    assert 'class="skip-link"' in html
    assert 'id="mainContent"' in html and 'aria-live="polite"' in html
    # Offline host: no external fonts or CDN references anywhere in the shell.
    assert "fonts.googleapis.com" not in html and "cdn." not in html
    assert "onclick=" not in html and "onclick=" not in js
    # Eight routed views, eight nav entries.
    assert html.count('class="dock__btn"') == 9
    for view in ("home", "ask", "library", "review", "files", "sources", "graph", "ops", "help"):
        assert f'id="view-{view}"' in html
    # Status dot starts unknown; the shell flips it after probing /ready.
    assert '>Ready<' not in html and '>Checking<' in html
    assert 'textContent = "Live"' in js and '"Offline"' in js
    # Accessibility floor from the design system: 44px targets + focus outlines
    # + reduced-motion handling live in base.css now.
    assert "min-height: 44px" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css


def test_ui_health_pulse_uses_non_json_probe_for_plain_text_ready():
    api_js = (STATIC / "ui" / "api.js").read_text()
    shell_js = (STATIC / "ui" / "shell.js").read_text()
    # /ready is a health response, not an API JSON contract. Keep its probe
    # on the same credentialed, timed fetch path as the JSON client.
    assert "async function ping(path)" in api_js
    assert "credentials: \"same-origin\"" in api_js
    assert "const TIMEOUT_MS = 30000;" in api_js
    assert "if (!res.ok) throw new KoErr(res.status);" in api_js
    assert "  ping," in api_js
    assert 'await api.ping("/ready");' in shell_js
    assert 'await api.get("/ready");' not in shell_js


def test_review_forbidden_memory_queue_is_quiet_but_failures_still_render():
    review_js = (STATIC / "ui" / "views" / "review.js").read_text()
    assert "const memoryZone = section.querySelector(\".rv-zone\");" in review_js
    assert "if (err && err.status === 403)" in review_js
    assert "memoryZone.hidden = true;" in review_js
    # A 5xx or other failure must remain visible through the normal renderer.
    assert "if (!hideMemoryReviewOnForbidden(e)) ctx.error(memoryList, e);" in review_js


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


@pytest.mark.parametrize("method", ["initialize", "tools/list", "ping"])
def test_mcp_anonymous_discovery_requires_authentication(tmp_path, monkeypatch, method):
    with _client(tmp_path, monkeypatch) as c:
        body = {"jsonrpc": "2.0", "id": 99, "method": method}
        r = c.post("/mcp", json=body, headers={"Authorization": ""})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        assert r.json()["error"] == {"code": -32001, "message": "unauthorized"}


def test_mcp_authenticated_discovery_keeps_result_shape(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 200
        assert r.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_mcp_tools_list_matches_manifest(tmp_path, monkeypatch):
    manifest_tools = {t["name"] for t in json.loads(MANIFEST.read_text())["tools"]}
    # 12 originals + the graph/links/stats/whoami read-parity tools.
    assert len(manifest_tools) == 16, manifest_tools
    assert {"graph", "links", "stats", "whoami"} <= manifest_tools
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


# ---------------- read-parity tools: graph / links / stats / whoami ----------------
@pytest.fixture()
def fresh_graph_caches():
    """graph_api caches are process-global and keyed by visibility identity, not
    db path — clear around each test so a prior tmp db never leaks through."""
    import knowledge_ops.graph_api as graph_api

    graph_api.invalidate_link_caches()
    yield
    graph_api.invalidate_link_caches()


def _call_tool(c, name, arguments, req_id=100, headers=None):
    return c.post("/mcp", json={
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }, headers=headers or {})


def test_mcp_tools_call_graph_matches_http_payload(tmp_path, monkeypatch, fresh_graph_caches):
    import knowledge_ops.config as config

    monkeypatch.setattr(config.settings, "enable_qdrant", False)
    monkeypatch.setattr(config.settings, "enable_falkordb", False)
    with _client(tmp_path, monkeypatch) as c:
        monkeypatch.setattr(config.settings, "api_require_auth", True)
        r = _call_tool(c, "graph", {"scope": "global", "limit_per_coll": 25})
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert set(payload) == {"nodes", "edges", "stats"}
        assert payload["stats"]["scope"] == "global"
        assert payload["stats"]["node_count"] == len(payload["nodes"])
        # Byte-for-byte the constellation payload the UI fetches for the same
        # principal (both paths share the per-visibility-identity cache).
        assert payload == c.get("/kb.graph", params={"scope": "global"}).json()


def test_mcp_tools_call_links_reflects_wiki_links(tmp_path, monkeypatch, fresh_graph_caches):
    with _client(tmp_path, monkeypatch) as c:
        docs = (
            ("guides/alpha.md", "# Alpha\n\nSee [[Beta]] and [[Nowhere Doc]]."),
            ("guides/beta.md", "# Beta\n\nBeta body."),
        )
        for i, (path, md) in enumerate(docs):
            r = _call_tool(c, "upsert_doc", {"path": path, "md": md, "scope": "global"}, req_id=110 + i)
            assert r.json()["result"]["isError"] is False
        r = _call_tool(c, "links", {}, req_id=112)
        assert r.status_code == 200
        payload = r.json()["result"]["structuredContent"]
        assert set(payload) == {"links", "missing", "orphans", "count"}
        assert payload["count"] == len(payload["links"])
        pairs = {(link["from_path"], link["to_path"]) for link in payload["links"]}
        assert ("guides/alpha.md", "guides/beta.md") in pairs
        assert {"from_path": "guides/alpha.md", "target": "Nowhere Doc"} in payload["missing"]
        assert "guides/alpha.md" in payload["orphans"]
        assert "guides/beta.md" not in payload["orphans"]
        # Same payload as GET /kb.links (writes through MCP invalidated the
        # link caches, so both paths see the just-upserted docs).
        assert payload == c.get("/kb.links").json()


def test_mcp_tools_call_stats_counts_sources(tmp_path, monkeypatch, fresh_graph_caches):
    import knowledge_ops.config as config

    monkeypatch.setattr(config.settings, "enable_qdrant", False)
    monkeypatch.setattr(config.settings, "enable_falkordb", False)
    with _client(tmp_path, monkeypatch) as c:
        r = _call_tool(c, "stats", {}, req_id=120)
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        sources = result["structuredContent"]["sources"]
        by_key = {s["key"]: s for s in sources}
        # the lifespan seeds one wiki doc from disk, so counts are non-trivial
        assert by_key["wiki"]["kind"] == "canonical" and by_key["wiki"]["count"] >= 1
        assert {"file_index", "metrics"} <= set(by_key)
        for source in sources:
            assert set(source) == {"key", "label", "count", "kind"}
        assert result["structuredContent"] == c.get("/kb.stats").json()


def test_mcp_tools_call_whoami_matches_kb_me(tmp_path, monkeypatch):
    import knowledge_ops.config as config
    with _client(tmp_path, monkeypatch) as c:
        monkeypatch.setattr(config.settings, "api_require_auth", True)
        r = _call_tool(c, "whoami", {}, req_id=130)
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert set(payload) == {"name", "scopes", "is_admin"}
        assert payload["name"] == "mcp-test" and payload["is_admin"] is True
        assert payload["scopes"] == sorted(payload["scopes"])
        assert payload == c.get("/kb.me").json()


def test_mcp_read_parity_tools_enforce_the_same_principal(tmp_path, monkeypatch, fresh_graph_caches):
    """Under an enforced Brigade JWT the new read tools resolve the same
    principal as the HTTP endpoints: whoami reflects the verified token and
    needs no scope; graph/links/stats sit behind read:knowledge exactly like
    their HTTP twins; scope=all stays admin-only through the MCP path too."""
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

    def mint(scope="read:knowledge"):
        now = int(time.time())
        claims = {"iss": "https://brigade.magicunicorn.dev", "aud": "knowledge-ops",
                  "sub": "jason", "iat": now, "exp": now + 300}
        if scope:
            claims["scope"] = scope
        return pyjwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})

    with _client(tmp_path, monkeypatch, setup_auth=False) as c:
        # whoami works without any scope and reflects the token, not local-system
        r = _call_tool(c, "whoami", {}, req_id=140,
                       headers={"Authorization": f"Bearer {mint(scope='')}"})
        assert r.status_code == 200
        body = r.json()["result"]["structuredContent"]
        assert body["name"] == "jason" and body["is_admin"] is False
        # the other read tools stay behind read:knowledge, like GET /kb.*
        for name in ("graph", "links", "stats"):
            denied = _call_tool(c, name, {}, req_id=141,
                                headers={"Authorization": f"Bearer {mint(scope='')}"})
            assert denied.status_code == 403, name
            assert denied.json()["error"]["code"] == -32003
        # a reader may call links...
        ok = _call_tool(c, "links", {}, req_id=142,
                        headers={"Authorization": f"Bearer {mint()}"})
        assert ok.status_code == 200 and ok.json()["result"]["isError"] is False
        # ...but scope=all remains admin-only inside the shared implementation
        # (surfaced as a sanitized MCP tool error, mirroring HTTP's 403).
        forbidden = _call_tool(c, "links", {"scope": "all"}, req_id=143,
                               headers={"Authorization": f"Bearer {mint()}"})
        assert forbidden.status_code == 200
        result = forbidden.json()["result"]
        assert result["isError"] is True
        assert "AuthorizationError" in result["content"][0]["text"]
        assert "administrator" not in result["content"][0]["text"]  # sanitized receipt


def test_mcp_jwt_gate_blocks_anonymous_tools_call_when_enforced(tmp_path, monkeypatch):
    """Every MCP method needs a Brigade JWT unless discovery compatibility is enabled."""
    import knowledge_ops.config as config

    with _client(tmp_path, monkeypatch) as c:
        monkeypatch.setattr(config.settings, "mcp_require_jwt", True)
        # tools/call without a token -> 401 (not a server error)
        r = c.post("/mcp", headers={"Authorization": ""}, json={
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {
                "name": "person@example.test?token=secret",
                "arguments": {"query": "x"},
            },
        })
        assert r.status_code == 401 and r.json()["error"]["code"] == -32001
        # Discovery is also denied without a credential.
        denied_list = c.post("/mcp", json={"jsonrpc": "2.0", "id": 8, "method": "tools/list"}, headers={"Authorization": ""})
        assert denied_list.status_code == 401
        assert denied_list.headers["www-authenticate"] == "Bearer"
        denied_init = c.post("/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "initialize"}, headers={"Authorization": ""})
        assert denied_init.status_code == 401
    from knowledge_ops.storage import connect

    with connect() as db:
        rows = [dict(row) for row in db.execute(
            "SELECT resource, details_json FROM audit_events WHERE action='auth.mcp.authentication'"
        )]
    persisted = json.dumps(rows)
    assert {row["resource"] for row in rows[-3:]} == {"tools/call", "tools/list", "initialize"}
    assert "person@example.test" not in persisted and "token=secret" not in persisted


def test_mcp_anonymous_discovery_escape_hatch_does_not_open_tools_call(tmp_path, monkeypatch):
    import knowledge_ops.config as config

    with _client(tmp_path, monkeypatch) as c:
        monkeypatch.setattr(config.settings, "mcp_allow_anonymous_discovery", True)
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers={"Authorization": ""}).status_code == 200
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers={"Authorization": ""}).status_code == 200
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"}, headers={"Authorization": ""}).status_code == 200
        denied = c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "list_sources", "arguments": {}},
        }, headers={"Authorization": ""})
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"] == "Bearer"


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
    with _client(tmp_path, monkeypatch, setup_auth=False) as c:
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
