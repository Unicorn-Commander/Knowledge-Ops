/* Knowledge-Ops shell — safe markdown renderer + HTML escaping.
   Escape-first: raw input is HTML-escaped before any markup is layered on, so
   nothing user-authored ever reaches innerHTML unescaped.
   Supports: headings, bold/em, inline code, fenced code, ul/ol lists, links
   (INTERNAL only — external hrefs render as plain text; the host is offline),
   tables (with header row detection), blockquotes, horizontal rules.

   Wikilinks: md(text, opts) accepts opts.resolveLink(target) ->
   {href, exists, path} | null. When a resolver is supplied, [[Target]] and
   [[Target|label]] (outside code fences/inline code) render as internal wiki
   anchors: resolved -> <a class="wl" data-path="...">, missing ->
   <a class="wl wl-missing" data-create="slugged/path.md">. Regular markdown
   links whose href ends in .md and resolves to a doc also become .wl anchors
   (never red-linked). Without a resolver, [[x]] stays plain escaped text —
   zero behavior change for existing callers. Targets/labels still pass
   through esc(); only the string handed to the resolver is unescaped. */

export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const INTERNAL_HREF = /^(#|\/(?!\/)|\.\/|\.\.\/)/;

/* Reverse of esc() — used ONLY to hand the raw target string to the caller's
   resolver; the reversed text never reaches the DOM. (&amp; last, so double
   entities don't over-decode.) */
function unesc(s) {
  return String(s)
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

/* Fallback slug for a missing [[target]] when the resolver supplies no create
   path: safe filename characters only, spaces -> dashes, case preserved. */
function slugTarget(t) {
  const path = String(t).trim().replace(/\.md$/i, "").split("/")
    .map((seg) => seg.trim()
      .replace(/\s+/g, "-")
      .replace(/[^A-Za-z0-9._-]/g, "")
      .replace(/-{2,}/g, "-")
      .replace(/^[-.]+|[-.]+$/g, ""))
    .filter(Boolean)
    .join("/");
  return path || "untitled";
}

/* Build a wiki anchor. rawTarget is the UNescaped target (resolver input);
   labelEsc is already-escaped display text ("" -> fall back to the target). */
function wikiAnchor(rawTarget, labelEsc, resolve) {
  const text = labelEsc || esc(rawTarget);
  let r = null;
  try { r = resolve(rawTarget); } catch { r = null; }
  if (r && r.exists) {
    const path = r.path || "";
    const href = r.href || ("#library?path=" + encodeURIComponent(path));
    return `<a class="wl" href="${esc(href)}" data-path="${esc(path)}">${text}</a>`;
  }
  const createPath = (r && r.path) || (slugTarget(rawTarget) + ".md");
  return `<a class="wl wl-missing" href="#" data-create="${esc(createPath)}" data-wl-target="${esc(rawTarget)}" title="Create ${esc(createPath)}">${text}</a>`;
}

function inline(t, resolve) {
  let s = esc(t);
  // protect inline code spans from further formatting (also keeps wikilink
  // parsing out of inline code — [[x]] inside backticks stays literal)
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  // wikilinks: [[Target]] / [[Target|label]] — parsed on escaped text, then
  // stashed as placeholders so strong/em/link passes can't chew the markup.
  const wls = [];
  if (resolve) {
    s = s.replace(/\[\[([^\[\]\n]+)\]\]/g, (m, inner) => {
      const bar = inner.indexOf("|");
      const targetEsc = bar >= 0 ? inner.slice(0, bar) : inner;
      const labelEsc = bar >= 0 ? inner.slice(bar + 1).trim() : "";
      const rawTarget = unesc(targetEsc).trim();
      if (!rawTarget) return m;
      wls.push(wikiAnchor(rawTarget, labelEsc, resolve));
      return `\u0001${wls.length - 1}\u0001`;
    });
  }
  s = s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^()\s]+)\)/g, (m, txt, href) => {
      // .md links that resolve to a doc are wiki links too (graph +
      // click-to-navigate) — but never red-linked when they don't resolve.
      if (resolve && /\.md$/i.test(href.split(/[?#]/)[0])) {
        const clean = unesc(href).replace(/^(\.\/|\.\.\/|\/)+/, "");
        let r = null;
        try { r = resolve(clean); } catch { r = null; }
        if (r && r.exists) {
          const target = r.href || ("#library?path=" + encodeURIComponent(r.path || ""));
          return `<a class="wl" href="${esc(target)}" data-path="${esc(r.path || "")}">${txt}</a>`;
        }
      }
      return INTERNAL_HREF.test(href) ? `<a href="${href}">${txt}</a>` : txt;
    });
  s = s.replace(/\u0001(\d+)\u0001/g, (_, i) => wls[+i]);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}

export function md(src, opts) {
  const resolve = opts && typeof opts.resolveLink === "function" ? opts.resolveLink : null;
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [], quote = [], table = [], codeBuf = [];
  let inUl = false, inOl = false, inCode = false;

  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "), resolve)}</p>`); para = []; } };
  const closeLists = () => {
    if (inUl) { out.push("</ul>"); inUl = false; }
    if (inOl) { out.push("</ol>"); inOl = false; }
  };
  const flushQuote = () => {
    if (quote.length) { out.push(`<blockquote><p>${inline(quote.join(" "), resolve)}</p></blockquote>`); quote = []; }
  };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
    let head = null, body = rows;
    if (rows.length > 1 && rows[1].length && rows[1].every((c) => /^:?-{2,}:?$/.test(c))) {
      head = rows[0];
      body = rows.slice(2);
    }
    let html = '<div class="tablewrap"><table>';
    if (head) html += "<thead><tr>" + head.map((c) => `<th>${inline(c, resolve)}</th>`).join("") + "</tr></thead>";
    html += "<tbody>" + body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c, resolve)}</td>`).join("") + "</tr>").join("") + "</tbody>";
    html += "</table></div>";
    out.push(html);
    table = [];
  };
  const flushAll = () => { flushPara(); closeLists(); flushQuote(); flushTable(); };

  for (const raw of lines) {
    if (/^```/.test(raw)) {
      if (inCode) { out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`); codeBuf = []; inCode = false; }
      else { flushAll(); inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }

    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) { flushAll(); continue; }

    let m;
    if (/^\|.*\|$/.test(line)) { flushPara(); closeLists(); flushQuote(); table.push(line); continue; }
    flushTable();

    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      flushAll();
      const lv = m[1].length;
      out.push(`<h${lv}>${inline(m[2], resolve)}</h${lv}>`);
      continue;
    }
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { flushAll(); out.push("<hr/>"); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { flushPara(); closeLists(); quote.push(m[1]); continue; }
    flushQuote();
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      flushPara();
      if (inOl) { out.push("</ol>"); inOl = false; }
      if (!inUl) { out.push("<ul>"); inUl = true; }
      out.push(`<li>${inline(m[1], resolve)}</li>`);
      continue;
    }
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      flushPara();
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (!inOl) { out.push("<ol>"); inOl = true; }
      out.push(`<li>${inline(m[1], resolve)}</li>`);
      continue;
    }
    closeLists();
    para.push(line);
  }
  if (inCode) out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`);
  flushAll();
  return out.join("\n");
}
