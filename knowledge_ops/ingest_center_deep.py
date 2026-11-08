"""Receiver for Center Deep Research monitor deliveries."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import authorization
from .authorization import AuthorizationError
from .config import cdr_webhook_key, settings
from .operations import sanitize_error
from .storage import connect, init_db, record_audit
from .wiki import get_doc, normalize_timestamp, publish_receiver_doc, safe_wiki_path

logger = logging.getLogger("knowledge_ops.ingest_center_deep")

_RECEIPT_TABLE = "center_deep_webhook_receipts"
_INVENTORY_TABLE = "center_deep_inventory_state"
_FEED_TABLE = "center_deep_feed_state"
_HEALTH_TABLE = "center_deep_receiver_health"
_RECEIPT_RETENTION = timedelta(hours=24)
_RECEIPT_STALE = timedelta(minutes=5)
_SIGNATURE_WINDOW_SECONDS = 300
_MAX_TEXT = 2_000
_INTERNAL_WORKSPACE = "magic-unicorn"
_RECEIVER_LOCK = threading.Lock()
_LINE_SEPARATORS = str.maketrans({
    "\r": " ",
    "\n": " ",
    "\v": " ",
    "\f": " ",
    "\x85": " ",
    "\u2028": " ",
    "\u2029": " ",
})


def _error(
    status: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": {"type": "center_deep_webhook", "message": message}},
        status_code=status,
        headers=headers,
    )


def _secret_key(secret: str) -> bytes | None:
    return cdr_webhook_key(secret)


def _verify_signature(
    *,
    secret: str,
    webhook_id: str,
    timestamp_header: str,
    signature_header: str,
    body: bytes,
) -> int | None:
    """Return the parsed timestamp when a Standard Webhooks signature is valid."""
    key = _secret_key(secret)
    if not key or not webhook_id or not signature_header:
        return None
    try:
        timestamp = int(timestamp_header)
    except (TypeError, ValueError):
        return None

    signed = webhook_id.encode("utf-8") + b"." + timestamp_header.encode("utf-8") + b"." + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest())
    for candidate in signature_header.split():
        version, separator, value = candidate.partition(",")
        if not separator or version != "v1":
            continue
        try:
            candidate_bytes = value.encode("ascii")
        except UnicodeEncodeError:
            continue
        if hmac.compare_digest(candidate_bytes, expected):
            return timestamp
    return None


def _canonical_paths() -> tuple[str, str]:
    doc_path, _ = safe_wiki_path(settings.cdr_doc_path)
    log_path, _ = safe_wiki_path(settings.cdr_log_path)
    if doc_path == log_path:
        raise ValueError("Center Deep document and release-log paths must differ")
    return doc_path, log_path


def cdr_receiver_status() -> tuple[str, str | None]:
    """Return ``ready``, ``disabled`` or ``misconfigured`` without raising."""
    raw_secret = str(settings.cdr_webhook_secret or "")
    if not raw_secret:
        return "disabled", None
    secret = raw_secret.strip()
    if not secret:
        return "misconfigured", "invalid_secret"
    if _secret_key(secret) is None:
        return "misconfigured", "invalid_secret"
    try:
        _canonical_paths()
    except (TypeError, ValueError):
        return "misconfigured", "invalid_paths"
    return "ready", None


def _ensure_receipt_table() -> None:
    init_db()
    with connect() as db:
        columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({_RECEIPT_TABLE})")}
        if "error" not in columns:
            db.execute(f"ALTER TABLE {_RECEIPT_TABLE} ADD COLUMN error TEXT")


def _received_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _reserve_receipt(webhook_id: str, run_id: str | None) -> str:
    """Reserve a delivery, distinguishing done, in-flight, and reclaimable receipts."""
    _ensure_receipt_table()
    current = datetime.now(timezone.utc)
    retention_cutoff = (current - _RECEIPT_RETENTION).isoformat()
    stale_cutoff = current - _RECEIPT_STALE
    received_at = current.isoformat()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            f"DELETE FROM {_RECEIPT_TABLE} WHERE received_at < ?",
            (retention_cutoff,),
        )
        if run_id:
            matches = db.execute(
                f"SELECT * FROM {_RECEIPT_TABLE} WHERE webhook_id=? OR run_id=?",
                (webhook_id, run_id),
            ).fetchall()
        else:
            matches = db.execute(
                f"SELECT * FROM {_RECEIPT_TABLE} WHERE webhook_id=?",
                (webhook_id,),
            ).fetchall()
        if any(str(row["status"]).lower() in {"done", "completed"} for row in matches):
            return "duplicate"
        for row in matches:
            status = str(row["status"]).lower()
            received = _received_datetime(row["received_at"])
            if status in {"reserved", "processing"} and received and received >= stale_cutoff:
                return "in_flight"
            db.execute(
                f"DELETE FROM {_RECEIPT_TABLE} WHERE webhook_id=?",
                (row["webhook_id"],),
            )
        db.execute(
            f"INSERT INTO {_RECEIPT_TABLE}(webhook_id, run_id, received_at, status) "
            "VALUES (?, ?, ?, 'reserved')",
            (webhook_id, run_id, received_at),
        )
    return "reserved"


def _finish_receipt(webhook_id: str) -> None:
    with connect() as db:
        db.execute(
            f"UPDATE {_RECEIPT_TABLE} SET status='done', error=NULL WHERE webhook_id=?",
            (webhook_id,),
        )


def _fail_receipt(webhook_id: str, error: str) -> None:
    with connect() as db:
        db.execute(
            f"UPDATE {_RECEIPT_TABLE} SET status='failed', error=? WHERE webhook_id=?",
            (error[:500], webhook_id),
        )


def _set_last_error(error: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute(
            f"""INSERT INTO {_HEALTH_TABLE}(id, last_error, last_error_at, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  last_error=excluded.last_error,
                  last_error_at=excluded.last_error_at,
                  updated_at=excluded.updated_at""",
            (error[:500], timestamp, timestamp),
        )


def _clear_last_error() -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute(
            f"""INSERT INTO {_HEALTH_TABLE}(id, last_error, last_error_at, updated_at)
                VALUES (1, NULL, NULL, ?)
                ON CONFLICT(id) DO UPDATE SET
                  last_error=NULL,
                  last_error_at=NULL,
                  updated_at=excluded.updated_at""",
            (timestamp,),
        )


def cdr_receiver_last_error() -> str | None:
    try:
        init_db()
        with connect() as db:
            row = db.execute(
                f"SELECT last_error FROM {_HEALTH_TABLE} WHERE id=1"
            ).fetchone()
        return str(row["last_error"]) if row and row["last_error"] else None
    except Exception:
        return None


def _text(value: Any, *, default: str = "", limit: int = _MAX_TEXT) -> str:
    if value is None:
        return default
    text = str(value).translate(_LINE_SEPARATORS).strip()
    return text[:limit]


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:500] if isinstance(item, dict)]


def _markdown_cell(value: Any, *, empty: str = "—") -> str:
    text = _text(value, default="")
    if not text:
        return empty
    return text.replace("\\", "\\\\").replace("|", "\\|")


def _unmarkdown_cell(value: str) -> str:
    return value.strip().replace("\\|", "|")


def _split_table_row(line: str) -> list[str]:
    """Parse one row for compatibility with old callers; receiver state never uses it."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped[1:-1]:
        if char == "|" and not escaped:
            cells.append(_unmarkdown_cell("".join(current)))
            current = []
            continue
        current.append(char)
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    cells.append(_unmarkdown_cell("".join(current)))
    return [cell.strip() for cell in cells]


def _component(item: dict[str, Any]) -> dict[str, str] | None:
    package = _text(item.get("package"), limit=500)
    if not package:
        return None
    return {
        "host": _text(item.get("host"), default="unknown-host", limit=200),
        "package": package,
        "ecosystem": _text(item.get("ecosystem"), limit=100),
        "version": _text(item.get("version"), limit=200),
        "repo": _text(item.get("repo"), limit=500),
    }


def _component_key(component: dict[str, str]) -> tuple[str, str, str]:
    return (
        component["host"].casefold(),
        component["package"].casefold(),
        component["ecosystem"].casefold(),
    )


def _inventory_components(value: Any) -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in _rows(value):
        component = _component(item)
        if component is None:
            continue
        key = _component_key(component)
        if key in seen:
            continue
        seen.add(key)
        components.append(component)
    return components


def _matches_component(item: dict[str, Any], component: dict[str, str]) -> bool:
    package = _text(item.get("package"))
    host = _text(item.get("host"))
    ecosystem = _text(item.get("ecosystem"))
    package_match = not package or package.casefold() == component["package"].casefold()
    host_match = not host or host.casefold() == component["host"].casefold()
    ecosystem_match = not ecosystem or ecosystem.casefold() == component["ecosystem"].casefold()
    return package_match and host_match and ecosystem_match


def _release_date(value: Any, fallback: str) -> str:
    raw = _text(value)
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).date().isoformat()
        except ValueError:
            if len(raw) >= 10:
                return raw[:10]
    return fallback[:10]


