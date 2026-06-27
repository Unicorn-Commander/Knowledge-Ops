from __future__ import annotations

from pathlib import Path

from .config import settings
from .models import Passage
from .storage import connect, init_db, now
from .text import chunk_text, lexical_score, stable_id


def _doc_id(path: str) -> str:
    return stable_id("wiki:" + path)


def upsert_doc(path: str, md: str, scope: str = "global", owner: str = "magic-unicorn") -> str:
    init_db()
    rel = path.strip().lstrip("/")
    if not rel.endswith(".md"):
        rel += ".md"
    title = next((line.lstrip("# ").strip() for line in md.splitlines() if line.strip().startswith("#")), Path(rel).stem)
    did = _doc_id(rel)
    ts = now()
    disk_path = settings.wiki_dir / rel
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_text(md, encoding="utf-8")
    with connect() as db:
        existing = db.execute("SELECT body FROM wiki_docs WHERE id=?", (did,)).fetchone()
        if existing:
            db.execute("INSERT INTO wiki_versions(doc_id, body, created_at) VALUES (?, ?, ?)", (did, existing["body"], ts))
        db.execute(
            """
            INSERT INTO wiki_docs(id, path, title, body, scope, owner, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body,
              scope=excluded.scope, owner=excluded.owner, updated_at=excluded.updated_at
            """,
            (did, rel, title, md, scope, owner, ts, ts),
        )
        db.execute("DELETE FROM wiki_fts WHERE doc_id=?", (did,))
        db.execute("INSERT INTO wiki_fts(doc_id, title, body) VALUES (?, ?, ?)", (did, title, md))
    return did


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


def get_doc(doc_id_or_path: str) -> dict | None:
    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT * FROM wiki_docs WHERE id=? OR path=?", (doc_id_or_path, doc_id_or_path)
        ).fetchone()
        return dict(row) if row else None


def list_docs() -> list[dict]:
    init_db()
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT id, path, title, scope, owner, updated_at FROM wiki_docs ORDER BY path")]


def history(doc_id_or_path: str) -> list[dict]:
    doc = get_doc(doc_id_or_path)
    if not doc:
        return []
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT version_id, doc_id, created_at, length(body) AS bytes FROM wiki_versions WHERE doc_id=? ORDER BY version_id DESC",
            (doc["id"],),
        )]


def search_wiki(query: str, k: int = 5, scope: str | None = None) -> list[Passage]:
    init_db()
    with connect() as db:
        rows = db.execute("SELECT * FROM wiki_docs").fetchall()
    hits: list[Passage] = []
    for r in rows:
        if scope and r["scope"] != scope and scope != "all":
            continue
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
                    metadata={"path": r["path"]},
                ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
