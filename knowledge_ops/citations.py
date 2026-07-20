from __future__ import annotations

import json
import re
from typing import Any

from .authorization import AuthorizationError, Principal, can_read_record
from .storage import connect, init_db
from .text import STOPWORDS, stable_id, tokenize
from .wiki import get_doc


def _matches(claim: str, excerpt: str, absolute_start: int) -> tuple[list[str], list[dict[str, int]]]:
    terms = sorted({term for term in tokenize(claim) if term not in STOPWORDS and len(term) > 2})
    ranges: list[dict[str, int]] = []
    matched: list[str] = []
    for term in terms:
        found = False
        for occurrence in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", excerpt, re.I):
            found = True
            ranges.append({
                "start": occurrence.start(), "end": occurrence.end(),
                "source_start": absolute_start + occurrence.start(),
                "source_end": absolute_start + occurrence.end(),
            })
        if found:
            matched.append(term)
    ranges.sort(key=lambda item: (item["start"], item["end"]))
    return matched, ranges


def inspect_citation(*, source: str, source_id: str, claim: str, principal: Principal) -> dict[str, Any]:
    principal.require("read:knowledge")
    claim_id = stable_id("claim:" + " ".join(tokenize(claim)))
    citation_id = stable_id(f"citation:{claim_id}:{source}:{source_id}")
    if source not in {"wiki", "file_index"}:
        return {
            "claim_id": claim_id, "citation_id": citation_id, "source": source,
            "source_id": source_id, "inspection_available": False,
            "reason": "The source adapter does not expose an inspectable local span.",
        }

    init_db()
    if source == "wiki":
        try:
            parts = source_id.rsplit(":", 2)
            if len(parts) == 3:
                doc_id, chunk_index_raw, expected_revision = parts
            else:
                doc_id, chunk_index_raw = parts
                expected_revision = ""
            chunk_index = int(chunk_index_raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid wiki citation id") from exc
        doc = get_doc(doc_id)
        if not doc or doc["lifecycle_state"] == "tombstoned":
            raise ValueError("citation source was not found")
        if not can_read_record(principal, doc):
            raise AuthorizationError("citation source is outside the caller's visibility")
        if expected_revision and stable_id(doc["body"]) != expected_revision:
            raise ValueError("citation source changed; rerun the query for a current source span")
        from .text import chunk_text_with_spans

        chunks = chunk_text_with_spans(doc["body"])
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise ValueError("citation span was not found")
        excerpt, span_start, span_end = chunks[chunk_index]
        provenance = doc.get("provenance")
        source_path = doc["path"]
        lifecycle = {
            "state": doc["lifecycle_state"], "valid_from": doc["valid_from"],
            "valid_until": doc["valid_until"],
        }
    else:
        with connect() as db:
            row = db.execute("SELECT * FROM file_chunks WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise ValueError("citation source was not found")
        record = dict(row)
        if not can_read_record(principal, record):
            raise AuthorizationError("citation source is outside the caller's visibility")
        excerpt = record["text"]
        span_start, span_end = int(record["span_start"]), int(record["span_end"])
        if span_end <= span_start:
            return {
                "claim_id": claim_id, "citation_id": citation_id, "source": source,
                "source_id": source_id, "inspection_available": False,
                "reason": "This legacy file chunk must be reindexed before exact span inspection.",
            }
        provenance = None
        source_path = record["path"]
        lifecycle = None

    matched_terms, match_ranges = _matches(claim, excerpt, span_start)
    if provenance and "metadata_json" in provenance:
        provenance = dict(provenance)
        provenance["metadata"] = json.loads(provenance.pop("metadata_json", "{}"))
    return {
        "claim_id": claim_id,
        "citation_id": citation_id,
        "source": source,
        "source_id": source_id,
        "inspection_available": True,
        "source_path": source_path,
        "source_span": {"start": span_start, "end": span_end, "unit": "unicode_codepoint"},
        "excerpt": excerpt,
        "matched_terms": matched_terms,
        "match_ranges": match_ranges,
        "provenance": provenance,
        "lifecycle": lifecycle,
    }
