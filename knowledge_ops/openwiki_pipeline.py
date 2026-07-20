"""Governed OpenWiki/OKF ingestion.

OpenWiki is treated as an untrusted producer. This module copies a bundle into
content-addressed, read-only staging, validates every document, and requires a
separate administrator review before promotion into the canonical wiki.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

import yaml

from .authorization import AuthorizationError, Principal, authorized_write_target
from .config import settings
from .storage import connect, init_db, now, record_audit
from .wiki import upsert_documents_atomic

MAX_DOCUMENTS = 500
MAX_DOCUMENT_BYTES = 1_000_000
MAX_BUNDLE_BYTES = 50_000_000

_SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "generic_secret": re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}"),
}
_PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "us_ssn": re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    "phone": re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"),
}
_INJECTION_PATTERNS = {
    "ignore_instructions": re.compile(r"(?i)\bignore (?:all |any )?(?:previous|prior|system) instructions\b"),
    "system_prompt": re.compile(r"(?i)\b(?:reveal|print|repeat|exfiltrate) (?:the )?system prompt\b"),
    "model_control_token": re.compile(r"<\|(?:im_start|system|assistant|endoftext)\|>"),
}


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    severity: str
    detail: str


def _allowed_source(source: Path) -> Path:
    try:
        resolved = source.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError("OpenWiki bundle does not exist") from exc
    if not resolved.is_dir():
        raise ValueError("OpenWiki bundle must be a directory")
    roots = [root.expanduser().resolve() for root in settings.openwiki_import_roots]
    if not roots or not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise AuthorizationError("OpenWiki bundle is outside KNOWLEDGE_OPS_OPENWIKI_IMPORT_ROOTS")
    return resolved


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    parsed = yaml.safe_load(raw) or {}
    if not isinstance(parsed, dict):
        raise ValueError("OKF frontmatter must be a mapping")
    return parsed, text[end + 4:].lstrip("\n")


def _scan(path: str, text: str, metadata: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    if not metadata.get("type"):
        findings.append(Finding(path, "okf", "blocking", "missing required OKF type"))
    if not metadata.get("source") and not metadata.get("source_uri"):
        findings.append(Finding(path, "provenance", "review", "upstream source provenance is not declared"))
    for name, pattern in _SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding(path, "secret", "blocking", name))
    for name, pattern in _INJECTION_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding(path, "prompt_injection", "blocking", name))
    for name, pattern in _PII_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding(path, "pii", "review", name))
    return findings


def _bundle_files(source: Path) -> list[Path]:
    files: list[Path] = []
    total = 0
    for path in sorted(source.rglob("*.md")):
        if path.is_symlink():
            raise ValueError(f"OpenWiki bundle contains a symlink: {path.name}")
        resolved = path.resolve()
        if not resolved.is_relative_to(source):
            raise ValueError("OpenWiki document escapes the bundle root")
        size = resolved.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError(f"OpenWiki document exceeds {MAX_DOCUMENT_BYTES} bytes: {resolved.name}")
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("OpenWiki bundle exceeds the ingestion size limit")
        files.append(resolved)
        if len(files) > MAX_DOCUMENTS:
            raise ValueError("OpenWiki bundle exceeds the document limit")
    if not files:
        raise ValueError("OpenWiki bundle contains no Markdown documents")
    return files


def _source_digest(source: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        rel = path.relative_to(source).as_posix()
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def stage_bundle(source_path: str, producer_version: str, principal: Principal) -> dict[str, Any]:
    principal.require("write:knowledge")
    if producer_version != settings.openwiki_producer_version:
        raise ValueError(
            f"OpenWiki producer must be pinned to {settings.openwiki_producer_version}; received {producer_version}"
        )
    source = _allowed_source(Path(source_path))
    files = _bundle_files(source)
    source_digest = _source_digest(source, files)
    import_id = f"ow-{source_digest[:20]}"
    destination = settings.openwiki_staging_dir / import_id
    manifest_path = destination / "manifest.json"

    init_db()
    with connect() as db:
        existing = db.execute("SELECT * FROM openwiki_imports WHERE import_id=?", (import_id,)).fetchone()
    if existing:
        return get_import(import_id, principal)

    documents: list[dict[str, Any]] = []
    findings: list[Finding] = []
    destination.mkdir(parents=True, exist_ok=False)
    docs_dir = destination / "documents"
    docs_dir.mkdir()
    try:
        for source_file in files:
            rel = source_file.relative_to(source).as_posix()
            text = source_file.read_text(encoding="utf-8", errors="strict")
            try:
                metadata, _ = _frontmatter(text)
            except (TypeError, ValueError, yaml.YAMLError) as exc:
                metadata = {}
                findings.append(Finding(rel, "okf", "blocking", f"invalid frontmatter: {type(exc).__name__}"))
            findings.extend(_scan(rel, text, metadata))
            content_digest = hashlib.sha256(text.encode()).hexdigest()
            requested_scope = str(metadata.get("scope") or "personal")
            if requested_scope not in {"personal", "global"}:
                findings.append(Finding(rel, "acl", "blocking", "scope must be personal or global"))
                requested_scope = "personal"
            owner = str(metadata.get("owner") or principal.subject)
            target = docs_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
            target.chmod(0o440)
            documents.append({
                "path": rel,
                "sha256": content_digest,
                "bytes": len(text.encode()),
                "okf_type": metadata.get("type"),
                "scope": requested_scope,
                "owner": owner,
                "producer": "openwiki",
                "producer_version": producer_version,
                "source_uri": metadata.get("source_uri") or f"openwiki://{import_id}/{rel}",
            })
        manifest = {
            "schema": "knowledge-ops/openwiki-staging-v1",
            "import_id": import_id,
            "producer": "openwiki",
            "producer_version": producer_version,
            "source_digest": source_digest,
            "documents": documents,
            "findings": [asdict(item) for item in findings],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        manifest_path.chmod(0o440)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    state = "blocked" if any(item.severity == "blocking" for item in findings) else "awaiting_review"
    with connect() as db:
        db.execute(
            """INSERT INTO openwiki_imports(
                 import_id, producer_version, source_digest, staged_path, state,
                 document_count, findings_json, staged_by, staged_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                import_id, producer_version, source_digest, str(destination), state,
                len(documents), json.dumps([asdict(item) for item in findings]), principal.subject, now(),
            ),
        )
    record_audit(principal.subject, "openwiki.stage", import_id, state, {"documents": len(documents)})
    return get_import(import_id, principal)


