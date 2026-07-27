/* Library — the reading room.
   Three zones: grouped doc list · reader · right rail (TOC + version timeline).
   Preserves the classic wiki behaviors (list, filter, +New, edit, delete, history)
   and adds deep-links (#library?path=...), scrollspy, and "Ask about this doc". */

const KNOWN_GROUPS = ["ai-platform", "apps", "engineering", "infrastructure", "marketing"];
const CHUNK = 200; // lazy-render chunk size for large libraries

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const css = `
  #view-library .lib-layout {
    display: grid;
    grid-template-columns: minmax(220px, 300px) minmax(0, 1fr) minmax(200px, 260px);
    gap: 1.25rem;
    align-items: start;
  }
  @media (max-width: 1100px) {
    #view-library .lib-layout { grid-template-columns: minmax(220px, 280px) minmax(0, 1fr); }
    #view-library .lib-rail { grid-column: 2; }
  }
  @media (max-width: 760px) {
    #view-library .lib-layout { grid-template-columns: minmax(0, 1fr); }
    #view-library .lib-rail { grid-column: auto; }
  }
  #view-library .lib-list-pane { min-width: 0; }
  #view-library .lib-group-kicker {
    margin: 1.1rem 0 .35rem;
    display: block;
  }
  #view-library .lib-group-kicker:first-child { margin-top: .25rem; }
  #view-library .lib-reader { min-width: 0; }
  #view-library .lib-doc-title {
    font-family: var(--mu-font-display);
    font-size: clamp(1.35rem, 2.6vw, 1.9rem);
    line-height: 1.15;
    color: var(--mu-color-text);
    margin: .25rem 0 .5rem;
    overflow-wrap: break-word;
  }
  #view-library .lib-md { max-width: 68ch; }
  #view-library .lib-md h2, #view-library .lib-md h3 {
    font-family: var(--mu-font-display);
    color: var(--mu-color-text);
    scroll-margin-top: 5rem;
  }
  #view-library .lib-md table {
    border-collapse: collapse;
    width: 100%;
    font-size: .9em;
    margin: .8rem 0;
  }
  #view-library .lib-md td, #view-library .lib-md th {
    border: 1px solid var(--mu-color-border);
    padding: .4rem .6rem;
    text-align: left;
  }
  #view-library .lib-md-scroll { overflow-x: auto; }
  #view-library .lib-md pre {
    background: var(--mu-color-surface-strong);
    border: 1px solid var(--mu-color-border);
    border-radius: var(--mu-radius-sm);
    padding: .75rem .9rem;
    overflow-x: auto;
    font-family: var(--mu-font-mono);
    font-size: .85em;
  }
  #view-library .lib-md code { font-family: var(--mu-font-mono); }
  #view-library .lib-md blockquote {
    border-left: 3px solid var(--mu-color-accent);
    margin: .8rem 0;
    padding: .1rem 0 .1rem .9rem;
    color: var(--mu-color-muted);
  }
  #view-library .lib-rail { position: sticky; top: 1rem; }
  #view-library .lib-toc { list-style: none; margin: .4rem 0 0; padding: 0; }
  #view-library .lib-toc button {
    display: block;
    width: 100%;
    min-height: 44px;
    text-align: left;
    background: none;
    border: none;
    border-left: 2px solid var(--mu-color-border);
    color: var(--mu-color-muted);
    font: inherit;
    font-size: .85rem;
    padding: .55rem .6rem;
    cursor: pointer;
    border-radius: 0 var(--mu-radius-sm) var(--mu-radius-sm) 0;
  }
  #view-library .lib-toc button:hover { color: var(--mu-color-text); background: var(--mu-color-surface); }
  #view-library .lib-toc li[data-level="3"] button { padding-left: 1.4rem; }
  #view-library .lib-toc button[aria-current="true"] {
    color: var(--mu-color-primary);
    border-left-color: var(--mu-color-primary);
    background: var(--mu-color-surface);
  }
  #view-library .lib-timeline { list-style: none; margin: .4rem 0 0; padding: 0; position: relative; }
  #view-library .lib-timeline::before {
    content: "";
    position: absolute;
    left: .55rem;
    top: .6rem;
    bottom: .6rem;
    width: 1px;
    background: var(--mu-color-border-strong);
  }
  #view-library .lib-timeline button {
    display: block;
    width: 100%;
    min-height: 44px;
    text-align: left;
    background: none;
    border: none;
    color: var(--mu-color-muted);
    font: inherit;
    font-size: .82rem;
    padding: .5rem .4rem .5rem 1.6rem;
    cursor: pointer;
    position: relative;
    border-radius: var(--mu-radius-sm);
  }
  #view-library .lib-timeline button::before {
    content: "";
    position: absolute;
    left: .3rem;
    top: 50%;
    transform: translateY(-50%);
    width: .55rem;
    height: .55rem;
    border-radius: 50%;
    background: var(--mu-color-surface-strong);
    border: 1px solid var(--mu-color-border-strong);
  }
  #view-library .lib-timeline button:hover { color: var(--mu-color-text); background: var(--mu-color-surface); }
  #view-library .lib-timeline button[aria-current="true"] { color: var(--mu-color-primary); }
  #view-library .lib-timeline button[aria-current="true"]::before {
    background: var(--mu-color-primary);
    border-color: var(--mu-color-primary);
  }
  #view-library .lib-version-banner {
    border: 1px solid var(--mu-color-warning);
    border-radius: var(--mu-radius-md);
    padding: .6rem .9rem;
    margin-bottom: 1rem;
    color: var(--mu-color-text);
    background: var(--mu-color-surface);
    display: flex;
    flex-wrap: wrap;
    gap: .6rem;
    align-items: center;
    justify-content: space-between;
  }
  #view-library .lib-item-btn { width: 100%; }
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
}

function relTime(value) {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  if (s < 604800) return Math.round(s / 86400) + "d ago";
  if (s < 2629800) return Math.round(s / 604800) + "w ago";
  if (s < 31557600) return Math.round(s / 2629800) + "mo ago";
  return Math.round(s / 31557600) + "y ago";
}
function fmtTime(t) { if (!t) return ""; try { return new Date(t).toLocaleString(); } catch { return String(t); } }
function localDateValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}
function toIso(value) { return value ? new Date(value).toISOString() : undefined; }
function groupOf(path) {
  const i = String(path || "").indexOf("/");
  return i > 0 ? path.slice(0, i) : "";
}
function parseHashPath() {
  const h = location.hash || "";
  if (!h.startsWith("#library")) return null;
  const q = h.indexOf("?");
  if (q < 0) return null;
  const params = new URLSearchParams(h.slice(q + 1));
  return params.get("path");
}

export function init(sectionEl, ctx) {
  injectStyles();
  const { api, bus, esc, md, toast, error } = ctx;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  sectionEl.innerHTML = `
    <span class="kicker">Library</span>
    <h1 class="title">The reading room</h1>
    <p class="lede">Governed documents with lineage — every revision kept, every change recorded.</p>
    <div class="row" style="margin:.75rem 0 1rem; align-items:flex-end; flex-wrap:wrap; gap:.6rem;">
      <div class="field" style="flex:1; min-width:220px; margin:0;">
        <label class="lbl" for="libFilter">Filter documents</label>
        <input class="input" id="libFilter" type="search" placeholder="title or path…" autocomplete="off" />
      </div>
      <button class="btn btn--primary" id="libNewBtn" type="button">+ New document</button>
    </div>
    <div class="lib-layout">
      <aside class="lib-list-pane" aria-label="Document list">
        <div class="list" id="libList" aria-live="polite" aria-busy="false"></div>
      </aside>
      <main class="lib-reader card" aria-label="Reader">
        <div id="libReader" aria-live="polite" aria-busy="false"></div>
      </main>
      <aside class="lib-rail" aria-label="Document outline and history">
        <div id="libRail"></div>
      </aside>
    </div>`;

  const listEl = sectionEl.querySelector("#libList");
  const readerEl = sectionEl.querySelector("#libReader");
  const railEl = sectionEl.querySelector("#libRail");
  const filterEl = sectionEl.querySelector("#libFilter");

  const state = {
    docs: [],
    doc: null,          // currently open full doc
    history: [],
    viewingVersion: null, // version row when previewing an old snapshot
    spy: null,          // IntersectionObserver for scrollspy
    lazy: null,         // IntersectionObserver for lazy list rendering
  };

  const loading = (el, msg) => { el.innerHTML = `<div class="loading"><span class="spinner"></span> ${esc(msg)}</div>`; };

  /* ---------- list ---------- */
  async function loadList() {
    listEl.setAttribute("aria-busy", "true");
    loading(listEl, "loading library…");
    try {
      const data = await api.get("/kb.docs");
      state.docs = data.docs || [];
      renderList();
    } catch (e) {
      error(listEl, e);
    } finally {
      listEl.setAttribute("aria-busy", "false");
    }
  }

  function renderList() {
    if (state.lazy) { state.lazy.disconnect(); state.lazy = null; }
    const filter = (filterEl.value || "").trim().toLowerCase();
    const docs = state.docs.filter(d => !filter || ((d.title || "") + " " + (d.path || "")).toLowerCase().includes(filter));
    if (!docs.length) {
      listEl.innerHTML = `<div class="empty">${state.docs.length
        ? "No documents match the filter."
        : `The library is empty. Start with your first governed doc — a brand bible, a runbook, an eval report.`}</div>`;
      return;
    }
    // group by top-level directory; known groups first, then others alpha, then ungrouped
    const groups = new Map();
    docs.forEach(d => {
      const g = groupOf(d.path);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(d);
    });
    const order = [
      ...KNOWN_GROUPS.filter(g => groups.has(g)),
      ...[...groups.keys()].filter(g => g && !KNOWN_GROUPS.includes(g)).sort(),
      ...(groups.has("") ? [""] : []),
    ];
    // flatten to render ops for simple lazy rendering
    const ops = [];
    order.forEach(g => {
      ops.push({ header: g || "unfiled" });
      groups.get(g).forEach(d => ops.push({ doc: d }));
    });

    listEl.innerHTML = "";
    let cursor = 0;
    const renderChunk = () => {
      const frag = document.createDocumentFragment();
      const end = Math.min(cursor + CHUNK, ops.length);
      for (; cursor < end; cursor++) {
        const op = ops[cursor];
        if (op.header != null) {
          const k = document.createElement("span");
          k.className = "kicker lib-group-kicker";
          k.textContent = op.header + "/";
          frag.appendChild(k);
        } else {
          frag.appendChild(docButton(op.doc));
        }
      }
      listEl.appendChild(frag);
      if (cursor < ops.length) {
        const sentinel = document.createElement("div");
        sentinel.setAttribute("aria-hidden", "true");
        sentinel.style.height = "1px";
        listEl.appendChild(sentinel);
        state.lazy = new IntersectionObserver(entries => {
          if (entries.some(en => en.isIntersecting)) {
            state.lazy.disconnect();
            sentinel.remove();
            renderChunk();
          }
        }, { rootMargin: "600px" });
        state.lazy.observe(sentinel);
      }
    };
    renderChunk();
    markActiveListItem();
  }

  function docButton(d) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "item lib-item-btn";
    b.dataset.docId = d.id;
    b.dataset.docPath = d.path || "";
    const stateCls = d.lifecycle_state === "active" || !d.lifecycle_state ? "pill--ok" : "pill--off";
    b.innerHTML = `<div style="min-width:0;text-align:left;">
        <div class="item__title">${esc(d.title || d.path)}</div>
        <div class="item__sub mono">${esc(d.path || "")}</div>
      </div>
      <div class="item__meta">
        <span class="pill pill--scope">${esc(d.scope || "global")}</span>
        <span class="pill ${stateCls}">${esc(d.lifecycle_state || "active")}</span>
      </div>`;
    b.addEventListener("click", () => openDoc(d.id));
    return b;
  }

  function markActiveListItem() {
    const id = state.doc && state.doc.id;
    listEl.querySelectorAll(".lib-item-btn").forEach(b => {
      if (id && b.dataset.docId === id) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
  }

  filterEl.addEventListener("input", renderList);
  sectionEl.querySelector("#libNewBtn").addEventListener("click", () => renderEditor(null));

  /* ---------- reader ---------- */
  async function openDoc(idOrPath, { updateHash = true } = {}) {
    readerEl.setAttribute("aria-busy", "true");
    loading(readerEl, "opening document…");
    railEl.innerHTML = "";
    state.viewingVersion = null;
    try {
      const { doc } = await api.get("/kb.get_doc", { id: idOrPath });
      if (!doc) {
        readerEl.innerHTML = `<div class="empty">Document not found${idOrPath ? ` — <span class="mono">${esc(String(idOrPath))}</span>` : ""}.</div>`;
        return;
      }
      state.doc = doc;
      if (updateHash && doc.path) {
        // replaceState does NOT fire hashchange, so no suppression flag is needed.
        history.replaceState(null, "", "#library?path=" + encodeURIComponent(doc.path));
      }
      renderReader(doc);
      markActiveListItem();
      loadRail(doc);
    } catch (e) {
      error(readerEl, e);
    } finally {
      readerEl.setAttribute("aria-busy", "false");
    }
  }

  function metaPills(doc) {
    const stateCls = doc.lifecycle_state === "active" || !doc.lifecycle_state ? "pill--ok" : "pill--off";
    const bits = [
      `<span class="pill pill--scope">${esc(doc.scope || "global")}</span>`,
      `<span class="pill">owner: ${esc(doc.owner || "—")}</span>`,
      `<span class="pill ${stateCls}">${esc(doc.lifecycle_state || "active")}</span>`,
      doc.updated_at ? `<span class="pill">updated ${esc(relTime(doc.updated_at))}</span>` : "",
      doc.review_due_at ? `<span class="pill">review due ${esc(relTime(doc.review_due_at))}</span>` : "",
      doc.provenance ? `<span class="pill">source: ${esc(doc.provenance.source_type)}</span>` : "",
    ];
    return bits.filter(Boolean).join("");
  }

  function renderReader(doc, opts = {}) {
    const { versionBanner = "", body = doc.body } = opts;
    const editable = doc.lifecycle_state === "active" || !doc.lifecycle_state;
    readerEl.innerHTML = `
      ${versionBanner}
      <div class="row" style="justify-content:space-between; align-items:flex-start; gap:.75rem; flex-wrap:wrap;">
        <div style="min-width:0;">
          <div class="item__sub mono">${esc(doc.path || "")}</div>
          <h2 class="lib-doc-title">${esc(doc.title || doc.path || "Untitled")}</h2>
          <div class="row" style="flex-wrap:wrap; gap:.35rem;">${metaPills(doc)}</div>
        </div>
        <div class="row" style="gap:.4rem;">
          ${editable && !state.viewingVersion ? '<button class="btn btn--sm" id="libEditBtn" type="button">Edit</button>' : ""}
          ${doc.lifecycle_state !== "tombstoned" && !state.viewingVersion ? '<button class="btn btn--sm" id="libDeleteBtn" type="button">Delete</button>' : ""}
        </div>
      </div>
      <hr style="border:none; border-top:1px solid var(--mu-color-border); margin:1rem 0;" />
      <div class="lib-md-scroll"><div class="lib-md" id="libMd">${body != null ? md(body) : `<div class="empty">This snapshot's body is not exposed by the backend — metadata only.</div>`}</div></div>`;
    const editBtn = readerEl.querySelector("#libEditBtn");
    if (editBtn) editBtn.addEventListener("click", () => renderEditor(doc));
    const delBtn = readerEl.querySelector("#libDeleteBtn");
    if (delBtn) delBtn.addEventListener("click", () => deleteDoc(doc));
    buildToc();
  }

  async function deleteDoc(doc) {
    const reason = window.prompt(`Deletion reason for ${doc.path}:`, "Retired from governed memory");
    if (!reason || reason.trim().length < 3) return;
    if (!window.confirm("Delete retrievable content and all local projections? This action is recorded.")) return;
    try {
      const data = await api.post("/kb.delete_doc?id=" + encodeURIComponent(doc.id), { reason: reason.trim() });
      const complete = data && data.deletion && data.deletion.complete;
      toast(complete ? "ok" : "warn", complete ? "Document deleted" : "Content deleted; projection cleanup is pending");
      await loadList();
      openDoc(doc.id, { updateHash: false });
    } catch (e) { toast("err", e.message); }
  }

  /* ---------- editor ---------- */
  function renderEditor(doc) {
    const isNew = !doc;
    const d = doc || { path: "", body: "", scope: "personal", review_due_at: "" };
    railEl.innerHTML = "";
    readerEl.innerHTML = `
      <h2 class="section" style="margin-top:0;">${isNew ? "New document" : "Edit document"}</h2>
      <div class="field"><label class="lbl" for="libEdPath">Path</label>
        <input class="input mono" id="libEdPath" value="${esc(d.path)}" placeholder="engineering/title.md" ${isNew ? "" : "readonly"} /></div>
      <div class="row" style="margin-bottom:.8rem; flex-wrap:wrap; gap:.6rem;">
        <div class="field" style="margin:0;"><label class="lbl" for="libEdScope">Visibility</label>
          <select class="select" id="libEdScope">
            <option ${d.scope === "personal" ? "selected" : ""}>personal</option>
            <option ${d.scope === "global" ? "selected" : ""}>global</option>
          </select></div>
        ${isNew ? `
        <div class="field" style="margin:0;"><label class="lbl" for="libEdValidFrom">Valid from</label>
          <input class="input" id="libEdValidFrom" type="datetime-local" /></div>
        <div class="field" style="margin:0;"><label class="lbl" for="libEdSupersedes">Supersedes ID</label>
          <input class="input mono" id="libEdSupersedes" /></div>` : ""}
        <div class="field" style="margin:0;"><label class="lbl" for="libEdReviewDue">Review due</label>
          <input class="input" id="libEdReviewDue" type="datetime-local" value="${esc(localDateValue(d.review_due_at))}" /></div>
      </div>
      <div class="field"><label class="lbl" for="libEdBody">Markdown</label>
        <textarea class="textarea mono" id="libEdBody" style="min-height:320px;">${esc(d.body || "")}</textarea></div>
      <div class="row" style="gap:.5rem;">
        <button class="btn btn--primary" id="libEdSave" type="button">Save</button>
        <button class="btn" id="libEdCancel" type="button">Cancel</button>
      </div>`;
    readerEl.querySelector("#libEdCancel").addEventListener("click", () => {
      if (isNew) {
        readerEl.innerHTML = `<div class="empty">Select a document from the library.</div>`;
        railEl.innerHTML = "";
      } else {
        openDoc(d.id, { updateHash: false });
      }
    });
    readerEl.querySelector("#libEdSave").addEventListener("click", async () => {
      const path = readerEl.querySelector("#libEdPath").value.trim();
      const body = readerEl.querySelector("#libEdBody").value;
      if (!path) { toast("err", "Path is required"); return; }
      if (!body.trim()) { toast("err", "Body is required"); return; }
      const saveBtn = readerEl.querySelector("#libEdSave");
      saveBtn.disabled = true;
      try {
        const payload = {
          path,
          md: body,
          scope: readerEl.querySelector("#libEdScope").value,
          review_due_at: toIso(readerEl.querySelector("#libEdReviewDue").value),
        };
        if (isNew) {
          payload.valid_from = toIso(readerEl.querySelector("#libEdValidFrom").value);
          payload.supersedes_id = readerEl.querySelector("#libEdSupersedes").value.trim() || undefined;
        }
        const { id } = await api.post("/kb.upsert_doc", payload);
        toast("ok", "Saved");
        await loadList();
        openDoc(id);
      } catch (e) {
        toast("err", e.message);
        saveBtn.disabled = false;
      }
    });
    const pathInput = readerEl.querySelector("#libEdPath");
    if (isNew) pathInput.focus();
  }

  /* ---------- right rail: TOC + timeline + ask ---------- */
  async function loadRail(doc) {
    railEl.innerHTML = `
      <div class="card">
        <span class="kicker">On this page</span>
        <ul class="lib-toc" id="libToc"><li class="empty" style="padding:.4rem 0;">No headings.</li></ul>
      </div>
      <div class="card" style="margin-top:1rem;">
        <span class="kicker">Versions</span>
        <ul class="lib-timeline" id="libTimeline" aria-live="polite"><li class="loading"><span class="spinner"></span> loading…</li></ul>
      </div>
      <button class="btn" id="libAskBtn" type="button" style="margin-top:1rem; width:100%;">Ask about this doc</button>`;
    railEl.querySelector("#libAskBtn").addEventListener("click", () => {
      const query = `What does "${doc.title || doc.path}" cover, and what decisions does it record?`;
      location.hash = "#ask";
      bus.emit("ask:run", { query, scope: doc.scope || undefined, k: 6 });
    });
    buildToc();
    try {
      const { history: hist } = await api.get("/kb.doc_history", { id: doc.id });
      state.history = hist || [];
      renderTimeline(doc);
    } catch (e) {
      const tl = railEl.querySelector("#libTimeline");
      if (tl) tl.innerHTML = `<li class="empty" style="padding:.4rem 0;">${esc(e.message)}</li>`;
    }
  }

  function renderTimeline(doc) {
    const tl = railEl.querySelector("#libTimeline");
    if (!tl) return;
    const items = [];
    items.push(`<li><button type="button" data-version="current" ${!state.viewingVersion ? 'aria-current="true"' : ""}>
        <strong>current</strong> · ${esc(relTime(doc.updated_at))}
      </button></li>`);
    state.history.forEach(h => {
      const active = state.viewingVersion && state.viewingVersion.version_id === h.version_id;
      items.push(`<li><button type="button" data-version="${esc(String(h.version_id))}" ${active ? 'aria-current="true"' : ""}>
          v${esc(String(h.version_id))} · ${esc(relTime(h.created_at))} <span class="mono" style="font-size:.9em;">(${esc(String(h.bytes))} B)</span>
        </button></li>`);
    });
    if (!state.history.length) {
      items.push(`<li class="empty" style="padding:.4rem 0 .4rem 1.6rem;">First revision — no prior versions.</li>`);
    }
    tl.innerHTML = items.join("");
    tl.querySelectorAll("button[data-version]").forEach(b => {
      b.addEventListener("click", () => {
        const v = b.dataset.version;
        if (v === "current") {
          state.viewingVersion = null;
          renderReader(doc);
        } else {
          previewVersion(doc, state.history.find(h => String(h.version_id) === v));
        }
        renderTimeline(doc);
      });
    });
  }

  async function previewVersion(doc, row) {
    if (!row) return;
    state.viewingVersion = row;
    const banner = `<div class="lib-version-banner" role="status">
        <span>Viewing <strong>v${esc(String(row.version_id))}</strong> · ${esc(fmtTime(row.created_at))} — read-only snapshot</span>
        <button class="btn btn--sm" id="libBackCurrent" type="button">Back to current</button>
      </div>`;
    // Version bodies: prefer an inline body if the backend ever includes one;
    // otherwise try a dedicated endpoint; otherwise degrade to metadata-only.
    let body = row.body != null ? row.body : null;
    if (body == null) {
      try {
        const res = await api.get("/kb.doc_version", { id: doc.id, version_id: row.version_id });
        if (res && res.version && res.version.body != null) body = res.version.body;
      } catch { /* endpoint absent on this backend build — degrade gracefully */ }
    }
    renderReader(doc, { versionBanner: banner, body });
    const back = readerEl.querySelector("#libBackCurrent");
    if (back) back.addEventListener("click", () => {
      state.viewingVersion = null;
      renderReader(doc);
      renderTimeline(doc);
    });
  }

  function buildToc() {
    const tocEl = railEl.querySelector("#libToc");
    const mdEl = readerEl.querySelector("#libMd");
    if (state.spy) { state.spy.disconnect(); state.spy = null; }
    if (!tocEl || !mdEl) return;
    const heads = Array.from(mdEl.querySelectorAll("h2, h3"));
    if (!heads.length) {
      tocEl.innerHTML = `<li class="empty" style="padding:.4rem 0;">No headings.</li>`;
      return;
    }
    heads.forEach((h, i) => { if (!h.id) h.id = "lib-h-" + i; });
    tocEl.innerHTML = heads.map(h =>
      `<li data-level="${h.tagName === "H3" ? 3 : 2}"><button type="button" data-target="${esc(h.id)}">${esc(h.textContent)}</button></li>`
    ).join("");
    const buttons = Array.from(tocEl.querySelectorAll("button[data-target]"));
    buttons.forEach(b => b.addEventListener("click", () => {
      const target = document.getElementById(b.dataset.target);
      if (target) target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    }));
    // scrollspy: highlight the heading nearest the top of the viewport
    const setCurrent = (id) => buttons.forEach(b => {
      if (b.dataset.target === id) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
    const visible = new Set();
    state.spy = new IntersectionObserver(entries => {
      entries.forEach(en => {
        if (en.isIntersecting) visible.add(en.target.id);
        else visible.delete(en.target.id);
      });
      if (visible.size) {
        const first = heads.find(h => visible.has(h.id));
        if (first) setCurrent(first.id);
      } else {
        // nothing in the band — highlight the last heading above the viewport
        let last = null;
        for (const h of heads) {
          if (h.getBoundingClientRect().top < 120) last = h; else break;
        }
        if (last) setCurrent(last.id);
      }
    }, { rootMargin: "-10% 0px -70% 0px" });
    heads.forEach(h => state.spy.observe(h));
    setCurrent(heads[0].id);
  }

  /* ---------- deep-link ---------- */
  function handleHash() {
    const path = parseHashPath();
    if (path && (!state.doc || state.doc.path !== path)) {
      openDoc(path, { updateHash: false });
    }
  }
  window.addEventListener("hashchange", handleHash);

  /* ---------- bus wiring (palette + constellation) ---------- */
  // Command palette: doc selected -> open it; "New document" action.
  bus.on("library:open", (p = {}) => { if (p.id || p.path) openDoc(p.id || p.path); });
  bus.on("library:new", () => renderEditor(null));
  // Constellation "Open in Library" emits nav {view:'library', ref, path}.
  bus.on("nav", (p = {}) => {
    if (p.view === "library" && (p.path || p.ref)) openDoc(p.path || p.ref);
  });

  /* ---------- boot ---------- */
  readerEl.innerHTML = `<div class="empty">Select a document from the library — or press <strong>+ New document</strong> to add one.</div>`;
  loadList().then(() => {
    const path = parseHashPath();
    if (path) openDoc(path, { updateHash: false });
  });
}
