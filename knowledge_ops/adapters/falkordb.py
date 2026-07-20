"""Read-only FalkorDB (graph) adapter.

Backing store on bigboy: ``unicorn-falkordb:6379`` (RedisGraph protocol).
We query named graphs (default ``brigade_global``) with ``GRAPH.RO_QUERY`` so the
adapter can NEVER mutate the graph. It is disabled by default and degrades to an
empty result on any connection/parse error, so an unreachable store never crashes
a federated search.

Enable with ``KNOWLEDGE_OPS_ENABLE_FALKORDB=1`` and point
``KNOWLEDGE_OPS_FALKORDB_URL`` at the store (e.g. ``redis://unicorn-falkordb:6379``
in-network, or ``redis://<host>:6380`` for the published port).
"""

from __future__ import annotations

import asyncio
import re

from ..config import settings
from ..authorization import Principal, can_read_record
from ..models import Passage
from ..text import STOPWORDS, lexical_score, tokenize

# Only alphanumerics survive into the Cypher string literal we build, so the
# inlined token cannot break out of the quoted CONTAINS clause. We additionally
# use the read-only query command as defense in depth.
_SAFE = re.compile(r"[^a-z0-9]")


def classify_graph(graph: str) -> tuple[str, str]:
    """Conservative fallback when graph nodes lack explicit scope metadata."""
    lower = graph.lower()
    if lower.startswith("cust_ops:ws:"):
        return f"workspace:{graph.split(':', 2)[2]}", graph
    personal = re.search(r"__([a-z0-9.-]+)_personal$", lower)
    if personal:
        return "personal", personal.group(1)
    org_global = {
        "brigade_global", "brigade_knowledge", "agent_meeting_ops_canonical",
        "contact_ops__magic_unicorn_inc", "majiks_research",
    }
    if lower in org_global:
        return "global", "magic-unicorn"
    return "restricted", graph


def _terms(query: str, limit: int = 6) -> list[str]:
    out: list[str] = []
    for tok in tokenize(query):
        if tok in STOPWORDS or len(tok) < 3:
            continue
        clean = _SAFE.sub("", tok)
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= limit:
            break
    return out


def _query_sync(query: str, k: int, graph: str) -> list[Passage]:
    try:
        import redis  # lazy: keep the base install slim
    except ModuleNotFoundError:
        return []

    terms = _terms(query)
    if not terms:
        return []

    where = " OR ".join(f"toLower(n.name) CONTAINS '{t}'" for t in terms)
    cypher = (
        "MATCH (n) WHERE n.name IS NOT NULL AND (" + where + ") "
        "RETURN labels(n)[0] AS label, n.name AS name, "
        "n.entity_type AS entity_type, n.type AS ntype, n.summary AS summary, "
        "n.scope AS scope, n.owner AS owner "
        f"LIMIT {max(1, int(k))}"
    )

    try:
        client = redis.Redis.from_url(
            settings.falkordb_url, socket_timeout=5, socket_connect_timeout=5, decode_responses=True
        )
        reply = client.execute_command("GRAPH.RO_QUERY", graph, cypher)
    except Exception:
        return []
    finally:
        try:
            client.close()  # type: ignore[possibly-undefined]
        except Exception:
            pass

    # Reply shape: [header, rows, stats]; each row is a list of column values.
    try:
        rows = reply[1] if len(reply) >= 2 else []
    except Exception:
        return []

    hits: list[Passage] = []
    for row in rows:
        try:
            label, name, entity_type, ntype, summary, node_scope, node_owner = (list(row) + [None] * 7)[:7]
        except Exception:
            continue
        if not name:
            continue
        kind = entity_type or ntype or label or "Entity"
        text = summary or f"{name} — {kind} (graph entity)"
        fallback_scope, fallback_owner = classify_graph(graph)
        item_scope = str(node_scope) if node_scope in {"global", "personal", "restricted"} else fallback_scope
        item_owner = str(node_owner or fallback_owner)
        hits.append(Passage(
            source=f"falkordb:{graph}",
            source_id=str(name),
            title=str(name),
            text=str(text),
            score=lexical_score(query, str(text), str(name)) + 1.0,
            scope=item_scope,
            owner=item_owner,
            uri="",
            metadata={"label": label, "kind": kind, "graph": graph},
        ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


async def search_falkordb(
    query: str,
    k: int = 5,
    scope: str | None = None,
    graphs: list[str] | None = None,
    *,
    principal: Principal | None = None,
) -> list[Passage]:
    if not settings.enable_falkordb:
        return []
    if principal is None:
        return []
    # The redis client is sync; run it off the event loop so we never block it.
    hits: list[Passage] = []
    for graph in graphs or settings.falkordb_graphs:
        graph_scope, graph_owner = classify_graph(graph)
        if not can_read_record(principal, {"scope": graph_scope, "owner": graph_owner}, scope):
            continue
        hits.extend(await asyncio.to_thread(_query_sync, query, k, graph))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
