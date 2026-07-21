from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import re
import sqlite3
from typing import Any

from .config import settings
from .storage import connect, init_db, json_dumps, now
from .text import stable_id

_LOGICAL_NAME = re.compile(r"[A-Za-z0-9._:-]+")
_ERROR_RECEIPT = re.compile(r"[a-z][a-z0-9_]{0,63}:[A-Za-z][A-Za-z0-9_.-]{0,79}:[a-f0-9]{16}")
_RECEIPT_NUMBER_KEYS = {
    "added", "auto_resolved", "changed", "chunks", "count", "created", "deleted",
    "detected", "documents", "duration_seconds", "eligible", "failed", "indexed", "items",
    "latency_ms", "matches", "open", "processed", "records", "removed", "skipped",
    "snapshots_purged", "succeeded", "total", "unchanged", "updated",
}
_RECEIPT_TIME_KEYS = {
    "checked_at", "completed_at", "created_at", "deleted_at", "finished_at", "scanned_at",
    "started_at", "updated_at",
}
_RECEIPT_DIGEST_KEYS = {"content_sha256", "receipt_digest", "result_digest", "sha256", "source_digest"}
_RECEIPT_NESTED_KEYS = {"counts", "summary"}


def _timestamp(value: str | None = None) -> str:
    if not value:
        return now()
    clean = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(clean)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _later(value: str, seconds: int) -> str:
    return (datetime.fromisoformat(value) + timedelta(seconds=max(1, seconds))).isoformat()


def _backoff(attempt: int) -> int:
    exponent = max(0, min(int(attempt) - 1, 20))
    return min(
        max(1, settings.worker_retry_max_seconds),
        max(1, settings.worker_retry_base_seconds) * (2**exponent),
    )


def sanitize_error(
    value: object,
    *,
    category: str = "operation_failed",
    exception_type: str | None = None,
) -> str:
    """Return a finite error receipt without retaining exception text."""
    existing = str(value)
    if _ERROR_RECEIPT.fullmatch(existing):
        return existing
    clean_category = re.sub(r"[^a-z0-9_]", "_", category.lower())[:64] or "operation_failed"
    raw_type = exception_type or (type(value).__name__ if not isinstance(value, str) else "Error")
    clean_type = re.sub(r"[^A-Za-z0-9_.-]", "_", raw_type)[:80] or "Error"
    code = stable_id(f"{clean_category}:{clean_type}")[:16]
    return f"{clean_category}:{clean_type}:{code}"


def sanitize_receipt(value: object, *, _depth: int = 0) -> dict[str, Any]:
    """Keep a finite operational receipt while excluding adapter-controlled text."""
    if not isinstance(value, dict) or _depth > 2:
        return {}
    cleaned: dict[str, Any] = {}
    allowed_status = {"ok", "success", "succeeded", "complete", "completed", "unchanged"}
    for raw_key, raw_value in list(value.items())[:40]:
        key = str(raw_key)
        if key in _RECEIPT_NESTED_KEYS and isinstance(raw_value, dict):
            cleaned[key] = sanitize_receipt(raw_value, _depth=_depth + 1)
        elif key in {"status", "outcome"} and str(raw_value).lower() in allowed_status:
            cleaned[key] = str(raw_value).lower()
        elif key in _RECEIPT_NUMBER_KEYS and isinstance(raw_value, bool):
            cleaned[key] = raw_value
        elif key in _RECEIPT_NUMBER_KEYS and isinstance(raw_value, (int, float)):
            numeric = float(raw_value)
            if math.isfinite(numeric) and 0 <= numeric <= 1_000_000_000_000:
                cleaned[key] = raw_value
        elif key in _RECEIPT_DIGEST_KEYS and re.fullmatch(r"[a-fA-F0-9]{32,128}", str(raw_value)):
            cleaned[key] = str(raw_value).lower()
        elif key in _RECEIPT_TIME_KEYS:
            try:
                cleaned[key] = _timestamp(str(raw_value))
            except (ValueError, TypeError):
                pass
    return cleaned


