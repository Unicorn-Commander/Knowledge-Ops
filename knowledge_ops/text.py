from __future__ import annotations

import hashlib
import re
from pathlib import Path

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "what", "where", "who", "why",
}


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def chunk_text(text: str, max_chars: int = 1400, overlap: int = 180) -> list[str]:
    return [chunk for chunk, _, _ in chunk_text_with_spans(text, max_chars=max_chars, overlap=overlap)]


def chunk_text_with_spans(text: str, max_chars: int = 1400, overlap: int = 180) -> list[tuple[str, int, int]]:
    """Chunk text while retaining exact character offsets into the source."""
    text = text or ""
    if not text:
        return []
    chunks: list[tuple[str, int, int]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if split > start + max_chars // 2:
                end = split + 1
        raw = text[start:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        span_start = start + leading
        span_end = end - trailing
        if span_end > span_start:
            chunks.append((text[span_start:span_end], span_start, span_end))
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def lexical_score(query: str, text: str, title: str = "") -> float:
    q = [t for t in tokenize(query) if t not in STOPWORDS]
    if not q:
        return 0.0
    hay = tokenize((title or "") + " " + (text or ""))
    if not hay:
        return 0.0
    counts: dict[str, int] = {}
    for t in hay:
        counts[t] = counts.get(t, 0) + 1
    score = 0.0
    hay_text = ((title or "") + " " + (text or "")).lower()
    title_text = (title or "").lower()
    for term in q:
        if term in counts:
            score += 2.0 + min(counts[term], 5) * 0.20
            if term in title_text:
                score += 0.75
    phrase = query.lower().strip()
    if phrase and phrase in hay_text:
        score += 4.0
    meaningful_phrase = " ".join(q)
    if len(q) > 1 and meaningful_phrase in hay_text:
        score += 6.0
    if "one-sentence story" in hay_text or "master one-liner" in hay_text:
        score += 1.5
    return score / max(1.0, len(q))
