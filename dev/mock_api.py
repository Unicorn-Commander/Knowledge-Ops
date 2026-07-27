#!/usr/bin/env python3
"""Offline dev harness for the Knowledge-Ops Constellation UI.

Stdlib-only. Serves knowledge_ops/static/ at /ui/ (and its landing/ subdir at
/landing/) plus realistic canned JSON for every /kb.* endpoint the UI calls,
shaped to match knowledge_ops/api.py + graph_api.py responses and seeded with
the real corpus facts (wiki 326 / org_memory 396 / meetings 1734 / stable 62).

Run:  python3 dev/mock_api.py [port]     (default 8765)
Admin persona: append ?as=admin to /kb.me (e.g. open /ui/?as=admin has no
effect — the UI calls /kb.me plainly; set MOCK_ADMIN=1 to boot as admin).
"""

import json
import mimetypes
import os
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "knowledge_ops", "static")
NOW = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)


def iso(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


# ---------------------------------------------------------------- wiki corpus
DOCS = [
    ("Home.md", "Home"),
    ("marketing/Magic-Unicorn-Marketing-Bible.md", "Magic Unicorn — Marketing & Brand Bible (v1)"),
    ("ai-platform/Model-Eval-AO-Worker-2026-07.md", "Model Eval: AO Worker Task-Group (2026-07-13/14)"),
    ("ai-platform/Qwen-First-Certification-Loop.md", "Qwen-First Model Certification Loop (P-00110)"),
    ("apps/Majiks-Launch-Runbook.md", "Majiks Launch Runbook"),
    ("engineering/meeting-ops-inference-pipeline.md", "Inference pipeline: concurrent diarization + 24k-slot summary tuning"),
    ("apps/Cognitive-Companion.md", "Cognitive Companion (LtCol Katie / LtCol MacKenzie)"),
    ("engineering/knowledge-ops-refresh-cadence.md", "Knowledge-Ops refresh cadence and deletion propagation"),
    ("infrastructure/gpu-fleet-service-map.md", "GPU fleet service map"),
    ("infrastructure/headscale-acl-workflow.md", "Headscale ACL change workflow"),
    ("marketing/Magic-Unicorn-Web-Property-Map-and-Agent-Topology.md", "Magic Unicorn Web Property Map & Agent Topology"),
    ("ai-platform/gateway-canonical-inference.md", "Gateway canonical inference routes"),
    ("engineering/suite-sso-brokered-idp-tokens.md", "Suite SSO: brokered IdP tokens"),
    ("apps/Meeting-Ops-Launch-Readiness.md", "Meeting-Ops launch readiness (2026-07-15)"),
    ("personal/aaron/folly-beach-cto-notes.md", "Folly Beach CTO notes"),
]


def doc_id(path):
    return "doc-" + format(abs(hash(path)) % 10**10, "010d")


def doc_row(path, title, i):
    personal = path.startswith("personal/")
    return {
        "id": doc_id(path),
        "path": path,
        "title": title,
        "scope": "personal" if personal else "global",
        "owner": "aaron" if personal else None,
        "lifecycle_state": "active",
        "status": "active",
        "updated_at": iso(days=i * 2 + 1),
        "review_due_at": iso(days=-(30 + i * 7)),
        "created_at": iso(days=40 + i * 3),
    }


DOC_ROWS = [doc_row(p, t, i) for i, (p, t) in enumerate(DOCS)]

DOC_BODY = """# {title}

> Governed document — every revision kept, every change recorded.

## Purpose

This document records the decisions and operating knowledge behind
**{title}**. It is retrieved as *evidence* — related passages are heuristic,
not verification.

## Decisions

- Qwen-first: candidate work routes to the local lane before any cloud model.
- Evidence-first governance: the product never presents heuristic retrieval
  as verified fact.
- Deletions propagate through the outbox to every federated source owner.

### Model lanes

| Lane | Model | Use |
| --- | --- | --- |
| local | qwen3.6-35b-a3b | primary task-group |
| cloud | frontier | non-PHI failover |

## Operations

Refresh cadence is every 24h via the `wiki-review` job. Review findings land
in the memory-quality queue; conflicts are resolved with a written note.

### Verification

Run the certification loop (P-00110) and record the metric
`ko.grounding.assessments` after each batch.

## History

Supersedes the 2026-06 draft. Review due quarterly.
"""


# Per-doc bodies carrying the wiki link web. [[Launch Retrospective]] and
# [[Diarization Bench Notes]] are deliberately MISSING targets; the personal
# folly-beach doc is the deliberate ORPHAN (no links in, no links out — it
# falls through to the generic DOC_BODY template, which has no wikilinks).
WIKI_BODIES = {
    "Home.md": """# Home

Welcome to the Knowledge-Ops wiki — the governed, versioned front door to
everything the org has decided. Start here, follow the links.

## Start here

- [[Magic-Unicorn-Marketing-Bible|Marketing & Brand Bible]] — voice, palette,
  and the rules every public word obeys.
- [[Majiks-Launch-Runbook]] — the live launch playbook for the Majiks suite.
- [[Meeting-Ops-Launch-Readiness|Meeting-Ops launch readiness]] — signup E2E
  status and the $1 real-card gate.
- [[gpu-fleet-service-map|GPU fleet service map]] — who runs what, and where.

## How this wiki stays healthy

Refresh and review are automated — see
[[knowledge-ops-refresh-cadence|the refresh cadence]] for the 24h loop and
deletion propagation. Identity and session plumbing is documented in
[Suite SSO: brokered IdP tokens](engineering/suite-sso-brokered-idp-tokens.md).

## Ground rules

- Evidence-first: retrieval is heuristic, never presented as verified fact.
- Every revision kept, every change recorded.
""",
    "marketing/Magic-Unicorn-Marketing-Bible.md": """# Magic Unicorn — Marketing & Brand Bible (v1)

> Governed document — every revision kept, every change recorded.

## Voice

Confident, technical, a little playful. We show working software, not
adjectives. Public claims must trace to a shipped artifact.

## Properties

The full domain and agent inventory lives in the
[[Magic-Unicorn-Web-Property-Map-and-Agent-Topology|web property map]].
Launch-facing copy for the music suite follows the
[[Majiks-Launch-Runbook]].

## Post-launch

Lessons learned are collected in the [[Launch Retrospective]] after each
release train closes.
""",
    "ai-platform/Model-Eval-AO-Worker-2026-07.md": """# Model Eval: AO Worker Task-Group (2026-07-13/14)

## Result

The AO worker task-group is **CERTIFIED-local** on qwen3.6-35b-a3b. The
certification procedure is defined in the
[[Qwen-First Model Certification Loop (P-00110)]].

## Routing

Candidate work enters through the routes in
[[gateway-canonical-inference]]; hardware placement per the
[[gpu-fleet-service-map]].

## Caveat

Lane-alias flip awaits an explicit go. Evidence-first: these scores are
retrieval-ranked triage, not verification.
""",
    "ai-platform/Qwen-First-Certification-Loop.md": """# Qwen-First Model Certification Loop (P-00110)

## Doctrine

Local first, always. A task-group runs on the local lane until the loop
certifies or rejects it; only then may a cloud lane be considered.

## Procedure

1. Freeze the task-group prompt set.
2. Run the batch on the local lane via [[gateway-canonical-inference]].
3. Score against the frontier baseline — see the latest
   [AO worker eval](ai-platform/Model-Eval-AO-Worker-2026-07.md).
4. Record `ko.grounding.assessments` after each batch.

## Exit criteria

Two consecutive certified batches, no regression on the held-out set.
""",
    "apps/Majiks-Launch-Runbook.md": """# Majiks Launch Runbook

## Before launch

- Copy and assets conform to the [[Magic-Unicorn-Marketing-Bible|Brand Bible]].
- Inference routes warm — verify against [[gateway-canonical-inference]].
- Cross-check the sibling checklist in [[Meeting-Ops-Launch-Readiness]].

## Launch day

Flip DNS, watch error budgets, keep the rollback tag pinned. Free-vault
search must return in <300ms at p95.

## After

File everything into the [[Launch Retrospective]] within 48h.
""",
    "engineering/meeting-ops-inference-pipeline.md": """# Inference pipeline: concurrent diarization + 24k-slot summary tuning

## Topology

Diarization and summarization run concurrently; slot budget is 24k. GPU
placement follows the [[gpu-fleet-service-map]].

## Tuning notes

Median-under-load is the gate metric — a green mean hides tail collapse.
Raw runs are parked in the [[Diarization Bench Notes]].

## Launch linkage

This pipeline is a hard dependency of [[Meeting-Ops-Launch-Readiness]].
""",
    "apps/Cognitive-Companion.md": """# Cognitive Companion (LtCol Katie / LtCol MacKenzie)

## Purpose

A memory-support companion for cognitive-decline use cases. PHI-adjacent:
all inference stays on our own GPUs.

## Auth

Sign-in is brokered through the suite —
[[suite-sso-brokered-idp-tokens|Suite SSO]] — no app-local passwords.

## Inference

Routes through [[gateway-canonical-inference]] with the PHI gate closed to
cloud fallback.
""",
    "engineering/knowledge-ops-refresh-cadence.md": """# Knowledge-Ops refresh cadence and deletion propagation

## Cadence

Every 24h the `wiki-review` job re-scans the corpus; findings land in the
memory-quality queue. The [[Home]] page links the queues.

## Deletion propagation

Deletes fan out through the outbox to every federated source owner; failed
tasks are redriven, never dropped.

## Evidence policy

Grounding verdicts follow the certification loop — see the
[[Model-Eval-AO-Worker-2026-07|latest model eval]] for current lane status.
""",
    "infrastructure/gpu-fleet-service-map.md": """# GPU fleet service map

## Nodes

bigboy (3090 primary lane), midboy, legacy1 (2×P40 standby), UC-1. The
canonical route table is [[gateway-canonical-inference]].

## Network

Node reachability and ACLs are managed per the
[[headscale-acl-workflow]].

## Rule

Verify the RUNNING artifact — a stale image pin makes deploys silent no-ops.
""",
    "infrastructure/headscale-acl-workflow.md": """# Headscale ACL change workflow

## Workflow

1. Edit the ACL in the repo, never live.
2. Dry-run against current peers.
3. Apply, then probe from BOTH sides of the changed edge.

## Blast radius

Every GPU node in the [[gpu-fleet-service-map]] rides these ACLs — a bad
push strands inference traffic.
""",
    "marketing/Magic-Unicorn-Web-Property-Map-and-Agent-Topology.md": """# Magic Unicorn Web Property Map & Agent Topology

## Domains

Apex plus many CNAME subdomains — repointing the apex breaks ALL of them.
Copy on every property follows the
[[Magic Unicorn — Marketing & Brand Bible (v1)|Brand Bible]].

## Agent topology

Suite agents authenticate via [[suite-sso-brokered-idp-tokens]]; each app
also exposes an MCP surface for agent-first operation.
""",
    "ai-platform/gateway-canonical-inference.md": """# Gateway canonical inference routes

## Canon

One gateway, named lanes, no direct model URLs in app code. Aliases are
pooled; metering is on, capping is not.

## Placement

Lane-to-hardware mapping lives in the [[gpu-fleet-service-map]].

## Certification

New task-groups enter through the
[[Qwen-First-Certification-Loop|Qwen-first certification loop]] before any
cloud lane is considered.
""",
    "engineering/suite-sso-brokered-idp-tokens.md": """# Suite SSO: brokered IdP tokens

## Model

Apps never hold provider credentials — Keycloak brokers the IdP and the
suite exchanges short-lived tokens. Sessions ride an HttpOnly cookie.

## Consumers

Every gateway call is made on behalf of a brokered identity — see
[[gateway-canonical-inference]] for how identity maps to lanes.
""",
    "apps/Meeting-Ops-Launch-Readiness.md": """# Meeting-Ops launch readiness (2026-07-15)

## Status

Signup E2E proven. Remaining gate: a $1 real-card charge end to end.

## Dependencies

- [[meeting-ops-inference-pipeline|Inference pipeline]] tuned and holding
  its p95 under load.
- Launch mechanics mirror the [[Majiks-Launch-Runbook]].

## After launch

Findings roll into the [[Launch Retrospective]].
""",
}

# Bodies edited at runtime via kb.upsert_doc — /kb.links and /kb.graph
# recompute from these, so edits stay consistent.
MD_OVERRIDES = {}


def get_body(row):
    path = row["path"]
    if path in MD_OVERRIDES:
        return MD_OVERRIDES[path]
    return WIKI_BODIES.get(path) or DOC_BODY.format(title=row["title"])


def historical_body(row, version):
    """Plausible older body for the versions listed in kb.doc_history (1..3).

    v3 = current text; v2 drops the final section; v1 is the short first
    draft. Each historical version carries a snapshot footer.
    """
    body = get_body(row)
    if version >= 3 or version <= 0:
        return body
    sections = body.split("\n## ")
    if version == 2:
        kept = sections[:-1] if len(sections) > 2 else sections
        note = "before the July revision tightened the operating sections"
    else:
        kept = sections[:2]
        note = "initial draft, prior to governance review"
    out = "\n## ".join(kept).rstrip()
    return f"{out}\n\n---\n\n*Historical snapshot — v{version} ({note}). Superseded.*"


def full_doc(row):
    d = dict(row)
    d["body"] = get_body(row)
    d["md"] = d["body"]
    d["valid_from"] = iso(days=60)
    d["valid_until"] = None
    d["supersedes_id"] = None
    d["provenance"] = {"source_type": "wiki", "source_ref": row["path"]}
    return d


def find_doc(key):
    for row in DOC_ROWS:
        if key in (row["id"], row["path"]):
            return row
    return None


# ---------------------------------------------------------------- wiki links
# Same resolution order the frontend uses (see shared contract): exact title
# (case-insensitive) → exact path → unique path-suffix → MISSING.
WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]")
MDLINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+\.md)\)")