def _terminalize_exhausted_refresh(db: sqlite3.Connection, timestamp: str) -> int:
    return db.execute(
        """UPDATE refresh_runs SET status='failed', next_attempt_at=NULL,
             completed_at=?, lease_owner=NULL, lease_token=NULL,
             lease_expires_at=NULL, heartbeat_at=NULL, error=?
           WHERE attempt_count >= max_attempts AND (
             status='pending' OR (
               status='running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
             )
           )""",
        (
            timestamp,
            sanitize_error("retry budget exhausted", category="refresh_lease_exhausted"),
            timestamp,
        ),
    ).rowcount


def _terminalize_exhausted_deletions(db: sqlite3.Connection, timestamp: str) -> int:
    return db.execute(
        """UPDATE deletion_outbox SET status='failed', next_attempt_at=?, completed_at=?,
             updated_at=?, lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
             heartbeat_at=NULL, last_error=?
           WHERE attempt_count >= max_attempts AND (
             status='pending' OR (
               status='running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
             )
           )""",
        (
            timestamp,
            timestamp,
            timestamp,
            sanitize_error("retry budget exhausted", category="deletion_lease_exhausted"),
            timestamp,
        ),
    ).rowcount


def _row(row: sqlite3.Row | None, *, include_lease: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    if "result_json" in item:
        item["result"] = json.loads(item.pop("result_json") or "{}")
    if "detail_json" in item:
        item["detail"] = json.loads(item.pop("detail_json") or "{}")
    if not include_lease:
        item.pop("lease_token", None)
    return item


def _validate_adapter(adapter: str) -> str:
    adapter = adapter.strip()
    if not adapter or len(adapter) > 120 or not _LOGICAL_NAME.fullmatch(adapter):
        raise ValueError("adapter must be a credential-free logical identifier")
    return adapter


def credential_free_source_ref(source_ref: str) -> str:
    source_ref = source_ref.strip()
    if (
        not source_ref
        or len(source_ref) > 500
        or any(char.isspace() or ord(char) < 32 for char in source_ref)
        or any(marker in source_ref for marker in ("?", "#", "@", "=", "://"))
    ):
        raise ValueError("source_ref must be a credential-free logical source identifier")
    return source_ref


def enqueue_deletion_task(
    deletion_id: str,
    *,
    adapter: str,
    source_ref: str,
    at: str | None = None,
    max_attempts: int | None = None,
    initial_error: str | None = None,
    db: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Insert one idempotent source-owner deletion request.

    The outbox stores only logical identifiers. Adapter configuration and all
    authentication material remain process-owned.
    """
    adapter = _validate_adapter(adapter)
    source_ref = credential_free_source_ref(source_ref)
    timestamp = _timestamp(at)
    attempts = max(1, min(int(max_attempts or settings.worker_max_attempts), 100))
    task_id = "do-" + stable_id(f"{deletion_id}:{adapter}:{source_ref}")[:28]
    own_connection = db is None
    connection = db or connect()
    try:
        connection.execute(
            """INSERT OR IGNORE INTO deletion_outbox(
                 task_id, deletion_id, adapter, source_ref, status, attempt_count,
                 max_attempts, next_attempt_at, last_error, created_at, updated_at
               ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
            (
                task_id, deletion_id, adapter, source_ref, attempts, timestamp,
                sanitize_error(initial_error, category="source_contract") if initial_error else None,
                timestamp, timestamp,
            ),
        )
        row = connection.execute(
            "SELECT * FROM deletion_outbox WHERE task_id=?", (task_id,)
        ).fetchone()
        if own_connection:
            connection.commit()
    finally:
        if own_connection:
            connection.close()
    return _row(row) or {}


def list_deletion_tasks(*, limit: int = 200, status: str = "all") -> list[dict[str, Any]]:
    init_db()
    if status not in {"all", "pending", "running", "failed", "succeeded"}:
        raise ValueError("unsupported deletion outbox status")
    where = "1=1" if status == "all" else "status=?"
    params: list[Any] = [] if status == "all" else [status]
    params.append(max(1, min(int(limit), 500)))
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM deletion_outbox WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row(row) or {} for row in rows]


def claim_deletion_task(
    worker_id: str,
    *,
    adapters: set[str],
    at: str | None = None,
    lease_seconds: int | None = None,
) -> dict[str, Any] | None:
    init_db()
    if not adapters:
        return None
    timestamp = _timestamp(at)
    lease_until = _later(timestamp, lease_seconds or settings.worker_lease_seconds)
    names = sorted(_validate_adapter(name) for name in adapters)
    placeholders = ",".join("?" for _ in names)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _terminalize_exhausted_deletions(db, timestamp)
        row = db.execute(
            f"""SELECT * FROM deletion_outbox
                WHERE adapter IN ({placeholders}) AND attempt_count < max_attempts AND (
                  (status='pending' AND next_attempt_at <= ?)
                  OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
                )
                ORDER BY next_attempt_at, created_at, task_id LIMIT 1""",
            (*names, timestamp, timestamp),
        ).fetchone()
        if row is None:
            return None
        lease_token = stable_id(
            f"deletion:{worker_id}:{row['task_id']}:{timestamp}:{int(row['attempt_count']) + 1}"
        )
        updated = db.execute(
            """UPDATE deletion_outbox SET status='running', attempt_count=attempt_count+1,
                 lease_owner=?, lease_token=?, lease_expires_at=?, heartbeat_at=?, updated_at=?
               WHERE task_id=? AND attempt_count < max_attempts AND (
                 status='pending' OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
               )""",
            (
                worker_id[:200], lease_token, lease_until, timestamp, timestamp,
                row["task_id"], timestamp,
            ),
        )
        if updated.rowcount != 1:
            return None
        claimed = db.execute(
            "SELECT * FROM deletion_outbox WHERE task_id=?", (row["task_id"],)
        ).fetchone()
    return _row(claimed, include_lease=True)


def finish_deletion_task(
    task_id: str,
    *,
    worker_id: str,
    lease_token: str,
    succeeded: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    timestamp = _timestamp(at)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM deletion_outbox WHERE task_id=?", (task_id,)
        ).fetchone()
        if (
            row is None
            or row["status"] != "running"
            or row["lease_owner"] != worker_id
            or row["lease_token"] != lease_token
        ):
            raise ValueError("leased deletion task was not found for this worker")
        if succeeded:
            status, next_attempt, completed, message = "succeeded", timestamp, timestamp, None
        elif int(row["attempt_count"]) >= int(row["max_attempts"]):
            status, next_attempt, completed = "failed", timestamp, timestamp
            message = sanitize_error(error or "deletion adapter failed", category="deletion_adapter_failed")
        else:
            status, completed = "pending", None
            next_attempt = _later(timestamp, _backoff(int(row["attempt_count"])))
            message = sanitize_error(error or "deletion adapter failed", category="deletion_adapter_failed")
        db.execute(
            """UPDATE deletion_outbox SET status=?, next_attempt_at=?, lease_owner=NULL,
                 lease_token=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                 last_error=?, result_json=?,
                 updated_at=?, completed_at=? WHERE task_id=?""",
            (
                status, next_attempt, message, json_dumps(sanitize_receipt(result or {})), timestamp,
                completed, task_id,
            ),
        )
        updated = db.execute(
            "SELECT * FROM deletion_outbox WHERE task_id=?", (task_id,)
        ).fetchone()
    return _row(updated) or {}


def claim_refresh_run(
    worker_id: str,
    *,
    connector_adapters: set[str],
    at: str | None = None,
    lease_seconds: int | None = None,
) -> dict[str, Any] | None:
    init_db()
    timestamp = _timestamp(at)
    lease_until = _later(timestamp, lease_seconds or settings.worker_lease_seconds)
    names = sorted(_validate_adapter(name) for name in connector_adapters)
    connector_clause = "0"
    adapter_params: tuple[str, ...] = ()
    if names:
        connector_clause = f"r.source_ref_snapshot IN ({','.join('?' for _ in names)})"
        adapter_params = tuple(names)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _terminalize_exhausted_refresh(db, timestamp)
        row = db.execute(
            f"""SELECT r.*, r.kind_snapshot AS kind,
                       r.source_ref_snapshot AS source_ref
                FROM refresh_runs r
                WHERE r.attempt_count < r.max_attempts
                  AND (r.kind_snapshot='review_scan' OR ({connector_clause})) AND (
                  (r.status='pending' AND coalesce(r.next_attempt_at, r.scheduled_for) <= ?)
                  OR (r.status='running' AND (r.lease_expires_at IS NULL OR r.lease_expires_at <= ?))
                )
                AND NOT EXISTS (
                  SELECT 1 FROM refresh_runs active
                  WHERE active.run_id != r.run_id AND active.status='running'
                    AND (active.lease_expires_at IS NULL OR active.lease_expires_at > ?)
                    AND (
                      active.job_id=r.job_id
                      OR active.source_ref_snapshot=r.source_ref_snapshot
                    )
                )
                ORDER BY coalesce(r.next_attempt_at, r.scheduled_for), r.run_id LIMIT 1""",
            (*adapter_params, timestamp, timestamp, timestamp),
        ).fetchone()
        if row is None:
            return None
        lease_token = stable_id(
            f"refresh:{worker_id}:{row['run_id']}:{timestamp}:{int(row['attempt_count']) + 1}"
        )
        updated = db.execute(
            """UPDATE refresh_runs SET status='running', attempt_count=attempt_count+1,
                 lease_owner=?, lease_token=?, lease_expires_at=?, heartbeat_at=?
               WHERE run_id=? AND attempt_count < max_attempts AND (
                 status='pending' OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
               )""",
            (worker_id[:200], lease_token, lease_until, timestamp, row["run_id"], timestamp),
        )
        if updated.rowcount != 1:
            return None
        claimed = db.execute(
            """SELECT r.*, r.kind_snapshot AS kind,
                      r.source_ref_snapshot AS source_ref
               FROM refresh_runs r WHERE r.run_id=?""",
            (row["run_id"],),
        ).fetchone()
    return _row(claimed, include_lease=True)


def finish_refresh_run(
    run_id: str,
    *,
    worker_id: str,
    lease_token: str,
    succeeded: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    timestamp = _timestamp(at)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM refresh_runs WHERE run_id=?", (run_id,)).fetchone()
        if (
            row is None
            or row["status"] != "running"
            or row["lease_owner"] != worker_id
            or row["lease_token"] != lease_token
        ):
            raise ValueError("leased refresh run was not found for this worker")
        if succeeded:
            status, next_attempt, completed, message = "completed", None, timestamp, None
        elif int(row["attempt_count"]) >= int(row["max_attempts"]):
            status, next_attempt, completed = "failed", None, timestamp
            message = sanitize_error(error or "refresh adapter failed", category="refresh_adapter_failed")
        else:
            status, completed = "pending", None
            next_attempt = _later(timestamp, _backoff(int(row["attempt_count"])))
            message = sanitize_error(error or "refresh adapter failed", category="refresh_adapter_failed")
        db.execute(
            """UPDATE refresh_runs SET status=?, next_attempt_at=?, completed_at=?,
                 lease_owner=NULL, lease_expires_at=NULL, heartbeat_at=NULL,
                 lease_token=NULL,
                 result_json=?, error=? WHERE run_id=?""",
            (
                status, next_attempt, completed, json_dumps(sanitize_receipt(result or {})),
                message, run_id,
            ),
        )
        updated = db.execute("SELECT * FROM refresh_runs WHERE run_id=?", (run_id,)).fetchone()
    return _row(updated) or {}


def renew_lease(
    kind: str,
    item_id: str,
    *,
    worker_id: str,
    lease_token: str,
    at: str | None = None,
    lease_seconds: int | None = None,
) -> bool:
    if kind not in {"refresh", "deletion"}:
        raise ValueError("lease kind must be refresh or deletion")
    timestamp = _timestamp(at)
    lease_until = _later(timestamp, lease_seconds or settings.worker_lease_seconds)
    table = "refresh_runs" if kind == "refresh" else "deletion_outbox"
    key = "run_id" if kind == "refresh" else "task_id"
    with connect() as db:
        updated = db.execute(
            f"""UPDATE {table} SET lease_expires_at=?, heartbeat_at=?
                WHERE {key}=? AND status='running' AND lease_owner=? AND lease_token=?""",
            (lease_until, timestamp, item_id, worker_id, lease_token),
        )
    return updated.rowcount == 1


def record_worker_heartbeat(
    worker_id: str,
    *,
    status: str = "running",
    detail: dict[str, Any] | None = None,
    at: str | None = None,
    starting: bool = False,
) -> dict[str, Any]:
    init_db()
    if status not in {"running", "stopping", "stopped", "failed"}:
        raise ValueError("unsupported worker status")
    timestamp = _timestamp(at)
    with connect() as db:
        db.execute(
            """INSERT INTO worker_heartbeats(
                 worker_id, worker_kind, started_at, heartbeat_at, status, detail_json, shutdown_at
               ) VALUES (?, 'scheduler', ?, ?, ?, ?, ?)
               ON CONFLICT(worker_id) DO UPDATE SET
                 worker_kind=excluded.worker_kind,
                 started_at=CASE WHEN ? THEN excluded.started_at ELSE worker_heartbeats.started_at END,
                 heartbeat_at=excluded.heartbeat_at, status=excluded.status,
                 detail_json=excluded.detail_json, shutdown_at=excluded.shutdown_at""",
            (
                worker_id[:200], timestamp, timestamp, status, json_dumps(detail or {}),
                timestamp if status in {"stopped", "failed"} else None, int(starting),
            ),
        )
        row = db.execute(
            "SELECT * FROM worker_heartbeats WHERE worker_id=?", (worker_id[:200],)
        ).fetchone()
    return _row(row) or {}


def list_worker_heartbeats(*, limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [_row(row) or {} for row in rows]


def worker_readiness(*, at: str | None = None, stale_seconds: int | None = None) -> dict[str, Any]:
    timestamp = _timestamp(at)
    cutoff = (
        datetime.fromisoformat(timestamp)
        - timedelta(seconds=max(1, stale_seconds or settings.worker_stale_seconds))
    ).isoformat()
    try:
        with connect() as db:
            rows = db.execute(
                "SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC LIMIT 200"
            ).fetchall()
        workers = [_row(row) or {} for row in rows]
    except sqlite3.Error:
        workers = []
    fresh = [
        worker for worker in workers
        if worker["status"] == "running" and worker["heartbeat_at"] >= cutoff
    ]
    selected = max(fresh, key=lambda worker: worker["heartbeat_at"], default=None)
    return {
        "ready": bool(selected),
        "required": settings.require_worker,
        "stale_after_seconds": stale_seconds or settings.worker_stale_seconds,
        "workers_seen": len(workers),
        "fresh_running_workers": len(fresh),
        "worker": (
            {
                "worker_kind": selected["worker_kind"],
                "status": selected["status"],
                "heartbeat_at": selected["heartbeat_at"],
            }
            if selected else None
        ),
    }


def release_worker_leases(worker_id: str, *, at: str | None = None) -> dict[str, int]:
    timestamp = _timestamp(at)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        refresh_failed = db.execute(
            """UPDATE refresh_runs SET status='failed', completed_at=?, next_attempt_at=NULL,
                 error=?, lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                 heartbeat_at=NULL
               WHERE status='running' AND lease_owner=? AND attempt_count >= max_attempts""",
            (
                timestamp,
                sanitize_error("retry budget exhausted", category="refresh_lease_exhausted"),
                worker_id,
            ),
        ).rowcount
        refresh = db.execute(
            """UPDATE refresh_runs SET status='pending', next_attempt_at=?, lease_owner=NULL,
                 lease_token=NULL, lease_expires_at=NULL, heartbeat_at=NULL
               WHERE status='running' AND lease_owner=? AND attempt_count < max_attempts""",
            (timestamp, worker_id),
        ).rowcount
        deletion_failed = db.execute(
            """UPDATE deletion_outbox SET status='failed', next_attempt_at=?, completed_at=?,
                 updated_at=?, last_error=?, lease_owner=NULL, lease_token=NULL,
                 lease_expires_at=NULL, heartbeat_at=NULL
               WHERE status='running' AND lease_owner=? AND attempt_count >= max_attempts""",
            (
                timestamp,
                timestamp,
                timestamp,
                sanitize_error("retry budget exhausted", category="deletion_lease_exhausted"),
                worker_id,
            ),
        ).rowcount
        deletion = db.execute(
            """UPDATE deletion_outbox SET status='pending', next_attempt_at=?, updated_at=?,
                 lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, heartbeat_at=NULL
               WHERE status='running' AND lease_owner=? AND attempt_count < max_attempts""",
            (timestamp, timestamp, worker_id),
        ).rowcount
    return {
        "refresh_runs": refresh + refresh_failed,
        "deletion_tasks": deletion + deletion_failed,
    }


def redrive_deletion_task(task_id: str, *, at: str | None = None) -> dict[str, Any]:
    """Reset one terminal source-owner deletion task for an explicit operator retry."""
    timestamp = _timestamp(at)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        updated = db.execute(
            """UPDATE deletion_outbox SET status='pending', attempt_count=0,
                 next_attempt_at=?, last_error=NULL, result_json='{}', updated_at=?,
                 completed_at=NULL, lease_owner=NULL, lease_token=NULL,
                 lease_expires_at=NULL, heartbeat_at=NULL
               WHERE task_id=? AND status='failed'""",
            (timestamp, timestamp, task_id),
        )
        if updated.rowcount != 1:
            raise ValueError("failed deletion task was not found")
        row = db.execute(
            "SELECT * FROM deletion_outbox WHERE task_id=?", (task_id,)
        ).fetchone()
    return _row(row) or {}


def update_source_health(
    source_ref: str,
    *,
    status: str,
    detail: str = "",
    at: str | None = None,
) -> dict[str, Any]:
    source_ref = credential_free_source_ref(source_ref)
    if status not in {"healthy", "degraded", "failed", "disabled", "unknown"}:
        raise ValueError("unsupported source health status")
    timestamp = _timestamp(at)
    with connect() as db:
        db.execute(
            """INSERT INTO source_health(
                 source_ref, status, detail, checked_at, last_success_at, last_failure_at
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_ref) DO UPDATE SET status=excluded.status,
                 detail=excluded.detail, checked_at=excluded.checked_at,
                 last_success_at=coalesce(excluded.last_success_at, source_health.last_success_at),
                 last_failure_at=coalesce(excluded.last_failure_at, source_health.last_failure_at)""",
            (
                source_ref, status,
                (
                    detail
                    if not detail or re.fullmatch(r"[a-z][a-z0-9_.:-]{0,199}", detail)
                    or _ERROR_RECEIPT.fullmatch(detail)
                    else sanitize_error(detail, category="source_health_detail")
                ),
                timestamp,
                timestamp if status == "healthy" else None,
                timestamp if status in {"degraded", "failed"} else None,
            ),
        )
        row = db.execute(
            "SELECT * FROM source_health WHERE source_ref=?", (source_ref,)
        ).fetchone()
    return dict(row) if row else {}


def list_source_health() -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM source_health ORDER BY source_ref").fetchall()
    return [dict(row) for row in rows]


def operations_snapshot() -> dict[str, Any]:
    init_db()
    with connect() as db:
        refresh = {
            str(row["status"]): int(row["count"])
            for row in db.execute("SELECT status, count(*) AS count FROM refresh_runs GROUP BY status")
        }
        outbox = {
            str(row["status"]): int(row["count"])
            for row in db.execute("SELECT status, count(*) AS count FROM deletion_outbox GROUP BY status")
        }
    readiness = worker_readiness()
    return {
        "worker": readiness,
        "refresh": refresh,
        "deletion_outbox": outbox,
        "deletion_tasks": list_deletion_tasks(limit=100),
        "sources": list_source_health(),
    }
