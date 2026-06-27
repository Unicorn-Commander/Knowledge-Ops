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
