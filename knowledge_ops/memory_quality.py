from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from .config import settings
from .operations import sanitize_error, sanitize_receipt
from .storage import connect, init_db, json_dumps, now
from .text import stable_id
from .wiki import normalize_timestamp

REVIEW_KINDS = {"stale", "conflict"}
REFRESH_KINDS = {"review_scan", "connector_refresh"}
_NEGATION = re.compile(r"\b(?:no|not|never|cannot|can't|doesn't|isn't|aren't|won't)\b", re.I)


def _review_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["details"] = json.loads(item.pop("details_json", "{}"))
    return item


def _refresh_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item.pop("lease_token", None)
    if "enabled" in item:
        item["enabled"] = bool(item["enabled"])
    if "result_json" in item:
        item["result"] = json.loads(item.pop("result_json", "{}"))
    return item


def _normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.lower()))


def _detected_items(at: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            """SELECT id, path, title, body, scope, owner, review_due_at
               FROM wiki_docs WHERE lifecycle_state='active'
               AND coalesce(valid_from, created_at) <= ?
               AND (valid_until IS NULL OR valid_until > ?)""",
            (at, at),
        )]
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["review_due_at"] and row["review_due_at"] <= at:
            items.append({
                "kind": "stale",
                "resource_type": "wiki_doc",
                "resource_id": row["id"],
                "dedupe_key": f"stale:{row['id']}:{row['review_due_at']}",
                "severity": "review",
                "reason": "Document review date has passed",
                "details": {"path": row["path"], "title": row["title"]},
                "due_at": row["review_due_at"],
            })

    by_title: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        title_key = _normalized_title(row["title"])
        if title_key:
            by_title.setdefault((title_key, row["scope"], row["owner"].lower()), []).append(row)
    for (title_key, _, _), candidates in by_title.items():
        positive = [row for row in candidates if not _NEGATION.search(row["body"])]
        negative = [row for row in candidates if _NEGATION.search(row["body"])]
        for left in positive:
            for right in negative:
                pair = sorted((left["id"], right["id"]))
                fingerprints = sorted((stable_id(left["body"]), stable_id(right["body"])))
                items.append({
                    "kind": "conflict",
                    "resource_type": "wiki_doc_pair",
                    "resource_id": ":".join(pair),
                    "dedupe_key": (
                        f"conflict:{pair[0]}:{pair[1]}:{fingerprints[0]}:{fingerprints[1]}:"
                        "title_polarity_v1"
                    ),
                    "severity": "review",
                    "reason": "Documents with the same title have opposing lexical polarity",
                    "details": {
                        "title": left["title"], "title_key": title_key,
                        "document_ids": pair, "paths": sorted((left["path"], right["path"])),
                        "method": "title_polarity_v1",
                    },
                    "due_at": None,
                })
    return items


def scan_review_queue(*, at: str | None = None) -> dict[str, Any]:
    init_db()
    detected_at = normalize_timestamp(at, default=now())
    detected = _detected_items(detected_at)
    keys = {item["dedupe_key"] for item in detected}
    with connect() as db:
        for item in detected:
            db.execute(
                """INSERT INTO review_items(
                     kind, resource_type, resource_id, dedupe_key, status, severity,
                     reason, details_json, detected_at, due_at
                   ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
                   ON CONFLICT(dedupe_key) DO UPDATE SET
                     status=CASE WHEN review_items.resolved_by='system:review-scan'
                                 THEN 'open' ELSE review_items.status END,
                     severity=excluded.severity, reason=excluded.reason,
                     details_json=excluded.details_json, detected_at=excluded.detected_at,
                     due_at=excluded.due_at,
                     resolved_at=CASE WHEN review_items.resolved_by='system:review-scan'
                                      THEN NULL ELSE review_items.resolved_at END,
                     resolved_by=CASE WHEN review_items.resolved_by='system:review-scan'
                                      THEN NULL ELSE review_items.resolved_by END,
                     resolution_note=CASE WHEN review_items.resolved_by='system:review-scan'
                                          THEN NULL ELSE review_items.resolution_note END""",
                (
                    item["kind"], item["resource_type"], item["resource_id"],
                    item["dedupe_key"], item["severity"], item["reason"],
                    json_dumps(item["details"]), detected_at, item["due_at"],
                ),
            )
        open_rows = db.execute("SELECT id, dedupe_key FROM review_items WHERE status='open'").fetchall()
        resolved = 0
        for row in open_rows:
            if row["dedupe_key"] not in keys:
                db.execute(
                    """UPDATE review_items SET status='resolved', resolved_at=?,
                       resolved_by='system:review-scan', resolution_note='Condition no longer detected'
                       WHERE id=?""",
                    (detected_at, row["id"]),
                )
                resolved += 1
    return {"detected": len(detected), "open": len(keys), "auto_resolved": resolved, "scanned_at": detected_at}


