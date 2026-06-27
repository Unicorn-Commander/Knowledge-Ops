from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path

from .models import Passage
from .storage import connect, init_db, now
from .text import chunk_text, lexical_score, read_text_file, stable_id

TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".sql", ".toml"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build"}


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def iter_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".Trash")]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in TEXT_SUFFIXES and p.is_file():
                out.append(p)
    return out


def index_root(root: Path, scope: str = "personal", owner: str = "aaron", limit: int | None = None) -> int:
    init_db()
    host = socket.gethostname()
    files = iter_files(root)
    if limit:
        files = files[:limit]
    count = 0
    with connect() as db:
        for path in files:
            try:
                stat = path.stat()
                if stat.st_size > 2_000_000:
                    continue
                text = read_text_file(path)
            except OSError:
                continue
            digest = _sha1_text(text)
            existing = db.execute(
                "SELECT sha1 FROM file_chunks WHERE path=? LIMIT 1", (str(path),)
            ).fetchone()
            if existing and existing["sha1"] == digest:
                continue
            db.execute("DELETE FROM file_fts WHERE chunk_id IN (SELECT id FROM file_chunks WHERE path=?)", (str(path),))
            db.execute("DELETE FROM file_chunks WHERE path=?", (str(path),))
            for idx, chunk in enumerate(chunk_text(text)):
                cid = stable_id(f"file:{host}:{path}:{idx}:{digest}")
                db.execute(
                    """
                    INSERT INTO file_chunks(id, host, path, mtime, sha1, chunk_index, text, scope, owner, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (cid, host, str(path), stat.st_mtime, digest, idx, chunk, scope, owner, now()),
                )
                db.execute("INSERT INTO file_fts(chunk_id, path, text) VALUES (?, ?, ?)", (cid, str(path), chunk))
                count += 1
    return count


def find_file(query: str, k: int = 8, scope: str | None = None) -> list[Passage]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM file_chunks").fetchall()
    best_by_path: dict[str, Passage] = {}
    for r in rows:
        if scope and r["scope"] != scope and scope != "all":
            continue
        score = lexical_score(query, r["text"], r["path"])
        if score <= 0:
            continue
        hit = Passage(
            source="file_index",
            source_id=r["id"],
            title=Path(r["path"]).name,
            text=r["text"],
            score=score,
            scope=r["scope"],
            owner=r["owner"],
            uri=r["path"],
            metadata={"path": r["path"], "host": r["host"], "chunk_index": r["chunk_index"]},
        )
        prev = best_by_path.get(r["path"])
        if not prev or hit.score > prev.score:
            best_by_path[r["path"]] = hit
    return sorted(best_by_path.values(), key=lambda h: h.score, reverse=True)[:k]


def deep_read(path: str, query: str = "", k: int = 8) -> list[Passage]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM file_chunks WHERE path=? ORDER BY chunk_index", (path,)).fetchall()
    hits: list[Passage] = []
    for r in rows:
        score = lexical_score(query, r["text"], r["path"]) if query else 1.0 / (1 + r["chunk_index"])
        hits.append(Passage(
            source="file_index",
            source_id=r["id"],
            title=Path(r["path"]).name,
            text=r["text"],
            score=score,
            scope=r["scope"],
            owner=r["owner"],
            uri=r["path"],
            metadata={"path": r["path"], "host": r["host"], "chunk_index": r["chunk_index"]},
        ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
