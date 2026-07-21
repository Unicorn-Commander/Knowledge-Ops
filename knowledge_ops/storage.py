from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Iterable, Iterator

from .config import settings
from .models import utcnow


SCHEMA_VERSION = 3


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split(None, 1)[0]
    if name not in _columns(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _migrate_v1(db: sqlite3.Connection) -> None:
    for definition in (
        "valid_from TEXT",
        "valid_until TEXT",
        "supersedes_id TEXT",
        "lifecycle_state TEXT NOT NULL DEFAULT 'active'",
        "tombstoned_at TEXT",
        "tombstone_reason TEXT",
        "review_due_at TEXT",
    ):
        _add_column(db, "wiki_docs", definition)
    for definition in (
        "span_start INTEGER NOT NULL DEFAULT 0",
        "span_end INTEGER NOT NULL DEFAULT 0",
    ):
        _add_column(db, "file_chunks", definition)
    _add_column(db, "openwiki_imports", "deleted_document_count INTEGER NOT NULL DEFAULT 0")

    db.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_wiki_docs_lifecycle
          ON wiki_docs(lifecycle_state, valid_from, valid_until);
        CREATE INDEX IF NOT EXISTS idx_wiki_docs_review_due
          ON wiki_docs(review_due_at, lifecycle_state);

        CREATE TABLE IF NOT EXISTS review_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          resource_type TEXT NOT NULL,
          resource_id TEXT NOT NULL,
          dedupe_key TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'open',
          severity TEXT NOT NULL DEFAULT 'review',
          reason TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}',
          detected_at TEXT NOT NULL,
          due_at TEXT,
          resolved_at TEXT,
          resolved_by TEXT,
          resolution_note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_review_items_status
          ON review_items(status, kind, detected_at);

        CREATE TABLE IF NOT EXISTS refresh_jobs (
          job_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          interval_seconds INTEGER NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS refresh_runs (
          run_id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL,
          idempotency_key TEXT NOT NULL,
          status TEXT NOT NULL,
          scheduled_for TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          actor TEXT NOT NULL,
          result_json TEXT NOT NULL DEFAULT '{}',
          error TEXT,
          UNIQUE(job_id, idempotency_key),
          FOREIGN KEY(job_id) REFERENCES refresh_jobs(job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_refresh_runs_job_time
          ON refresh_runs(job_id, scheduled_for DESC);

        CREATE TABLE IF NOT EXISTS deletion_events (
          deletion_id TEXT PRIMARY KEY,
          doc_id TEXT NOT NULL,
          path TEXT NOT NULL,
          actor TEXT NOT NULL,
          reason TEXT NOT NULL,
          deleted_at TEXT NOT NULL,
          projections_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_deletion_events_doc
          ON deletion_events(doc_id, deleted_at DESC);
        """
    )
    db.execute(
        "UPDATE wiki_docs SET valid_from=created_at WHERE valid_from IS NULL OR valid_from=''"
    )


def _migrate_v2(db: sqlite3.Connection) -> None:
    for definition in (
        "attempt_count INTEGER NOT NULL DEFAULT 0",
        "max_attempts INTEGER NOT NULL DEFAULT 5",
        "next_attempt_at TEXT",
        "lease_owner TEXT",
        "lease_token TEXT",
        "lease_expires_at TEXT",
        "heartbeat_at TEXT",
    ):
        _add_column(db, "refresh_runs", definition)

    db.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_refresh_runs_dispatch
          ON refresh_runs(status, next_attempt_at, lease_expires_at, scheduled_for);

        CREATE TABLE IF NOT EXISTS worker_heartbeats (
          worker_id TEXT PRIMARY KEY,
          worker_kind TEXT NOT NULL,
          started_at TEXT NOT NULL,
          heartbeat_at TEXT NOT NULL,
          status TEXT NOT NULL,
          detail_json TEXT NOT NULL DEFAULT '{}',
          shutdown_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_status
          ON worker_heartbeats(status, heartbeat_at DESC);

        CREATE TABLE IF NOT EXISTS deletion_outbox (
          task_id TEXT PRIMARY KEY,
          deletion_id TEXT NOT NULL,
          adapter TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          max_attempts INTEGER NOT NULL DEFAULT 5,
          next_attempt_at TEXT NOT NULL,
          lease_owner TEXT,
          lease_token TEXT,
          lease_expires_at TEXT,
          heartbeat_at TEXT,
          last_error TEXT,
          result_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          completed_at TEXT,
          UNIQUE(deletion_id, adapter, source_ref),
          FOREIGN KEY(deletion_id) REFERENCES deletion_events(deletion_id)
        );
        CREATE INDEX IF NOT EXISTS idx_deletion_outbox_dispatch
          ON deletion_outbox(status, next_attempt_at, lease_expires_at);
        CREATE INDEX IF NOT EXISTS idx_deletion_outbox_deletion
          ON deletion_outbox(deletion_id, created_at);

        CREATE TABLE IF NOT EXISTS source_health (
          source_ref TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          detail TEXT NOT NULL DEFAULT '',
          checked_at TEXT NOT NULL,
          last_success_at TEXT,
          last_failure_at TEXT
        );
        """
    )
    db.execute(
        """UPDATE refresh_runs
           SET next_attempt_at=coalesce(scheduled_for, started_at)
           WHERE status='pending' AND next_attempt_at IS NULL"""
    )


def _migrate_v3(db: sqlite3.Connection) -> None:
    """Snapshot queued contracts and record verified-email ownership upgrades."""
    _add_column(db, "refresh_runs", "kind_snapshot TEXT")
    _add_column(db, "refresh_runs", "source_ref_snapshot TEXT")
    db.execute(
        """UPDATE refresh_runs
           SET kind_snapshot=coalesce(
                 kind_snapshot,
                 (SELECT kind FROM refresh_jobs WHERE refresh_jobs.job_id=refresh_runs.job_id)
               ),
               source_ref_snapshot=coalesce(
                 source_ref_snapshot,
                 (SELECT source_ref FROM refresh_jobs WHERE refresh_jobs.job_id=refresh_runs.job_id)
               )
           WHERE kind_snapshot IS NULL OR source_ref_snapshot IS NULL"""
    )
    missing_contract = (
        "refresh_contract_missing:Migration:"
        + hashlib.sha256(b"refresh-contract-missing-v3").hexdigest()[:16]
    )
    db.execute(
        """UPDATE refresh_runs
           SET status='failed', completed_at=coalesce(completed_at, ?),
               next_attempt_at=NULL, error=coalesce(error, ?),
               lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
               heartbeat_at=NULL
           WHERE status IN ('pending', 'running')
             AND (kind_snapshot IS NULL OR source_ref_snapshot IS NULL)""",
        (now(), missing_contract),
    )
    db.execute(
        """UPDATE refresh_runs
           SET kind_snapshot=coalesce(kind_snapshot, 'legacy-unresolved'),
               source_ref_snapshot=coalesce(source_ref_snapshot, 'legacy-unresolved')
           WHERE kind_snapshot IS NULL OR source_ref_snapshot IS NULL"""
    )
    db.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_refresh_runs_contract_dispatch
          ON refresh_runs(status, kind_snapshot, source_ref_snapshot, next_attempt_at);

        CREATE TABLE IF NOT EXISTS identity_ownership_migrations (
          issuer TEXT NOT NULL,
          subject TEXT NOT NULL,
          email_sha256 TEXT NOT NULL,
          wiki_docs_updated INTEGER NOT NULL,
          file_chunks_updated INTEGER NOT NULL,
          migrated_at TEXT NOT NULL,
          PRIMARY KEY(issuer, subject, email_sha256)
        );
        """
    )


def connect() -> sqlite3.Connection:
    settings.ensure_dirs()
    database_path = settings.db_path.expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _migration_lock() -> Iterator[None]:
    settings.ensure_dirs()
    database_path = settings.db_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = database_path.with_name(f".{database_path.name}.schema-migration.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def init_db() -> None:
    with _migration_lock():
        with connect() as db:
            db.executescript(
                """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS wiki_docs (
              id TEXT PRIMARY KEY,
              path TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              scope TEXT NOT NULL DEFAULT 'global',
              owner TEXT NOT NULL DEFAULT 'magic-unicorn',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wiki_versions (
              version_id INTEGER PRIMARY KEY AUTOINCREMENT,
              doc_id TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(doc_id UNINDEXED, title, body);
            CREATE TABLE IF NOT EXISTS wiki_provenance (
              doc_id TEXT PRIMARY KEY,
              source_type TEXT NOT NULL,
              source_uri TEXT NOT NULL,
              source_id TEXT NOT NULL,
              content_sha256 TEXT NOT NULL,
              import_id TEXT NOT NULL,
              reviewed_by TEXT NOT NULL,
              reviewed_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS file_chunks (
              id TEXT PRIMARY KEY,
              host TEXT NOT NULL,
              path TEXT NOT NULL,
              mtime REAL NOT NULL,
              sha1 TEXT NOT NULL,
              chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL,
              scope TEXT NOT NULL DEFAULT 'personal',
              owner TEXT NOT NULL DEFAULT 'aaron',
              updated_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS file_fts USING fts5(chunk_id UNINDEXED, path, text);

            CREATE TABLE IF NOT EXISTS metrics (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              value REAL NOT NULL,
              tags_json TEXT NOT NULL DEFAULT '{}',
              recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(name, recorded_at);

            CREATE TABLE IF NOT EXISTS audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              actor TEXT NOT NULL,
              action TEXT NOT NULL,
              resource TEXT NOT NULL,
              outcome TEXT NOT NULL,
              details_json TEXT NOT NULL DEFAULT '{}',
              recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_events_time ON audit_events(recorded_at);

            CREATE TABLE IF NOT EXISTS openwiki_imports (
              import_id TEXT PRIMARY KEY,
              producer_version TEXT NOT NULL,
              source_digest TEXT NOT NULL,
              staged_path TEXT NOT NULL,
              state TEXT NOT NULL,
              document_count INTEGER NOT NULL,
              findings_json TEXT NOT NULL,
              staged_by TEXT NOT NULL,
              staged_at TEXT NOT NULL,
              reviewed_by TEXT,
              reviewed_at TEXT,
              review_note TEXT
            );
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              applied_at TEXT NOT NULL
            );
                """
            )
            applied = {
                int(row["version"])
                for row in db.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                _migrate_v1(db)
                db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, now()),
                )
            if 2 not in applied:
                _migrate_v2(db)
                db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, now()),
                )
            if 3 not in applied:
                _migrate_v3(db)
                db.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (3, now()),
                )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def execute_many(sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
    with connect() as db:
        db.executemany(sql, rows)


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def now() -> str:
    return utcnow()


def record_audit(actor: str, action: str, resource: str, outcome: str, details: dict[str, Any] | None = None) -> int:
    """Append a bounded structured audit event without storing document content."""
    clean_details = {str(k)[:80]: str(v)[:500] for k, v in (details or {}).items()}
    with connect() as db:
        row = db.execute(
            "INSERT INTO audit_events(actor, action, resource, outcome, details_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (actor[:200], action[:120], resource[:500], outcome[:40], json_dumps(clean_details), now()),
        )
        return int(row.lastrowid)


def migrate_verified_email_ownership(
    *,
    issuer: str,
    subject: str,
    verified_email: str,
) -> dict[str, Any]:
    """Move native personal rows from a verified legacy email owner to OIDC ``sub``.

    The email itself is used only as a bound update value. Durable migration and
    audit receipts retain a digest, never the address.
    """
    clean_issuer = issuer.strip()
    clean_subject = subject.strip()
    clean_email = verified_email.strip().casefold()
    if not clean_issuer or len(clean_issuer) > 500:
        raise ValueError("verified ownership migration requires a bounded issuer")
    if not clean_subject or len(clean_subject) > 200:
        raise ValueError("verified ownership migration requires a bounded subject")
    if (
        len(clean_email) > 320
        or not re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,255}", clean_email)
        or any(ord(char) < 32 for char in clean_email)
    ):
        raise ValueError("verified ownership migration requires a valid email alias")

    init_db()
    email_sha256 = hashlib.sha256(clean_email.encode("utf-8")).hexdigest()
    timestamp = now()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            """SELECT wiki_docs_updated, file_chunks_updated, migrated_at
               FROM identity_ownership_migrations
               WHERE issuer=? AND subject=? AND email_sha256=?""",
            (clean_issuer, clean_subject, email_sha256),
        ).fetchone()
        if existing:
            return {
                "migrated": False,
                "email_sha256": email_sha256,
                "wiki_docs_updated": int(existing["wiki_docs_updated"]),
                "file_chunks_updated": int(existing["file_chunks_updated"]),
                "migrated_at": str(existing["migrated_at"]),
            }
        wiki_docs_updated = db.execute(
            """UPDATE wiki_docs SET owner=?
               WHERE scope='personal' AND lower(owner)=? AND lower(owner) != lower(?)""",
            (clean_subject, clean_email, clean_subject),
        ).rowcount
        file_chunks_updated = db.execute(
            """UPDATE file_chunks SET owner=?
               WHERE scope='personal' AND lower(owner)=? AND lower(owner) != lower(?)""",
            (clean_subject, clean_email, clean_subject),
        ).rowcount
        db.execute(
            """INSERT INTO identity_ownership_migrations(
                 issuer, subject, email_sha256, wiki_docs_updated,
                 file_chunks_updated, migrated_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                clean_issuer,
                clean_subject,
                email_sha256,
                wiki_docs_updated,
                file_chunks_updated,
                timestamp,
            ),
        )
        db.execute(
            """INSERT INTO audit_events(
                 actor, action, resource, outcome, details_json, recorded_at
               ) VALUES (?, 'identity.owner_migrate', ?, 'success', ?, ?)""",
            (
                clean_subject,
                f"email-sha256:{email_sha256[:16]}",
                json_dumps({
                    "file_chunks_updated": str(file_chunks_updated),
                    "wiki_docs_updated": str(wiki_docs_updated),
                }),
                timestamp,
            ),
        )
    return {
        "migrated": True,
        "email_sha256": email_sha256,
        "wiki_docs_updated": wiki_docs_updated,
        "file_chunks_updated": file_chunks_updated,
        "migrated_at": timestamp,
    }


def audit_rows(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """SELECT id, actor, action, resource, outcome, details_json, recorded_at
               FROM audit_events ORDER BY id DESC LIMIT ?""",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json", "{}"))
        result.append(item)
    return result
