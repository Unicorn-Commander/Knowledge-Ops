from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .models import utcnow


def connect() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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
            """
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