def list_review_items(*, status: str = "open", kind: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    if status not in {"open", "resolved", "all"}:
        raise ValueError("review status must be open, resolved, or all")
    if kind and kind not in REVIEW_KINDS:
        raise ValueError("unsupported review kind")
    clauses: list[str] = []
    params: list[Any] = []
    if status != "all":
        clauses.append("status=?")
        params.append(status)
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    where = " AND ".join(clauses) or "1=1"
    params.append(max(1, min(int(limit), 500)))
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM review_items WHERE {where} ORDER BY status, detected_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_review_row(row) for row in rows]


def resolve_review_item(item_id: int, *, actor: str, note: str) -> dict[str, Any]:
    if not note.strip():
        raise ValueError("resolution note is required")
    init_db()
    resolved_at = now()
    with connect() as db:
        updated = db.execute(
            """UPDATE review_items SET status='resolved', resolved_at=?, resolved_by=?,
               resolution_note=? WHERE id=? AND status='open'""",
            (resolved_at, actor[:200], note[:2000], int(item_id)),
        )
        if updated.rowcount != 1:
            raise ValueError("open review item was not found")
        row = db.execute("SELECT * FROM review_items WHERE id=?", (int(item_id),)).fetchone()
    return _review_row(row)


def upsert_refresh_job(
    job_id: str,
    *,
    kind: str,
    source_ref: str,
    interval_seconds: int,
    enabled: bool = True,
) -> dict[str, Any]:
    init_db()
    job_id = job_id.strip()
    if not job_id or len(job_id) > 120 or not re.fullmatch(r"[A-Za-z0-9._:-]+", job_id):
        raise ValueError("refresh job id must use letters, numbers, dot, colon, underscore, or dash")
    if kind not in REFRESH_KINDS:
        raise ValueError("unsupported refresh job kind")
    source_ref = source_ref.strip() or "wiki"
    if (
        len(source_ref) > 200
        or not re.fullmatch(r"[A-Za-z0-9._:/-]+", source_ref)
        or "://" in source_ref
    ):
        raise ValueError("source_ref must be a credential-free logical source identifier")
    if kind == "review_scan" and source_ref not in {"", "wiki"}:
        raise ValueError("review_scan source_ref must be wiki")
    if not 60 <= int(interval_seconds) <= 31_536_000:
        raise ValueError("refresh interval must be between 60 seconds and one year")
    timestamp = now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT INTO refresh_jobs(
                 job_id, kind, source_ref, interval_seconds, enabled, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET kind=excluded.kind,
                 source_ref=excluded.source_ref, interval_seconds=excluded.interval_seconds,
                 enabled=excluded.enabled, updated_at=excluded.updated_at""",
            (job_id, kind, source_ref, int(interval_seconds), int(enabled), timestamp, timestamp),
        )
        row = db.execute("SELECT * FROM refresh_jobs WHERE job_id=?", (job_id,)).fetchone()
    return _refresh_row(row)


def list_refresh_jobs() -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM refresh_jobs ORDER BY job_id").fetchall()
    return [_refresh_row(row) for row in rows]


def list_refresh_runs(*, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM refresh_runs ORDER BY scheduled_for DESC, run_id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [_refresh_row(row) for row in rows]


def run_refresh_job(
    job_id: str,
    *,
    actor: str,
    idempotency_key: str,
    scheduled_for: str | None = None,
) -> dict[str, Any]:
    init_db()
    idempotency_key = idempotency_key.strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("idempotency key is required and must be at most 200 characters")
    schedule = normalize_timestamp(scheduled_for, default=now())
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM refresh_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("refresh job was not found")
        run_id = "rr-" + stable_id(f"{job_id}:{idempotency_key}")[:24]
        existing = db.execute(
            "SELECT * FROM refresh_runs WHERE job_id=? AND idempotency_key=?",
            (job_id, idempotency_key),
        ).fetchone()
        if existing:
            return _refresh_row(existing)
        if job["kind"] == "review_scan":
            overlapping = db.execute(
                """SELECT 1 FROM refresh_runs
                   WHERE status='running'
                     AND (job_id=? OR source_ref_snapshot=?)
                   LIMIT 1""",
                (job_id, str(job["source_ref"])),
            ).fetchone()
            if overlapping:
                raise ValueError("an overlapping refresh run is already active")
        initial_status = "running" if job["kind"] == "review_scan" else "pending"
        attempt_count = 1 if job["kind"] == "review_scan" else 0
        inserted = db.execute(
            """INSERT OR IGNORE INTO refresh_runs(
                 run_id, job_id, idempotency_key, status, scheduled_for, started_at, actor,
                 attempt_count, max_attempts, next_attempt_at,
                 kind_snapshot, source_ref_snapshot
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, job_id, idempotency_key, initial_status, schedule, now(),
                actor[:200], attempt_count, settings.worker_max_attempts, schedule,
                str(job["kind"]), str(job["source_ref"]),
            ),
        )
        if inserted.rowcount != 1:
            raise RuntimeError("refresh run enqueue lost its serialized insert")
    if job["kind"] == "review_scan":
        try:
            result = scan_review_queue(at=schedule)
        except Exception as exc:
            with connect() as db:
                db.execute(
                    """UPDATE refresh_runs SET status='failed', completed_at=?, error=?
                       WHERE run_id=?""",
                    (now(), sanitize_error(f"{type(exc).__name__}: {exc}"), run_id),
                )
            raise
        else:
            with connect() as db:
                db.execute(
                    """UPDATE refresh_runs SET status='completed', completed_at=?, result_json=?
                       WHERE run_id=?""",
                    (now(), json_dumps(sanitize_receipt(result)), run_id),
                )
    with connect() as db:
        row = db.execute("SELECT * FROM refresh_runs WHERE run_id=?", (run_id,)).fetchone()
    return _refresh_row(row)


