/* Knowledge-Ops shell — safe markdown renderer + HTML escaping.
   Escape-first: raw input is HTML-escaped before any markup is layered on, so
   nothing user-authored ever reaches innerHTML unescaped.
   Supports: headings, bold/em, inline code, fenced code, ul/ol lists, links
   (INTERNAL only — external hrefs render as plain text; the host is offline),
   tables (with header row detection), blockquotes, horizontal rules. */

export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const INTERNAL_HREF = /^(#|\/(?!\/)|\.\/|\.\.\/)/;

function inline(t) {
  let s = esc(t);
  // protect inline code spans from further formatting
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^()\s]+)\)/g, (m, txt, href) =>
      INTERNAL_HREF.test(href) ? `<a href="${href}">${txt}</a>` : txt);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}

export function md(src) {
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [], quote = [], table = [], codeBuf = [];
  let inUl = false, inOl = false, inCode = false;

  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const closeLists = () => {
    if (inUl) { out.push("</ul>"); inUl = false; }
    if (inOl) { out.push("</ol>"); inOl = false; }
  };
  const flushQuote = () => {
    if (quote.length) { out.push(`<blockquote><p>${inline(quote.join(" "))}</p></blockquote>`); quote = []; }
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
    if (head) html += "<thead><tr>" + head.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead>";
    html += "<tbody>" + body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody>";
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
      out.push(`<h${lv}>${inline(m[2])}</h${lv}>`);
      continue;
    }
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { flushAll(); out.push("<hr/>"); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { flushPara(); closeLists(); quote.push(m[1]); continue; }
    flushQuote();
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      flushPara();
      if (inOl) { out.push("</ol>"); inOl = false; }
      if (!inUl) { out.push("<ul>"); inUl = true; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      flushPara();
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (!inOl) { out.push("<ol>"); inOl = true; }
      out.push(`<li>${inline(m[1])}</li>`);
      continue;
    }
    closeLists();
    para.push(line);
  }
  if (inCode) out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`);
  flushAll();
  return out.join("\n");
}
