from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tarfile

import pytest
import yaml


def _settings(tmp_path: Path, monkeypatch) -> None:
    import knowledge_ops.config as config

    root = tmp_path / "runtime"
    monkeypatch.setattr(config.settings, "data_dir", root / "data")
    monkeypatch.setattr(config.settings, "db_path", root / "data" / "knowledge.sqlite3")
    monkeypatch.setattr(config.settings, "wiki_dir", root / "wiki")
    monkeypatch.setattr(
        config.settings, "openwiki_staging_dir", root / "data" / "openwiki" / "staging"
    )
    monkeypatch.setattr(config.settings, "default_file_roots", [tmp_path])
    monkeypatch.setattr(config.settings, "default_personal_owner", "local-system")
    monkeypatch.setattr(config.settings, "api_require_auth", False)
    monkeypatch.setattr(config.settings, "require_worker", False)
    monkeypatch.setattr(config.settings, "worker_stale_seconds", 60)
    monkeypatch.setattr(config.settings, "worker_lease_seconds", 5)
    monkeypatch.setattr(config.settings, "worker_retry_base_seconds", 1)
    monkeypatch.setattr(config.settings, "worker_retry_max_seconds", 4)
    monkeypatch.setattr(config.settings, "worker_max_attempts", 2)
    monkeypatch.setattr(config.settings, "worker_batch_size", 10)
    monkeypatch.setattr(config.settings, "deletion_targets", {})


def _provenance(doc_id: str, *, source_type: str = "crm", source_id: str = "record-1") -> None:
    from knowledge_ops.storage import connect

    with connect() as db:
        db.execute(
            """INSERT INTO wiki_provenance(
                 doc_id, source_type, source_uri, source_id, content_sha256,
                 import_id, reviewed_by, reviewed_at, metadata_json
               ) VALUES (?, ?, 'logical-source', ?, 'abc', 'import-1', 'reviewer',
                         '2026-01-01T00:00:00+00:00', '{}')""",
            (doc_id, source_type, source_id),
        )


def test_compose_runtime_services_are_least_privilege_and_readiness_gated():
    root = Path(__file__).parents[1]
    base = yaml.safe_load((root / "docker-compose.yml").read_text())

    def assert_hardened(service: dict) -> None:
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert any(str(item).startswith("/tmp:rw,noexec,nosuid,nodev") for item in service["tmpfs"])

    for name in ("knowledge-ops", "knowledge-ops-worker"):
        assert_hardened(base["services"][name])
    assert "/ready" in " ".join(base["services"]["knowledge-ops"]["healthcheck"]["test"])

    class ComposeLoader(yaml.SafeLoader):
        pass

    ComposeLoader.add_constructor(
        "!reset", lambda loader, node: loader.construct_sequence(node)
    )
    bigboy = yaml.load(
        (root / "docker-compose.bigboy.yml").read_text(), Loader=ComposeLoader
    )
    proxy = bigboy["services"]["knowledge-ops-oauth2-proxy"]
    assert_hardened(proxy)
    assert proxy["depends_on"] == {"knowledge-ops": {"condition": "service_healthy"}}
    assert "OAUTH2_PROXY_INSECURE_OIDC_ALLOW_UNVERIFIED_EMAIL" not in proxy["environment"]


