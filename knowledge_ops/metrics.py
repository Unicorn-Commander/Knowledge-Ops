from __future__ import annotations

from datetime import datetime, timezone
import json
from html import escape
from typing import Any

from . import __version__, federation
from .authorization import oidc_stale_fallback_total
from .brigade_auth import jwks_stale_fallback_total
from .config import settings
from .operations import list_source_health, list_worker_heartbeats
from .storage import connect, init_db, json_dumps, now


def record_metric(name: str, value: float, tags: dict | None = None) -> int:
    init_db()
    with connect() as db:
        cur = db.execute(
            "INSERT INTO metrics(name, value, tags_json, recorded_at) VALUES (?, ?, ?, ?)",
            (name, float(value), json_dumps(tags or {}), now()),
        )
        return int(cur.lastrowid)


def metric_rows(name: str, limit: int = 200) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM metrics WHERE name=? ORDER BY recorded_at DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    out = []
    for r in reversed(rows):
        d = dict(r)
        d["tags"] = json.loads(d.pop("tags_json") or "{}")
        out.append(d)
    return out


def render_metric_chart(name: str, limit: int = 80) -> str:
    rows = metric_rows(name, limit)
    width, height, pad = 760, 260, 36
    if not rows:
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'><text x='24' y='40'>No data for {escape(name)}</text></svg>"
    values = [float(r["value"]) for r in rows]
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    points = []
    for i, v in enumerate(values):
        x = pad + (width - pad * 2) * (i / max(1, len(values) - 1))
        y = height - pad - ((v - lo) / span) * (height - pad * 2)
        points.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(points)
    latest = rows[-1]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#10141f"/>
  <text x="24" y="28" fill="#f4f7fb" font-family="Inter, Arial" font-size="16">{escape(name)}</text>
  <text x="24" y="52" fill="#9fb0c7" font-family="Inter, Arial" font-size="12">latest {latest['value']:.3f} - {escape(latest['recorded_at'])}</text>
  <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#334155"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#334155"/>
  <polyline points="{poly}" fill="none" stroke="#7dd3fc" stroke-width="3"/>
  <circle cx="{points[-1].split(',')[0]}" cy="{points[-1].split(',')[1]}" r="4" fill="#fbbf24"/>
  <text x="{width-pad-120}" y="{pad+12}" fill="#9fb0c7" font-family="Inter, Arial" font-size="12">min {lo:.3f} / max {hi:.3f}</text>