def resolve_wikilink(target):
    t = target.strip().lower()
    if not t:
        return None
    for row in DOC_ROWS:
        if row["title"].lower() == t:
            return row["path"]
    for row in DOC_ROWS:
        if row["path"].lower() in (t, t + ".md"):
            return row["path"]
    suffix_hits = [row["path"] for row in DOC_ROWS
                   if row["path"].lower().endswith(("/" + t, "/" + t + ".md"))]
    if len(suffix_hits) == 1:
        return suffix_hits[0]
    return None


def resolve_mdlink(href):
    # Regular markdown links to .md files count as wiki links (never missing).
    h = href.strip().lstrip("./").lstrip("/")
    while h.startswith("../"):
        h = h[3:]
    return resolve_wikilink(h)


def compute_links():
    """Recompute the wiki link graph from the doc bodies (regex, live).

    Field names mirror knowledge_ops/graph_api.py /kb.links EXACTLY:
    links[].{from_path,to_path,text}, missing[].{from_path,target},
    orphans[] (docs never linked-TO — linking out does not rescue), count.
    """
    links, missing, linked_to = [], [], set()
    seen_link, seen_miss = set(), set()
    for row in DOC_ROWS:
        src = row["path"]
        body = get_body(row)
        for m in WIKILINK_RE.finditer(body):
            target = m.group(1).strip()
            label = (m.group(2) or "").strip()
            dst = resolve_wikilink(target)
            if dst and dst != src:
                linked_to.add(dst)  # self-links never rescue an orphan
                if (src, dst) not in seen_link:
                    seen_link.add((src, dst))
                    links.append({"from_path": src, "to_path": dst,
                                  "text": label or target})
            elif not dst and (src, target.lower()) not in seen_miss:
                seen_miss.add((src, target.lower()))
                missing.append({"from_path": src, "target": target})
        for m in MDLINK_RE.finditer(body):
            label = m.group(1).strip()
            dst = resolve_mdlink(m.group(2))
            if dst and dst != src:
                linked_to.add(dst)
                if (src, dst) not in seen_link:
                    seen_link.add((src, dst))
                    links.append({"from_path": src, "to_path": dst,
                                  "text": label or m.group(2)})
    orphans = [row["path"] for row in DOC_ROWS if row["path"] not in linked_to]
    return {"links": links, "missing": missing, "orphans": orphans,
            "count": len(links)}


