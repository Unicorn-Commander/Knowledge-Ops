from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import time

import pytest


SECRET_KEY = b"fictional-center-deep-test-key"
SECRET = "whsec_" + base64.b64encode(SECRET_KEY).decode("ascii")


def _settings(tmp_path: Path, monkeypatch) -> None:
    import knowledge_ops.config as config
    import knowledge_ops.storage as storage
    import knowledge_ops.wiki as wiki

    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "db_path", tmp_path / "data" / "knowledge.sqlite3")
    monkeypatch.setattr(config.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(config.settings, "openwiki_staging_dir", tmp_path / "data" / "openwiki" / "staging")
    monkeypatch.setattr(config.settings, "api_require_auth", False)
    monkeypatch.setattr(config.settings, "cdr_webhook_secret", SECRET)
    monkeypatch.setattr(config.settings, "cdr_doc_path", "ops/self-hosted-stack-versions.md")
    monkeypatch.setattr(config.settings, "cdr_log_path", "ops/self-hosted-stack-releases-log.md")
    monkeypatch.setattr(storage.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(storage.settings, "db_path", tmp_path / "data" / "knowledge.sqlite3")
    monkeypatch.setattr(storage.settings, "wiki_dir", tmp_path / "wiki")
    monkeypatch.setattr(wiki.settings, "wiki_dir", tmp_path / "wiki")


def _delivery(*, feed: str, run_id: str, releases: list[dict] | None = None, kev_matches: list[dict] | None = None) -> dict:
    return {
        "type": "monitor.run.completed",
        "id": f"delivery-{run_id}",
        "timestamp": "2026-11-08T12:00:00Z",
        "data": {
            "monitor_id": "monitor-fictional-stack",
            "run_id": run_id,
            "question": "What is current in the fictional stack?",
            "status": "completed",
            "report_url": f"https://research.example.com/#/runs/{run_id}",
            "situation": {
                "feed": feed,
                "state": "WATCH",
                "observed_at": "2026-11-08T12:00:00Z",
                "complete": True,
                "inventory": [{
                    "host": "lab-node.example.com",
                    "package": "example.com/fictional/relay",
                    "ecosystem": "Go",
                    "version": "1.2.3",
                    "repo": "fictional/relay",
                }],
                "kev_matches": kev_matches or [],
                "releases": releases or [],
                "comparisons": ([{
                    "package": "example.com/fictional/relay",
                    "repo": "fictional/relay",
                    "current": "1.2.3",
                    "latest_tag": "v1.3.0",
                    "comparison": "1.2.3 is behind v1.3.0",
                    "behind": True,
                    "security_newer": True,
                }] if feed == "github_releases" else []),
            },
        },
    }


def _release() -> dict:
    return {
        "repo": "fictional/relay",
        "package": "example.com/fictional/relay",
        "tag": "v1.3.0",
        "name": "v1.3.0",
        "url": "https://github.com/fictional/relay/releases/tag/v1.3.0",
        "published_at": "2026-11-07T10:00:00Z",
        "prerelease": False,
        "security": True,
    }


def _post_signed(client, body: dict, webhook_id: str, *, timestamp: int | None = None, secret: str = SECRET):
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    timestamp = timestamp or int(time.time())
    if secret.startswith("whsec_"):
        key = base64.b64decode(secret[len("whsec_"):])
    else:
        key = secret.encode("utf-8")
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


def test_signed_release_and_kev_runs_merge_and_ground(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    import knowledge_ops.api as api
    from knowledge_ops.wiki import get_doc

    release = _post_signed(
        TestClient(api.app),
        _delivery(feed="github_releases", run_id="run-release", releases=[_release()]),
        "webhook-release",
    )
    assert release.status_code == 200
    assert release.json()["rows"] == 1

    with TestClient(api.app) as client:
        kev = _post_signed(
            client,
            _delivery(
                feed="kev",
                run_id="run-kev",
                kev_matches=[{
                    "host": "lab-node.example.com",
                    "package": "example.com/fictional/relay",
                    "ecosystem": "Go",
                    "version": "1.2.3",
                    "cve": "CVE-2026-12345",
                    "osv_id": "GHSA-fictional-1234",
                    "kev": True,
                    "summary": "Fictional advisory",
                    "published": "2026-11-07T00:00:00Z",
                    "state": "WATCH",
                }],
            ),
            "webhook-kev",
        )
        assert kev.status_code == 200

        doc = get_doc("ops/self-hosted-stack-versions.md")
        assert doc["scope"] == "global"
        assert doc["owner"] == "center-deep"
        assert "v1.3.0 (2026-11-07)" in doc["body"]
        assert "yes | yes | 1 (CVE-2026-12345)" in doc["body"]
        assert doc["review_due_at"] == "2026-11-22T12:00:00+00:00"

        grounded = client.post(
            "/kb.ground",
            json={
                "query": "CVE-2026-12345 fictional relay",
                "scope": "global",
                "sources": ["wiki"],
            },
        )
        assert grounded.status_code == 200
        assert any("CVE-2026-12345" in item["text"] for item in grounded.json()["passages"])


def test_duplicate_delivery_and_release_log_are_idempotent(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    import knowledge_ops.api as api
    from knowledge_ops.wiki import get_doc

    body = _delivery(feed="github_releases", run_id="run-one", releases=[_release()])
    with TestClient(api.app) as client:
        first = _post_signed(client, body, "webhook-one")
        assert first.status_code == 200
        duplicate = _post_signed(client, body, "webhook-one")
        assert duplicate.status_code == 200
        assert duplicate.json() == {"duplicate": True}

        second = _post_signed(
            client,
            _delivery(feed="github_releases", run_id="run-two", releases=[_release()]),
            "webhook-two",
        )
        assert second.status_code == 200
        log = get_doc("ops/self-hosted-stack-releases-log.md")["body"]
        assert log.count("fictional/relay v1.3.0") == 1


def test_bad_signature_stale_delivery_and_unset_secret(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    import knowledge_ops.api as api
    import knowledge_ops.config as config

    with TestClient(api.app) as client:
        body = _delivery(feed="github_releases", run_id="run-bad", releases=[_release()])
        bad = _post_signed(client, body, "webhook-bad", secret="wrong-secret")
        assert bad.status_code == 401

        stale = _post_signed(
            client,
            body,
            "webhook-stale",
            timestamp=int(datetime.now(timezone.utc).timestamp()) - 301,
        )
        assert stale.status_code == 409

        monkeypatch.setattr(config.settings, "cdr_webhook_secret", "")
        assert _post_signed(client, body, "webhook-disabled").status_code == 404


@pytest.mark.parametrize("secret", ["fictional-raw-secret-123", SECRET])
def test_signature_accepts_raw_and_whsec_key_derivations(tmp_path, monkeypatch, secret):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config.settings, "cdr_webhook_secret", secret)
    import knowledge_ops.api as api

    with TestClient(api.app) as client:
        response = _post_signed(
            client,
            _delivery(feed="kev", run_id=f"run-{secret[:4]}"),
            f"webhook-{secret[:4]}",
            secret=secret,
        )
        assert response.status_code == 200
