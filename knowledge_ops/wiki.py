from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from typing import Callable

from .config import settings
from .authorization import AuthorizationError, Principal, can_read_record, sqlite_visibility_clause
from .models import Passage
from .storage import connect, init_db, json_dumps, now, record_audit
from .text import chunk_text_with_spans, lexical_score, stable_id

logger = logging.getLogger("knowledge_ops.wiki")


def _doc_id(path: str) -> str:
    return stable_id("wiki:" + path)


def normalize_timestamp(value: str | None, *, default: str | None = None) -> str:
    raw = value or default
    if not raw:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


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


def _validated_doc(
    path: str,
    md: str,
    scope: str,
    owner: str,
    *,
    valid_from: str | None = None,
    valid_until: str | None = None,
    supersedes_id: str | None = None,
    review_due_at: str | None = None,
) -> dict:
    rel, disk_path = safe_wiki_path(path)
    if scope not in {"global", "personal"}:
        raise ValueError("wiki documents require global or personal scope")
    if not owner.strip():
        raise ValueError("wiki document owner is required")
    title = next((line.lstrip("# ").strip() for line in md.splitlines() if line.strip().startswith("#")), Path(rel).stem)
    normalized_from = normalize_timestamp(valid_from, default=now())
    normalized_until = normalize_timestamp(valid_until) if valid_until else None
    normalized_review = normalize_timestamp(review_due_at) if review_due_at else None
    if normalized_until and normalized_until <= normalized_from:
        raise ValueError("valid_until must be later than valid_from")
    return {
        "path": rel,
        "disk_path": disk_path,
        "md": md,
        "scope": scope,
        "owner": owner,
        "title": title,
        "id": _doc_id(rel),
        "valid_from": normalized_from,
        "valid_until": normalized_until,
        "supersedes_id": supersedes_id.strip() if supersedes_id else None,
        "review_due_at": normalized_review,
    }


