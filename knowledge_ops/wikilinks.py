"""Wiki-link semantics: parsing, resolution, and the doc-to-doc link graph.

Pure functions over in-memory doc dicts — no I/O, no store access. The wiki
store (``wiki.py``) and the HTTP layer (``graph_api.py``) feed docs in.

Contract (shared with the frontend renderer):
  * ``[[Target]]`` / ``[[Target|label]]`` are wikilinks.
  * Regular markdown links whose href ends in ``.md`` are also links for the
    graph (kind ``mdlink``) but are never red-linked when unresolved.
  * Resolution order against the doc index: exact title match
    (case-insensitive) -> exact path match -> unique path-suffix match ->
    otherwise MISSING.
  * Text inside fenced code blocks and inline code spans never parses.

Targets and labels are returned as plain data; HTML-escaping happens at render
time in the UI (md.js escapes before markdown transforms).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

__all__ = ["parse_links", "resolver", "build_link_graph"]

# A fence opener: up to 3 leading spaces then a run of >= 3 backticks or tildes.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# Inline code span: a backtick run closed by an identical-length run. `.` does
# not match newlines, so spans never leak across lines.
_INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
# [[Target]] / [[Target|label]] — target may not contain brackets or a pipe,
# so nested "[[a [[b]] c]]" yields only the innermost well-formed link.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|\n]+?)(?:\|([^\[\]\n]*?))?\]\]")
# [label](href), [label](<href>), [label](href "title"); (?<!\!) excludes images.
_MDLINK_RE = re.compile(r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+[^)]*)?\)")
# An href with a URI scheme (https:, mailto:, javascript:, ...) is external.
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def _strip_code(md_text: str) -> str:
    """Blank fenced code blocks and inline code spans so they never parse."""
    out_lines: list[str] = []
    fence: str | None = None  # the run that opened the current fence
    for line in md_text.splitlines():
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
                out_lines.append("")
                continue
            out_lines.append(line)
        else:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            out_lines.append("")
    text = "\n".join(out_lines)
    return _INLINE_CODE_RE.sub(" ", text)


def parse_links(md_text: str) -> list[dict]:
    """Extract links in document order.

    Returns ``[{target, label, kind}]`` where kind is ``'wikilink'`` or
    ``'mdlink'``. Targets are raw text — resolution and escaping happen later.
    """
    text = _strip_code(str(md_text or ""))
    found: list[tuple[int, dict]] = []
    for match in _WIKILINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        label = (match.group(2) or "").strip()
        found.append((match.start(), {
            "target": target, "label": label or target, "kind": "wikilink",
        }))
    for match in _MDLINK_RE.finditer(text):
        href = match.group(2).strip()
        base = href.split("#", 1)[0].split("?", 1)[0]
        if not base.lower().endswith(".md"):
            continue
        label = match.group(1).strip()
        found.append((match.start(), {
            "target": base, "label": label or base, "kind": "mdlink",
        }))
    found.sort(key=lambda pair: pair[0])
    return [link for _pos, link in found]


def resolver(docs: Iterable[dict]) -> Callable[[str], str | None]:
    """Build ``resolve(target) -> path | None`` over a doc index.

    ``docs`` items need ``path`` and (optionally) ``title``. Resolution order:
    exact case-insensitive title (when unique) -> exact path (``.md`` may be
    omitted; exact-case first, then unique case-insensitive) -> unique
    path-suffix match -> None.
    """
    titles: dict[str, set[str]] = {}
    exact_paths: set[str] = set()
    lower_paths: dict[str, set[str]] = {}
    for doc in docs:
        path = str(doc.get("path") or "")
        if not path:
            continue
        exact_paths.add(path)
        lower_paths.setdefault(path.lower(), set()).add(path)
        title = str(doc.get("title") or "").strip()
        if title:
            titles.setdefault(title.lower(), set()).add(path)

    def resolve(target: str) -> str | None:
        text = str(target or "").strip().replace("\\", "/")
        if not text or text.startswith("//") or _SCHEME_RE.match(text):
            return None
        while text.startswith("./"):
            text = text[2:]
        text = text.lstrip("/")
        if not text:
            return None
        # 1. exact title match (case-insensitive), only when unambiguous.
        title_hit = titles.get(text.lower())
        if title_hit and len(title_hit) == 1:
            return next(iter(title_hit))
        candidates = [text] if text.lower().endswith(".md") else [text, text + ".md"]
        # 2. exact path match (exact case first, then unique case-insensitive).
        for cand in candidates:
            if cand in exact_paths:
                return cand
        for cand in candidates:
            path_hit = lower_paths.get(cand.lower())
            if path_hit and len(path_hit) == 1:
                return next(iter(path_hit))
        # 3. unique path-suffix match ("Majiks-Launch-Runbook" ->
        #    "apps/Majiks-Launch-Runbook.md").
        suffix_hits: set[str] = set()
        for cand in candidates:
            needle = "/" + cand.lower()
            suffix_hits.update(p for p in exact_paths if p.lower().endswith(needle))
        if len(suffix_hits) == 1:
            return next(iter(suffix_hits))
        return None

    return resolve


def build_link_graph(docs_with_bodies: Iterable[dict[str, Any]]) -> dict:
    """Parse every doc body and assemble the doc-to-doc link graph.

    ``docs_with_bodies`` items need ``path`` plus ``body`` (or ``md``) and
    optionally ``title``. Returns::

        {links:   [{from_path, to_path, text}],   # resolved, deduped, no self
         missing: [{from_path, target}],          # unresolved wikilinks only
         orphans: [path, ...]}                    # docs never linked-to

    Unresolved markdown links are dropped silently — the contract never
    red-links them. Self-links resolve but create no edge and do not rescue a
    doc from orphanhood.
    """
    docs = [doc for doc in docs_with_bodies if doc.get("path")]
    resolve = resolver(docs)
    links: list[dict] = []
    missing: list[dict] = []
    seen_links: set[tuple[str, str]] = set()
    seen_missing: set[tuple[str, str]] = set()
    linked_to: set[str] = set()
    for doc in docs:
        from_path = str(doc["path"])
        body = doc.get("body")
        if body is None:
            body = doc.get("md") or ""
        for link in parse_links(str(body)):
            to_path = resolve(link["target"])
            if to_path is None:
                if link["kind"] != "wikilink":
                    continue  # mdlinks are never red-linked
                key = (from_path, link["target"])
                if key not in seen_missing:
                    seen_missing.add(key)
                    missing.append({"from_path": from_path, "target": link["target"]})
                continue
            if to_path == from_path:
                continue
            linked_to.add(to_path)
            key = (from_path, to_path)
            if key not in seen_links:
                seen_links.add(key)
                links.append({
                    "from_path": from_path, "to_path": to_path, "text": link["label"],
                })
    orphans = [str(doc["path"]) for doc in docs if str(doc["path"]) not in linked_to]
    return {"links": links, "missing": missing, "orphans": orphans}