def list_imports(principal: Principal) -> list[dict[str, Any]]:
    principal.require("read:knowledge")
    with connect() as db:
        rows = db.execute(
            """SELECT import_id, producer_version, source_digest, state, document_count,
                      findings_json, staged_by, staged_at, reviewed_by, reviewed_at, review_note
               FROM openwiki_imports ORDER BY staged_at DESC"""
        ).fetchall()
    visible = [dict(row) for row in rows if principal.is_admin or str(row["staged_by"]).lower() in principal.identities]
    return [_row(row) for row in visible]


def _row(row: dict[str, Any]) -> dict[str, Any]:
    row["findings"] = json.loads(row.pop("findings_json", "[]"))
    return row


def get_import(import_id: str, principal: Principal) -> dict[str, Any]:
    principal.require("read:knowledge")
    with connect() as db:
        row = db.execute("SELECT * FROM openwiki_imports WHERE import_id=?", (import_id,)).fetchone()
    if not row:
        raise ValueError("OpenWiki import not found")
    if not principal.is_admin and str(row["staged_by"]).lower() not in principal.identities:
        raise AuthorizationError("OpenWiki import is outside the caller's visibility")
    result = _row(dict(row))
    result.pop("staged_path", None)
    manifest_path = settings.openwiki_staging_dir / import_id / "manifest.json"
    result["documents"] = json.loads(manifest_path.read_text(encoding="utf-8"))["documents"]
    return result