# ---------------------------------------------------------------- passages
def passage(row, i, score, source="wiki", text=None):
    return {
        "source": source,
        "source_id": f"{row['id']}:{i}:rev{i}",
        "title": row["title"],
        "text": text or (
            f"{row['title']} — evidence excerpt {i + 1}. Qwen-first routing, "
            "evidence-first governance, and per-source deletion propagation are "
            "the three invariants this document protects."
        ),
        "score": round(score, 3),
        "scope": row["scope"],
        "owner": row["owner"],
        "uri": f"wiki://{row['path']}",
        "ref": row["path"],
        "metadata": {"path": row["path"], "chunk": i},
    }


def qdrant_passage(coll, i, score, title, text):
    return {
        "source": f"qdrant:{coll}",
        "source_id": f"{coll}-pt-{1000 + i}",
        "title": title,
        "text": text,
        "score": round(score, 3),
        "scope": "global",
        "owner": None,
        "uri": None,
        "ref": f"{coll}:pt-{1000 + i}",
        "metadata": {"collection": coll},
    }


def search_passages(query, k=8):
    rng = random.Random(hash(query) & 0xFFFF)
    out = []
    rows = sorted(DOC_ROWS[:8], key=lambda r: rng.random())
    for i, row in enumerate(rows[: max(2, k - 3)]):
        out.append(passage(row, i, 0.93 - i * 0.07))
    out.append(qdrant_passage(
        "meeting_transcripts", 1, 0.71,
        "Standup 2026-07-14 — AO worker certification",
        "…agreed the AO worker task-group is CERTIFIED-local on qwen3.6-35b-a3b; "
        "lane alias flips only on Aaron's go…"))
    out.append(qdrant_passage(
        "org_memory", 2, 0.66,
        "Org memory — Qwen-first doctrine",
        "Memory: PO tasks are assigned by difficulty lane — Qwen(local)/Flash/"
        "Large/Frontier. Local first, always."))
    out.append(qdrant_passage(
        "stable_documents", 3, 0.61,
        "Stable — grounding copy review",
        "Copy check: keep 'related evidence — heuristic, not verification' on "
        "every constellation link."))
    return out[:k]