</svg>"""


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: int | float, labels: dict[str, Any] | None = None) -> str:
    suffix = ""
    if labels:
        suffix = "{" + ",".join(
            f'{key}="{_label(labels[key])}"' for key in sorted(labels)
        ) + "}"
    return f"{name}{suffix} {float(value):.6g}"


def _seconds_since(value: str | None, current: datetime) -> float:
    if not value:
        return -1
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (current - parsed.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return -1


def prometheus_metrics() -> str:
    """Render low-cardinality operational metrics without external dependencies."""
    init_db()
    current = datetime.now(timezone.utc)
    lines = [
        "# HELP knowledge_ops_build_info Static service build information.",
        "# TYPE knowledge_ops_build_info gauge",
        _sample("knowledge_ops_build_info", 1, {"version": __version__}),
        "# HELP knowledge_ops_auth_failures_total Rejected API and MCP authentication or authorization attempts.",
        "# TYPE knowledge_ops_auth_failures_total counter",
    ]
    with connect() as db:
        auth_failures = db.execute(
            """SELECT count(*) FROM audit_events
               WHERE outcome='denied' AND (action LIKE 'auth.%' OR action='mcp.tool')"""
        ).fetchone()[0]
        review_rows = db.execute(
            "SELECT kind, count(*) AS count FROM review_items WHERE status='open' GROUP BY kind"
        ).fetchall()
        refresh_rows = db.execute(
            "SELECT status, count(*) AS count FROM refresh_runs GROUP BY status"
        ).fetchall()
        refresh_failures = db.execute(
            """SELECT count(*) FROM audit_events
               WHERE action='worker.refresh' AND outcome != 'success'"""
        ).fetchone()[0]
        refresh_age_rows = db.execute(
            """SELECT j.kind, max(
                   CASE WHEN r.status='completed' THEN r.completed_at END
                 ) AS last_success_at
               FROM refresh_jobs j LEFT JOIN refresh_runs r ON r.job_id=j.job_id
               GROUP BY j.kind"""
        ).fetchall()
        outbox_rows = db.execute(
            "SELECT status, count(*) AS count FROM deletion_outbox GROUP BY status"
        ).fetchall()
        deletion_rows = db.execute("SELECT projections_json FROM deletion_events").fetchall()

    lines.append(_sample("knowledge_ops_auth_failures_total", int(auth_failures)))
    lines.extend([
        "# HELP knowledge_ops_jwks_stale_fallback_total Requests verified with a cached signing key after refresh failed.",
        "# TYPE knowledge_ops_jwks_stale_fallback_total counter",
        _sample(
            "knowledge_ops_jwks_stale_fallback_total",
            oidc_stale_fallback_total(),
            {"surface": "api"},
        ),
        _sample(
            "knowledge_ops_jwks_stale_fallback_total",
            jwks_stale_fallback_total(),
            {"surface": "mcp"},
        ),
    ])
    lines.extend([
        "# HELP knowledge_ops_review_backlog Open stale or conflict review items.",
        "# TYPE knowledge_ops_review_backlog gauge",
    ])
    review_counts = {str(row["kind"]): int(row["count"]) for row in review_rows}
    for kind in ("stale", "conflict"):
        lines.append(_sample("knowledge_ops_review_backlog", review_counts.get(kind, 0), {"kind": kind}))

    lines.extend([
        "# HELP knowledge_ops_refresh_runs Refresh runs by current state.",
        "# TYPE knowledge_ops_refresh_runs gauge",
    ])
    refresh_counts = {str(row["status"]): int(row["count"]) for row in refresh_rows}
    for status in ("pending", "running", "completed", "failed"):
        lines.append(_sample("knowledge_ops_refresh_runs", refresh_counts.get(status, 0), {"status": status}))
    lines.extend([
        "# HELP knowledge_ops_refresh_failures_total Worker refresh attempts that did not succeed.",
        "# TYPE knowledge_ops_refresh_failures_total counter",
        _sample("knowledge_ops_refresh_failures_total", int(refresh_failures)),
        "# HELP knowledge_ops_refresh_last_success_age_seconds Seconds since the last successful run by finite job kind, or -1 if none.",
        "# TYPE knowledge_ops_refresh_last_success_age_seconds gauge",
    ])
    for row in refresh_age_rows:
        lines.append(_sample(
            "knowledge_ops_refresh_last_success_age_seconds",
            _seconds_since(row["last_success_at"], current),
            {"kind": row["kind"]},
        ))

    lines.extend([
        "# HELP knowledge_ops_deletion_outbox Source-owner deletion tasks by state.",
        "# TYPE knowledge_ops_deletion_outbox gauge",
    ])
    outbox_counts = {str(row["status"]): int(row["count"]) for row in outbox_rows}
    for status in ("pending", "running", "failed", "succeeded"):
        lines.append(_sample("knowledge_ops_deletion_outbox", outbox_counts.get(status, 0), {"status": status}))
    pending_projections = 0
    for row in deletion_rows:
        projections = json.loads(row["projections_json"] or "{}")
        pending_projections += sum(
            1 for key, status in projections.items()
            if not key.startswith("_") and status not in {"success", "not_applicable"}
        )
    lines.extend([
        "# HELP knowledge_ops_deletion_pending_projections Local or source-owner deletion projections not converged.",
        "# TYPE knowledge_ops_deletion_pending_projections gauge",
        _sample("knowledge_ops_deletion_pending_projections", pending_projections),
    ])

    lines.extend([
        "# HELP knowledge_ops_worker_up Whether at least one scheduler worker heartbeat is current.",
        "# TYPE knowledge_ops_worker_up gauge",
        "# HELP knowledge_ops_worker_heartbeat_age_seconds Seconds since the most recent worker heartbeat.",
        "# TYPE knowledge_ops_worker_heartbeat_age_seconds gauge",
        "# HELP knowledge_ops_worker_count Workers by finite lifecycle state.",
        "# TYPE knowledge_ops_worker_count gauge",
    ])
    workers = list_worker_heartbeats(limit=50)
    ages = [_seconds_since(worker["heartbeat_at"], current) for worker in workers]
    running_ages = [
        age for worker, age in zip(workers, ages)
        if worker["status"] == "running" and age >= 0
    ]
    lines.append(_sample(
        "knowledge_ops_worker_up",
        int(any(age <= settings.worker_stale_seconds for age in running_ages)),
    ))
    lines.append(_sample(
        "knowledge_ops_worker_heartbeat_age_seconds",
        min((age for age in ages if age >= 0), default=-1),
    ))
    worker_counts: dict[str, int] = {}
    for worker in workers:
        worker_counts[str(worker["status"])] = worker_counts.get(str(worker["status"]), 0) + 1
    for status in ("running", "stopping", "stopped", "failed"):
        lines.append(_sample(
            "knowledge_ops_worker_count", worker_counts.get(status, 0), {"status": status}
        ))

    lines.extend([
        "# HELP knowledge_ops_source_health_count Operational sources by finite health state.",
        "# TYPE knowledge_ops_source_health_count gauge",
    ])
    health_counts: dict[str, int] = {}
    for source in list_source_health():
        status = str(source["status"])
        health_counts[status] = health_counts.get(status, 0) + 1
    for status in ("healthy", "degraded", "failed", "disabled", "unknown"):
        lines.append(_sample(
            "knowledge_ops_source_health_count", health_counts.get(status, 0), {"status": status},
        ))
    lines.extend([
        "# HELP knowledge_ops_federated_source_count Federated read sources by enabled state and finite status.",
        "# TYPE knowledge_ops_federated_source_count gauge",
    ])
    federated_counts: dict[tuple[str, str], int] = {}
    for source in federation.list_sources():
        key = ("true" if source.enabled else "false", source.status)
        federated_counts[key] = federated_counts.get(key, 0) + 1
    for (enabled, status), count in sorted(federated_counts.items()):
        lines.append(_sample(
            "knowledge_ops_federated_source_count", count,
            {"enabled": enabled, "status": status},
        ))
    lines.append("# EOF")
    return "\n".join(lines) + "\n"