def _release_cell(release: dict[str, Any], observed_at: str) -> str:
    tag = _text(release.get("tag") or release.get("name"))
    if not tag:
        return ""
    return f"{tag} ({_release_date(release.get('published_at'), observed_at)})"


def _comparison_for(
    comparison_rows: list[dict[str, Any]],
    release_rows: list[dict[str, Any]],
    component: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    repo = component.get("repo", "")
    comparison: dict[str, Any] | None = None
    for item in comparison_rows:
        package = _text(item.get("package"))
        item_repo = _text(item.get("repo"))
        if package and package.casefold() == component["package"].casefold():
            comparison = item
            break
        if repo and item_repo and item_repo.casefold() == repo.casefold():
            comparison = item
            break

    release: dict[str, Any] | None = None
    latest_tag = _text(comparison.get("latest_tag")) if comparison else ""
    for item in release_rows:
        package = _text(item.get("package"))
        item_repo = _text(item.get("repo"))
        if package and package.casefold() != component["package"].casefold():
            continue
        if repo and item_repo and item_repo.casefold() != repo.casefold():
            continue
        item_tag = _text(item.get("tag") or item.get("name"))
        if latest_tag and item_tag == latest_tag:
            release = item
            break
        if release is None:
            release = item
    return comparison, release


def _advisory_cell(matches: list[dict[str, Any]], component: dict[str, str]) -> str:
    matching = [item for item in matches if _matches_component(item, component)]
    if not matching:
        return "0"
    cves = sorted({
        _text(item.get("cve"))
        for item in matching
        if _text(item.get("cve"))
    })
    suffix = f" ({', '.join(cves)})" if cves else ""
    return f"{len(matching)}{suffix}"


def _bool_cell(value: Any) -> str:
    if value is True or (isinstance(value, str) and value.casefold() in {"true", "yes", "1"}):
        return "yes"
    if value is False or (isinstance(value, str) and value.casefold() in {"false", "no", "0"}):
        return "no"
    return "unknown"


def _feed_is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.casefold() in {"true", "yes", "1"})


