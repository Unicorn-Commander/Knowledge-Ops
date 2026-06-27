from __future__ import annotations

import json

from ..config import settings
from ..models import Passage
from ..text import lexical_score


def search_ai_memory(query: str, k: int = 5, scope: str | None = None) -> list[Passage]:
    path = settings.ai_memory_chunks
    if not path.exists():
        return []
    hits: list[Passage] = []
    with path.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            item_scope = item.get("scope", "personal")
            if scope and scope != "all" and item_scope != scope:
                continue
            text = item.get("text", "")
            title = item.get("title", "")
            score = lexical_score(query, text, title)
            if score <= 0:
                continue
            hits.append(Passage(
                source="org_memory",
                source_id=item.get("id", ""),
                title=title,
                text=text,
                score=score,
                scope=item_scope,
                owner="aaron" if item_scope == "personal" else "magic-unicorn",
                uri=item.get("source_file", ""),
                metadata={"tags": item.get("tags", []), "cluster": item.get("cluster"), "as_of": item.get("as_of")},
            ))
    return sorted(hits, key=lambda h: h.score, reverse=True)[:k]