# ---------------------------------------------------------------- graph
SRC_PLAN = [  # (collection, node count) — wiki count is len(DOC_ROWS), live
    ("org_memory", 34),
    ("meeting_transcripts", 46),
    ("stable_documents", 20),
]
MEETING_TITLES = [
    "Standup — AO worker certification", "Majiks release train sync",
    "Meeting-Ops pipeline tuning", "Suite entitlement review",
    "Ops-Center deploy retro", "Brigade hardening triage",
    "GPU fleet rebalancing", "Qwen embedding migration check-in",
]
MEMORY_TITLES = [
    "Qwen-first doctrine", "Deployed ≠ merged", "Verify the running artifact",
    "Silent degradation class", "Green signal on the wrong axis",
    "Idle-detection signal rot", "ZFS ARC cap lever", "Evidence-first copy rule",
]
STABLE_TITLES = [
    "Grounding copy review", "Landing draft", "Governance trio spec",
    "MCP manifest notes", "Launch checklist",
]


def build_graph():
    """Rebuilt on every /kb.graph request so wiki-link edges track doc edits.

    Wiki nodes: exactly one per doc, ref = doc path. Wiki↔wiki edges are the
    resolved link web (kind:'link', consistent with /kb.links); every other
    edge is kind:'semantic'.
    """
    rng = random.Random(20260727)
    nodes, edges = [], []
    by_coll = {}

    # wiki: one node per doc, ref = path
    wiki_ids = []
    id_by_path = {}
    for i, row in enumerate(DOC_ROWS):
        nid = f"knowledge_ops_wiki:pt-{i}"
        nodes.append({
            "id": nid, "label": row["title"], "source": "knowledge_ops_wiki",
            "collection": "knowledge_ops_wiki", "scope": row["scope"],
            "size": 2 + (i * 7) % 4, "ref": row["path"],
        })
        wiki_ids.append(nid)
        id_by_path[row["path"]] = nid
    link_web = compute_links()
    for link in link_web["links"]:
        a, b = id_by_path.get(link["from_path"]), id_by_path.get(link["to_path"])
        if a and b:
            edges.append({"a": a, "b": b, "w": 1, "kind": "link"})

    # other collections: chains + shortcuts, all semantic
    for coll, count in SRC_PLAN:
        ids = []
        pool = {"org_memory": MEMORY_TITLES,
                "meeting_transcripts": MEETING_TITLES,
                "stable_documents": STABLE_TITLES}[coll]
        for i in range(count):
            nid = f"{coll}:pt-{i}"
            nodes.append({
                "id": nid, "label": f"{pool[i % len(pool)]} ({i + 1})",
                "source": coll, "collection": coll, "scope": "global",
                "size": 1 + (i * 7 + len(coll)) % 5, "ref": f"pt-{i}",
            })
            ids.append(nid)
        by_coll[coll] = ids
        for a, b in zip(ids, ids[1:]):
            edges.append({"a": a, "b": b, "kind": "semantic",
                          "w": round(0.4 + rng.random() * 0.5, 2)})
        for _ in range(count // 4):
            a, b = rng.sample(ids, 2)
            edges.append({"a": a, "b": b, "kind": "semantic",
                          "w": round(0.3 + rng.random() * 0.4, 2)})
    # cross-source: wiki docs anchor meetings/memory/stable evidence
    for coll in ("org_memory", "meeting_transcripts", "stable_documents"):
        for nid in by_coll[coll][:: 3]:
            edges.append({"a": rng.choice(wiki_ids), "b": nid, "kind": "semantic",
                          "w": round(0.55 + rng.random() * 0.3, 2)})
    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {"node_count": len(nodes), "edge_count": len(edges),
                  "collections": ["knowledge_ops_wiki"] + [c for c, _ in SRC_PLAN]},
    }

