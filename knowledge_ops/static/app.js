/* Knowledge-Ops GUI — vanilla JS, no build step.
   Talks to the same-origin REST API (/kb.*). When deployed behind oauth2-proxy
   the browser carries the SSO cookie automatically, so every fetch is gated. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };

/* ---------- safety + formatting ---------- */
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function fmtScore(s) { return (typeof s === "number") ? s.toFixed(2) : esc(s); }
function fmtTime(t) { if (!t) return ""; try { return new Date(t).toLocaleString(); } catch { return esc(t); } }
function srcClass(source) {
  const base = String(source || "other").split(":")[0];
  const known = ["wiki", "falkordb", "qdrant", "file_index", "org_memory", "center_deep", "rest", "postgres"];
  return "src-" + (known.includes(base) ? base : "other");
}
function sourceLabel(source) {
  return String(source || "other").replace(/^qdrant:/, "Q ").replace(/^falkordb:/, "G ").replace(/^rest:/, "API ").replace(/^postgres:/, "SQL ");
}

/* tiny, escape-first markdown renderer (headings, bold/italic, code, lists, links, quotes, hr, tables) */
function renderMarkdown(md) {
  const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
  let out = [], inUl = false, inOl = false, inCode = false, codeBuf = [], para = [];
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const closeLists = () => { if (inUl) { out.push("</ul>"); inUl = false; } if (inOl) { out.push("</ol>"); inOl = false; } };
  for (let raw of lines) {
    if (/^```/.test(raw)) { if (inCode) { out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`); codeBuf = []; inCode = false; } else { flushPara(); closeLists(); inCode = true; } continue; }
    if (inCode) { codeBuf.push(raw); continue; }
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) { flushPara(); closeLists(); continue; }
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { flushPara(); closeLists(); const lv = m[1].length; out.push(`<h${lv}>${inline(m[2])}</h${lv}>`); continue; }
    if (/^\s*([-*_])\1{1,}\s*$/.test(line)) { flushPara(); closeLists(); out.push("<hr/>"); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { flushPara(); closeLists(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) { flushPara(); if (inOl) { out.push("</ol>"); inOl = false; } if (!inUl) { out.push("<ul>"); inUl = true; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) { flushPara(); if (inUl) { out.push("</ul>"); inUl = false; } if (!inOl) { out.push("<ol>"); inOl = true; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if (/^\|.*\|$/.test(line)) {
      flushPara(); closeLists();
      const cells = line.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue; // separator row
      const tag = "td";
      out.push(`<table><tr>${cells.map(c => `<${tag}>${inline(c)}</${tag}>`).join("")}</tr></table>`);
      continue;
    }
    para.push(line);
  }
  if (inCode) out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`);
  flushPara(); closeLists();
  // merge adjacent single-row tables into one
  return out.join("\n").replace(/<\/table>\n<table>/g, "");
}

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Accept": "application/json" }, ...opts });
  if (res.status === 401 || res.status === 403 || (res.redirected && /oauth2|auth\./.test(res.url))) {
    throw new Error("Not authorized — your session may have expired. Reload to sign in.");
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("image/svg")) return await res.text();
  let body;
  try { body = await res.json(); } catch { body = null; }
  if (!res.ok) {
    const raw = body && (body.detail || body.error || body);
    const detail = typeof raw === "string" ? raw : raw && (raw.message || JSON.stringify(raw));
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return body;
}
const apiJSON = (path, payload) => api(path, { method: "POST", headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify(payload) });

/* ---------- toast ---------- */
function toast(msg, kind = "") {
  const t = el("div", "toast__item " + kind, esc(msg));
  $("#toast").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 250); }, 3200);
}

/* ---------- nav ---------- */
function showView(name) {
  $$(".side__btn").forEach(b => {
    const selected = b.dataset.view === name;
    b.classList.toggle("active", selected);
    if (selected) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  $$(".view").forEach(v => v.classList.toggle("active", v.id === "view-" + name));
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "wiki") loadWiki();
  if (name === "review") loadReview();
  if (name === "sources") loadSources();
  if (name === "metrics") loadAudit();
}
$$(".side__btn").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));

/* ============ ASK / GROUND ============ */
function passageCard(p, i) {
  const sc = srcClass(p.source);
  const owner = p.owner ? `owner=${esc(p.owner)}` : "";
  const scope = p.scope ? `<span class="pill pill--scope">${esc(p.scope)}</span>` : "";
  const uri = p.uri ? `<div class="dim mono" style="margin-top:.4rem;font-size:.72rem;word-break:break-all;">${esc(p.uri)}</div>` : "";
  const text = esc(p.text || "");
  const relation = p.metadata && p.metadata.evidence_relation
    ? `<span class="pill">${esc(String(p.metadata.evidence_relation).replaceAll("_", " "))}</span>` : "";
  return `<div class="cite">
    <div class="cite__head">
      <span class="cite__n">${i}</span>
      <span class="src-badge ${sc}">${esc(sourceLabel(p.source))}</span>
      <span class="cite__title">${esc(p.title || p.source_id || "untitled")}</span>
      ${scope}
      ${relation}
      <span class="cite__meta">score ${fmtScore(p.score)} · ${owner}</span>
    </div>
    <div class="cite__text" data-full="${text.length > 480 ? '1' : ''}">${text || '<span class="dim">(no snippet)</span>'}</div>
    ${text.length > 480 ? '<button class="cite__more">Show more ▾</button>' : ''}
    ${uri}
  </div>`;
}

async function doAsk() {
  const q = $("#askQuery").value.trim();
  const box = $("#askResult");
  if (!q) { box.innerHTML = `<div class="error">Enter a question or claim to ground.</div>`; return; }
  const scope = $("#askScope").value || undefined;
  const k = parseInt($("#askK").value, 10) || 6;
  const mode = $("#askMode").value;
  $("#askBtn").disabled = true;
  box.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span> Federating stores, merging &amp; re-ranking evidence…</div></div>`;
  try {
    const data = await apiJSON(mode === "search" ? "/kb.search" : "/kb.ground", { query: q, scope, k });
    const passages = data.passages || [];
    let html = "";
    if (mode === "ground") {
      const labels = {
        possible_support: ["pill--ok", "possible support"],
        possible_contradiction: ["", "possible contradiction"],
        possible_conflict: ["", "possible conflict"],
        related_evidence: ["pill--scope", "related evidence"],
        not_enough_evidence: ["pill--off", "not enough evidence"],
      };
      const [cls, label] = labels[data.verdict] || ["pill--off", "unassessed"];
      const verdict = `<span class="pill ${cls}">${esc(label)}</span>`;
      html += `<div class="card card--strong"><div class="answer"><div class="verdict">${verdict}<span class="pill pill--off">heuristic, not verified</span></div>${esc(data.answer || "")}</div></div>`;
    } else if (data.answer) {
      html += `<div class="card card--strong"><div class="answer">${esc(data.answer.split(/\n\nCitations:/)[0])}</div></div>`;
    }
    if (passages.length) {
      const srcs = [...new Set(passages.map(p => String(p.source).split(":")[0]))];
      html += `<h2 class="section">Evidence <span class="dim">· ${passages.length} passage${passages.length === 1 ? '' : 's'} from ${srcs.length} source${srcs.length === 1 ? '' : 's'}</span></h2>`;
      html += `<div class="card">${passages.map((p, i) => passageCard(p, i + 1)).join("")}</div>`;
    } else {
      html += `<div class="empty">No accessible evidence found.</div>`;
    }
    box.innerHTML = html;
    $$("#askResult .cite__more").forEach(btn => btn.addEventListener("click", () => {
      const t = btn.previousElementSibling; t.classList.toggle("expanded");
      btn.textContent = t.classList.contains("expanded") ? "Show less ▴" : "Show more ▾";
    }));
  } catch (e) {
    box.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  } finally {
    $("#askBtn").disabled = false;
  }
}
$("#askBtn").addEventListener("click", doAsk);
$("#askQuery").addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") doAsk(); });

/* ============ WIKI ============ */
let _wikiDocs = [];
async function loadWiki() {
  const list = $("#wikiList");
  list.innerHTML = `<div class="loading"><span class="spinner"></span> loading…</div>`;
  try {
    const data = await api("/kb.docs");
    _wikiDocs = data.docs || [];
    renderWikiList();
  } catch (e) { list.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}
function renderWikiList() {
  const filter = ($("#wikiFilter").value || "").toLowerCase();
  const list = $("#wikiList");
  const docs = _wikiDocs.filter(d => !filter || (d.title + " " + d.path).toLowerCase().includes(filter));
  if (!docs.length) { list.innerHTML = `<div class="empty"><div class="big">▤</div>${_wikiDocs.length ? "No docs match the filter." : "No wiki documents yet."}</div>`; return; }
  list.innerHTML = "";
  docs.forEach(d => {
    const it = el("button", "item");
    it.type = "button";
    it.innerHTML = `<div style="min-width:0;"><div class="item__title">${esc(d.title || d.path)}</div><div class="item__sub mono">${esc(d.path)}</div></div>
      <div class="item__meta"><span class="pill pill--scope">${esc(d.scope || "global")}</span></div>`;
    it.addEventListener("click", () => viewDoc(d.id));
    list.appendChild(it);
  });
}
$("#wikiFilter").addEventListener("input", renderWikiList);

async function viewDoc(id) {
  const pane = $("#wikiPane");
  pane.innerHTML = `<div class="loading"><span class="spinner"></span> loading…</div>`;
  try {
    const { doc } = await api("/kb.get_doc?id=" + encodeURIComponent(id));
    if (!doc) { pane.innerHTML = `<div class="empty">Document not found.</div>`; return; }
    pane.dataset.docId = doc.id;
    const provenance = doc.provenance
      ? `<span class="pill">source: ${esc(doc.provenance.source_type)}</span><span class="pill">reviewed ${fmtTime(doc.provenance.reviewed_at)}</span>` : "";
    pane.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:flex-start;">
        <div style="min-width:0;">
          <div class="item__sub mono">${esc(doc.path)}</div>
          <h2 class="section" style="margin:.2rem 0 .4rem;">${esc(doc.title)}</h2>
          <div class="row"><span class="pill pill--scope">${esc(doc.scope)}</span><span class="pill">owner: ${esc(doc.owner)}</span><span class="pill dim">updated ${fmtTime(doc.updated_at)}</span>${provenance}</div>
        </div>
        <div class="row">
          <button class="btn btn--sm" id="wikiEditBtn">Edit</button>
          <button class="btn btn--sm" id="wikiHistBtn">History</button>
        </div>
      </div>
      <hr class="md" style="border:none;border-top:1px solid var(--mu-color-border);margin:1rem 0;" />
      <div class="md" id="wikiBody">${renderMarkdown(doc.body)}</div>`;
    $("#wikiEditBtn").addEventListener("click", () => editDoc(doc));
    $("#wikiHistBtn").addEventListener("click", () => histDoc(doc.id));
  } catch (e) { pane.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

function editDoc(doc) {
  const pane = $("#wikiPane");
  const isNew = !doc;
  doc = doc || { path: "", body: "", scope: "personal", owner: "" };
  pane.innerHTML = `
    <h2 class="section" style="margin-top:0;">${isNew ? "New document" : "Edit document"}</h2>
    <div class="field"><label class="lbl">Path</label><input class="input mono" id="edPath" value="${esc(doc.path)}" placeholder="folder/title.md" ${isNew ? "" : "readonly"} /></div>
    <div class="row" style="margin-bottom:.8rem;">
      <div><label class="lbl" for="edScope">Visibility</label><select class="select" id="edScope"><option ${doc.scope==='personal'?'selected':''}>personal</option><option ${doc.scope==='global'?'selected':''}>global</option></select></div>
    </div>
    <div class="field"><label class="lbl">Markdown</label><textarea class="textarea" id="edBody" style="min-height:280px;">${esc(doc.body)}</textarea></div>
    <div class="row"><button class="btn btn--primary" id="edSave">Save</button><button class="btn" id="edCancel">Cancel</button></div>`;
  $("#edCancel").addEventListener("click", () => { if (isNew) { pane.innerHTML = `<div class="empty"><div class="big">▤</div>Select a document.</div>`; } else { viewDoc(doc.id); } });
  $("#edSave").addEventListener("click", async () => {
    const path = $("#edPath").value.trim();
    const md = $("#edBody").value;
    if (!path) { toast("Path is required", "err"); return; }
    if (!md.trim()) { toast("Body is required", "err"); return; }
    $("#edSave").disabled = true;
    try {
      const { id } = await apiJSON("/kb.upsert_doc", { path, md, scope: $("#edScope").value });
      toast("Saved", "ok");
      await loadWiki();
      viewDoc(id);
    } catch (e) { toast(e.message, "err"); $("#edSave").disabled = false; }
  });
}
$("#wikiNewBtn").addEventListener("click", () => editDoc(null));

async function histDoc(id) {
  const pane = $("#wikiPane");
  try {
    const { history } = await api("/kb.doc_history?id=" + encodeURIComponent(id));
    let body = history && history.length
      ? `<div class="list">${history.map(h => `<div class="item"><div><div class="item__title">version #${esc(h.version_id)}</div><div class="item__sub">${esc(h.bytes)} bytes</div></div><div class="item__meta dim">${fmtTime(h.created_at)}</div></div>`).join("")}</div>`
      : `<div class="empty">No prior versions — this is the first revision.</div>`;
    pane.insertAdjacentHTML("afterbegin", `<div class="history-panel" id="historyPanel"><div class="row" style="justify-content:space-between;"><h2 class="section" style="margin:0;">History</h2><button type="button" class="btn btn--sm" id="historyClose">Close</button></div>${body}</div>`);
    $("#historyClose").addEventListener("click", () => $("#historyPanel").remove());
  } catch (e) { toast(e.message, "err"); }
}

/* ============ OPENWIKI REVIEW ============ */
async function loadReview() {
  const list = $("#reviewList");
  list.innerHTML = `<div class="loading"><span class="spinner"></span> loading...</div>`;
  try {
    const data = await api("/kb.openwiki_imports");
    const imports = data.imports || [];
    if (!imports.length) { list.innerHTML = `<div class="empty">Queue is clear.</div>`; return; }
    list.innerHTML = "";
    imports.forEach(item => {
      const button = el("button", "item");
      button.type = "button";
      const blocking = (item.findings || []).filter(f => f.severity === "blocking").length;
      button.innerHTML = `<div style="min-width:0;text-align:left;"><div class="item__title">${esc(item.import_id)}</div><div class="item__sub">${esc(item.document_count)} docs · ${fmtTime(item.staged_at)}</div></div><div class="item__meta"><span class="pill ${blocking ? '' : 'pill--ok'}">${esc(item.state)}</span></div>`;
      button.addEventListener("click", () => viewImport(item.import_id));
      list.appendChild(button);
    });
  } catch (e) {
    list.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

async function viewImport(id) {
  const pane = $("#reviewPane");
  pane.innerHTML = `<div class="loading"><span class="spinner"></span> loading...</div>`;
  try {
    const data = await api("/kb.openwiki_import?id=" + encodeURIComponent(id));
    const item = data.import;
    const findings = item.findings || [];
    const documents = item.documents || [];
    pane.innerHTML = `<div class="row" style="justify-content:space-between;align-items:flex-start;">
      <div><div class="item__sub mono">${esc(item.import_id)}</div><h2 class="section" style="margin:.2rem 0;">${esc(item.document_count)} documents</h2></div>
      <span class="pill">${esc(item.state)}</span></div>
      <h3 class="subhead">Findings</h3>
      <div class="list">${findings.length ? findings.map(f => `<div class="finding finding--${esc(f.severity)}"><strong>${esc(f.category)}</strong><span>${esc(f.path)} · ${esc(f.detail)}</span></div>`).join("") : `<div class="empty">No findings.</div>`}</div>
      <h3 class="subhead">Documents</h3>
      <div class="list">${documents.map(d => `<div class="item"><div><div class="item__title">${esc(d.path)}</div><div class="item__sub">${esc(d.okf_type || "missing OKF type")}</div></div><div class="item__meta"><span class="pill pill--scope">${esc(d.scope)}</span></div></div>`).join("")}</div>
      ${["approved", "rejected"].includes(item.state) ? `<p class="dim">Reviewed ${fmtTime(item.reviewed_at)} by ${esc(item.reviewed_by || "")}</p>` : `<div class="review-actions"><label class="lbl" for="reviewNote">Review note</label><textarea class="textarea" id="reviewNote" rows="3"></textarea><label class="check"><input type="checkbox" id="acceptPii" /> PII is expected and approved for this visibility</label><div class="row"><button type="button" class="btn btn--primary" id="approveImport">Approve</button><button type="button" class="btn" id="rejectImport">Reject</button></div></div>`}`;
    if ($("#approveImport")) $("#approveImport").addEventListener("click", () => reviewImport(id, true));
    if ($("#rejectImport")) $("#rejectImport").addEventListener("click", () => reviewImport(id, false));
  } catch (e) { pane.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

async function reviewImport(id, approve) {
  const note = $("#reviewNote").value.trim();
  if (note.length < 3) { toast("Add a review note", "err"); return; }
  const endpoint = approve ? "/kb.openwiki_approve" : "/kb.openwiki_reject";
  try {
    await apiJSON(endpoint + "?id=" + encodeURIComponent(id), { note, accept_pii: Boolean($("#acceptPii").checked) });
    toast(approve ? "Import approved" : "Import rejected", "ok");
    await loadReview();
    await viewImport(id);
  } catch (e) { toast(e.message, "err"); }
}
$("#reviewRefresh").addEventListener("click", loadReview);

/* ============ FILES ============ */
async function doFileSearch() {
  const q = $("#fileQuery").value.trim();
  const box = $("#fileResult");
  $("#fileDeep").innerHTML = "";
  if (!q) { box.innerHTML = `<div class="error">Enter a search query.</div>`; return; }
  const scope = $("#fileScope").value;
  box.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span> searching the file index…</div></div>`;
  try {
    const data = await api(`/kb.find_file?query=${encodeURIComponent(q)}&k=12${scope ? "&scope=" + encodeURIComponent(scope) : ""}`);
    const ps = data.passages || [];
    if (!ps.length) { box.innerHTML = `<div class="empty"><div class="big">⌕</div>No files matched. The index may be empty on this host (run <code class="mono">index-files</code>).</div>`; return; }
    box.innerHTML = `<h2 class="section">Matches <span class="dim">· ${ps.length}</span></h2><div class="list">${ps.map(p => `
      <div class="item" data-path="${esc(p.uri || (p.metadata && p.metadata.path) || '')}">
        <div style="min-width:0;"><div class="item__title">${esc(p.title)}</div><div class="item__sub mono">${esc(p.uri || '')}</div></div>
        <div class="item__meta"><span class="pill pill--scope">${esc(p.scope)}</span><span class="pill dim">score ${fmtScore(p.score)}</span><button class="btn btn--sm">Deep-read →</button></div>
      </div>`).join("")}</div>`;
    $$("#fileResult .item").forEach(it => it.addEventListener("click", () => deepRead(it.dataset.path, q)));
  } catch (e) { box.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}
async function deepRead(path, query) {
  const box = $("#fileDeep");
  if (!path) { toast("No path for this result", "err"); return; }
  box.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span> deep-reading…</div></div>`;
  try {
    const data = await api(`/kb.deep_read?path=${encodeURIComponent(path)}&query=${encodeURIComponent(query || "")}&k=20`);
    const ps = data.passages || [];
    box.innerHTML = `<h2 class="section">Deep read <span class="dim mono">· ${esc(path)}</span></h2>` +
      (ps.length ? `<div class="card">${ps.map((p, i) => passageCard(p, i + 1)).join("")}</div>` : `<div class="empty">No chunks indexed for this file.</div>`);
    $$("#fileDeep .cite__more").forEach(btn => btn.addEventListener("click", () => { const t = btn.previousElementSibling; t.classList.toggle("expanded"); btn.textContent = t.classList.contains("expanded") ? "Show less ▴" : "Show more ▾"; }));
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { box.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}
$("#fileBtn").addEventListener("click", doFileSearch);
$("#fileQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") doFileSearch(); });

/* ============ METRICS ============ */
async function doMetric() {
  const name = $("#metricName").value.trim();
  const box = $("#metricResult");
  if (!name) { box.innerHTML = `<div class="error">Enter a metric name.</div>`; return; }
  box.innerHTML = `<div class="card"><div class="loading"><span class="spinner"></span> rendering…</div></div>`;
  try {
    const [svg, rows] = await Promise.all([
      api(`/kb.get_metric_chart?name=${encodeURIComponent(name)}&limit=120`),
      api(`/kb.metrics?name=${encodeURIComponent(name)}&limit=20`).catch(() => ({ metrics: [] })),
    ]);
    const metrics = (rows && rows.metrics) || [];
    let html = `<div class="chartwrap">${svg}</div>`;
    if (metrics.length) {
      html += `<h2 class="section">Recent values</h2><div class="list">${metrics.slice().reverse().map(m => `<div class="item"><div><div class="item__title mono">${esc(Number(m.value).toFixed(3))}</div><div class="item__sub">${esc(JSON.stringify(m.tags || {}))}</div></div><div class="item__meta dim">${fmtTime(m.recorded_at)}</div></div>`).join("")}</div>`;
    } else {
      html += `<div class="empty" style="margin-top:1rem;">No recorded values for <b>${esc(name)}</b> yet.</div>`;
    }
    box.innerHTML = html;
  } catch (e) { box.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}
$("#metricBtn").addEventListener("click", doMetric);
$("#metricName").addEventListener("keydown", (e) => { if (e.key === "Enter") doMetric(); });

async function loadAudit() {
  const box = $("#auditResult");
  box.innerHTML = `<div class="loading"><span class="spinner"></span> loading...</div>`;
  try {
    const data = await api("/kb.audit?limit=50");
    const events = data.events || [];
    box.innerHTML = events.length ? events.map(event => `<div class="item"><div><div class="item__title">${esc(event.action)}</div><div class="item__sub">${esc(event.actor)} · ${esc(event.resource)}</div></div><div class="item__meta"><span class="pill ${event.outcome === 'success' || event.outcome === 'approved' ? 'pill--ok' : ''}">${esc(event.outcome)}</span><span class="dim">${fmtTime(event.recorded_at)}</span></div></div>`).join("") : `<div class="empty">No recorded changes.</div>`;
  } catch (e) { box.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

/* ============ SOURCES / STATUS ============ */
async function loadSources() {
  const grid = $("#sourcesGrid"), tools = $("#toolsList");
  try {
    const data = await api("/kb.list_sources");
    const sources = data.sources || [];
    grid.innerHTML = sources.map(s => {
      const pill = s.enabled ? `<span class="pill pill--ok">● ${esc(s.status)}</span>` : `<span class="pill pill--off">○ ${esc(s.status)}</span>`;
      return `<div class="card"><div class="row" style="justify-content:space-between;"><span class="src-badge ${srcClass(s.name)}">${esc(sourceLabel(s.name))}</span>${pill}</div><div class="dim mono" style="margin-top:.6rem;font-size:.74rem;word-break:break-all;">${esc(s.detail || "")}</div></div>`;
    }).join("");
  } catch (e) { grid.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  // MCP tools from the manifest
  try {
    const man = await api("/.well-known/mcps.json");
    $("#toolNs").textContent = man.tool_namespace || "knowledge";
    $("#versionTag").textContent = "v" + (man.version || "");
    tools.innerHTML = (man.tools || []).map(t => `<div class="tool"><div><div><code>${esc(man.tool_namespace)}.${esc(t.name)}</code> ${t.read_only ? '<span class="pill pill--ok" style="margin-left:.3rem;">read-only</span>' : '<span class="pill" style="margin-left:.3rem;">write</span>'}</div><div class="dim" style="margin-top:.25rem;font-size:.84rem;">${esc(t.description || "")}</div></div></div>`).join("");
  } catch (e) { tools.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
}

/* ---------- status pulse ---------- */
async function pulse() {
  try {
    const h = await api("/ready");
    $("#statusDot").className = "dot dot--ok";
    $("#statusText").textContent = (h && h.service) || "online";
  } catch {
    $("#statusDot").className = "dot dot--bad";
    $("#statusText").textContent = "offline";
  }
}

/* ---------- boot ---------- */
(function init() {
  const start = (location.hash || "#ask").slice(1);
  if (["ask", "wiki", "review", "files", "metrics", "sources"].includes(start)) showView(start); else showView("ask");
  pulse();
  setInterval(pulse, 30000);
})();