def _state_key(row: Any) -> tuple[str, str, str]:
    return str(row["host_key"]), str(row["package_key"]), str(row["ecosystem_key"])


def _ensure_state_row(db: sqlite3.Connection, component: dict[str, str], updated_at: str) -> None:
    host_key, package_key, ecosystem_key = _component_key(component)
    db.execute(
        f"""INSERT OR IGNORE INTO {_INVENTORY_TABLE}(
              host_key, package_key, ecosystem_key, host, package, ecosystem,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            host_key, package_key, ecosystem_key,
            component["host"], component["package"], component["ecosystem"], updated_at,
        ),
    )


def _set_feed_state(
    db: sqlite3.Connection,
    *,
    situation: dict[str, Any],
    feed: str,
    run_id: str,
    report_url: str,
    observed_at: str,
    complete: bool,
    truncated: bool,
    updated_at: str,
) -> None:
    previous = db.execute(
        f"SELECT * FROM {_FEED_TABLE} WHERE feed=?", (feed,)
    ).fetchone()
    state = _text(situation.get("state"), limit=80)
    if not state and previous:
        state = str(previous["state"])
    clean_run_id = _text(run_id, limit=500) or (str(previous["run_id"]) if previous else "")
    clean_report_url = _text(report_url, limit=1_000) or (
        str(previous["report_url"]) if previous else ""
    )
    db.execute(
        f"""INSERT INTO {_FEED_TABLE}(
              feed, state, observed_at, run_id, report_url, complete,
              truncated, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed) DO UPDATE SET
              state=excluded.state,
              observed_at=excluded.observed_at,
              run_id=excluded.run_id,
              report_url=excluded.report_url,
              complete=excluded.complete,
              truncated=excluded.truncated,
              updated_at=excluded.updated_at""",
        (
            feed, state, observed_at, clean_run_id, clean_report_url,
            int(complete), int(truncated), updated_at,
        ),
    )


def _mark_partial(
    db: sqlite3.Connection,
    *,
    feed: str,
    run_id: str,
    report_url: str,
    observed_at: str,
    updated_at: str,
) -> None:
    prefix = "kev" if feed == "kev" else "release"
    db.execute(
        f"""UPDATE {_INVENTORY_TABLE} SET
              {prefix}_complete=0,
              {prefix}_partial=1,
              {prefix}_run_id=CASE WHEN ? <> '' THEN ? ELSE {prefix}_run_id END,
              {prefix}_report_url=CASE WHEN ? <> '' THEN ? ELSE {prefix}_report_url END,
              {prefix}_observed_at=?,
              updated_at=?""",
        (run_id, run_id, report_url, report_url, observed_at, updated_at),
    )


def _merge_state(
    db: sqlite3.Connection,
    *,
    situation: dict[str, Any],
    feed: str,
    run_id: str,
    report_url: str,
    observed_at: str,
    updated_at: str,
) -> None:
    complete = situation.get("complete") is True
    truncated = _feed_is_true(situation.get("truncated"))
    partial = not complete or truncated
    _set_feed_state(
        db,
        situation=situation,
        feed=feed,
        run_id=run_id,
        report_url=report_url,
        observed_at=observed_at,
        complete=complete,
        truncated=truncated,
        updated_at=updated_at,
    )
    if feed not in {"kev", "github_releases"}:
        return
    if partial:
        _mark_partial(
            db,
            feed=feed,
            run_id=run_id,
            report_url=report_url,
            observed_at=observed_at,
            updated_at=updated_at,
        )
        return

    inventory = _inventory_components(situation.get("inventory"))
    if feed == "kev":
        matches = _rows(situation.get("kev_matches"))
        keys: set[tuple[str, str, str]] = set()
        for component in inventory:
            key = _component_key(component)
            keys.add(key)
            _ensure_state_row(db, component, updated_at)
            db.execute(
                f"""UPDATE {_INVENTORY_TABLE} SET
                      host=?, package=?, ecosystem=?,
                      kev_version=CASE WHEN ? <> '' THEN ? ELSE kev_version END,
                      kev_advisories=?, kev_complete=1, kev_partial=0,
                      kev_run_id=CASE WHEN ? <> '' THEN ? ELSE kev_run_id END,
                      kev_report_url=CASE WHEN ? <> '' THEN ? ELSE kev_report_url END,
                      kev_observed_at=?, updated_at=?
                    WHERE host_key=? AND package_key=? AND ecosystem_key=?""",
                (
                    component["host"], component["package"], component["ecosystem"],
                    component["version"], component["version"],
                    _advisory_cell(matches, component),
                    run_id, run_id, report_url, report_url, observed_at, updated_at,
                    *key,
                ),
            )
        existing = db.execute(
            f"SELECT host_key, package_key, ecosystem_key FROM {_INVENTORY_TABLE}"
        ).fetchall()
        for row in existing:
            if _state_key(row) not in keys:
                db.execute(
                    f"""DELETE FROM {_INVENTORY_TABLE}
                        WHERE host_key=? AND package_key=? AND ecosystem_key=?""",
                    _state_key(row),
                )
        return

    db.execute(
        f"""UPDATE {_INVENTORY_TABLE} SET
              release_complete=1, release_partial=0,
              release_run_id=CASE WHEN ? <> '' THEN ? ELSE release_run_id END,
              release_report_url=CASE WHEN ? <> '' THEN ? ELSE release_report_url END,
              release_observed_at=?, updated_at=?""",
        (run_id, run_id, report_url, report_url, observed_at, updated_at),
    )
    comparisons = _rows(situation.get("comparisons"))
    releases = _rows(situation.get("releases"))
    for component in inventory:
        key = _component_key(component)
        _ensure_state_row(db, component, updated_at)
        existing = db.execute(
            f"SELECT * FROM {_INVENTORY_TABLE} WHERE host_key=? AND package_key=? AND ecosystem_key=?",
            key,
        ).fetchone()
        latest = str(existing["release_latest"])
        behind = str(existing["release_behind"])
        security_newer = str(existing["release_security_newer"])
        notes = str(existing["release_notes"])
        comparison, release = _comparison_for(comparisons, releases, component)
        if comparison:
            latest_tag = _text(comparison.get("latest_tag"))
            if latest_tag:
                latest = f"{latest_tag} ({_release_date(release.get('published_at') if release else None, observed_at)})"
            if "behind" in comparison:
                behind = _bool_cell(comparison.get("behind"))
            if "security_newer" in comparison:
                security_newer = _bool_cell(comparison.get("security_newer"))
            if "comparison" in comparison:
                notes = _text(comparison.get("comparison"))
        elif release:
            latest = _release_cell(release, observed_at)
        db.execute(
            f"""UPDATE {_INVENTORY_TABLE} SET
                  host=?, package=?, ecosystem=?,
                  release_version=CASE WHEN ? <> '' THEN ? ELSE release_version END,
                  release_repo=CASE WHEN ? <> '' THEN ? ELSE release_repo END,
                  release_latest=?, release_behind=?, release_security_newer=?,
                  release_notes=?, release_complete=1, release_partial=0,
                  release_run_id=CASE WHEN ? <> '' THEN ? ELSE release_run_id END,
                  release_report_url=CASE WHEN ? <> '' THEN ? ELSE release_report_url END,
                  release_observed_at=?, updated_at=?
                WHERE host_key=? AND package_key=? AND ecosystem_key=?""",
            (
                component["host"], component["package"], component["ecosystem"],
                component["version"], component["version"],
                component["repo"], component["repo"],
                latest, behind, security_newer, notes,
                run_id, run_id, report_url, report_url, observed_at, updated_at,
                *key,
            ),
        )


def _run_link(run_id: Any, report_url: Any) -> str:
    run = _text(run_id, default="", limit=500)
    url = _text(report_url, default="", limit=1_000)
    label = _markdown_cell(run, empty="run")
    if not url:
        return label if run else ""
    return f"[{label}]({_markdown_cell(url)})"


def _render_versions_doc(
    db: sqlite3.Connection,
    *,
    situation: dict[str, Any],
    feed: str,
    run_id: str,
    report_url: str,
    observed_at: str,
    review_due_at: str,
) -> tuple[str, int]:
    rows = [dict(row) for row in db.execute(
        f"SELECT * FROM {_INVENTORY_TABLE}"
    ).fetchall()]
    feed_states = {
        str(row["feed"]): dict(row)
        for row in db.execute(f"SELECT * FROM {_FEED_TABLE}").fetchall()
    }

    def partial(row: dict[str, Any], feed_name: str) -> bool:
        marker = bool(row[f"{feed_name}_partial"])
        state_name = "github_releases" if feed_name == "release" else feed_name
        state = feed_states.get(state_name)
        return marker or bool(state and (not state["complete"] or state["truncated"]))

    rendered_rows: list[str] = []
    for row in sorted(rows, key=lambda item: (
        str(item["host"]).casefold(),
        str(item["package"]).casefold(),
        str(item["ecosystem"]).casefold(),
    )):
        kev_partial = partial(row, "kev")
        release_partial = partial(row, "release")
        version = row["kev_version"] or row["release_version"]
        latest = "unknown (partial)" if release_partial else row["release_latest"]
        behind = "unknown (partial)" if release_partial else row["release_behind"]
        security_newer = "unknown (partial)" if release_partial else row["release_security_newer"]
        notes = "unknown (partial)" if release_partial else row["release_notes"]
        advisories = "unknown (partial)" if kev_partial else (row["kev_advisories"] or "0")
        cells = [
            _markdown_cell(row["host"]),
            _markdown_cell(row["package"]),
            _markdown_cell(row["ecosystem"]),
            _markdown_cell(version),
            _markdown_cell(latest),
            _markdown_cell(behind, empty="unknown"),
            _markdown_cell(security_newer, empty="unknown"),
            _markdown_cell(advisories, empty="0"),
            _markdown_cell(notes),
            _run_link(row["release_run_id"], row["release_report_url"]) or "—",
            _run_link(row["kev_run_id"], row["kev_report_url"]) or "—",
        ]
        rendered_rows.append("| " + " | ".join(cells) + " |")

    state = _text(situation.get("state"), default="", limit=80)
    current_feed_state = feed_states.get(feed)
    if not state and current_feed_state:
        state = _text(current_feed_state.get("state"), default="UNKNOWN", limit=80)
    state = state or "UNKNOWN"
    source_run = _run_link(run_id, report_url) or "(not supplied)"
    header = [
        "# Self-hosted stack versions",
        "",
        f"- Observed at: {_markdown_cell(observed_at)}",
        f"- State: {_markdown_cell(state)}",
        f"- Center Deep run: {source_run}",
        f"- Review due at: {_markdown_cell(review_due_at)}",
        f"- Feed: {_markdown_cell(feed or 'unknown')}",
        f"- Run ID: {_markdown_cell(run_id, empty='(not supplied)')}",
        "",
        "| Host | Component / package | Ecosystem | Running version | Latest release (tag + date) | Behind? | Security release newer? | Advisories | Notes / comparison | Releases source run | KEV source run |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    return "\n".join(header + rendered_rows) + "\n", len(rendered_rows)


_LOG_LINE_RE = re.compile(r"^-\s+[^·]+·\s+(\S+)\s+(\S+)\s+·")


def _log_key(line: str) -> tuple[str, str] | None:
    match = _LOG_LINE_RE.match(line.strip())
    if not match:
        return None
    return (
        _unmarkdown_cell(match.group(1)).replace("\\\\", "\\"),
        _unmarkdown_cell(match.group(2)).replace("\\\\", "\\"),
    )


def _release_log_body(previous_body: str, releases: list[dict[str, Any]], observed_at: str) -> str:
    lines = [_text(line) for line in previous_body.splitlines() if line.strip()]
    known = {key for line in lines if (key := _log_key(line))}
    for release in releases:
        repo = _text(release.get("repo"), limit=500)
        tag = _text(release.get("tag") or release.get("name"), limit=200)
        if not repo or not tag or (repo, tag) in known:
            continue
        published = _release_date(release.get("published_at"), observed_at)
        label = "security" if release.get("security") is True else "release"
        url = _text(release.get("url"), default="(no URL)", limit=1_000)
        lines.append(f"- {published} · {_markdown_cell(repo)} {_markdown_cell(tag)} · {label} · {_markdown_cell(url)}")
        known.add((repo, tag))
    lines = lines[-500:]
    return "\n".join(lines) + ("\n" if lines else "")


def _observed_at(situation: dict[str, Any], payload: dict[str, Any]) -> str:
    candidate = situation.get("observed_at") or payload.get("timestamp")
    if not candidate:
        raise ValueError("situation.observed_at is required")
    return normalize_timestamp(_text(candidate, limit=100))


def _payload_parts(payload: Any) -> tuple[dict[str, Any], dict[str, Any], str, str, str]:
    if not isinstance(payload, dict):
        raise ValueError("webhook body must be a JSON object")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("webhook body data must be an object")
    situation = data.get("situation")
    if not isinstance(situation, dict):
        raise ValueError("webhook body data.situation must be an object")
    feed = _text(situation.get("feed"), limit=100)
    run_id = _text(data.get("run_id"), limit=500)
    report_url = _text(data.get("report_url"), limit=1_000)
    return data, situation, feed, run_id, report_url


def _process_delivery(
    *,
    payload: dict[str, Any],
    data: dict[str, Any],
    situation: dict[str, Any],
    feed: str,
    run_id: str,
    report_url: str,
    observed_at: str,
) -> dict[str, Any]:
    del payload, data
    doc_path, log_path = _canonical_paths()
    review_due_at = (datetime.fromisoformat(observed_at) + timedelta(days=14)).isoformat()
    principal = authorization.system_principal()
    scope, owner = authorization.authorized_write_target(principal, "global", "center-deep")
    with _RECEIVER_LOCK:
        previous_log = get_doc(log_path)
        previous_log_body = previous_log["body"] if previous_log else ""
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _merge_state(
                db,
                situation=situation,
                feed=feed,
                run_id=run_id,
                report_url=report_url,
                observed_at=observed_at,
                updated_at=observed_at,
            )
            versions_body, rows = _render_versions_doc(
                db,
                situation=situation,
                feed=feed,
                run_id=run_id,
                report_url=report_url,
                observed_at=observed_at,
                review_due_at=review_due_at,
            )
        partial = situation.get("complete") is not True or _feed_is_true(situation.get("truncated"))
        releases = _rows(situation.get("releases")) if feed == "github_releases" and not partial else []
        log_body = _release_log_body(previous_log_body, releases, observed_at)
        visibility = {"workspace_id": _INTERNAL_WORKSPACE}
        doc_id = publish_receiver_doc(
            doc_path,
            versions_body,
            scope=scope,
            owner=owner,
            review_due_at=review_due_at,
            visibility=visibility,
        )
        log_id = publish_receiver_doc(
            log_path,
            log_body,
            scope=scope,
            owner=owner,
            visibility=visibility,
        )
        record_audit(
            "center-deep-receiver",
            "center_deep.webhook",
            doc_id,
            "success",
            {"feed": feed, "run_id": run_id, "rows": rows, "log_id": log_id},
        )
    return {"doc_id": doc_id, "log_id": log_id, "rows": rows}


async def handle_center_deep_ingest(request: Request) -> JSONResponse:
    """Authenticate and publish one Center Deep delivery."""
    status, _ = cdr_receiver_status()
    if status != "ready":
        return JSONResponse({"detail": "not found"}, status_code=404)

    try:
        body = await request.body()
    except Exception:
        return _error(400, "request body could not be read")

    webhook_id = _text(request.headers.get("webhook-id"), limit=500)
    timestamp_header = _text(request.headers.get("webhook-timestamp"), limit=40)
    signature_header = _text(request.headers.get("webhook-signature"), limit=10_000)
    timestamp = _verify_signature(
        secret=str(settings.cdr_webhook_secret).strip(),
        webhook_id=webhook_id,
        timestamp_header=timestamp_header,
        signature_header=signature_header,
        body=body,
    )
    if timestamp is None:
        return _error(401, "invalid webhook signature")
    if abs(time.time() - timestamp) > _SIGNATURE_WINDOW_SECONDS:
        return _error(409, "webhook timestamp is stale")

    try:
        payload = json.loads(body)
        data, situation, feed, run_id, report_url = _payload_parts(payload)
        observed_at = _observed_at(situation, payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return _error(400, str(exc))

    if not webhook_id:
        return _error(401, "invalid webhook signature")
    try:
        reservation = _reserve_receipt(webhook_id, run_id or None)
    except Exception as exc:
        code = sanitize_error(exc, category="center_deep_receipt_reservation_failed")
        try:
            _set_last_error(code)
        except Exception:
            logger.exception("center-deep receipt reservation health update failed")
        logger.exception("center-deep receipt reservation failed code=%s", code)
        return _error(503, "delivery could not be reserved", headers={"Retry-After": "60"})
    if reservation == "duplicate":
        return JSONResponse({"duplicate": True}, status_code=200)
    if reservation == "in_flight":
        return _error(
            503,
            "delivery is still processing",
            headers={"Retry-After": "60"},
        )

    try:
        result = await run_in_threadpool(
            _process_delivery,
            payload=payload,
            data=data,
            situation=situation,
            feed=feed,
            run_id=run_id,
            report_url=report_url,
            observed_at=observed_at,
        )
        _finish_receipt(webhook_id)
        _clear_last_error()
        return JSONResponse(result, status_code=200)
    except AuthorizationError:
        code = "center_deep_document_authorization_failed"
        try:
            _fail_receipt(webhook_id, code)
            _set_last_error(code)
        except Exception:
            logger.exception("center-deep authorization failure receipt update failed")
        logger.warning("center-deep delivery authorization failed code=%s", code)
        return _error(403, "configured receiver document cannot be written")
    except Exception as exc:
        code = sanitize_error(exc, category="center_deep_delivery_failed")
        try:
            _fail_receipt(webhook_id, code)
            _set_last_error(code)
        except Exception:
            logger.exception("center-deep delivery failure receipt update failed")
        logger.exception("center-deep delivery failed code=%s", code)
        return _error(503, "delivery processing failed", headers={"Retry-After": "60"})
