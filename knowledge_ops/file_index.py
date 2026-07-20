from __future__ import annotations

import hashlib
import os
import socket
from pathlib import Path

from .config import settings
from .authorization import Principal, sqlite_visibility_clause
from .models import Passage
from .storage import connect, init_db, now
from .text import chunk_text_with_spans, lexical_score, read_text_file, stable_id

TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".sql", ".toml"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build"}
SENSITIVE_NAMES = {"credentials.json", "secrets.json", "id_rsa", "id_ed25519"}


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def allowed_index_root(root: Path) -> Path:
    """Resolve an indexing root and prove it is inside a configured allowlist."""
    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("index root does not exist") from exc
    if not resolved.is_dir():
        raise ValueError("index root must be a directory")
    allowed = [item.expanduser().resolve() for item in settings.default_file_roots]
    if not any(resolved == base or resolved.is_relative_to(base) for base in allowed):
        raise ValueError("index root is outside KNOWLEDGE_OPS_FILE_ROOT allowlist")
    return resolved


def iter_files(root: Path) -> list[Path]:
    root = allowed_index_root(root)
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".Trash") and not (Path(dirpath) / d).is_symlink()
        ]
        for name in filenames:
            p = Path(dirpath) / name
            if name.lower() in SENSITIVE_NAMES or "secret" in name.lower() or "credential" in name.lower():
                continue
            if p.is_symlink() or p.suffix.lower() not in TEXT_SUFFIXES or not p.is_file():
                continue
            resolved = p.resolve()
            if resolved.is_relative_to(root):
                out.append(resolved)
    return out


def index_root(root: Path, scope: str = "personal", owner: str = "", limit: int | None = None) -> int:
    init_db()
    if scope not in {"global", "personal"}:
        raise ValueError("file index scope must be global or personal")
    owner = owner.strip() or settings.default_personal_owner
    if not owner:
        raise ValueError("file index owner must be derived from an authenticated identity")
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
            for idx, (chunk, span_start, span_end) in enumerate(chunk_text_with_spans(text)):
                cid = stable_id(f"file:{host}:{path}:{idx}:{digest}")
                db.execute(
                    """
                    INSERT INTO file_chunks(
                      id, host, path, mtime, sha1, chunk_index, text, scope, owner,
                      updated_at, span_start, span_end
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cid, host, str(path), stat.st_mtime, digest, idx, chunk, scope,
                        owner, now(), span_start, span_end,
                    ),
                )
                db.execute("INSERT INTO file_fts(chunk_id, path, text) VALUES (?, ?, ?)", (cid, str(path), chunk))
                count += 1
    return count


def find_file(query: str, k: int = 8, scope: str | None = None, *, principal: Principal) -> list[Passage]:
    init_db()
    where, params = sqlite_visibility_clause(principal, scope)
    with connect() as db:
        rows = db.execute(f"SELECT * FROM file_chunks WHERE {where}", params).fetchall()
    best_by_path: dict[str, Passage] = {}
    for r in rows:
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
            metadata={
                "path": r["path"], "host": r["host"], "chunk_index": r["chunk_index"],
                "source_span": {
                    "start": r["span_start"], "end": r["span_end"],
                    "unit": "unicode_codepoint",
                },
            },
        )
        prev = best_by_path.get(r["path"])
        if not prev or hit.score > prev.score:
            best_by_path[r["path"]] = hit
    return sorted(best_by_path.values(), key=lambda h: h.score, reverse=True)[:k]


def deep_read(path: str, query: str = "", k: int = 8, *, principal: Principal) -> list[Passage]:
    init_db()
    where, params = sqlite_visibility_clause(principal, None)
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM file_chunks WHERE path=? AND ({where}) ORDER BY chunk_index",
            (path, *params),
        ).fetchall()
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
            metadata={
                "path": r["path"], "host": r["host"], "chunk_index": r["chunk_index"],
                "source_span": {
                    "start": r["span_start"], "end": r["span_end"],
                    "unit": "unicode_codepoint",
                },
            },
        ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
