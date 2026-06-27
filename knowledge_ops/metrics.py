from __future__ import annotations

import json
from html import escape

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
