from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Callable

from .config import settings
from .authorization import Principal, sqlite_visibility_clause
from .models import Passage
from .storage import connect, init_db, now
from .text import chunk_text, lexical_score, stable_id

logger = logging.getLogger("knowledge_ops.wiki")


def _doc_id(path: str) -> str:
    return stable_id("wiki:" + path)


def safe_wiki_path(path: str) -> tuple[str, Path]:
    """Return a canonical relative path confined beneath ``wiki_dir``."""
    raw = path.strip().replace("\\", "/")
    candidate = Path(raw)
    if not raw or candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ValueError("wiki path must be a non-empty relative path without traversal")
    if candidate.suffix.lower() != ".md":
        candidate = candidate.with_suffix(candidate.suffix + ".md" if candidate.suffix else ".md")
    base = settings.wiki_dir.resolve()
    disk_path = (base / candidate).resolve()
    if not disk_path.is_relative_to(base):
        raise ValueError("wiki path escapes the configured wiki directory")
    return candidate.as_posix(), disk_path


def _staged_markdown(disk_path: Path, md: str) -> Path:
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{disk_path.name}.", suffix=".tmp", dir=disk_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(md)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return Path(tmp_name)


def _project_markdown(disk_path: Path, md: str) -> None:
    staged = _staged_markdown(disk_path, md)
    try:
        os.replace(staged, disk_path)
    finally:
        staged.unlink(missing_ok=True)


def _validated_doc(path: str, md: str, scope: str, owner: str) -> dict:
    rel, disk_path = safe_wiki_path(path)
    if scope not in {"global", "personal"}:
        raise ValueError("wiki documents require global or personal scope")
    if not owner.strip():
        raise ValueError("wiki document owner is required")
    title = next((line.lstrip("# ").strip() for line in md.splitlines() if line.strip().startswith("#")), Path(rel).stem)
    return {"path": rel, "disk_path": disk_path, "md": md, "scope": scope, "owner": owner, "title": title, "id": _doc_id(rel)}


def _upsert_db(db, doc: dict, timestamp: str, provenance: dict | None = None) -> None:
    existing = db.execute("SELECT body FROM wiki_docs WHERE id=?", (doc["id"],)).fetchone()
    if existing and existing["body"] != doc["md"]:
        db.execute("INSERT INTO wiki_versions(doc_id, body, created_at) VALUES (?, ?, ?)", (doc["id"], existing["body"], timestamp))
    db.execute(
        """
        INSERT INTO wiki_docs(id, path, title, body, scope, owner, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body,
          scope=excluded.scope, owner=excluded.owner, updated_at=excluded.updated_at
        """,
        (doc["id"], doc["path"], doc["title"], doc["md"], doc["scope"], doc["owner"], timestamp, timestamp),
    )
    db.execute("DELETE FROM wiki_fts WHERE doc_id=?", (doc["id"],))
    db.execute("INSERT INTO wiki_fts(doc_id, title, body) VALUES (?, ?, ?)", (doc["id"], doc["title"], doc["md"]))
    if provenance:
        _set_provenance_db(db, doc["id"], provenance)
    else:
        # A manual edit is not still a reviewed OpenWiki artifact.
        db.execute("DELETE FROM wiki_provenance WHERE doc_id=?", (doc["id"],))


def _set_provenance_db(db, doc_id: str, provenance: dict) -> None:
    db.execute(
        """INSERT INTO wiki_provenance(
             doc_id, source_type, source_uri, source_id, content_sha256,
             import_id, reviewed_by, reviewed_at, metadata_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(doc_id) DO UPDATE SET
             source_type=excluded.source_type, source_uri=excluded.source_uri,
             source_id=excluded.source_id, content_sha256=excluded.content_sha256,
             import_id=excluded.import_id, reviewed_by=excluded.reviewed_by,
             reviewed_at=excluded.reviewed_at, metadata_json=excluded.metadata_json""",
        (
            doc_id, provenance["source_type"], provenance["source_uri"], provenance["source_id"],
            provenance["content_sha256"], provenance["import_id"], provenance["reviewed_by"],
            provenance["reviewed_at"], json.dumps(provenance.get("metadata") or {}, sort_keys=True),
        ),
    )


def upsert_documents_atomic(
    documents: list[dict],
    *,
    finalize: Callable[[object], None] | None = None,
) -> list[str]:
    """Atomically publish docs + provenance in SQLite, then repairable projections."""
    init_db()
    prepared: list[dict] = []
    seen: set[str] = set()
    try:
        for item in documents:
            doc = _validated_doc(item["path"], item["md"], item["scope"], item["owner"])
            if doc["path"] in seen:
                raise ValueError(f"duplicate canonical document path: {doc['path']}")
            seen.add(doc["path"])
            doc["provenance"] = item.get("provenance")
            doc["staged"] = _staged_markdown(doc["disk_path"], doc["md"])
            prepared.append(doc)
        timestamp = now()
        with connect() as db:
            for doc in prepared:
                _upsert_db(db, doc, timestamp, doc.get("provenance"))
            if finalize:
                finalize(db)
        for doc in prepared:
            try:
                os.replace(doc["staged"], doc["disk_path"])
            except OSError:
                # SQLite is canonical and already committed. Startup reconciliation
                # repairs this projection without exposing a partial DB publication.
                logger.exception("markdown projection failed for %s", doc["path"])
    finally:
        for doc in prepared:
            doc.get("staged", Path("/nonexistent")).unlink(missing_ok=True)
    return [doc["id"] for doc in prepared]


