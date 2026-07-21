from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from typing import Any, Iterator

from .config import settings
from .storage import SCHEMA_VERSION, connect, init_db, json_dumps

BACKUP_FORMAT_VERSION = 1
_ARCHIVE_ROOT = "knowledge-ops-backup"
MAX_BACKUP_MEMBERS = 100_000
MAX_BACKUP_UNCOMPRESSED_BYTES = 20 * 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> int:
    """Copy a tree through no-follow descriptors into a controlled snapshot."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("backup requires no-follow filesystem support")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(source, flags)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise ValueError("backup root must be a real directory, not a symbolic link") from exc
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)

    def same_entry(expected: os.stat_result, opened: os.stat_result) -> bool:
        return (
            expected.st_dev == opened.st_dev
            and expected.st_ino == opened.st_ino
            and stat.S_IFMT(expected.st_mode) == stat.S_IFMT(opened.st_mode)
        )

    def copy_directory(directory_fd: int, relative: Path) -> int:
        copied = 0
        with os.scandir(directory_fd) as entries:
            ordered = sorted(entries, key=lambda item: item.name)
            for entry in ordered:
                entry_relative = relative / entry.name
                try:
                    expected = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ValueError(
                        f"backup source entry could not be inspected safely: {entry_relative}"
                    ) from exc
                if stat.S_ISLNK(expected.st_mode):
                    raise ValueError(
                        f"backup source contains a symbolic link: {entry_relative}"
                    )
                if stat.S_ISDIR(expected.st_mode):
                    try:
                        child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
                    except OSError as exc:
                        raise ValueError(
                            f"backup source directory changed or could not be opened safely: {entry_relative}"
                        ) from exc
                    try:
                        if not same_entry(expected, os.fstat(child_fd)):
                            raise ValueError(
                                f"backup source directory changed while opening: {entry_relative}"
                            )
                        target_dir = destination / entry_relative
                        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                        target_dir.chmod(0o700)
                        copied += copy_directory(child_fd, entry_relative)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(expected.st_mode):
                    raise ValueError(
                        f"backup source contains an unsupported entry: {entry_relative}"
                    )
                try:
                    file_fd = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise ValueError(
                        f"backup source file changed or could not be opened safely: {entry_relative}"
                    ) from exc
                try:
                    opened = os.fstat(file_fd)
                    if not stat.S_ISREG(opened.st_mode) or not same_entry(expected, opened):
                        raise ValueError(
                            f"backup source file changed while opening: {entry_relative}"
                    )
                    target = destination / entry_relative
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with (
                        os.fdopen(file_fd, "rb", closefd=False) as input_stream,
                        target.open("xb") as output_stream,
                    ):
                        shutil.copyfileobj(input_stream, output_stream)
                    target.chmod(0o600)
                finally:
                    os.close(file_fd)
                copied += 1
        return copied

    try:
        return copy_directory(root_fd, Path())
    finally:
        os.close(root_fd)


def _secure_tree(root: Path) -> None:
    if not root.exists():
        return
    root.chmod(0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("restored data must not contain symbolic links")
        path.chmod(0o700 if path.is_dir() else 0o600)


def _manifest_files(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json"
    ]


def create_backup(output: Path) -> dict[str, Any]:
    """Create a checksummed archive from a transactionally consistent DB copy."""
    init_db()
    output = output.expanduser().resolve()
    protected_roots = {
        settings.data_dir.expanduser().resolve(),
        settings.wiki_dir.expanduser().resolve(),
        settings.openwiki_staging_dir.expanduser().resolve(),
    }
    database_path = settings.db_path.expanduser().resolve()
    protected_files = {
        database_path,
        Path(f"{database_path}-shm"),
        Path(f"{database_path}-wal"),
    }
    if any(output == root or output.is_relative_to(root) for root in protected_roots):
        raise ValueError("backup output must be outside the backed-up data and wiki roots")
    if output in protected_files:
        raise ValueError("backup output must not replace the live database or its sidecars")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.tmp-", dir=output.parent
    )
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    temporary_output = Path(temporary_name)
    try:
        with tempfile.TemporaryDirectory(prefix="knowledge-ops-backup-") as temporary:
            root = Path(temporary) / _ARCHIVE_ROOT
            root.mkdir()
            database_copy = root / "database.sqlite3"
            with connect() as source, sqlite3.connect(database_copy) as destination:
                source.backup(destination)
            database_copy.chmod(0o600)
            wiki_count = _copy_tree(settings.wiki_dir, root / "wiki")
            staging_count = _copy_tree(
                settings.openwiki_staging_dir, root / "openwiki" / "staging"
            )
            with sqlite3.connect(database_copy) as db:
                schema_version = int(
                    db.execute(
                        "SELECT coalesce(max(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
            manifest = {
                "format": "knowledge-ops-backup",
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "schema_version": schema_version,
                "files": _manifest_files(root),
            }
            (root / "manifest.json").write_text(
                json_dumps(manifest) + "\n", encoding="utf-8"
            )
            with tarfile.open(temporary_output, "w:gz") as archive:
                archive.add(root, arcname=_ARCHIVE_ROOT, recursive=True)
            os.replace(temporary_output, output)
            output.chmod(0o600)
    finally:
        temporary_output.unlink(missing_ok=True)
    return {
        "archive": str(output),
        "schema_version": schema_version,
        "wiki_files": wiki_count,
        "staging_files": staging_count,
        "sha256": _sha256(output),
    }


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_BACKUP_MEMBERS:
            raise ValueError("backup contains too many archive members")
        total_bytes = sum(member.size for member in members if member.isfile())
        if total_bytes > MAX_BACKUP_UNCOMPRESSED_BYTES:
            raise ValueError("backup exceeds the uncompressed size limit")
        names: set[str] = set()
        for member in members:
            relative = PurePosixPath(member.name)
            canonical_name = relative.as_posix()
            if canonical_name in names:
                raise ValueError("backup contains a duplicate archive member")
            names.add(canonical_name)
            if (
                relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != _ARCHIVE_ROOT
                or ".." in relative.parts
                or member.issym()
                or member.islnk()
                or member.isdev()
                or member.size < 0
            ):
                raise ValueError("backup contains an unsafe archive member")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("backup contains an unsupported archive member")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("backup member could not be read")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return destination / _ARCHIVE_ROOT


def _load_and_verify_manifest(root: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest is missing or invalid") from exc
    if (
        manifest.get("format") != "knowledge-ops-backup"
        or manifest.get("format_version") != BACKUP_FORMAT_VERSION
    ):
        raise ValueError("unsupported backup format")
    schema_version = manifest.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
        or schema_version > SCHEMA_VERSION
    ):
        raise ValueError("backup manifest has an unsupported schema version")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("backup manifest file inventory is invalid")
    expected_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("backup manifest file entry is invalid")
        raw_path = item.get("path")
        raw_bytes = item.get("bytes")
        raw_digest = item.get("sha256")
        if (
            not isinstance(raw_path, str)
            or isinstance(raw_bytes, bool)
            or not isinstance(raw_bytes, int)
            or raw_bytes < 0
            or not isinstance(raw_digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", raw_digest)
        ):
            raise ValueError("backup manifest file entry is invalid")
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("backup manifest contains an unsafe path")
        path = root.joinpath(*relative.parts)
        relative_name = relative.as_posix()
        if relative_name in expected_paths:
            raise ValueError("backup manifest contains a duplicate path")
        expected_paths.add(relative_name)
        if not path.is_file() or path.stat().st_size != raw_bytes:
            raise ValueError(f"backup file is missing or has the wrong size: {relative}")
        if _sha256(path) != raw_digest:
            raise ValueError(f"backup checksum failed: {relative}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json"
    }
    if actual_paths != expected_paths:
        raise ValueError("backup file set does not match its manifest")
    if "database.sqlite3" not in expected_paths:
        raise ValueError("backup database is missing")
    return manifest


def restore_backup(archive_path: Path, target_root: Path) -> dict[str, Any]:
    """Restore into an explicit empty target. Existing data is never overwritten."""
    archive_path = archive_path.expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    if not archive_path.is_file():
        raise ValueError("backup archive was not found")
    if target_root.exists() and (not target_root.is_dir() or any(target_root.iterdir())):
        raise ValueError("restore target must be an empty directory")
    target_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="knowledge-ops-restore-") as temporary:
        extracted = _extract_archive(archive_path, Path(temporary))
        manifest = _load_and_verify_manifest(extracted)
        staged_root = Path(tempfile.mkdtemp(
            prefix=f".{target_root.name}-restore-", dir=target_root.parent
        ))
        data_dir = staged_root / "data"
        wiki_dir = staged_root / "wiki"
        staging_dir = data_dir / "openwiki" / "staging"
        try:
            data_dir.mkdir(parents=True)
            wiki_dir.mkdir(parents=True)
            shutil.copy2(extracted / "database.sqlite3", data_dir / "knowledge_ops.sqlite3")
            _copy_tree(extracted / "wiki", wiki_dir)
            _copy_tree(extracted / "openwiki" / "staging", staging_dir)
            _secure_tree(staged_root)
            verification = verify_restored_root(staged_root)
            if not verification["passed"]:
                raise ValueError("restored staging failed integrity or reference validation")
            if target_root.exists():
                target_root.rmdir()
            os.replace(staged_root, target_root)
        finally:
            if staged_root.exists():
                shutil.rmtree(staged_root)
    return {
        "target": str(target_root),
        "database": str(target_root / "data" / "knowledge_ops.sqlite3"),
        "backup_schema_version": int(manifest["schema_version"]),
        "schema_version": int(verification["schema_version"]),
        "verification": verification,
    }


@contextmanager
def _configured_root(root: Path) -> Iterator[None]:
    original = (
        settings.data_dir,
        settings.db_path,
        settings.wiki_dir,
        settings.openwiki_staging_dir,
    )
    settings.data_dir = root / "data"
    settings.db_path = settings.data_dir / "knowledge_ops.sqlite3"
    settings.wiki_dir = root / "wiki"
    settings.openwiki_staging_dir = settings.data_dir / "openwiki" / "staging"
    try:
        yield
    finally:
        (
            settings.data_dir,
            settings.db_path,
            settings.wiki_dir,
            settings.openwiki_staging_dir,
        ) = original


def reindex_wiki() -> dict[str, int]:
    """Rebuild the canonical wiki FTS projection without resurrecting tombstones."""
    init_db()
    with connect() as db:
        db.execute("DELETE FROM wiki_fts")
        db.execute(
            """INSERT INTO wiki_fts(doc_id, title, body)
               SELECT id, title, body FROM wiki_docs WHERE lifecycle_state != 'tombstoned'"""
        )
        indexed = int(db.execute("SELECT count(*) FROM wiki_fts").fetchone()[0])
        active = int(db.execute(
            "SELECT count(*) FROM wiki_docs WHERE lifecycle_state != 'tombstoned'"
        ).fetchone()[0])
    return {"indexed": indexed, "eligible": active}


def verify_restored_root(root: Path) -> dict[str, Any]:
    from .wiki import reconcile_pending_deletions, sync_wiki_to_disk
    from .openwiki_pipeline import verify_staging_snapshots
    from .operations import sanitize_error

    root = root.expanduser().resolve()
    with _configured_root(root):
        init_db()
        reindex = reindex_wiki()
        repaired = sync_wiki_to_disk()
        deletions = reconcile_pending_deletions(limit=settings.worker_batch_size)
        try:
            openwiki = verify_staging_snapshots()
        except Exception as exc:
            openwiki = {
                "valid": False,
                "verified": 0,
                "error": sanitize_error(
                    exc,
                    category="openwiki_restore_verification_failed",
                    exception_type=type(exc).__name__,
                ),
            }
        with connect() as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
            schema_version = int(
                db.execute("SELECT coalesce(max(version), 0) FROM schema_migrations").fetchone()[0]
            )
            tombstone_leaks = int(db.execute(
                """SELECT count(*) FROM wiki_docs d
                   WHERE d.lifecycle_state='tombstoned' AND (
                     d.body != '' OR EXISTS(SELECT 1 FROM wiki_fts f WHERE f.doc_id=d.id)
                   )"""
            ).fetchone()[0])
            local_pending = external_pending = external_failed = 0
            for row in db.execute("SELECT projections_json FROM deletion_events"):
                projections = json.loads(row["projections_json"] or "{}")
                local_pending += sum(
                    1 for key, status in projections.items()
                    if not key.startswith("_")
                    and key != "source_owner_outbox"
                    and status not in {"success", "not_applicable"}
                )
            external_pending = int(db.execute(
                "SELECT count(*) FROM deletion_outbox WHERE status IN ('pending', 'running')"
            ).fetchone()[0])
            external_failed = int(db.execute(
                "SELECT count(*) FROM deletion_outbox WHERE status='failed'"
            ).fetchone()[0])
        passed = (
            integrity == "ok"
            and schema_version == SCHEMA_VERSION
            and reindex["indexed"] == reindex["eligible"]
            and tombstone_leaks == 0
            and local_pending == 0
            and openwiki["valid"]
        )
        _secure_tree(root)
        return {
            "passed": passed,
            "integrity": integrity,
            "schema_version": schema_version,
            "reindex": reindex,
            "markdown_repaired": repaired,
            "deletions": deletions,
            "tombstone_leaks": tombstone_leaks,
            "local_deletion_projections_pending": local_pending,
            "external_deletion_tasks_pending": external_pending,
            "external_deletion_tasks_failed": external_failed,
            "openwiki_staging": openwiki,
        }


def restore_and_verify(archive_path: Path, target_root: Path) -> dict[str, Any]:
    restored = restore_backup(archive_path, target_root)
    verification = restored.pop("verification")
    return {"restore": restored, "verification": verification}
