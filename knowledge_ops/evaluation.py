from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

from .authorization import Principal, system_principal
from .citations import inspect_citation
from .config import settings
from .federation import ground, search
from .storage import init_db
from .text import STOPWORDS, tokenize
from .wiki import tombstone_doc, upsert_doc


@contextmanager
def _isolated_store() -> Iterator[None]:
    fields = (
        "data_dir", "wiki_dir", "db_path", "openwiki_staging_dir",
        "ai_memory_chunks", "default_file_roots",
    )
    original = {name: getattr(settings, name) for name in fields}
    with TemporaryDirectory(prefix="knowledge-ops-eval-") as tmp:
        root = Path(tmp)
        settings.data_dir = root / "data"
        settings.wiki_dir = root / "wiki"
        settings.db_path = root / "data" / "evaluation.sqlite3"
        settings.openwiki_staging_dir = root / "data" / "openwiki" / "staging"
        settings.ai_memory_chunks = root / "absent.jsonl"
        settings.default_file_roots = [root]
        try:
            init_db()
            yield
        finally:
            for name, value in original.items():
                setattr(settings, name, value)


def _rank(passages, expected_id: str) -> int | None:
    for index, passage in enumerate(passages, 1):
        if passage.metadata.get("document_id") == expected_id:
            return index
    return None


async def _run() -> dict:
    admin = system_principal()
    alice = Principal(
        subject="alice@example.com",
        scopes=frozenset({"read:knowledge", "write:knowledge"}),
        identities=frozenset({"alice@example.com"}),
    )
    bob = Principal(
        subject="bob@example.com",
        scopes=frozenset({"read:knowledge"}),
        identities=frozenset({"bob@example.com"}),
    )

    old_id = upsert_doc(
        "policy/retention-2025-01.md",
        "# Retention Policy\n\nThe Quasar retention period is thirty days.",
        valid_from="2025-01-01T00:00:00+00:00",
    )
    new_id = upsert_doc(
        "policy/retention-2025-02.md",
        "# Retention Policy\n\nThe Quasar retention period is ninety days.",
        valid_from="2025-02-01T00:00:00+00:00",
        supersedes_id=old_id,
    )
    current = await search(
        "Quasar retention ninety days", sources=["wiki"], k=5, principal=admin,
    )
    historical = await search(
        "Quasar retention thirty days", sources=["wiki"], k=5, principal=admin,
        as_of="2025-01-15T00:00:00+00:00",
    )

    upsert_doc("facts/launch-positive.md", "# Launch Window\n\nThe Helios launch window is Friday.")
    upsert_doc("facts/launch-negative.md", "# Launch Window\n\nThe Helios launch window is not Friday.")
    conflict = await ground(
        "The Helios launch window is Friday", sources=["wiki"], k=8, principal=admin,
    )
    abstention = await ground(
        "Where is the ZYXQ-9917 transponder stored?", sources=["wiki"], k=5, principal=admin,
    )

    upsert_doc(
        "personal/alice-secret.md",
        "# Private Recall\n\nThe unique cobalt phrase is LANTERN-4821.",
        "personal", alice.subject,
    )
    unauthorized = await search(
        "LANTERN-4821", sources=["wiki"], k=5, principal=bob,
    )

    delete_id = upsert_doc(
        "facts/deletion.md", "# Deletion Fixture\n\nThe deletion nonce is OBSIDIAN-7755."
    )
    before_delete = await search(
        "OBSIDIAN-7755", sources=["wiki"], k=5, principal=admin,
    )
    tombstone_doc(delete_id, principal=admin, reason="deterministic deletion evaluation")
    after_delete = await search(
        "OBSIDIAN-7755", sources=["wiki"], k=5, principal=admin,
    )

    ranks = [_rank(current.passages, new_id), _rank(historical.passages, old_id)]
    recall_at_5 = sum(rank is not None and rank <= 5 for rank in ranks) / len(ranks)
    mrr = sum((1 / rank) if rank else 0 for rank in ranks) / len(ranks)

    citation_checks = []
    for response, expected_id, claim in (
        (current, new_id, "Quasar retention ninety days"),
        (historical, old_id, "Quasar retention thirty days"),
    ):
        passage = next((item for item in response.passages if item.metadata.get("document_id") == expected_id), None)
        if not passage:
            citation_checks.append(False)
            continue
        inspected = inspect_citation(
            source=passage.source, source_id=passage.source_id, claim=claim, principal=admin,
        )
        span = inspected["source_span"]
        expected_terms = {
            term for term in tokenize(claim) if term not in STOPWORDS and len(term) > 2
        }
        citation_checks.append(
            inspected["inspection_available"]
            and inspected["excerpt"] == passage.text
            and span == passage.metadata["source_span"]
            and expected_terms <= set(inspected["matched_terms"])
        )
    citation_precision = sum(citation_checks) / len(citation_checks)

    scenarios = {
        "temporal_current": ranks[0] == 1,
        "temporal_as_of": ranks[1] == 1,
        "contradiction_triage": conflict["verdict"] == "possible_conflict",
        "abstention": abstention["verdict"] == "not_enough_evidence" and not abstention["passages"],
        "unauthorized_recall_zero": len(unauthorized.passages) == 0,
        "deletion_recall_zero": bool(before_delete.passages) and len(after_delete.passages) == 0,
        "citation_spans_exact": all(citation_checks),
    }
    metrics = {
        "recall_at_5": round(recall_at_5, 4),
        "mrr": round(mrr, 4),
        "citation_precision": round(citation_precision, 4),
        "unauthorized_recall": len(unauthorized.passages),
        "deleted_recall": len(after_delete.passages),
    }
    thresholds = {
        "recall_at_5": 1.0,
        "mrr": 1.0,
        "citation_precision": 1.0,
        "unauthorized_recall": 0,
        "deleted_recall": 0,
    }
    passed = all(scenarios.values()) and all(metrics[name] == target for name, target in thresholds.items())
    return {
        "suite": "knowledge-ops/memory-quality-v1",
        "passed": passed,
        "metrics": metrics,
        "thresholds": thresholds,
        "scenarios": scenarios,
    }


def evaluate_memory() -> dict:
    """Run a deterministic, offline evaluation against an isolated database."""
    with _isolated_store():
        return asyncio.run(_run())
