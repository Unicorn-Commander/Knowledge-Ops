"""Plan and apply the receiver-side binding for legacy wiki rows.

The migration that introduced visibility columns intentionally did not guess a
workspace. This module keeps that choice explicit: a caller supplies the
verified workspace identifier, and legal rows must also receive a project
identifier before an apply is allowed.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import sqlite3
from typing import Any, Iterable

import httpx

from .authorization import normalize_visibility
from .config import settings
from .maintenance import reindex_wiki
from .storage import connect


@dataclass(frozen=True)
class LegacyRow:
    id: str
    path: str
    scope: str
    owner: str
    workspace_id: str | None
    project_id: str | None

    @property
    def target_project_id(self) -> str | None:
        return self._target_project_id

    _target_project_id: str | None = None

    def as_qdrant_record(self, workspace_id: str) -> dict[str, str | None]:
        return {
            "id": self.id,
            "path": self.path,
            "scope": self.scope,
            "workspace_id": workspace_id,
            "project_id": self.target_project_id,
        }


@dataclass(frozen=True)
class RetagPlan:
    workspace_id: str
    legal_project_id: str | None
    rows: tuple[LegacyRow, ...]

    @property
    def groups(self) -> list[dict[str, Any]]:
        counts: Counter[tuple[str, str, str, str | None]] = Counter(
            (row.scope, row.owner, self.workspace_id, row.target_project_id)
            for row in self.rows
        )
        return [
            {
                "scope": scope,
                "owner": owner,
                "workspace": workspace,
                "project": project,
                "count": count,
            }
            for (scope, owner, workspace, project), count in sorted(counts.items())
        ]


def _identifier(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} must not be empty")
    try:
        binding = normalize_visibility({"workspace_id": clean})
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc
    if not binding or binding["workspace_id"] != clean:
        raise ValueError(f"invalid {label}")
    return clean


def _optional_identifier(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, label)


def _target_project(scope: str, legal_project_id: str | None) -> str | None:
    return legal_project_id if scope.casefold() == "legal" else None


def _row_from_sql(row: Any, legal_project_id: str | None) -> LegacyRow:
    scope = str(row["scope"] or "")
    return LegacyRow(
        id=str(row["id"]),
        path=str(row["path"]),
        scope=scope,
        owner=str(row["owner"]),
        workspace_id=row["visibility_workspace_id"],
        project_id=row["visibility_project_id"],
        _target_project_id=_target_project(scope, legal_project_id),
    )


def _read_only_connect() -> sqlite3.Connection:
    database_path = settings.db_path.expanduser().resolve()
    if not database_path.is_file():
        raise ValueError(f"configured SQLite database does not exist: {database_path}")
    db = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def build_plan(workspace_id: str, legal_project_id: str | None = None) -> RetagPlan:
    """Read all still-unbound wiki rows and calculate their target bindings."""
    clean_workspace = _identifier(workspace_id, "workspace")
    clean_legal_project = _optional_identifier(legal_project_id, "legal project")
    with _read_only_connect() as db:
        partial = int(
            db.execute(
                """SELECT count(*) FROM wiki_docs
               WHERE visibility_workspace_id IS NULL
                 AND visibility_project_id IS NOT NULL"""
            ).fetchone()[0]
        )
        if partial:
            raise ValueError(
                f"cannot retag while {partial} rows have a partial visibility binding"
            )
        rows = db.execute(
            """SELECT id, path, scope, owner, visibility_workspace_id,
                      visibility_project_id
               FROM wiki_docs
               WHERE visibility_workspace_id IS NULL
                 AND visibility_project_id IS NULL
               ORDER BY id"""
        ).fetchall()
    return RetagPlan(
        workspace_id=clean_workspace,
        legal_project_id=clean_legal_project,
        rows=tuple(_row_from_sql(row, clean_legal_project) for row in rows),
    )


def _path_values(path: str) -> list[str]:
    values = {path, f"wiki/{path}", f"openwiki/{path}"}
    return sorted(values)


def _qdrant_filter(row: LegacyRow) -> dict[str, list[dict[str, Any]]]:
    paths = _path_values(row.path)
    clauses = [
        {"key": key, "match": {"any": paths}}
        for key in ("wikipath", "path", "source_file", "source_uri", "uri")
    ]
    clauses.extend(
        {"key": key, "match": {"value": row.id}} for key in ("document_id", "uid")
    )
    return {"should": clauses}


async def sync_qdrant_payloads(
    rows: Iterable[LegacyRow], workspace_id: str
) -> dict[str, Any]:
    """Set visibility payloads for the legacy rows in every configured collection."""
    rows = tuple(rows)
    if not settings.enable_qdrant:
        return {
            "enabled": False,
            "collections": [],
            "updated": 0,
            "deleted_project_keys": 0,
        }
    collections = [
        str(collection).strip()
        for collection in settings.qdrant_collections
        if str(collection).strip() == "knowledge_ops_wiki"
    ]
    if not collections:
        raise RuntimeError("Qdrant is enabled but knowledge_ops_wiki is not configured")
    updated = 0
    deleted_project_keys = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for row in rows:
            payload = {"workspace_id": workspace_id}
            if row.target_project_id:
                payload["project_id"] = row.target_project_id
            for collection in collections:
                base = (
                    f"{settings.qdrant_url.rstrip('/')}/collections/{collection}/points"
                )
                response = await client.post(
                    f"{base}/set_payload?wait=true",
                    json={"payload": payload, "filter": _qdrant_filter(row)},
                )
                response.raise_for_status()
                updated += 1
                if not row.target_project_id:
                    response = await client.post(
                        f"{base}/delete_payload?wait=true",
                        json={
                            "keys": ["project_id", "visibility_project_id"],
                            "filter": _qdrant_filter(row),
                        },
                    )
                    response.raise_for_status()
                    deleted_project_keys += 1
    return {
        "enabled": True,
        "collections": collections,
        "updated": updated,
        "deleted_project_keys": deleted_project_keys,
    }


def _apply_sqlite(plan: RetagPlan) -> int:
    updated = 0
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in plan.rows:
            current = db.execute(
                """SELECT visibility_workspace_id, visibility_project_id
                   FROM wiki_docs WHERE id=?""",
                (row.id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"legacy document disappeared during apply: {row.id}")
            if (
                current["visibility_workspace_id"] is not None
                or current["visibility_project_id"] is not None
            ):
                raise ValueError(f"legacy document changed during apply: {row.id}")
            db.execute(
                """UPDATE wiki_docs
                   SET visibility_workspace_id=?, visibility_project_id=?
                   WHERE id=? AND visibility_workspace_id IS NULL
                     AND visibility_project_id IS NULL""",
                (plan.workspace_id, row.target_project_id, row.id),
            )
            updated += 1
    return updated


def retag_legacy(
    workspace_id: str,
    legal_project_id: str | None = None,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Return a dry-run plan or apply it after an explicit ``apply=True``."""
    plan = build_plan(workspace_id, legal_project_id)
    legal_rows = [row for row in plan.rows if row.scope.casefold() == "legal"]
    result: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "workspace": plan.workspace_id,
        "legal_project": plan.legal_project_id,
        "legacy_rows": len(plan.rows),
        "groups": plan.groups,
    }
    if not apply:
        if legal_rows and not plan.legal_project_id:
            result["apply_blocked"] = (
                "--legal-project is required when legal rows are present"
            )
        result["would_update"] = len(plan.rows)
        return result
    if legal_rows and not plan.legal_project_id:
        raise ValueError("--legal-project is required when legal rows are present")
    if not plan.rows:
        result.update(
            {
                "updated": 0,
                "qdrant": {
                    "enabled": bool(settings.enable_qdrant),
                    "collections": [],
                    "updated": 0,
                },
                "reindex": None,
            }
        )
        return result

    # Qdrant is updated first so a failed external projection leaves SQLite
    # legacy rows available for a complete, idempotent retry.
    result["qdrant"] = asyncio.run(sync_qdrant_payloads(plan.rows, plan.workspace_id))
    result["updated"] = _apply_sqlite(plan)
    result["reindex"] = reindex_wiki()
    return result