def upsert_doc(path: str, md: str, scope: str = "global", owner: str = "magic-unicorn") -> str:
    return upsert_documents_atomic([{"path": path, "md": md, "scope": scope, "owner": owner}])[0]


def ingest_marketing_docs() -> list[str]:
    docs = [
        (Path.home() / "Documents" / "Magic-Unicorn-Marketing-Bible.md", "marketing/Magic-Unicorn-Marketing-Bible.md"),
        (Path.home() / "Documents" / "Magic-Unicorn-Web-Property-Map-and-Agent-Topology.md", "marketing/Magic-Unicorn-Web-Property-Map-and-Agent-Topology.md"),
    ]
    ids: list[str] = []
    for src, dest in docs:
        ids.append(upsert_doc(dest, src.read_text(encoding="utf-8", errors="ignore"), scope="global", owner="magic-unicorn"))
    return ids


def seed_wiki_from_disk() -> list[str]:
    """Ingest any ``*.md`` already present under ``wiki_dir`` into the DB.

    Idempotent and offline: used on service startup so a fresh data volume
    (empty SQLite) self-seeds from the markdown that ships in the repo, making
    grounded answers work without first running an ingest command.
    """
    init_db()
    base = settings.wiki_dir
    if not base.exists():
        return []
    with connect() as db:
        known = {r["path"] for r in db.execute("SELECT path FROM wiki_docs")}
    ids: list[str] = []
    for md_path in sorted(base.rglob("*.md")):
        rel = md_path.relative_to(base).as_posix()
        if rel in known:
            continue
        try:
            body = md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        ids.append(upsert_doc(rel, body, scope="global", owner="magic-unicorn"))
    return ids


def sync_wiki_to_disk() -> int:
    """Rebuild Markdown projections from canonical SQLite rows after a crash."""
    init_db()
    with connect() as db:
        rows = db.execute("SELECT path, body FROM wiki_docs").fetchall()
    count = 0
    for row in rows:
        _, disk_path = safe_wiki_path(row["path"])
        try:
            if disk_path.exists() and disk_path.read_text(encoding="utf-8") == row["body"]:
                continue
            _project_markdown(disk_path, row["body"])
            count += 1
        except OSError:
            continue
    return count


def set_provenance(
    doc_id: str,
    *,
    source_type: str,
    source_uri: str,
    source_id: str,
    content_sha256: str,
    import_id: str,
    reviewed_by: str,
    reviewed_at: str,
    metadata: dict | None = None,
) -> None:
    with connect() as db:
        _set_provenance_db(db, doc_id, {
            "source_type": source_type, "source_uri": source_uri, "source_id": source_id,
            "content_sha256": content_sha256, "import_id": import_id,
            "reviewed_by": reviewed_by, "reviewed_at": reviewed_at, "metadata": metadata or {},
        })


def get_doc(doc_id_or_path: str) -> dict | None:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM wiki_docs WHERE id=? OR path=?", (doc_id_or_path, doc_id_or_path)
        ).fetchone()
        if not row:
            return None
        doc = dict(row)
        provenance = db.execute("SELECT * FROM wiki_provenance WHERE doc_id=?", (doc["id"],)).fetchone()
        if provenance:
            doc["provenance"] = dict(provenance)
            doc["provenance"]["metadata"] = json.loads(doc["provenance"].pop("metadata_json", "{}"))
        return doc


def list_docs() -> list[dict]:
    init_db()
    with connect() as db:
        return [dict(r) for r in db.execute(
            """SELECT d.id, d.path, d.title, d.scope, d.owner, d.updated_at,
                      p.source_type AS provenance_source
               FROM wiki_docs d LEFT JOIN wiki_provenance p ON p.doc_id=d.id ORDER BY d.path"""
        )]


def history(doc_id_or_path: str) -> list[dict]:
    doc = get_doc(doc_id_or_path)
    if not doc:
        return []
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT version_id, doc_id, created_at, length(body) AS bytes FROM wiki_versions WHERE doc_id=? ORDER BY version_id DESC",
            (doc["id"],),
        )]


def search_wiki(query: str, k: int = 5, scope: str | None = None, *, principal: Principal) -> list[Passage]:
    init_db()
    where, params = sqlite_visibility_clause(principal, scope)
    with connect() as db:
        rows = db.execute(f"SELECT * FROM wiki_docs WHERE {where}", params).fetchall()
        provenance = {
            row["doc_id"]: dict(row)
            for row in db.execute("SELECT * FROM wiki_provenance")
        }
    hits: list[Passage] = []
    for r in rows:
        chunks = chunk_text(r["body"])
        for idx, chunk in enumerate(chunks):
            score = lexical_score(query, chunk, r["title"])
            if score > 0:
                hits.append(Passage(
                    source="wiki",
                    source_id=f"{r['id']}:{idx}",
                    title=r["title"],
                    text=chunk,
                    score=score,
                    scope=r["scope"],
                    owner=r["owner"],
                    uri=str(settings.wiki_dir / r["path"]),
                    metadata={"path": r["path"], "provenance": provenance.get(r["id"])},
                ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