def _upsert_db(db, doc: dict, timestamp: str, provenance: dict | None = None) -> None:
    existing = db.execute("SELECT * FROM wiki_docs WHERE id=?", (doc["id"],)).fetchone()
    if existing and existing["body"] != doc["md"]:
        db.execute("INSERT INTO wiki_versions(doc_id, body, created_at) VALUES (?, ?, ?)", (doc["id"], existing["body"], timestamp))
    valid_from = doc["valid_from"]
    if existing and not doc.get("valid_from_explicit") and existing["lifecycle_state"] != "tombstoned":
        valid_from = existing["valid_from"] or existing["created_at"]
    valid_until = doc["valid_until"]
    supersedes_id = doc["supersedes_id"]
    review_due_at = doc["review_due_at"]
    if existing:
        if not doc.get("valid_until_explicit"):
            valid_until = existing["valid_until"]
        if not doc.get("supersedes_explicit"):
            supersedes_id = existing["supersedes_id"]
        if not doc.get("review_due_explicit"):
            review_due_at = existing["review_due_at"]
    if valid_until and valid_until <= valid_from:
        raise ValueError("valid_until must be later than valid_from")
    db.execute(
        """
        INSERT INTO wiki_docs(
          id, path, title, body, scope, owner, created_at, updated_at,
          valid_from, valid_until, supersedes_id, lifecycle_state,
          tombstoned_at, tombstone_reason, review_due_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body,
          scope=excluded.scope, owner=excluded.owner, updated_at=excluded.updated_at,
          valid_from=excluded.valid_from, valid_until=excluded.valid_until,
          supersedes_id=excluded.supersedes_id, lifecycle_state=excluded.lifecycle_state,
          tombstoned_at=NULL, tombstone_reason=NULL, review_due_at=excluded.review_due_at
        """,
        (
            doc["id"], doc["path"], doc["title"], doc["md"], doc["scope"],
            doc["owner"], timestamp, timestamp, valid_from, valid_until,
            supersedes_id, review_due_at,
        ),
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
            doc = _validated_doc(
                item["path"], item["md"], item["scope"], item["owner"],
                valid_from=item.get("valid_from"), valid_until=item.get("valid_until"),
                supersedes_id=item.get("supersedes_id"), review_due_at=item.get("review_due_at"),
            )
            doc["valid_from_explicit"] = bool(item.get("valid_from"))
            doc["valid_until_explicit"] = bool(item.get("valid_until"))
            doc["supersedes_explicit"] = bool(item.get("supersedes_id"))
            doc["review_due_explicit"] = bool(item.get("review_due_at"))
            if doc["path"] in seen:
                raise ValueError(f"duplicate canonical document path: {doc['path']}")
            seen.add(doc["path"])
            doc["provenance"] = item.get("provenance")
            doc["staged"] = _staged_markdown(doc["disk_path"], doc["md"])
            prepared.append(doc)
        timestamp = now()
        retired_paths: set[Path] = set()
        with connect() as db:
            for doc in prepared:
                existing_target = db.execute(
                    "SELECT * FROM wiki_docs WHERE id=?", (doc["id"],)
                ).fetchone()
                if existing_target and existing_target["lifecycle_state"] != "active":
                    raise ValueError(
                        f"{existing_target['lifecycle_state']} documents are immutable; publish a replacement"
                    )
                if existing_target and (
                    existing_target["scope"] != doc["scope"]
                    or existing_target["owner"].lower() != doc["owner"].lower()
                ):
                    raise AuthorizationError("existing document scope and owner are immutable")
                if (
                    existing_target
                    and doc["valid_from_explicit"]
                    and doc["valid_from"] != existing_target["valid_from"]
                ):
                    raise ValueError("valid_from is immutable; publish a replacement")
                if (
                    existing_target
                    and doc["supersedes_explicit"]
                    and doc["supersedes_id"] != existing_target["supersedes_id"]
                ):
                    raise ValueError("supersedes_id is immutable; publish a replacement")
                predecessor_id = doc.get("supersedes_id")
                if predecessor_id:
                    if predecessor_id == doc["id"]:
                        raise ValueError("a document cannot supersede itself")
                    predecessor = db.execute("SELECT * FROM wiki_docs WHERE id=?", (predecessor_id,)).fetchone()
                    if not predecessor:
                        raise ValueError("superseded document was not found")
                    if predecessor["lifecycle_state"] == "tombstoned":
                        raise ValueError("a tombstoned document cannot be superseded")
                    if predecessor["lifecycle_state"] == "superseded":
                        repeated = (
                            existing_target
                            and existing_target["supersedes_id"] == predecessor_id
                        )
                        if not repeated:
                            raise ValueError("the predecessor has already been superseded")
                    if predecessor["scope"] != doc["scope"] or predecessor["owner"].lower() != doc["owner"].lower():
                        raise ValueError("supersession must preserve scope and owner")
                    predecessor_from = predecessor["valid_from"] or predecessor["created_at"]
                    if doc["valid_from"] <= predecessor_from:
                        raise ValueError("replacement valid_from must be later than its predecessor")
                    other_successor = db.execute(
                        """SELECT id FROM wiki_docs WHERE supersedes_id=? AND id != ?
                           AND lifecycle_state != 'tombstoned' LIMIT 1""",
                        (predecessor_id, doc["id"]),
                    ).fetchone()
                    if other_successor:
                        raise ValueError("the predecessor already has another successor")
                    db.execute(
                        """UPDATE wiki_docs SET lifecycle_state='superseded', valid_until=?, updated_at=?
                           WHERE id=?""",
                        (doc["valid_from"], timestamp, predecessor_id),
                    )
                    db.execute("DELETE FROM wiki_fts WHERE doc_id=?", (predecessor_id,))
                    predecessor_path = safe_wiki_path(predecessor["path"])[1]
                    db.execute(
                        "DELETE FROM file_fts WHERE chunk_id IN (SELECT id FROM file_chunks WHERE path=?)",
                        (str(predecessor_path),),
                    )
                    db.execute("DELETE FROM file_chunks WHERE path=?", (str(predecessor_path),))
                    retired_paths.add(predecessor_path)
                db.execute(
                    "DELETE FROM file_fts WHERE chunk_id IN (SELECT id FROM file_chunks WHERE path=?)",
                    (str(doc["disk_path"]),),
                )
                db.execute("DELETE FROM file_chunks WHERE path=?", (str(doc["disk_path"]),))
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
        for retired_path in retired_paths:
            try:
                retired_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("retired markdown projection cleanup failed for %s", retired_path)
    finally:
        for doc in prepared:
            doc.get("staged", Path("/nonexistent")).unlink(missing_ok=True)
    return [doc["id"] for doc in prepared]


def upsert_doc(
    path: str,
    md: str,
    scope: str = "global",
    owner: str = "magic-unicorn",
    *,
    valid_from: str | None = None,
    valid_until: str | None = None,
    supersedes_id: str | None = None,
    review_due_at: str | None = None,
) -> str:
    return upsert_documents_atomic([{
        "path": path, "md": md, "scope": scope, "owner": owner,
        "valid_from": valid_from, "valid_until": valid_until,
        "supersedes_id": supersedes_id, "review_due_at": review_due_at,
    }])[0]


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
        rows = db.execute("SELECT path, body, lifecycle_state FROM wiki_docs").fetchall()
    count = 0
    for row in rows:
        _, disk_path = safe_wiki_path(row["path"])
        try:
            if row["lifecycle_state"] != "active":
                if disk_path.exists():
                    disk_path.unlink()
                    count += 1
                continue
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
                      d.valid_from, d.valid_until, d.supersedes_id, d.lifecycle_state,
                      d.tombstoned_at, d.review_due_at,
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


def search_wiki(
    query: str,
    k: int = 5,
    scope: str | None = None,
    *,
    principal: Principal,
    as_of: str | None = None,
) -> list[Passage]:
    init_db()
    effective_at = normalize_timestamp(as_of, default=now())
    where, params = sqlite_visibility_clause(principal, scope)
    with connect() as db:
        rows = db.execute(
            f"""SELECT * FROM wiki_docs WHERE ({where})
                AND lifecycle_state != 'tombstoned'
                AND coalesce(valid_from, created_at) <= ?
                AND (valid_until IS NULL OR valid_until > ?)""",
            (*params, effective_at, effective_at),
        ).fetchall()
        provenance = {
            row["doc_id"]: dict(row)
            for row in db.execute("SELECT * FROM wiki_provenance")
        }
    hits: list[Passage] = []
    for r in rows:
        chunks = chunk_text_with_spans(r["body"])
        for idx, (chunk, span_start, span_end) in enumerate(chunks):
            score = lexical_score(query, chunk, r["title"])
            if score > 0:
                hits.append(Passage(
                    source="wiki",
                    source_id=f"{r['id']}:{idx}:{stable_id(r['body'])}",
                    title=r["title"],
                    text=chunk,
                    score=score,
                    scope=r["scope"],
                    owner=r["owner"],
                    uri=str(settings.wiki_dir / r["path"]),
                    metadata={
                        "path": r["path"], "document_id": r["id"], "chunk_index": idx,
                        "source_span": {
                            "start": span_start, "end": span_end, "unit": "unicode_codepoint",
                        },
                        "valid_from": r["valid_from"], "valid_until": r["valid_until"],
                        "lifecycle_state": r["lifecycle_state"], "provenance": provenance.get(r["id"]),
                    },
                ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


def _deletion_result(row: dict, projections: dict) -> dict:
    public = {key: value for key, value in projections.items() if not key.startswith("_")}
    complete = all(value in {"success", "not_applicable"} for value in public.values())
    result = {key: value for key, value in row.items() if key != "projections_json"}
    result.update({"projections": public, "complete": complete})
    return result


def _reconcile_deletion(deletion_id: str) -> dict:
    """Retry non-transactional deletion projections from a durable receipt."""
    with connect() as db:
        event_row = db.execute(
            "SELECT * FROM deletion_events WHERE deletion_id=?", (deletion_id,)
        ).fetchone()
    if not event_row:
        raise ValueError("deletion receipt was not found")
    event = dict(event_row)
    projections = json.loads(event.pop("projections_json", "{}"))

    disk_path = safe_wiki_path(event["path"])[1]
    with connect() as db:
        db.execute(
            "DELETE FROM file_fts WHERE chunk_id IN (SELECT id FROM file_chunks WHERE path=?)",
            (str(disk_path),),
        )
        db.execute("DELETE FROM file_chunks WHERE path=?", (str(disk_path),))
        db.execute("DELETE FROM wiki_fts WHERE doc_id=?", (event["doc_id"],))
        db.execute("DELETE FROM wiki_versions WHERE doc_id=?", (event["doc_id"],))
        db.execute("DELETE FROM wiki_provenance WHERE doc_id=?", (event["doc_id"],))
        db.execute(
            "UPDATE wiki_docs SET title='[deleted]', body='', lifecycle_state='tombstoned' WHERE id=?",
            (event["doc_id"],),
        )
    projections.update({
        "canonical_body": "success", "wiki_fts": "success",
        "wiki_versions": "success", "file_index": "success",
    })

    if projections.get("markdown") in {"pending", "success"}:
        try:
            disk_path.unlink(missing_ok=True)
            projections["markdown"] = "success"
        except OSError:
            logger.exception("markdown deletion remains pending for %s", event["path"])
            projections["markdown"] = "pending"

    if projections.get("openwiki_staging") in {"pending", "success"}:
        source_id = str(projections.get("_openwiki_source_id") or "")
        try:
            from .openwiki_pipeline import purge_staging_references

            outcome = purge_staging_references(source_id)
            projections["_openwiki_snapshots_purged"] = outcome["snapshots_purged"]
            projections["openwiki_staging"] = "success"
        except (OSError, ValueError, json.JSONDecodeError):
            logger.exception("OpenWiki deletion remains pending for %s", source_id)
            projections["openwiki_staging"] = "pending"

    with connect() as db:
        db.execute(
            "UPDATE deletion_events SET projections_json=? WHERE deletion_id=?",
            (json_dumps(projections), deletion_id),
        )
    return _deletion_result(event, projections)


def reconcile_pending_deletions() -> dict[str, int]:
    """Retry projection cleanup for every incomplete tombstone at startup."""
    init_db()
    with connect() as db:
        deletion_ids = [str(row["deletion_id"]) for row in db.execute(
            "SELECT deletion_id FROM deletion_events ORDER BY deleted_at"
        )]
    completed = pending = 0
    for deletion_id in deletion_ids:
        result = _reconcile_deletion(deletion_id)
        if result["complete"]:
            completed += 1
        else:
            pending += 1
    return {"completed": completed, "pending": pending}


def tombstone_doc(doc_id_or_path: str, *, principal: Principal, reason: str) -> dict:
    """Purge retrievable content and durably converge every local projection."""
    principal.require("write:knowledge")
    if not reason.strip():
        raise ValueError("deletion reason is required")
    init_db()
    timestamp = now()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM wiki_docs WHERE id=? OR path=?", (doc_id_or_path, doc_id_or_path)
        ).fetchone()
        if not row:
            raise ValueError("document was not found")
        doc = dict(row)
        if not can_read_record(principal, doc):
            raise AuthorizationError("document is outside the caller's visibility")
        if doc["scope"] == "global" and not principal.is_admin:
            raise AuthorizationError("global document deletion requires knowledge-admin")
        if doc["scope"] == "personal" and not principal.is_admin and doc["owner"].lower() not in principal.identities:
            raise AuthorizationError("personal document deletion requires its owner")
        existing_event = db.execute(
            "SELECT * FROM deletion_events WHERE doc_id=? ORDER BY deleted_at DESC LIMIT 1", (doc["id"],)
        ).fetchone()
        if doc["lifecycle_state"] == "tombstoned" and existing_event:
            deletion_id = str(existing_event["deletion_id"])
        else:
            provenance = db.execute(
                "SELECT * FROM wiki_provenance WHERE doc_id=?", (doc["id"],)
            ).fetchone()
            disk_path = safe_wiki_path(doc["path"])[1]
            projection_path = str(disk_path)
            file_count = db.execute(
                "SELECT count(*) FROM file_chunks WHERE path=?", (projection_path,)
            ).fetchone()[0]
            db.execute(
                "DELETE FROM file_fts WHERE chunk_id IN (SELECT id FROM file_chunks WHERE path=?)",
                (projection_path,),
            )
            db.execute("DELETE FROM file_chunks WHERE path=?", (projection_path,))
            db.execute("DELETE FROM wiki_fts WHERE doc_id=?", (doc["id"],))
            db.execute("DELETE FROM wiki_versions WHERE doc_id=?", (doc["id"],))
            db.execute("DELETE FROM wiki_provenance WHERE doc_id=?", (doc["id"],))
            closed_at = min(timestamp, doc["valid_until"]) if doc.get("valid_until") else timestamp
            db.execute(
                """UPDATE wiki_docs SET title='[deleted]', body='', lifecycle_state='tombstoned', valid_until=?,
                   tombstoned_at=?, tombstone_reason=?, updated_at=? WHERE id=?""",
                (closed_at, timestamp, reason[:1000], timestamp, doc["id"]),
            )
            source_id = ""
            if provenance and provenance["source_type"] == "openwiki":
                source_id = str(provenance["source_id"])
            elif doc["path"].startswith("openwiki/"):
                source_id = doc["path"][len("openwiki/"):]
            projections = {
                "canonical_body": "success",
                "wiki_fts": "success",
                "wiki_versions": "success",
                "file_index": "success",
                "markdown": "pending",
                "openwiki_staging": "pending" if source_id else "not_applicable",
                "_file_chunks_deleted": int(file_count),
                "_openwiki_source_id": source_id,
            }
            deletion_id = stable_id(f"delete:{doc['id']}:{timestamp}")
            db.execute(
                """INSERT INTO deletion_events(
                     deletion_id, doc_id, path, actor, reason, deleted_at, projections_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    deletion_id, doc["id"], doc["path"], principal.subject,
                    reason[:1000], timestamp, json_dumps(projections),
                ),
            )

    result = _reconcile_deletion(deletion_id)
    record_audit(
        principal.subject, "wiki.tombstone", doc["id"],
        "success" if result["complete"] else "pending",
        {"path": doc["path"], "reason": reason[:200]},
    )
    return result