# ---------------------------------------------------------------- fixed payloads
STATS = {"sources": [
    {"key": "qdrant:knowledge_ops_wiki", "label": "Knowledge Ops Wiki", "count": 326, "kind": "semantic"},
    {"key": "qdrant:org_memory", "label": "Org Memory", "count": 396, "kind": "semantic"},
    {"key": "qdrant:meeting_transcripts", "label": "Meeting Transcripts", "count": 1734, "kind": "semantic"},
    {"key": "qdrant:stable_documents", "label": "Stable Documents", "count": 62, "kind": "semantic"},
    {"key": "wiki", "label": "Wiki", "count": len(DOC_ROWS), "kind": "canonical"},
    {"key": "file_index", "label": "File index", "count": 512, "kind": "files"},
    {"key": "metrics", "label": "Metrics", "count": 240, "kind": "metrics"},
    {"key": "falkordb:org_graph", "label": "Org Graph", "count": 0, "kind": "graph"},
]}

LIST_SOURCES = {"sources": [
    {"name": "wiki", "enabled": True, "status": "ok",
     "detail": f"sqlite wiki store, {len(DOC_ROWS)} governed docs"},
    {"name": "qdrant", "enabled": True, "status": "ok", "detail": "4 collections · 2,518 points"},
    {"name": "file_index", "enabled": True, "status": "ok", "detail": "512 chunks over /srv/shared"},
    {"name": "falkordb", "enabled": True, "status": "configured", "detail": "org_graph awaiting population"},
    {"name": "center_deep", "enabled": False, "status": "disabled", "detail": ""},
    {"name": "rest", "enabled": True, "status": "ok", "detail": "stable bridge (read-only)"},
]}

AUDIT = {"events": [
    {"actor": "aaron", "action": "wiki.upsert", "resource": DOC_ROWS[3]["id"], "outcome": "success", "recorded_at": iso(hours=2)},
    {"actor": "librarian-agent", "action": "memory.review_scan", "resource": "wiki", "outcome": "success", "recorded_at": iso(hours=6)},
    {"actor": "aaron", "action": "openwiki.approve", "resource": "imp-2026-07-24-a", "outcome": "approved", "recorded_at": iso(days=1)},
    {"actor": "scheduler", "action": "refresh.run", "resource": "wiki-review", "outcome": "success", "recorded_at": iso(days=1, hours=3)},
    {"actor": "aaron", "action": "wiki.delete", "resource": "doc-0000000042", "outcome": "success", "recorded_at": iso(days=2)},
]}