def _verified_manifest(import_id: str) -> tuple[dict[str, Any], Path]:
    base = (settings.openwiki_staging_dir / import_id).resolve()
    if not base.is_relative_to(settings.openwiki_staging_dir.resolve()):
        raise ValueError("invalid OpenWiki import id")
    manifest_path = base / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("import_id") != import_id:
        raise ValueError("staging manifest identity mismatch")
    for doc in manifest.get("documents") or []:
        path = (base / "documents" / doc["path"]).resolve()
        if not path.is_relative_to(base / "documents"):
            raise ValueError("staging document escapes snapshot")
        if hashlib.sha256(path.read_bytes()).hexdigest() != doc["sha256"]:
            raise ValueError("staging snapshot has changed since validation")
    return manifest, base


def approve_import(import_id: str, principal: Principal, note: str, *, accept_pii: bool = False) -> dict[str, Any]:
    if not principal.is_admin:
        raise AuthorizationError("OpenWiki promotion requires knowledge-admin")
    if not note.strip():
        raise ValueError("review note is required")
    current = get_import(import_id, principal)
    if current["state"] not in {"awaiting_review", "blocked"}:
        raise ValueError("OpenWiki import has already been reviewed")
    blocking = [item for item in current["findings"] if item["severity"] == "blocking"]
    if blocking:
        raise ValueError("blocking security or OKF findings must be fixed and restaged")
    pii = [item for item in current["findings"] if item["category"] == "pii"]
    if pii and not accept_pii:
        raise ValueError("PII findings require an explicit accept_pii review decision")

    manifest, base = _verified_manifest(import_id)
    reviewed_at = now()
    publications: list[dict[str, Any]] = []
    for doc in manifest["documents"]:
        target_scope, target_owner = authorized_write_target(principal, doc["scope"], doc.get("owner"))
        body = (base / "documents" / doc["path"]).read_text(encoding="utf-8")
        publications.append({
            "path": f"openwiki/{doc['path']}",
            "md": body,
            "scope": target_scope,
            "owner": target_owner,
            "provenance": {
                "source_type": "openwiki",
                "source_uri": doc["source_uri"],
                "source_id": doc["path"],
                "content_sha256": doc["sha256"],
                "import_id": import_id,
                "reviewed_by": principal.subject,
                "reviewed_at": reviewed_at,
                "metadata": {"producer_version": manifest["producer_version"], "source_digest": manifest["source_digest"]},
            },
        })

    def finalize(db) -> None:
        updated = db.execute(
            """UPDATE openwiki_imports
               SET state='approved', reviewed_by=?, reviewed_at=?, review_note=?
               WHERE import_id=? AND state IN ('awaiting_review', 'blocked')""",
            (principal.subject, reviewed_at, note[:2000], import_id),
        )
        if updated.rowcount != 1:
            raise ValueError("OpenWiki import changed during review")

    promoted = upsert_documents_atomic(publications, finalize=finalize)
    record_audit(principal.subject, "openwiki.approve", import_id, "approved", {"documents": len(promoted), "accept_pii": accept_pii})
    return {"import_id": import_id, "state": "approved", "promoted_doc_ids": promoted}


def reject_import(import_id: str, principal: Principal, note: str) -> dict[str, Any]:
    if not principal.is_admin:
        raise AuthorizationError("OpenWiki rejection requires knowledge-admin")
    if not note.strip():
        raise ValueError("review note is required")
    current = get_import(import_id, principal)
    if current["state"] in {"approved", "rejected"}:
        raise ValueError("OpenWiki import has already been reviewed")
    with connect() as db:
        db.execute(
            "UPDATE openwiki_imports SET state='rejected', reviewed_by=?, reviewed_at=?, review_note=? WHERE import_id=?",
            (principal.subject, now(), note[:2000], import_id),
        )
    record_audit(principal.subject, "openwiki.reject", import_id, "rejected")
    return {"import_id": import_id, "state": "rejected"}
