from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient


SECRET_KEY = b"fictional-center-deep-review-key-012345"
SECRET = "whsec_" + base64.b64encode(SECRET_KEY).decode("ascii")


def _settings(tmp_path: Path, monkeypatch, secret: str = SECRET) -> None:
    from knowledge_ops import config, storage, wiki

    for module in (config, storage, wiki):
        monkeypatch.setattr(module.settings, "data_dir", tmp_path / "data")
        monkeypatch.setattr(module.settings, "db_path", tmp_path / "data" / "knowledge.sqlite3")
        monkeypatch.setattr(module.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(config.settings, "openwiki_staging_dir", tmp_path / "data" / "openwiki" / "staging")
    monkeypatch.setattr(config.settings, "api_require_auth", False)
    monkeypatch.setattr(config.settings, "allow_legacy_global_visibility", False)
    monkeypatch.setattr(config.settings, "cdr_webhook_secret", secret)
    monkeypatch.setattr(config.settings, "cdr_doc_path", "ops/stack-versions")
    monkeypatch.setattr(config.settings, "cdr_log_path", "ops/stack-releases-log.md")


def _delivery(
    *,
    feed: str,
    run_id: str,
    inventory: list[dict],
    kev_matches: list[dict] | None = None,
    releases: list[dict] | None = None,
    comparisons: list[dict] | None = None,
    complete: bool = True,
    truncated: bool | None = None,
    state: str | None = "WATCH",
    observed: bool = True,
) -> dict:
    situation = {
        "feed": feed,
        "state": state,
        "complete": complete,
        "inventory": inventory,
        "kev_matches": kev_matches or [],
        "releases": releases or [],
        "comparisons": comparisons or [],
        "observed_at": "2026-11-08T12:00:00Z" if observed else None,
    }
    if truncated is not None:
        situation["truncated"] = truncated
    return {
        "type": "monitor.run.completed",
        "id": run_id,
        "timestamp": "2026-11-08T12:00:00Z",
        "data": {
            "run_id": run_id,
            "report_url": f"https://research.example.com/#/runs/{run_id}",
            "situation": situation,
        },
    }


def _post_signed(client, body: dict, webhook_id: str, *, secret: str = SECRET, timestamp: int | None = None):
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    timestamp = timestamp or int(time.time())
    key = base64.b64decode(secret[6:]) if secret.startswith("whsec_") else secret.encode("utf-8")
    signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + raw
    digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
    return client.post(
        "/kb.ingest.center_deep",
        content=raw,
        headers={
            "content-type": "application/json",
            "webhook-id": webhook_id,
            "webhook-timestamp": str(timestamp),
            "webhook-signature": f"v1,{digest}",
        },
    )


def _component(package: str, *, host: str = "node.example.com", version: str = "1.2.3", repo: str | None = None) -> dict:
    return {
        "host": host,
        "package": package,
        "ecosystem": "Go",
        "version": version,
        "repo": repo,
    }


def _release() -> dict:
    return {
        "repo": "fictional/relay",
        "package": "example.com/fictional/relay",
        "tag": "v1.3.0",
        "name": "v1.3.0",
        "url": "https://github.com/fictional/relay/releases/tag/v1.3.0",
        "published_at": "2026-11-07T10:00:00Z",
        "security": True,
    }


def _comparison() -> dict:
    return {
        "package": "example.com/fictional/relay",
        "repo": "fictional/relay",
        "latest_tag": "v1.3.0",
        "comparison": "1.2.3 is behind v1.3.0",
        "behind": True,
        "security_newer": True,
    }


def test_releases_do_not_delete_kev_only_rows_but_complete_kev_can_remove_them(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api
    from knowledge_ops.wiki import get_doc

    relay = _component("example.com/fictional/relay", repo="fictional/relay")
    deb = _component("pkg:deb/openssl", version="3.0.2")
    kev = [{"host": deb["host"], "package": deb["package"], "cve": "CVE-2026-0001"}]
    with TestClient(api.app) as client:
        assert _post_signed(
            client,
            _delivery(feed="kev", run_id="kev-1", inventory=[relay, deb], kev_matches=kev),
            "webhook-kev-1",
        ).status_code == 200
        assert _post_signed(
            client,
            _delivery(
                feed="github_releases", run_id="release-1", inventory=[relay],
                releases=[_release()], comparisons=[_comparison()],
            ),
            "webhook-release-1",
        ).status_code == 200
        body = get_doc("ops/stack-versions.md")["body"]
        assert "pkg:deb/openssl" in body and "CVE-2026-0001" in body

        assert _post_signed(
            client,
            _delivery(feed="kev", run_id="kev-2", inventory=[relay]),
            "webhook-kev-2",
        ).status_code == 200
        assert "pkg:deb/openssl" not in get_doc("ops/stack-versions.md")["body"]


def test_partial_and_truncated_runs_preserve_state_and_mark_columns_unknown(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api
    from knowledge_ops.storage import connect
    from knowledge_ops.wiki import get_doc

    relay = _component("example.com/fictional/relay", repo="fictional/relay")
    kev = [{"host": relay["host"], "package": relay["package"], "cve": "CVE-2026-0002"}]
    with TestClient(api.app) as client:
        assert _post_signed(
            client,
            _delivery(feed="kev", run_id="kev-good", inventory=[relay], kev_matches=kev),
            "webhook-kev-good",
        ).status_code == 200
        assert _post_signed(
            client,
            _delivery(
                feed="github_releases", run_id="release-good", inventory=[relay],
                releases=[_release()], comparisons=[_comparison()],
            ),
            "webhook-release-good",
        ).status_code == 200

        partial = _post_signed(
            client,
            _delivery(feed="kev", run_id="kev-partial", inventory=[], complete=False, state=None, observed=False),
            "webhook-kev-partial",
        )
        assert partial.status_code == 200
        body = get_doc("ops/stack-versions.md")["body"]
        assert "node.example.com" in body and "1.2.3" in body
        assert "unknown (partial)" in body
        assert "CVE-2026-0002" not in body
        with connect() as db:
            row = db.execute(
                "SELECT kev_advisories FROM center_deep_inventory_state"
            ).fetchone()
            assert row["kev_advisories"] == "1 (CVE-2026-0002)"

        truncated = _post_signed(
            client,
            _delivery(
                feed="github_releases", run_id="release-truncated", inventory=[],
                complete=True, truncated=True, observed=False,
            ),
            "webhook-release-truncated",
        )
        assert truncated.status_code == 200
        body = get_doc("ops/stack-versions.md")["body"]
        assert "v1.3.0" not in body
        assert body.count("unknown (partial)") >= 4
        with connect() as db:
            row = db.execute(
                "SELECT release_latest FROM center_deep_inventory_state"
            ).fetchone()
            assert row["release_latest"] == "v1.3.0 (2026-11-07)"


def test_non_ascii_signature_is_a_401_not_a_type_error(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api

    body = _delivery(feed="kev", run_id="nonascii", inventory=[])
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/kb.ingest.center_deep",
            content=raw,
            headers={
                "webhook-id": "webhook-nonascii",
                "webhook-timestamp": str(int(time.time())),
                "webhook-signature": b"v1,\xe9",
            },
        )
    assert response.status_code == 401


def test_failed_receipt_is_retryable_and_recent_inflight_is_503(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api
    import knowledge_ops.ingest_center_deep as ingest
    from knowledge_ops.storage import connect
    from knowledge_ops.wiki import get_doc

    original_get_doc = ingest.get_doc
    calls = {"count": 0}

    def flaky(path):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("fictional database lock")
        return original_get_doc(path)

    monkeypatch.setattr(ingest, "get_doc", flaky)
    body = _delivery(feed="kev", run_id="retryable", inventory=[_component("example.com/retry")])
    with TestClient(api.app) as client:
        first = _post_signed(client, body, "webhook-retryable")
        assert first.status_code == 503
        second = _post_signed(client, body, "webhook-retryable")
        assert second.status_code == 200
        assert get_doc("ops/stack-versions.md") is not None

        inflight_body = _delivery(feed="kev", run_id="inflight", inventory=[_component("example.com/inflight")])
        assert ingest._reserve_receipt("webhook-inflight", "inflight") == "reserved"
        inflight = _post_signed(client, inflight_body, "webhook-inflight")
        assert inflight.status_code == 503
        with connect() as db:
            db.execute(
                "UPDATE center_deep_webhook_receipts SET received_at=? WHERE webhook_id=?",
                ((datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(), "webhook-inflight"),
            )
        reclaimed = _post_signed(client, inflight_body, "webhook-inflight")
        assert reclaimed.status_code == 200


def test_non_admin_with_internal_workspace_reads_bound_doc_when_auth_is_on(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api, config
    from knowledge_ops.authorization import Principal

    monkeypatch.setattr(config.settings, "api_require_auth", True)
    principal = Principal(
        subject="fictional-agent",
        scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"fictional-agent"}),
        workspaces=frozenset({"magic-unicorn"}),
    )
    api.app.dependency_overrides[api.current_principal] = lambda: principal
    try:
        with TestClient(api.app) as client:
            assert _post_signed(
                client,
                _delivery(feed="kev", run_id="visible", inventory=[_component("example.com/visible")]),
                "webhook-visible",
            ).status_code == 200
            grounded = client.post(
                "/kb.ground",
                json={"query": "example.com/visible", "scope": "global", "sources": ["wiki"]},
            )
            assert grounded.status_code == 200
            assert any("example.com/visible" in item["text"] for item in grounded.json()["passages"])
    finally:
        api.app.dependency_overrides.clear()


def test_owner_conflict_is_403_and_tombstone_gets_new_revision(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.wiki import get_doc, tombstone_doc, upsert_doc

    with TestClient(api.app) as client:
        upsert_doc("ops/stack-versions.md", "# Human page\n", scope="global", owner="fictional-human")
        denied = _post_signed(
            client,
            _delivery(feed="kev", run_id="owner-conflict", inventory=[_component("example.com/owner")]),
            "webhook-owner-conflict",
        )
        assert denied.status_code == 403

    _settings(tmp_path / "replacement", monkeypatch)
    from knowledge_ops import config
    config.settings.data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(api.app) as client:
        first = _post_signed(
            client,
            _delivery(feed="kev", run_id="tombstone-before", inventory=[_component("example.com/tombstone")]),
            "webhook-tombstone-before",
        )
        assert first.status_code == 200
        old = get_doc("ops/stack-versions.md")
        tombstone_doc(old["id"], principal=system_principal(), reason="fictional cleanup")
        second = _post_signed(
            client,
            _delivery(feed="kev", run_id="tombstone-after", inventory=[_component("example.com/tombstone")]),
            "webhook-tombstone-after",
        )
        assert second.status_code == 200
        replacement = get_doc("ops/stack-versions.md")
        assert replacement["lifecycle_state"] == "active"
        assert replacement["id"] != old["id"]


def test_state_table_and_rendered_fields_do_not_depend_on_markdown_reparse(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops import api
    from knowledge_ops.storage import connect
    from knowledge_ops.wiki import get_doc

    weird = _component("example.com/pkg|odd", host="host\u2028one", version="1.0\x85tail")
    weird["ecosystem"] = "Go\vLang"
    comparison = _comparison()
    comparison["package"] = weird["package"]
    comparison["comparison"] = "line\u2029break|pipe"
    comparison["repo"] = "fictional/weird"
    weird["repo"] = comparison["repo"]
    with TestClient(api.app) as client:
        assert _post_signed(
            client,
            _delivery(
                feed="github_releases", run_id="weird-fields", inventory=[weird],
                comparisons=[comparison], releases=[{**_release(), "package": weird["package"], "repo": weird["repo"]}],
            ),
            "webhook-weird-fields",
        ).status_code == 200
    body = get_doc("ops/stack-versions.md")["body"]
    assert "host one" in body and "1.0 tail" in body
    assert "host\u2028one" not in body and "line\u2029break" not in body
    assert "example.com/pkg\\|odd" in body
    with connect() as db:
        assert db.execute("SELECT count(*) FROM center_deep_inventory_state").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM wiki_docs WHERE path=?", ("ops/stack-versions.md",)).fetchone()[0] == 1


def test_invalid_secret_and_equal_paths_disable_receiver_and_report_readiness(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch, secret=" ")
    from knowledge_ops import api

    with TestClient(api.app) as client:
        assert client.get("/ready").status_code == 503
        ready = client.get("/ready").json()
        assert ready["checks"]["cdr_receiver"] == "misconfigured"
        assert ready["cdr_receiver"]["status"] == "misconfigured"
        assert _post_signed(
            client,
            _delivery(feed="kev", run_id="disabled", inventory=[]),
            "webhook-disabled",
            secret=" ",
        ).status_code == 404

    _settings(tmp_path / "equal", monkeypatch)
    from knowledge_ops import config
    monkeypatch.setattr(config.settings, "cdr_log_path", "ops/stack-versions.md")
    with TestClient(api.app) as client:
        assert client.post("/kb.ingest.center_deep", content=b"{}").status_code == 404
        assert client.get("/ready").json()["cdr_receiver"]["last_error"] == "invalid_paths"


def test_noncanonical_whsec_values_are_disabled(tmp_path, monkeypatch):
    import base64 as b64
    from knowledge_ops import api, config

    urlsafe_key = bytes(range(232, 256))
    urlsafe_secret = "whsec_" + b64.urlsafe_b64encode(urlsafe_key).decode("ascii")
    _settings(tmp_path, monkeypatch, secret=urlsafe_secret)
    with TestClient(api.app) as client:
        assert client.post("/kb.ingest.center_deep", content=b"{}").status_code == 404

    padded = "whsec_" + b64.b64encode(b"fictional-padding-key-123").decode("ascii").rstrip("=")
    monkeypatch.setattr(config.settings, "cdr_webhook_secret", padded)
    with TestClient(api.app) as client:
        assert client.post("/kb.ingest.center_deep", content=b"{}").status_code == 404
