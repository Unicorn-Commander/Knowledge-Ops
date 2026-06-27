from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Passage:
    source: str
    source_id: str
    title: str
    text: str
    score: float
    scope: str = "global"
    owner: str = "magic-unicorn"
    uri: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def citation(self) -> str:
        label = self.title or self.source_id
        return f"{self.source}:{label}"


@dataclass
class SearchResponse:
    query: str
    passages: list[Passage]
    answer: str


@dataclass
class SourceStatus:
    name: str
    enabled: bool
    status: str
    detail: str = ""