def test_v1_refresh_queue_migrates_and_backfills_pending_dispatch_contract(
    tmp_path, monkeypatch
):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.storage import connect, init_db

    config.settings.data_dir.mkdir(parents=True)
    with sqlite3.connect(config.settings.db_path) as db:
        db.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00+00:00');
            CREATE TABLE refresh_jobs(
              job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_ref TEXT NOT NULL,
              interval_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE refresh_runs(
              run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
              status TEXT NOT NULL, scheduled_for TEXT NOT NULL, started_at TEXT NOT NULL,
              completed_at TEXT, actor TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
              error TEXT, UNIQUE(job_id, idempotency_key)
            );
            CREATE TABLE deletion_events(
              deletion_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, path TEXT NOT NULL,
              actor TEXT NOT NULL, reason TEXT NOT NULL, deleted_at TEXT NOT NULL,
              projections_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO refresh_jobs VALUES(
              'crm', 'connector_refresh', 'crm', 60, 1,
              '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
            );
            INSERT INTO refresh_runs(
              run_id, job_id, idempotency_key, status, scheduled_for, started_at, actor
            ) VALUES(
              'legacy-run', 'crm', 'legacy', 'pending',
              '2026-01-02T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 'scheduler'
            );
            """
        )
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM refresh_runs WHERE run_id='legacy-run'").fetchone()
        version = db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        outbox_exists = db.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='deletion_outbox'"
        ).fetchone()[0]
    assert version == 3 and outbox_exists == 1
    assert row["next_attempt_at"] == row["scheduled_for"]
    assert row["lease_token"] is None and row["max_attempts"] == 5
    assert row["kind_snapshot"] == "connector_refresh"
    assert row["source_ref_snapshot"] == "crm"


def test_concurrent_process_initialization_serializes_migrations(tmp_path):
    runtime = tmp_path / "concurrent"
    shared = {
        **os.environ,
        "KNOWLEDGE_OPS_DB": str(runtime / "database" / "knowledge.sqlite3"),
        "KNOWLEDGE_OPS_WIKI": str(runtime / "wiki"),
        "KNOWLEDGE_OPS_OPENWIKI_STAGING": str(runtime / "staging"),
    }
    command = [
        sys.executable,
        "-c",
        "from knowledge_ops.storage import init_db; init_db()",
    ]
    processes = [
        subprocess.Popen(
            command,
            cwd=Path(__file__).parents[1],
            env={**shared, "KNOWLEDGE_OPS_DATA": str(runtime / f"data-{index % 2}")},
        )
        for index in range(8)
    ]
    assert [process.wait(timeout=30) for process in processes] == [0] * len(processes)
    with sqlite3.connect(shared["KNOWLEDGE_OPS_DB"]) as db:
        versions = [row[0] for row in db.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )]
        columns = {row[1] for row in db.execute("PRAGMA table_info(refresh_runs)")}
    assert versions == [1, 2, 3]
    assert {
        "lease_token", "next_attempt_at", "attempt_count",
        "kind_snapshot", "source_ref_snapshot",
    } <= columns
    lock = runtime / "database" / ".knowledge.sqlite3.schema-migration.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_expired_lease_is_fenced_and_graceful_release_is_reclaimable(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import enqueue_refresh_run, upsert_refresh_job
    from knowledge_ops.operations import (
        claim_refresh_run,
        finish_refresh_run,
        release_worker_leases,
    )

    upsert_refresh_job("crm", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="crash",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    stale = claim_refresh_run(
        "worker-a", connector_adapters={"crm"},
        at="2026-01-01T00:00:00Z", lease_seconds=5,
    )
    current = claim_refresh_run(
        "worker-b", connector_adapters={"crm"},
        at="2026-01-01T00:00:06Z", lease_seconds=5,
    )
    assert stale and current and stale["lease_token"] != current["lease_token"]
    with pytest.raises(ValueError, match="leased refresh run"):
        finish_refresh_run(
            stale["run_id"], worker_id="worker-a", lease_token=stale["lease_token"],
            succeeded=True, at="2026-01-01T00:00:07Z",
        )
    completed = finish_refresh_run(
        current["run_id"], worker_id="worker-b", lease_token=current["lease_token"],
        succeeded=True,
        result={
            "changed": 1,
            "status": "success",
            "counts": {"processed": 2, "person@example.test": 9},
            "person@example.test": 1,
            "token-value": "do-not-store",
            "latency_ms": float("inf"),
        },
        at="2026-01-01T00:00:07Z",
    )
    assert completed["result"] == {
        "changed": 1, "counts": {"processed": 2}, "status": "success",
    }
    assert "person@example.test" not in json.dumps(completed)
    assert "token-value" not in json.dumps(completed)
    assert "lease_token" not in completed

    enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="shutdown",
        scheduled_for="2026-01-01T00:01:00Z",
    )
    claimed = claim_refresh_run(
        "worker-a", connector_adapters={"crm"}, at="2026-01-01T00:01:00Z"
    )
    assert claimed and release_worker_leases("worker-a", at="2026-01-01T00:01:01Z")["refresh_runs"] == 1
    reclaimed = claim_refresh_run(
        "worker-b", connector_adapters={"crm"}, at="2026-01-01T00:01:01Z"
    )
    assert reclaimed and reclaimed["run_id"] == claimed["run_id"]


def test_retry_exhaustion_sanitizes_error_and_becomes_terminal(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import enqueue_refresh_run, upsert_refresh_job
    from knowledge_ops.operations import claim_refresh_run, finish_refresh_run

    upsert_refresh_job("crm", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="retry",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    first = claim_refresh_run("worker", connector_adapters={"crm"}, at="2026-01-01T00:00:00Z")
    pending = finish_refresh_run(
        first["run_id"], worker_id="worker", lease_token=first["lease_token"],
        succeeded=False,
        error="GET https://user:pass@example.test/a?token=secret email person@example.com",
        at="2026-01-01T00:00:00Z",
    )
    assert pending["status"] == "pending" and pending["next_attempt_at"].endswith("00:00:01+00:00")
    assert "secret" not in pending["error"] and "person@example.com" not in pending["error"]
    assert "example.test" not in pending["error"] and "GET" not in pending["error"]
    assert pending["error"].startswith("refresh_adapter_failed:Error:")
    second = claim_refresh_run("worker", connector_adapters={"crm"}, at="2026-01-01T00:00:01Z")
    failed = finish_refresh_run(
        second["run_id"], worker_id="worker", lease_token=second["lease_token"],
        succeeded=False, error="password=hunter2", at="2026-01-01T00:00:01Z",
    )
    assert failed["status"] == "failed" and failed["completed_at"]
    assert "hunter2" not in failed["error"]


def test_crashed_and_released_leases_cannot_exceed_retry_budget(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.memory_quality import enqueue_refresh_run, list_refresh_runs, upsert_refresh_job
    from knowledge_ops.operations import (
        claim_deletion_task,
        claim_refresh_run,
        list_deletion_tasks,
        release_worker_leases,
    )
    from knowledge_ops.wiki import tombstone_doc, upsert_doc

    upsert_refresh_job("crm", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="crash-budget",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    assert claim_refresh_run(
        "worker-a", connector_adapters={"crm"}, at="2026-01-01T00:00:00Z", lease_seconds=5,
    )
    assert claim_refresh_run(
        "worker-b", connector_adapters={"crm"}, at="2026-01-01T00:00:06Z", lease_seconds=5,
    )
    assert claim_refresh_run(
        "worker-c", connector_adapters={"crm"}, at="2026-01-01T00:00:12Z", lease_seconds=5,
    ) is None
    refresh = list_refresh_runs()[0]
    assert refresh["status"] == "failed" and refresh["attempt_count"] == 2
    assert refresh["error"].startswith("refresh_lease_exhausted:")

    monkeypatch.setattr(config.settings, "deletion_targets", {"crm": "crm-delete"})
    doc_id = upsert_doc("external/crash.md", "# Crash deletion")
    _provenance(doc_id)
    tombstone_doc(doc_id, principal=system_principal(), reason="source erasure")
    assert claim_deletion_task(
        "worker-a", adapters={"crm-delete"}, at="2099-01-01T00:01:00Z", lease_seconds=5,
    )
    assert claim_deletion_task(
        "worker-b", adapters={"crm-delete"}, at="2099-01-01T00:01:06Z", lease_seconds=5,
    )
    assert release_worker_leases("worker-b", at="2099-01-01T00:01:07Z")["deletion_tasks"] == 1
    assert claim_deletion_task(
        "worker-c", adapters={"crm-delete"}, at="2099-01-01T00:01:08Z", lease_seconds=5,
    ) is None
    deletion = list_deletion_tasks()[0]
    assert deletion["status"] == "failed" and deletion["attempt_count"] == 2
    assert deletion["last_error"].startswith("deletion_lease_exhausted:")


def test_refresh_claims_serialize_each_job_and_source(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import enqueue_refresh_run, upsert_refresh_job
    from knowledge_ops.operations import claim_refresh_run, finish_refresh_run

    upsert_refresh_job("crm-a", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    upsert_refresh_job("crm-b", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    enqueue_refresh_run(
        "crm-a", actor="scheduler", idempotency_key="one",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    enqueue_refresh_run(
        "crm-b", actor="scheduler", idempotency_key="two",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    first = claim_refresh_run(
        "worker-a", connector_adapters={"crm"}, at="2026-01-01T00:00:00Z",
    )
    assert first
    assert claim_refresh_run(
        "worker-b", connector_adapters={"crm"}, at="2026-01-01T00:00:01Z",
    ) is None
    finish_refresh_run(
        first["run_id"], worker_id="worker-a", lease_token=first["lease_token"],
        succeeded=True, at="2026-01-01T00:00:02Z",
    )
    second = claim_refresh_run(
        "worker-b", connector_adapters={"crm"}, at="2026-01-01T00:00:02Z",
    )
    assert second and second["run_id"] != first["run_id"]


def test_synchronous_review_scan_refuses_an_overlapping_source_run(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import (
        enqueue_refresh_run,
        run_refresh_job,
        upsert_refresh_job,
    )
    from knowledge_ops.operations import claim_refresh_run

    upsert_refresh_job("review-a", kind="review_scan", source_ref="wiki", interval_seconds=60)
    upsert_refresh_job("review-b", kind="review_scan", source_ref="wiki", interval_seconds=60)
    enqueue_refresh_run(
        "review-a", actor="scheduler", idempotency_key="worker-owned",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    active = claim_refresh_run(
        "worker-a", connector_adapters=set(), at="2026-01-01T00:00:00Z"
    )
    assert active and active["status"] == "running"
    with pytest.raises(ValueError, match="overlapping"):
        run_refresh_job(
            "review-b", actor="operator", idempotency_key="manual-overlap",
            scheduled_for="2026-01-01T00:00:01Z",
        )


def test_queued_refresh_run_keeps_its_snapshotted_contract_after_job_edit(
    tmp_path, monkeypatch
):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import enqueue_refresh_run, upsert_refresh_job
    from knowledge_ops.operations import claim_refresh_run, finish_refresh_run

    upsert_refresh_job("crm", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    queued = enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="queued",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    assert queued["kind_snapshot"] == "connector_refresh"
    assert queued["source_ref_snapshot"] == "crm"
    updated = upsert_refresh_job(
        "crm", kind="connector_refresh", source_ref="erp", interval_seconds=120,
    )
    assert updated["source_ref"] == "erp" and updated["interval_seconds"] == 120

    old = claim_refresh_run(
        "worker-a", connector_adapters={"crm"}, at="2026-01-01T00:00:00Z"
    )
    assert old and old["source_ref"] == "crm"
    finish_refresh_run(
        old["run_id"], worker_id="worker-a", lease_token=old["lease_token"],
        succeeded=True, at="2026-01-01T00:00:01Z",
    )
    new = enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="after-edit",
        scheduled_for="2026-01-01T00:01:00Z",
    )
    assert new["source_ref_snapshot"] == "erp"
    assert claim_refresh_run(
        "worker-b", connector_adapters={"crm"}, at="2026-01-01T00:01:00Z"
    ) is None
    claimed = claim_refresh_run(
        "worker-b", connector_adapters={"erp"}, at="2026-01-01T00:01:00Z"
    )
    assert claimed and claimed["source_ref"] == "erp"


def test_worker_never_persists_raw_adapter_exception_text(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.memory_quality import enqueue_refresh_run, upsert_refresh_job
    from knowledge_ops.storage import connect
    from knowledge_ops.worker import DurableWorker

    class BrokenAdapter:
        def refresh(self, *, source_ref, idempotency_key):
            raise RuntimeError(
                "token=raw-secret person@example.com https://example.test/path?password=raw"
            )

    upsert_refresh_job("crm", kind="connector_refresh", source_ref="crm", interval_seconds=60)
    enqueue_refresh_run(
        "crm", actor="scheduler", idempotency_key="worker-error",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    worker = DurableWorker(worker_id="test-worker", refresh_adapters={"crm": BrokenAdapter()})
    try:
        worker.run_once(at="2026-01-01T00:00:00Z")
    finally:
        worker.stop()
    with connect() as db:
        persisted = " ".join(str(value) for row in db.execute(
            """SELECT error, result_json FROM refresh_runs
               UNION ALL SELECT detail, '{}' FROM source_health
               UNION ALL SELECT details_json, '{}' FROM audit_events"""
        ) for value in row)
    assert "raw-secret" not in persisted
    assert "person@example.com" not in persisted
    assert "example.test" not in persisted
    assert "adapter_execution_failed:RuntimeError:" in persisted


def test_any_fresh_worker_satisfies_required_readiness(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    import knowledge_ops.config as config
    from knowledge_ops.api import app
    from knowledge_ops.operations import record_worker_heartbeat, worker_readiness

    current = datetime.now(timezone.utc)
    record_worker_heartbeat("live-host", at=current.isoformat(), starting=True)
    record_worker_heartbeat(
        "newer-stopped-host", status="stopped",
        at=(current + timedelta(seconds=1)).isoformat(), starting=True,
    )
    status = worker_readiness(at=(current + timedelta(seconds=2)).isoformat())
    assert status["ready"] is True and status["fresh_running_workers"] == 1
    assert "worker_id" not in json.dumps(status) and "detail" not in json.dumps(status)

    monkeypatch.setattr(config.settings, "require_worker", True)
    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200 and ready.json()["checks"]["worker"] is True
        record_worker_heartbeat(
            "live-host", status="stopped", at=(current + timedelta(seconds=3)).isoformat()
        )
        assert client.get("/ready").status_code == 503


@pytest.mark.parametrize(
    ("source_id", "targets", "expected_error"),
    [
        ("record-1", {"crm": "https://bad-adapter"}, "InvalidAdapter"),
        ("", {}, "MissingSourceId"),
    ],
)
def test_malformed_source_owner_contract_returns_failed_receipt(
    tmp_path, monkeypatch, source_id, targets, expected_error
):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.operations import list_deletion_tasks
    from knowledge_ops.wiki import tombstone_doc, upsert_doc

    monkeypatch.setattr(config.settings, "deletion_targets", targets)
    doc_id = upsert_doc("external/item.md", "# External\n\nSensitive source-owned record.")
    _provenance(doc_id, source_id=source_id)
    receipt = tombstone_doc(doc_id, principal=system_principal(), reason="retention request")
    assert receipt["complete"] is False
    assert receipt["projections"]["source_owner_outbox"] == "failed"
    assert not list_deletion_tasks()
    from knowledge_ops.storage import connect

    with connect() as db:
        stored = json.loads(db.execute(
            "SELECT projections_json FROM deletion_events WHERE deletion_id=?",
            (receipt["deletion_id"],),
        ).fetchone()[0])
    assert expected_error in stored["_source_owner_outbox_error"]


def test_deletion_outbox_success_is_visible_and_receipt_is_sanitized(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.operations import (
        claim_deletion_task,
        finish_deletion_task,
        list_deletion_tasks,
    )
    from knowledge_ops.wiki import reconcile_pending_deletions, tombstone_doc, upsert_doc

    monkeypatch.setattr(config.settings, "deletion_targets", {"crm": "crm-delete"})
    doc_id = upsert_doc("external/delete.md", "# Delete me")
    _provenance(doc_id)
    receipt = tombstone_doc(doc_id, principal=system_principal(), reason="source erasure")
    assert receipt["projections"]["source_owner_outbox"] == "pending"
    task = claim_deletion_task("worker", adapters={"crm-delete"})
    assert task and "lease_token" in task
    done = finish_deletion_task(
        task["task_id"], worker_id="worker", lease_token=task["lease_token"],
        succeeded=True,
        result={
            "deleted": 1,
            "person@example.test": "ok",
            "token-value": "secret-id",
            "duration_seconds": float("nan"),
        },
    )
    assert done["status"] == "succeeded"
    assert done["result"] == {"deleted": 1}
    assert "person@example.test" not in json.dumps(done)
    assert "token-value" not in json.dumps(done)
    assert "lease_token" not in done and "lease_token" not in list_deletion_tasks()[0]
    assert reconcile_pending_deletions()["pending"] == 0


def test_completed_deletions_are_not_reprocessed_by_worker_poll(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.authorization import system_principal
    import knowledge_ops.wiki as wiki

    doc_id = wiki.upsert_doc("facts/deleted.md", "# Delete")
    receipt = wiki.tombstone_doc(doc_id, principal=system_principal(), reason="retention")
    assert receipt["complete"] is True
    calls = 0
    real_reconcile = wiki._reconcile_deletion

    def counted(deletion_id):
        nonlocal calls
        calls += 1
        return real_reconcile(deletion_id)

    monkeypatch.setattr(wiki, "_reconcile_deletion", counted)
    assert wiki.reconcile_pending_deletions() == {"completed": 0, "pending": 0}
    assert calls == 0
    assert wiki.reconcile_pending_deletions(include_completed=True)["completed"] == 1
    assert calls == 1


def test_admin_can_redrive_terminal_deletion_with_audit(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    import knowledge_ops.config as config
    from knowledge_ops.api import app
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.operations import claim_deletion_task, finish_deletion_task
    from knowledge_ops.storage import connect
    from knowledge_ops.wiki import tombstone_doc, upsert_doc

    monkeypatch.setattr(config.settings, "deletion_targets", {"crm": "crm-delete"})
    doc_id = upsert_doc("external/redrive.md", "# Redrive")
    _provenance(doc_id)
    tombstone_doc(doc_id, principal=system_principal(), reason="source erasure")
    for attempt, at in enumerate(("2099-01-01T00:00:00Z", "2099-01-01T00:00:01Z")):
        task = claim_deletion_task("worker", adapters={"crm-delete"}, at=at)
        assert task
        failed = finish_deletion_task(
            task["task_id"], worker_id="worker", lease_token=task["lease_token"],
            succeeded=False, error="adapter unavailable", at=at,
        )
        if attempt == 0:
            assert failed["status"] == "pending"
    assert failed["status"] == "failed"

    with TestClient(app) as client:
        response = client.post(f"/kb.deletion_outbox/{failed['task_id']}/redrive")
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["status"] == "pending" and task["attempt_count"] == 0
    with connect() as db:
        audit = db.execute(
            "SELECT resource, outcome FROM audit_events WHERE action='deletion.outbox_redrive'"
        ).fetchone()
    assert tuple(audit) == (failed["task_id"], "success")


def test_metrics_are_authenticated_aggregates_without_identifier_labels(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    import knowledge_ops.config as config
    from knowledge_ops.api import app
    from knowledge_ops.memory_quality import enqueue_refresh_run, upsert_refresh_job
    from knowledge_ops.metrics import prometheus_metrics
    from knowledge_ops.operations import claim_refresh_run, record_worker_heartbeat

    upsert_refresh_job("private-job-id", kind="connector_refresh", source_ref="private-source", interval_seconds=60)
    enqueue_refresh_run(
        "private-job-id", actor="scheduler", idempotency_key="one",
        scheduled_for="2026-01-01T00:00:00Z",
    )
    claim_refresh_run("private-worker-host", connector_adapters={"private-source"}, at="2026-01-01T00:00:00Z")
    record_worker_heartbeat("private-worker-host", starting=True)
    metrics = prometheus_metrics()
    assert "private-job-id" not in metrics
    assert "private-source" not in metrics
    assert "private-worker-host" not in metrics
    assert all(label not in metrics for label in ('job_id="', 'source_ref="', 'worker_id="'))
    assert 'knowledge_ops_jwks_stale_fallback_total{surface="api"}' in metrics
    assert 'knowledge_ops_jwks_stale_fallback_total{surface="mcp"}' in metrics

    monkeypatch.setattr(config.settings, "api_require_auth", True)
    with TestClient(app) as client:
        assert client.get(
            "/metrics", headers={"authorization": "Bearer malformed-secret-token"}
        ).status_code == 401
    from knowledge_ops.storage import connect

    with connect() as db:
        audit = " ".join(
            str(row[0]) for row in db.execute(
                "SELECT details_json FROM audit_events WHERE action='auth.api.authentication'"
            )
        )
    assert "malformed-secret-token" not in audit
    assert "authorization_failed" in audit
    monkeypatch.setattr(config.settings, "api_require_auth", False)
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200 and response.text.endswith("# EOF\n")
        refresh = client.get("/kb.refresh_jobs").json()
        assert "lease_token" not in json.dumps(refresh)


def test_backup_restore_reindex_is_restrictive_and_external_backlog_is_recoverable(
    tmp_path, monkeypatch
):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.maintenance import create_backup, restore_and_verify
    from knowledge_ops.wiki import tombstone_doc, upsert_doc

    monkeypatch.setattr(config.settings, "deletion_targets", {"crm": "crm-delete"})
    upsert_doc("facts/current.md", "# Current\n\nRestorable evidence.")
    external = upsert_doc("external/pending.md", "# Pending deletion")
    _provenance(external)
    tombstone_doc(external, principal=system_principal(), reason="pending source erasure")

    archive = tmp_path / "archives" / "knowledge-ops.tar.gz"
    result = create_backup(archive)
    assert result["schema_version"] == 3
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="outside"):
        create_backup(config.settings.wiki_dir / "recursive.tar.gz")

    target = tmp_path / "restored"
    restored = restore_and_verify(archive, target)
    verification = restored["verification"]
    assert verification["passed"] is True
    assert verification["external_deletion_tasks_pending"] == 1
    assert verification["local_deletion_projections_pending"] == 0
    for directory in (target, target / "data", target / "wiki"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / "data" / "knowledge_ops.sqlite3").stat().st_mode) == 0o600
    assert stat.S_IMODE((target / "wiki" / "facts" / "current.md").stat().st_mode) == 0o600


def test_backup_rejects_symbolic_links_in_retained_projections(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.maintenance import create_backup
    from knowledge_ops.wiki import upsert_doc

    upsert_doc("facts/source.md", "# Source")
    (config.settings.wiki_dir / "linked.md").symlink_to(
        config.settings.wiki_dir / "facts" / "source.md"
    )
    with pytest.raises(ValueError, match="symbolic link"):
        create_backup(tmp_path / "symlink-backup.tar.gz")


def test_backup_cannot_replace_a_split_path_live_database(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.maintenance import create_backup
    from knowledge_ops.wiki import upsert_doc

    database = tmp_path / "database-elsewhere" / "live.sqlite3"
    monkeypatch.setattr(config.settings, "db_path", database)
    upsert_doc("facts/safe.md", "# Safe")
    before = database.read_bytes()[:16]
    assert before == b"SQLite format 3\x00"
    with pytest.raises(ValueError, match="live database"):
        create_backup(database)
    assert database.read_bytes()[:16] == before


def test_backup_checksums_and_restore_verifies_openwiki_manifests(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.config as config
    from knowledge_ops.authorization import system_principal
    from knowledge_ops.maintenance import create_backup, restore_backup, verify_restored_root
    from knowledge_ops.openwiki_pipeline import stage_bundle

    source = tmp_path / "openwiki-output"
    source.mkdir()
    (source / "fact.md").write_text("---\ntype: knowledge\n---\n# Fact\n")
    monkeypatch.setattr(config.settings, "openwiki_import_roots", [source])
    staged = stage_bundle(str(source), "0.2.0", system_principal())
    archive = tmp_path / "openwiki-backup.tar.gz"
    create_backup(archive)
    nested_name = (
        "knowledge-ops-backup/openwiki/staging/"
        f"{staged['import_id']}/manifest.json"
    )
    with tarfile.open(archive, "r:gz") as original:
        root_manifest = json.loads(
            original.extractfile("knowledge-ops-backup/manifest.json").read()
        )
    assert nested_name.removeprefix("knowledge-ops-backup/") in {
        item["path"] for item in root_manifest["files"]
    }

    tampered = tmp_path / "tampered-openwiki-backup.tar.gz"
    with tarfile.open(archive, "r:gz") as original, tarfile.open(tampered, "w:gz") as changed:
        for member in original.getmembers():
            if not member.isfile():
                changed.addfile(member)
                continue
            payload = original.extractfile(member).read()
            if member.name == nested_name:
                payload = b"{}"
                member.size = len(payload)
            changed.addfile(member, io.BytesIO(payload))
    with pytest.raises(ValueError, match="wrong size|checksum"):
        restore_backup(tampered, tmp_path / "tampered-restore")

    restored_root = tmp_path / "missing-snapshot"
    restore_backup(archive, restored_root)
    manifest = (
        restored_root / "data" / "openwiki" / "staging"
        / staged["import_id"] / "manifest.json"
    )
    manifest.unlink()
    verification = verify_restored_root(restored_root)
    assert verification["passed"] is False
    assert verification["openwiki_staging"]["valid"] is False

    source_manifest = (
        config.settings.openwiki_staging_dir / staged["import_id"] / "manifest.json"
    )
    source_manifest.unlink()
    inconsistent_archive = tmp_path / "inconsistent-openwiki-backup.tar.gz"
    create_backup(inconsistent_archive)
    inconsistent_target = tmp_path / "inconsistent-restore"
    with pytest.raises(ValueError, match="staging failed"):
        restore_backup(inconsistent_archive, inconsistent_target)
    assert not inconsistent_target.exists()


def test_readiness_rejects_schema_version_drift(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    from knowledge_ops.api import app
    from knowledge_ops.storage import connect

    with TestClient(app) as client:
        with connect() as db:
            db.execute("DELETE FROM schema_migrations WHERE version=3")
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["database"] is True
        assert response.json()["checks"]["schema"] is False


def test_restore_rejects_duplicate_members_and_size_bombs(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    import knowledge_ops.maintenance as maintenance

    duplicate = tmp_path / "duplicate.tar.gz"
    payload = b"{}"
    with tarfile.open(duplicate, "w:gz") as archive:
        for _ in range(2):
            member = tarfile.TarInfo("knowledge-ops-backup/manifest.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    with pytest.raises(ValueError, match="duplicate"):
        maintenance.restore_backup(duplicate, tmp_path / "duplicate-target")

    bomb = tmp_path / "bomb.tar.gz"
    with tarfile.open(bomb, "w:gz") as archive:
        member = tarfile.TarInfo("knowledge-ops-backup/database.sqlite3")
        member.size = 2
        archive.addfile(member, io.BytesIO(b"xx"))
    monkeypatch.setattr(maintenance, "MAX_BACKUP_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        maintenance.restore_backup(bomb, tmp_path / "bomb-target")


def test_restore_rejects_an_incomplete_manifest_before_install(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch)
    from knowledge_ops.maintenance import create_backup, restore_backup
    from knowledge_ops.wiki import upsert_doc

    upsert_doc("facts/retained.md", "# Retained")
    archive = tmp_path / "valid.tar.gz"
    create_backup(archive)
    malformed = tmp_path / "missing-schema.tar.gz"
    with tarfile.open(archive, "r:gz") as source, tarfile.open(malformed, "w:gz") as target:
        for member in source.getmembers():
            if not member.isfile():
                target.addfile(member)
                continue
            payload = source.extractfile(member).read()
            if member.name == "knowledge-ops-backup/manifest.json":
                manifest = json.loads(payload)
                manifest.pop("schema_version")
                payload = (json.dumps(manifest) + "\n").encode()
                member.size = len(payload)
            target.addfile(member, io.BytesIO(payload))

    restore_target = tmp_path / "must-not-install"
    with pytest.raises(ValueError, match="schema version"):
        restore_backup(malformed, restore_target)
    assert not restore_target.exists()