REVIEW_ITEMS = {"items": [
    {"id": 1, "kind": "stale", "status": "open", "doc_id": DOC_ROWS[7]["id"],
     "path": DOC_ROWS[7]["path"], "title": DOC_ROWS[7]["title"],
     "detail": "review_due_at passed 12 days ago", "created_at": iso(days=12)},
    {"id": 2, "kind": "conflict", "status": "open", "doc_id": DOC_ROWS[2]["id"],
     "path": DOC_ROWS[2]["path"], "title": DOC_ROWS[2]["title"],
     "detail": "possible contradiction with meeting_transcripts pt-1001 on lane-alias timing",
     "created_at": iso(days=3)},
]}

REFRESH_JOBS = {"jobs": [
    {"job_id": "wiki-review", "kind": "review_scan", "source_ref": "wiki",
     "interval_seconds": 86400, "enabled": True, "next_due_at": iso(days=-1)},
    {"job_id": "qdrant-wiki-sync", "kind": "connector_refresh", "source_ref": "qdrant:knowledge_ops_wiki",
     "interval_seconds": 21600, "enabled": True, "next_due_at": iso(hours=-4)},
], "runs": [
    {"run_id": "run-9101", "job_id": "wiki-review", "status": "succeeded", "scheduled_for": iso(days=1)},
    {"run_id": "run-9100", "job_id": "qdrant-wiki-sync", "status": "succeeded", "scheduled_for": iso(days=1, hours=6)},
    {"run_id": "run-9099", "job_id": "qdrant-wiki-sync", "status": "failed", "scheduled_for": iso(days=2)},
]}

OPERATIONS = {
    "worker": {"ready": True, "fresh_running_workers": 1,
               "worker": {"worker_kind": "scheduler", "status": "running", "heartbeat_at": iso(hours=0)}},
    "refresh": {"succeeded": 41, "failed": 1},
    "deletion_outbox": {"pending": 1, "failed": 1, "succeeded": 12},
    "deletion_tasks": [
        {"task_id": "del-301", "adapter": "qdrant", "source_ref": "knowledge_ops_wiki/doc-0000000042",
         "status": "failed", "attempt_count": 3, "max_attempts": 5},
        {"task_id": "del-302", "adapter": "file_index", "source_ref": "/srv/shared/old-runbook.md",
         "status": "pending", "attempt_count": 0, "max_attempts": 5},
    ],
    "sources": [{"name": "wiki", "status": "ok"}, {"name": "qdrant", "status": "ok"}],
}

OPENWIKI_IMPORTS = {"imports": [
    {"import_id": "imp-2026-07-26-a", "state": "staged", "document_count": 3, "staged_at": iso(days=1),
     "findings": [{"severity": "blocking", "category": "pii", "path": "hr/on-call-roster.md",
                   "detail": "phone numbers detected — needs explicit PII acceptance"}]},
    {"import_id": "imp-2026-07-24-a", "state": "approved", "document_count": 5, "staged_at": iso(days=3),
     "findings": []},
]}


def openwiki_import_detail(import_id):
    base = next((i for i in OPENWIKI_IMPORTS["imports"] if i["import_id"] == import_id),
                OPENWIKI_IMPORTS["imports"][0])
    item = dict(base)
    item["documents"] = [
        {"path": "hr/on-call-roster.md", "okf_type": "reference", "scope": "global"},
        {"path": "engineering/incident-runbook.md", "okf_type": "runbook", "scope": "global"},
        {"path": "notes/scratch.md", "okf_type": None, "scope": "global"},
    ][: item["document_count"]]
    if item["state"] == "approved":
        item["reviewed_at"] = iso(days=2)
        item["reviewed_by"] = "aaron"
    return {"import": item}


def metrics_payload(name, limit):
    rng = random.Random(hash(name) & 0xFFFF)
    n = min(int(limit or 120), 120)
    base = 2.0 + (hash(name) % 50) / 10.0
    rows = []
    for i in range(n):
        rows.append({
            "name": name,
            "value": round(base + rng.uniform(-0.4, 0.6) + i * 0.01, 3),
            "tags": {"host": "bigboy" if i % 3 else "midboy"},
            "recorded_at": iso(hours=n - i),
        })
    return {"metrics": rows}


def metric_chart_svg(name):
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 240 60' width='240' height='60'>"
            "<polyline fill='none' stroke='#46c2b3' stroke-width='1.5' "
            "points='3,50 30,44 60,46 90,38 120,40 150,30 180,26 210,28 237,18'/>"
            f"<title>{name}</title></svg>")