def complete_refresh_run(
    run_id: str,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise ValueError("refresh completion status must be completed or failed")
    if status == "failed" and not (error or "").strip():
        raise ValueError("failed refresh runs require an error summary")
    init_db()
    with connect() as db:
        updated = db.execute(
            """UPDATE refresh_runs SET status=?, completed_at=?, result_json=?, error=?,
                 lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, heartbeat_at=NULL
               WHERE run_id=? AND status IN ('pending', 'running')""",
            (
                status, now(), json_dumps(sanitize_receipt(result or {})),
                sanitize_error(error) if error else None, run_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("pending refresh run was not found")
        row = db.execute("SELECT * FROM refresh_runs WHERE run_id=?", (run_id,)).fetchone()
    return _refresh_row(row)


def enqueue_refresh_run(
    job_id: str,
    *,
    actor: str,
    idempotency_key: str,
    scheduled_for: str | None = None,
) -> dict[str, Any]:
    """Durably enqueue a run without executing it in the caller process."""
    init_db()
    idempotency_key = idempotency_key.strip()
    if not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("idempotency key is required and must be at most 200 characters")
    schedule = normalize_timestamp(scheduled_for, default=now())
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT * FROM refresh_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("refresh job was not found")
        run_id = "rr-" + stable_id(f"{job_id}:{idempotency_key}")[:24]
        db.execute(
            """INSERT OR IGNORE INTO refresh_runs(
                 run_id, job_id, idempotency_key, status, scheduled_for, started_at, actor,
                 attempt_count, max_attempts, next_attempt_at,
                 kind_snapshot, source_ref_snapshot
               ) VALUES (?, ?, ?, 'pending', ?, ?, ?, 0, ?, ?, ?, ?)""",
            (
                run_id, job_id, idempotency_key, schedule, now(), actor[:200],
                settings.worker_max_attempts, schedule, str(job["kind"]), str(job["source_ref"]),
            ),
        )
        row = db.execute(
            "SELECT * FROM refresh_runs WHERE job_id=? AND idempotency_key=?",
            (job_id, idempotency_key),
        ).fetchone()
    return _refresh_row(row)


def run_due_refresh_jobs(*, actor: str, at: str | None = None) -> list[dict[str, Any]]:
    timestamp = normalize_timestamp(at, default=now())
    moment = datetime.fromisoformat(timestamp)
    results: list[dict[str, Any]] = []
    for job in list_refresh_jobs():
        if not job["enabled"]:
            continue
        with connect() as db:
            last = db.execute(
                "SELECT scheduled_for FROM refresh_runs WHERE job_id=? ORDER BY scheduled_for DESC LIMIT 1",
                (job["job_id"],),
            ).fetchone()
        if last:
            due_at = datetime.fromisoformat(last["scheduled_for"]) + timedelta(seconds=job["interval_seconds"])
            if due_at > moment:
                continue
        else:
            seconds = int(moment.timestamp())
            due_at = datetime.fromtimestamp(
                seconds - (seconds % job["interval_seconds"]), tz=timezone.utc
            )
        scheduled = due_at.astimezone(timezone.utc).isoformat()
        results.append(run_refresh_job(
            job["job_id"], actor=actor,
            idempotency_key=f"scheduled:{scheduled}", scheduled_for=scheduled,
        ))
    return results


def enqueue_due_refresh_jobs(*, actor: str, at: str | None = None) -> list[dict[str, Any]]:
    """Schedule every due job for durable worker execution."""
    timestamp = normalize_timestamp(at, default=now())
    moment = datetime.fromisoformat(timestamp)
    results: list[dict[str, Any]] = []
    for job in list_refresh_jobs():
        if not job["enabled"]:
            continue
        with connect() as db:
            last = db.execute(
                "SELECT scheduled_for FROM refresh_runs WHERE job_id=? ORDER BY scheduled_for DESC LIMIT 1",
                (job["job_id"],),
            ).fetchone()
        if last:
            due_at = datetime.fromisoformat(last["scheduled_for"]) + timedelta(
                seconds=job["interval_seconds"]
            )
            if due_at > moment:
                continue
        else:
            seconds = int(moment.timestamp())
            due_at = datetime.fromtimestamp(
                seconds - (seconds % job["interval_seconds"]), tz=timezone.utc
            )
        scheduled = due_at.astimezone(timezone.utc).isoformat()
        results.append(enqueue_refresh_run(
            job["job_id"], actor=actor,
            idempotency_key=f"scheduled:{scheduled}", scheduled_for=scheduled,
        ))
    return results
