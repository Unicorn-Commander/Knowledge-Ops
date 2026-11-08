"""Receiver for Center Deep Research monitor deliveries."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from . import authorization
from .config import settings
from .storage import connect, init_db, record_audit
from .wiki import get_doc, normalize_timestamp, upsert_doc

logger = logging.getLogger("knowledge_ops.ingest_center_deep")

_RECEIPT_TABLE = "center_deep_webhook_receipts"
_RECEIPT_RETENTION = timedelta(hours=24)
_SIGNATURE_WINDOW_SECONDS = 300
_MAX_TEXT = 2_000


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"type": "center_deep_webhook", "message": message}},
        status_code=status,
    )


def _secret_key(secret: str) -> bytes | None:
    if secret.startswith("whsec_"):
        encoded = secret[len("whsec_"):]
        if not encoded:
            return None
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return None
        return key or None
    return secret.encode("utf-8") or None


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
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode("ascii")
    for candidate in signature_header.split():
        version, separator, value = candidate.partition(",")
        if separator and version == "v1" and hmac.compare_digest(value, expected):
            return timestamp
    return None


def _ensure_receipt_table() -> None:
    init_db()
    with connect() as db:
        db.execute(
            f"""CREATE TABLE IF NOT EXISTS {_RECEIPT_TABLE} (
                webhook_id TEXT PRIMARY KEY,
                run_id TEXT UNIQUE,
                received_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing'
            )"""
        )
        db.execute(
            f"""CREATE INDEX IF NOT EXISTS idx_{_RECEIPT_TABLE}_received_at
                ON {_RECEIPT_TABLE}(received_at)"""
        )


def _reserve_receipt(webhook_id: str, run_id: str | None) -> bool:
    """Reserve a delivery id or run id, returning False for a recent duplicate."""
    _ensure_receipt_table()
    cutoff = (datetime.now(timezone.utc) - _RECEIPT_RETENTION).isoformat()
    received_at = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            f"DELETE FROM {_RECEIPT_TABLE} WHERE received_at < ?",
            (cutoff,),
        )
        duplicate = db.execute(
            f"""SELECT 1 FROM {_RECEIPT_TABLE}
                WHERE webhook_id=? OR (? IS NOT NULL AND run_id=?)
                LIMIT 1""",
            (webhook_id, run_id, run_id),
        ).fetchone()
        if duplicate:
            return False
        db.execute(
            f"INSERT INTO {_RECEIPT_TABLE}(webhook_id, run_id, received_at) VALUES (?, ?, ?)",
            (webhook_id, run_id, received_at),
        )
    return True


def _finish_receipt(webhook_id: str) -> None:
    with connect() as db:
        db.execute(
            f"UPDATE {_RECEIPT_TABLE} SET status='completed' WHERE webhook_id=?",
            (webhook_id,),
        )


def _release_receipt(webhook_id: str) -> None:
    with connect() as db:
        db.execute(f"DELETE FROM {_RECEIPT_TABLE} WHERE webhook_id=?", (webhook_id,))


def _text(value: Any, *, default: str = "", limit: int = _MAX_TEXT) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text[:limit]


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:500] if isinstance(item, dict)]


def _markdown_cell(value: Any, *, empty: str = "—") -> str:
    text = _text(value, default="")
    if not text:
        return empty
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _unmarkdown_cell(value: str) -> str:
    return value.strip().replace("\\|", "|")


def _split_table_row(line: str) -> list[str]:
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


def _column_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _parse_previous_rows(body: str) -> dict[tuple[str, str], dict[str, str]]:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        headers = _split_table_row(line)
        normalized = {_column_name(value): position for position, value in enumerate(headers)}
        if "host" not in normalized or not any(
            name in normalized for name in ("component package", "component", "package")
        ):
            continue
        if index + 1 >= len(lines) or not _split_table_row(lines[index + 1]):
            continue
        package_column = next(
            name for name in ("component package", "component", "package") if name in normalized
        )

        def get(cells: list[str], *names: str) -> str:
            for name in names:
                position = normalized.get(name)
                if position is not None and position < len(cells):
                    return cells[position]
            return ""

        parsed: dict[tuple[str, str], dict[str, str]] = {}
        for row_line in lines[index + 2:]:
            cells = _split_table_row(row_line)
            if not cells or len(cells) < len(headers):
                break
            host = get(cells, "host")
            package = get(cells, package_column)
            if host and package:
                parsed[(host.casefold(), package.casefold())] = {
                    "latest_release": get(cells, "latest release", "latest release tag date"),
                    "behind": get(cells, "behind"),
                    "security_newer": get(cells, "security release newer"),
                    "advisories": get(cells, "advisories"),
                    "notes": get(cells, "notes comparison", "notes"),
                }
        return parsed
    return {}


def _component_key(host: str, package: str) -> tuple[str, str]:
    return host.casefold(), package.casefold()


def _matches_component(item: dict[str, Any], component: dict[str, str]) -> bool:
    package = _text(item.get("package"))
    host = _text(item.get("host"))
    package_match = not package or package.casefold() == component["package"].casefold()
    host_match = not host or host.casefold() == component["host"].casefold()
    return package_match and host_match


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


def _build_versions_doc(
    *,
    situation: dict[str, Any],
    previous_body: str,
    report_url: str,
    run_id: str,
    feed: str,
    observed_at: str,
    review_due_at: str,
) -> tuple[str, int]:
    previous = _parse_previous_rows(previous_body)
    inventory_rows = _rows(situation.get("inventory"))
    components: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in inventory_rows:
        host = _text(item.get("host"), default="unknown-host", limit=200)
        package = _text(item.get("package"), limit=500)
        if not package:
            continue
        key = _component_key(host, package)
        if key in seen:
            continue
        seen.add(key)
        components.append({
            "host": host,
            "package": package,
            "ecosystem": _text(item.get("ecosystem")),
            "version": _text(item.get("version")),
            "repo": _text(item.get("repo")),
        })

    if not components:
        for (host, package), old in previous.items():
            components.append({
                "host": host,
                "package": package,
                "ecosystem": "",
                "version": "",
                "repo": "",
                "_old": old,
            })

    comparisons = _rows(situation.get("comparisons"))
    releases = _rows(situation.get("releases"))
    kev_matches = _rows(situation.get("kev_matches"))
    rendered_rows: list[str] = []
    for component in sorted(components, key=lambda item: (item["host"].casefold(), item["package"].casefold())):
        old = previous.get(_component_key(component["host"], component["package"]), {})
        row = {
            "latest_release": old.get("latest_release", ""),
            "behind": old.get("behind", ""),
            "security_newer": old.get("security_newer", ""),
            "advisories": old.get("advisories", "0") or "0",
            "notes": old.get("notes", ""),
        }
        if feed == "github_releases":
            comparison, release = _comparison_for(comparisons, releases, component)
            if comparison:
                latest_tag = _text(comparison.get("latest_tag"))
                if latest_tag:
                    row["latest_release"] = f"{latest_tag} ({_release_date(release.get('published_at') if release else None, observed_at)})"
                row["behind"] = _bool_cell(comparison.get("behind"))
                row["security_newer"] = _bool_cell(comparison.get("security_newer"))
                comparison_text = _text(comparison.get("comparison"))
                if comparison_text:
                    row["notes"] = comparison_text
            elif release:
                row["latest_release"] = _release_cell(release, observed_at)
        elif feed == "kev":
            row["advisories"] = _advisory_cell(kev_matches, component)

        rendered_rows.append(
            "| " + " | ".join([
                _markdown_cell(component["host"]),
                _markdown_cell(component["package"]),
                _markdown_cell(component["ecosystem"]),
                _markdown_cell(component["version"]),
                _markdown_cell(row["latest_release"]),
                _markdown_cell(row["behind"], empty="unknown"),
                _markdown_cell(row["security_newer"], empty="unknown"),
                _markdown_cell(row["advisories"], empty="0"),
                _markdown_cell(row["notes"]),
            ]) + " |"
        )

    state = _text(situation.get("state"), default="UNKNOWN", limit=80)
    run_line = _markdown_cell(report_url, empty="(not supplied)")
    header = [
        "# Self-hosted stack versions",
        "",
        f"- Observed at: {observed_at}",
        f"- State: {state}",
        f"- Center Deep run: [{run_line}]({run_line})" if report_url else "- Center Deep run: (not supplied)",
        f"- Review due at: {review_due_at}",
        f"- Feed: {feed or 'unknown'}",
        f"- Run ID: {_markdown_cell(run_id, empty='(not supplied)')}",
        "",
        "| Host | Component / package | Ecosystem | Running version | Latest release (tag + date) | Behind? | Security release newer? | Advisories | Notes / comparison |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    return "\n".join(header + rendered_rows) + "\n", len(rendered_rows)


def _bool_cell(value: Any) -> str:
    if value is True or (isinstance(value, str) and value.casefold() in {"true", "yes", "1"}):
        return "yes"
    if value is False or (isinstance(value, str) and value.casefold() in {"false", "no", "0"}):
        return "no"
    return "unknown"


_LOG_LINE_RE = re.compile(r"^-\s+[^·]+·\s+(\S+)\s+(\S+)\s+·")


def _log_key(line: str) -> tuple[str, str] | None:
    match = _LOG_LINE_RE.match(line.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def _release_log_body(previous_body: str, releases: list[dict[str, Any]], observed_at: str) -> str:
    lines = [line.strip() for line in previous_body.splitlines() if line.strip()]
    known = {key for line in lines if (key := _log_key(line))}
    for release in releases:
        repo = _text(release.get("repo"), limit=500)
        tag = _text(release.get("tag") or release.get("name"), limit=200)
        if not repo or not tag or (repo, tag) in known:
            continue
        published = _release_date(release.get("published_at"), observed_at)
        label = "security" if release.get("security") is True else "release"
        url = _text(release.get("url"), default="(no URL)", limit=1_000)
        lines.append(f"- {published} · {repo} {tag} · {label} · {url}")
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


async def handle_center_deep_ingest(request: Request) -> JSONResponse:
    """Authenticate and publish one Center Deep delivery."""
    if not settings.cdr_webhook_secret:
        return JSONResponse({"detail": "not found"}, status_code=404)

    try:
        body = await request.body()
    except Exception:
        return _error(400, "request body could not be read")

    webhook_id = _text(request.headers.get("webhook-id"), limit=500)
    timestamp_header = _text(request.headers.get("webhook-timestamp"), limit=40)
    signature_header = _text(request.headers.get("webhook-signature"), limit=10_000)
    timestamp = _verify_signature(
        secret=settings.cdr_webhook_secret,
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
    if not _reserve_receipt(webhook_id, run_id or None):
        return JSONResponse({"duplicate": True}, status_code=200)

    review_due_at = (
        datetime.fromisoformat(observed_at) + timedelta(days=14)
    ).isoformat()
    principal = authorization.system_principal()
    scope, owner = authorization.authorized_write_target(principal, "global", "center-deep")
    doc_path = settings.cdr_doc_path
    log_path = settings.cdr_log_path
    previous_doc = get_doc(doc_path)
    previous_log = get_doc(log_path)
    previous_body = previous_doc["body"] if previous_doc else ""
    previous_log_body = previous_log["body"] if previous_log else ""
    releases = _rows(situation.get("releases"))
    try:
        versions_body, rows = _build_versions_doc(
            situation=situation,
            previous_body=previous_body,
            report_url=report_url,
            run_id=run_id,
            feed=feed,
            observed_at=observed_at,
            review_due_at=review_due_at,
        )
        log_body = _release_log_body(previous_log_body, releases, observed_at)
        doc_id = upsert_doc(
            doc_path, versions_body, scope=scope, owner=owner,
            review_due_at=review_due_at,
        )
        log_id = upsert_doc(log_path, log_body, scope=scope, owner=owner)
        _finish_receipt(webhook_id)
        record_audit(
            principal.subject,
            "center_deep.webhook",
            doc_id,
            "success",
            {"feed": feed, "run_id": run_id, "rows": rows},
        )
        return JSONResponse({"doc_id": doc_id, "log_id": log_id, "rows": rows}, status_code=200)
    except Exception:
        _release_receipt(webhook_id)
        logger.exception("center-deep delivery failed")
        raise