def inspect_citation(body):
    source = body.get("source", "wiki")
    source_id = body.get("source_id", "")
    if not (source == "wiki" or "wiki" in source or source == "file_index"):
        return {"citation": {
            "claim_id": "clm-mock", "citation_id": "cit-mock", "source": source,
            "source_id": source_id, "inspection_available": False,
            "reason": "The source adapter does not expose an inspectable local span.",
        }}
    row = DOC_ROWS[1]
    excerpt = ("The AO worker task-group is CERTIFIED-local on qwen3.6-35b-a3b. "
               "Lane-alias flip awaits an explicit go. Evidence-first: related "
               "passages are heuristic, not verification.")
    return {"citation": {
        "claim_id": "clm-8f31", "citation_id": "cit-2b90", "source": "wiki",
        "source_id": source_id or f"{row['id']}:0:rev0",
        "source_path": row["path"], "inspection_available": True,
        "excerpt": excerpt, "source_span": {"start": 812, "end": 812 + len(excerpt)},
        "match_ranges": [[4, 34], [63, 78]],
        "lifecycle": {"state": "active", "valid_from": iso(days=60), "valid_until": None},
    }}


def find_file(query, k):
    rows = []
    for i, name in enumerate(["deploy/knowledge-ops.service", "scripts/backup.sh",
                              "docs/OPERATIONS-RUNBOOK.md", "wiki/marketing/Magic-Unicorn-Marketing-Bible.md"]):
        rows.append({
            "source": "file_index", "source_id": f"file-{i}", "title": os.path.basename(name),
            "text": f"…{query} appears in {name} near the refresh-cadence section…",
            "score": round(0.9 - i * 0.12, 3), "scope": "global", "owner": None,
            "uri": name, "metadata": {"path": name},
        })
    return {"passages": rows[: int(k or 8)]}


def deep_read(path, query):
    chunks = []
    for i in range(4):
        chunks.append({
            "source": "file_index", "source_id": f"{path}#c{i}", "title": f"chunk {i}",
            "text": (f"[{path} · chunk {i}]\n"
                     f"Lines {i * 40 + 1}-{i * 40 + 40}: context around '{query}'.\n"
                     "refresh cadence: 24h · deletion outbox: redrive on failure\n"
                     "evidence-first: heuristic retrieval is never verified fact."),
            "score": round(0.85 - i * 0.1, 3), "scope": "global", "owner": None,
            "uri": path, "metadata": {"path": path, "chunk": i},
        })
    return {"passages": chunks}


# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        sys.stderr.write("[mock] %s\n" % (fmt % args))

    def send_json(self, obj, status=200):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, fs_path):
        if not os.path.isfile(fs_path):
            return self.send_json({"detail": "Not found."}, 404)
        ctype = mimetypes.guess_type(fs_path)[0] or "application/octet-stream"
        if fs_path.endswith(".js"):
            ctype = "text/javascript"
        with open(fs_path, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # ---- GET -------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path

        # static
        if p == "/" or p == "/ui":
            self.send_response(307)
            self.send_header("Location", "/ui/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.startswith("/ui/"):
            rel = p[len("/ui/"):] or "index.html"
            return self.send_file(os.path.join(STATIC, *rel.split("/")))
        if p == "/landing" or p == "/landing/":
            return self.send_file(os.path.join(STATIC, "landing", "index.html"))
        if p.startswith("/landing/"):
            rel = p[len("/landing/"):]
            return self.send_file(os.path.join(STATIC, "landing", *rel.split("/")))
        if p == "/.well-known/mcps.json":
            return self.send_file(os.path.join(ROOT, ".well-known", "mcps.json"))

        if p == "/ready":
            return self.send_json({"ok": True, "checks": {"wiki": "ok", "qdrant": "ok"}})
        if p == "/health":
            return self.send_json({"ok": True, "service": "knowledge-ops-mock"})

        if p == "/kb.me":
            admin = q.get("as") == "admin" or os.environ.get("MOCK_ADMIN") == "1"
            return self.send_json({
                "name": "aaron",
                "scopes": ["read:knowledge", "write:knowledge"] + (["admin:knowledge"] if admin else []),
                "is_admin": admin,
            })
        if p == "/kb.graph":
            return self.send_json(build_graph())
        if p == "/kb.links":
            return self.send_json(compute_links())
        if p == "/kb.stats":
            return self.send_json(STATS)
        if p == "/kb.docs":
            return self.send_json({"docs": DOC_ROWS})
        if p == "/kb.get_doc":
            row = find_doc(q.get("id") or q.get("path") or "")
            return self.send_json({"doc": full_doc(row) if row else None})
        if p == "/kb.doc_history":
            return self.send_json({"history": [
                {"version_id": 3, "created_at": iso(days=1), "bytes": 4180},
                {"version_id": 2, "created_at": iso(days=9), "bytes": 3921},
                {"version_id": 1, "created_at": iso(days=44), "bytes": 2610},
            ]})
        if p == "/kb.doc_version":
            row = find_doc(q.get("id") or q.get("path") or "")
            try:
                v = int(q.get("version_id") or q.get("version") or 0)
            except ValueError:
                v = 0
            body = historical_body(row, v) if row else ""
            created = iso(days={1: 44, 2: 9}.get(v, 1))
            # Field names mirror knowledge_ops/graph_api.py /kb.doc_version.
            return self.send_json({"version": {
                "version_id": v or None, "version": v or None,
                "doc_id": (row or {}).get("id"), "body": body,
                "created_at": created, "saved_at": created,
                "bytes": len(body),
            }})
        if p == "/kb.list_sources":
            return self.send_json(LIST_SOURCES)
        if p == "/kb.audit":
            return self.send_json(AUDIT)
        if p == "/kb.review_items":
            return self.send_json(REVIEW_ITEMS)
        if p == "/kb.refresh_jobs":
            return self.send_json(REFRESH_JOBS)
        if p == "/kb.operations":
            return self.send_json(OPERATIONS)
        if p == "/kb.deletion_outbox":
            return self.send_json({"tasks": OPERATIONS["deletion_tasks"]})
        if p == "/kb.openwiki_imports":
            return self.send_json(OPENWIKI_IMPORTS)
        if p == "/kb.openwiki_import":
            return self.send_json(openwiki_import_detail(q.get("id", "")))
        if p == "/kb.find_file":
            return self.send_json(find_file(q.get("query", ""), q.get("k")))
        if p == "/kb.deep_read":
            return self.send_json(deep_read(q.get("path", ""), q.get("query", "")))
        if p == "/kb.metrics":
            return self.send_json(metrics_payload(q.get("name", "metric"), q.get("limit")))
        if p == "/kb.get_metric_chart":
            raw = metric_chart_svg(q.get("name", "metric")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self.send_json({"detail": "Not found."}, 404)

    # ---- POST ------------------------------------------------------------
    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        body = self.body_json()

        if p == "/kb.search":
            query = body.get("query", "")
            passages = search_passages(query, int(body.get("k") or 8))
            return self.send_json({"query": query, "answer": None, "passages": passages})
        if p == "/kb.ground":
            query = body.get("query", "")
            passages = search_passages(query, int(body.get("k") or 8))
            for pa in passages[:2]:
                pa.setdefault("metadata", {})["evidence_relation"] = "possible_support"
            return self.send_json({
                "query": query,
                "verdict": "possible_support",
                "method": "lexical_polarity_triage_v1",
                "answer": (f"Heuristic assessment for “{query}”: the retrieved "
                           "evidence leans supportive. This is retrieval-ranked "
                           "triage — related evidence, not verification."),
                "passages": passages,
            })
        if p == "/kb.inspect_citation":
            return self.send_json(inspect_citation(body))
        if p == "/kb.upsert_doc":
            path = body.get("path", "unfiled/new-doc.md")
            row = find_doc(path)
            if not row:
                title = os.path.splitext(os.path.basename(path))[0].replace("-", " ")
                row = doc_row(path, title, len(DOC_ROWS))
                DOC_ROWS.append(row)
            if body.get("md") is not None:
                # keep /kb.links + /kb.graph consistent with the saved body
                MD_OVERRIDES[row["path"]] = body["md"]
            row["updated_at"] = iso()
            return self.send_json({"id": row["id"]})
        if p == "/kb.delete_doc":
            return self.send_json({"deletion": {"doc_id": q.get("id"), "state": "tombstoned",
                                                "outbox_tasks": 2, "reason": body.get("reason")}})
        if p == "/kb.review_scan":
            return self.send_json({"scanned": len(DOC_ROWS), "stale": 1, "conflicts": 1, "opened": 2})
        if p == "/kb.review_resolve":
            item = dict(REVIEW_ITEMS["items"][0])
            item.update({"id": int(q.get("id") or 1), "status": "resolved", "note": body.get("note")})
            return self.send_json({"item": item})
        if p == "/kb.refresh_job":
            return self.send_json({"job": {**body, "next_due_at": iso(hours=-24)}})
        if p == "/kb.refresh_due":
            return self.send_json({"runs": [{"run_id": "run-9102", "job_id": "wiki-review",
                                             "status": "running", "scheduled_for": iso()}]})
        if p == "/kb.index_files":
            return self.send_json({"root": q.get("root"), "indexed": 128, "skipped": 4})
        if p == "/kb.openwiki_approve" or p == "/kb.openwiki_reject":
            state = "approved" if p.endswith("approve") else "rejected"
            for imp in OPENWIKI_IMPORTS["imports"]:
                if imp["import_id"] == q.get("id"):
                    imp["state"] = state
            return self.send_json({"import_id": q.get("id"), "state": state, "note": body.get("note")})
        if p.startswith("/kb.deletion_outbox/") and p.endswith("/redrive"):
            task_id = p.split("/")[2]
            for t in OPERATIONS["deletion_tasks"]:
                if t["task_id"] == task_id:
                    t["status"] = "pending"
            return self.send_json({"task_id": task_id, "status": "pending"})

        self.send_json({"detail": "Not found."}, 404)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8765))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[mock] Knowledge-Ops mock API on http://127.0.0.1:{port}/ui/  (landing: /landing/)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
