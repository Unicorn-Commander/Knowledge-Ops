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
from ..models import Passage
from ..text import STOPWORDS, lexical_score, tokenize

# Only alphanumerics survive into the Cypher string literal we build, so the
# inlined token cannot break out of the quoted CONTAINS clause. We additionally
# use the read-only query command as defense in depth.
_SAFE = re.compile(r"[^a-z0-9]")


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
        "n.entity_type AS entity_type, n.type AS ntype, n.summary AS summary "
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
            label, name, entity_type, ntype, summary = (list(row) + [None] * 5)[:5]
        except Exception:
            continue
        if not name:
            continue
        kind = entity_type or ntype or label or "Entity"
        text = summary or f"{name} — {kind} (graph entity)"
        hits.append(Passage(
            source=f"falkordb:{graph}",
            source_id=str(name),
            title=str(name),
            text=str(text),
            score=lexical_score(query, str(text), str(name)) + 1.0,
            scope="global",
            owner="magic-unicorn",
            uri="",
            metadata={"label": label, "kind": kind, "graph": graph},
        ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


async def search_falkordb(query: str, k: int = 5, scope: str | None = None) -> list[Passage]:
    if not settings.enable_falkordb:
        return []
    # The redis client is sync; run it off the event loop so we never block it.
    return await asyncio.to_thread(_query_sync, query, k, settings.falkordb_graph)
