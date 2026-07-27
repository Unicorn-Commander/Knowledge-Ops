/* Library — the wiki reading room.
   Three zones: collapsible directory tree · reader · right rail (TOC +
   version timeline + backlinks). Preserves the classic behaviors (filter,
   +New, edit, delete, history, deep-links #library?path=..., scrollspy) and
   adds wiki semantics: [[wikilinks]] with red-link create flow, breadcrumbs,
   backlinks/wanted panels from /kb.links (tolerates its absence), a wiki home
   start page, and a side-by-side live-preview editor. Emits
   'wiki:links-changed' after saves so the constellation can refresh lazily. */

const KNOWN_GROUPS = ["ai-platform", "apps", "engineering", "infrastructure", "marketing"];
const EXPANDED_KEY = "ko.lib.expanded"; // sessionStorage: expanded dir paths
const LINKS_TTL_MS = 60000;             // /kb.links cache window

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
  /* wiki links — resolved links ride the standard accent anchor styling;
     missing pages are red-links: danger at reduced opacity, dotted underline. */
  #view-library a.wl-missing,
  #view-library button.lib-wanted-btn {
    color: color-mix(in srgb, var(--mu-color-danger) 72%, transparent);
    text-decoration: underline dotted;
    text-underline-offset: 3px;
  }
  #view-library a.wl-missing:hover,
  #view-library button.lib-wanted-btn:hover {
    color: var(--mu-color-danger);
  }
  /* directory tree */
  #view-library .lib-tree, #view-library .lib-tree ul { list-style: none; margin: 0; padding: 0; }
  #view-library .lib-tree ul.lib-tree-group {
    margin-left: .55rem;
    padding-left: .75rem;
    border-left: 1px solid var(--mu-color-border);
  }
  #view-library .lib-tree li { margin: .1rem 0; }
  #view-library .lib-tree-dir, #view-library .lib-tree-doc {
    display: flex; align-items: center; gap: .45rem;
    width: 100%; min-height: 44px; padding: .5rem .55rem;
    border: 1px solid transparent;
    border-radius: var(--mu-radius-sm);
    background: transparent;
    color: var(--mu-color-muted);
    font: inherit; font-size: .88rem; text-align: left;
    cursor: pointer;
    transition: background 140ms cubic-bezier(0.22, 1, 0.36, 1),
      color 140ms cubic-bezier(0.22, 1, 0.36, 1),
      border-color 140ms cubic-bezier(0.22, 1, 0.36, 1);
  }
  #view-library .lib-tree-dir:hover, #view-library .lib-tree-doc:hover {
    color: var(--mu-color-text);
    background: var(--mu-color-surface);
  }
  #view-library .lib-tree-dir { font-weight: 700; }
  #view-library .lib-tree-doc[aria-current="true"] {
    color: var(--mu-color-text);
    background: var(--mu-color-surface-strong);
    border-color: var(--mu-color-border-strong);
  }
  #view-library .lib-caret {
    flex: none;
    width: .5rem; height: .5rem;
    margin-inline: .1rem .05rem;
    border-right: 1.5px solid currentColor;
    border-bottom: 1.5px solid currentColor;
    transform: rotate(-45deg);
    transition: transform 140ms cubic-bezier(0.22, 1, 0.36, 1);
  }
  #view-library .lib-tree-dir[aria-expanded="true"] .lib-caret { transform: rotate(45deg); }
  #view-library .lib-doc-name { min-width: 0; overflow-wrap: anywhere; }
  #view-library .lib-tree-doc .lib-doc-name { font-weight: 600; }
  #view-library .lib-count {
    margin-left: auto;
    font-size: .72rem;
    color: var(--mu-color-dim);
  }
  #view-library .lib-leaf-meta { margin-left: auto; display: flex; gap: .3rem; }
  /* breadcrumbs */
  #view-library .lib-crumbs {
    display: flex; flex-wrap: wrap; align-items: center; gap: .05rem;
    margin: 0 0 .15rem;
    font-family: var(--mu-font-mono);
    font-size: .78rem;
  }
  #view-library .lib-crumbs button {
    min-height: 44px; padding: .2rem .3rem;
    background: none; border: none;
    border-radius: var(--mu-radius-sm);
    color: var(--mu-color-accent);
    font: inherit; cursor: pointer;
  }
  #view-library .lib-crumbs button:hover { text-decoration: underline; text-underline-offset: 3px; }
  #view-library .lib-crumb-sep { color: var(--mu-color-dim); }
  #view-library .lib-crumb-leaf { color: var(--mu-color-dim); padding: .2rem .3rem; overflow-wrap: anywhere; }
  /* right-rail link lists (backlinks / wanted) */
  #view-library .lib-links { list-style: none; margin: .4rem 0 0; padding: 0; }
  #view-library .lib-links button {
    display: block; width: 100%; min-height: 44px;
    text-align: left;
    background: none; border: none;
    border-radius: var(--mu-radius-sm);
    color: var(--mu-color-muted);
    font: inherit; font-size: .85rem;
    padding: .55rem .6rem;
    cursor: pointer;
    overflow-wrap: anywhere;
  }
  #view-library .lib-links button:hover { color: var(--mu-color-text); background: var(--mu-color-surface); }
  #view-library .lib-links button.lib-wanted-btn:hover { color: var(--mu-color-danger); }
  /* editor: side-by-side live preview */
  #view-library .lib-ed-split {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 1rem;
    align-items: start;
  }
  @media (max-width: 860px) {
    #view-library .lib-ed-split { grid-template-columns: minmax(0, 1fr); }
  }
  #view-library .lib-ed-preview {
    border: 1px solid var(--mu-color-border);
    border-radius: var(--mu-radius-sm);
    background: var(--mu-color-bg-elevated);
    padding: .75rem .95rem;
    min-height: 320px;
    max-height: 70vh;
    overflow: auto;
  }
  /* wiki home */
  #view-library .lib-home-grid {
    display: grid; gap: 1.25rem;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: start;
  }
  @media (max-width: 860px) {
    #view-library .lib-home-grid { grid-template-columns: minmax(0, 1fr); }
  }
  /* TOC + timeline (unchanged vocabulary) */
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
function dirOf(path) {
  const i = String(path || "").lastIndexOf("/");
  return i > 0 ? String(path).slice(0, i) : "";
}
function parseHashPath() {
  const h = location.hash || "";
  if (!h.startsWith("#library")) return null;
  const q = h.indexOf("?");
  if (q < 0) return null;
  const params = new URLSearchParams(h.slice(q + 1));
  return params.get("path");
}
function slugSeg(s) {
  return String(s).trim()
    .replace(/\s+/g, "-")
    .replace(/[^A-Za-z0-9._-]/g, "")
    .replace(/-{2,}/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
}
/* Create path for a missing [[target]]: explicit paths are slugged per
   segment from the root; bare names are slugged under the current doc's dir. */
function createPathFor(target, dir) {
  const clean = String(target || "").trim().replace(/\.md$/i, "");
  if (clean.includes("/")) {
    const p = clean.split("/").map(slugSeg).filter(Boolean).join("/");
    return (p || "untitled") + ".md";
  }
  const base = slugSeg(clean) || "untitled";
  return (dir ? dir + "/" : "") + base + ".md";
}
function loadExpanded() {
  try { return new Set(JSON.parse(sessionStorage.getItem(EXPANDED_KEY) || "[]")); }
  catch { return new Set(); }
}

/* Tolerant /kb.links normalizer -> {links: [{src,dst}], missing: [{src,target}]}.
   Accepts key aliases and rows flagged exists:false in the main array. */
function normalizeLinks(raw) {
  const links = [], missing = [];
  const pick = (r, keys) => {
    for (const k of keys) { const v = r[k]; if (v != null && v !== "") return String(v); }
    return null;
  };
  const rows = (raw && (raw.links || raw.edges || raw.rows)) || (Array.isArray(raw) ? raw : []);
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const src = pick(r, ["src", "src_path", "source", "from", "from_path"]);
    const dst = pick(r, ["dst", "dst_path", "to", "to_path", "target_path", "path"]);
    const target = pick(r, ["target", "title", "label"]);
    if (r.exists === false || r.resolved === false || (!dst && target)) {
      if (src || target) missing.push({ src: src || "", target: target || dst || "" });
    } else if (src && dst) {
      links.push({ src, dst });
    }
  }
  for (const r of ((raw && (raw.missing || raw.wanted)) || [])) {
    if (!r) continue;
    if (typeof r === "string") { missing.push({ src: "", target: r }); continue; }
    const src = pick(r, ["src", "src_path", "source", "from", "from_path"]) || "";
    const target = pick(r, ["target", "dst", "title", "label"]) || "";
    if (target) missing.push({ src, target });
  }
  return { links, missing };
}

export function init(sectionEl, ctx) {
  injectStyles();
  const { api, bus, esc, md, toast, error } = ctx;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  sectionEl.innerHTML = `
    <span class="kicker">Library</span>
    <h1 class="title">The reading room</h1>
    <p class="lede">Governed documents with lineage — every revision kept, every change recorded, every page linked.</p>
    <div class="row" style="margin:.75rem 0 1rem; align-items:flex-end; flex-wrap:wrap; gap:.6rem;">
      <div class="field" style="flex:1; min-width:220px; margin:0;">
        <label class="lbl" for="libFilter">Filter documents</label>
        <input class="input" id="libFilter" type="search" placeholder="title or path…" autocomplete="off" />
      </div>
      <button class="btn btn--primary" id="libNewBtn" type="button">+ New document</button>
    </div>
    <div class="lib-layout">
      <aside class="lib-list-pane" aria-label="Document tree">
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
    expanded: loadExpanded(), // expanded dir paths (sessionStorage-backed)
    links: { at: 0, data: null, unavailable: false, inflight: null },
  };

  const loading = (el, msg) => { el.innerHTML = `<div class="loading"><span class="spinner"></span> ${esc(msg)}</div>`; };
  const saveExpanded = () => {
    try { sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...state.expanded])); } catch { /* private mode */ }
  };

  /* ---------- wikilink resolver (per CONTRACT resolution order) ---------- */
  function makeResolver(currentDir) {
    const docs = state.docs.filter(d => d.path && (!d.lifecycle_state || d.lifecycle_state === "active"));
    const byTitle = new Map(), byPath = new Map(), bySuffix = new Map();
    for (const d of docs) {
      const t = String(d.title || "").trim().toLowerCase();
      if (t && !byTitle.has(t)) byTitle.set(t, d.path);
      byPath.set(d.path, d.path);
      const noExt = d.path.replace(/\.md$/i, "").toLowerCase();
      const base = noExt.split("/").pop();
      if (!bySuffix.has(base)) bySuffix.set(base, []);
      bySuffix.get(base).push({ noExt, path: d.path });
    }
    const found = (path) => ({ href: "#library?path=" + encodeURIComponent(path), exists: true, path });
    return (target) => {
      const t = String(target || "").trim().replace(/^(\.\/|\/)+/, "");
      if (!t) return null;
      const lower = t.toLowerCase();
      if (byTitle.has(lower)) return found(byTitle.get(lower));          // 1. exact title (ci)
      if (byPath.has(t)) return found(byPath.get(t));                    // 2. exact path
      const noExt = lower.replace(/\.md$/, "");
      const base = noExt.split("/").pop();
      const cands = (bySuffix.get(base) || [])
        .filter(c => c.noExt === noExt || c.noExt.endsWith("/" + noExt));
      if (cands.length === 1) return found(cands[0].path);               // 3. unique suffix
      return { exists: false, path: createPathFor(t, currentDir) };      // MISSING (ambiguous too)
    };
  }

  /* ---------- /kb.links (cached, tolerant of a backend without it) ------- */
  function fetchLinks() {
    if (state.links.inflight) return state.links.inflight;
    if (Date.now() - state.links.at < LINKS_TTL_MS && (state.links.data || state.links.unavailable)) {
      return Promise.resolve(state.links.data);
    }
    state.links.inflight = (async () => {
      try {
        const raw = await api.get("/kb.links");
        state.links.data = normalizeLinks(raw);
        state.links.unavailable = false;
      } catch {
        // 404 (endpoint not shipped) or transient failure — hide the panels quietly.
        state.links.data = null;
        state.links.unavailable = true;
      }
      state.links.at = Date.now();
      state.links.inflight = null;
      return state.links.data;
    })();
    return state.links.inflight;
  }
  const invalidateLinks = () => { state.links.at = 0; state.links.data = null; state.links.unavailable = false; };

  /* ---------- directory tree ---------- */
  async function loadList() {
    listEl.setAttribute("aria-busy", "true");
    loading(listEl, "loading library…");
    try {
      const data = await api.get("/kb.docs");
      state.docs = data.docs || [];
      renderTree();
    } catch (e) {
      error(listEl, e);
    } finally {
      listEl.setAttribute("aria-busy", "false");
    }
  }

  function buildTreeModel(docs) {
    const root = { dirs: new Map(), docs: [] };
    for (const d of docs) {
      const parts = String(d.path || "").split("/").filter(Boolean);
      let node = root;
      for (let i = 0; i < parts.length - 1; i++) {
        const seg = parts[i];
        if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), docs: [] });
        node = node.dirs.get(seg);
      }
      node.docs.push(d);
    }
    return root;
  }
  function countDocs(node) {
    let n = node.docs.length;
    for (const child of node.dirs.values()) n += countDocs(child);
    return n;
  }

  function renderTree() {
    const filter = (filterEl.value || "").trim().toLowerCase();
    const docs = state.docs.filter(d => !filter || ((d.title || "") + " " + (d.path || "")).toLowerCase().includes(filter));
    if (!docs.length) {
      listEl.innerHTML = `<div class="empty">${state.docs.length
        ? "No documents match the filter."
        : `The library is empty. Start with your first governed doc — a brand bible, a runbook, an eval report.`}</div>`;
      return;
    }
    const root = buildTreeModel(docs);
    const tree = document.createElement("ul");
    tree.className = "lib-tree";
    renderNode(root, tree, "", !!filter); // filter -> auto-expand all matches
    listEl.innerHTML = "";
    listEl.appendChild(tree);
    markActiveListItem();
    initRoving();
  }

  function renderNode(node, ul, prefix, forceOpen) {
    const names = [...node.dirs.keys()];
    const ordered = prefix === ""
      ? [...KNOWN_GROUPS.filter(g => node.dirs.has(g)), ...names.filter(g => !KNOWN_GROUPS.includes(g)).sort()]
      : names.sort();
    for (const name of ordered) {
      const dirPath = prefix ? prefix + "/" + name : name;
      const child = node.dirs.get(name);
      const open = forceOpen || state.expanded.has(dirPath);
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "lib-tree-dir";
      btn.dataset.dir = dirPath;
      btn.setAttribute("aria-expanded", String(open));
      btn.innerHTML = `<span class="lib-caret" aria-hidden="true"></span><span class="lib-doc-name">${esc(name)}/</span><span class="lib-count mono">${countDocs(child)}</span>`;
      const group = document.createElement("ul");
      group.className = "lib-tree-group";
      group.hidden = !open;
      btn.addEventListener("click", () => {
        const isOpen = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!isOpen));
        group.hidden = isOpen;
        if (isOpen) state.expanded.delete(dirPath); else state.expanded.add(dirPath);
        saveExpanded();
      });
      li.appendChild(btn);
      li.appendChild(group);
      ul.appendChild(li);
      renderNode(child, group, dirPath, forceOpen);
    }
    const docsSorted = [...node.docs].sort((a, b) =>
      String(a.title || a.path || "").localeCompare(String(b.title || b.path || "")));
    for (const d of docsSorted) {
      const li = document.createElement("li");
      li.appendChild(docButton(d));
      ul.appendChild(li);
    }
  }

  function docButton(d) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "lib-tree-doc";
    b.dataset.docId = d.id;
    b.dataset.docPath = d.path || "";
    b.title = d.path || "";
    const flags = [];
    if (d.scope === "personal") flags.push('<span class="pill pill--scope">personal</span>');
    if (d.lifecycle_state && d.lifecycle_state !== "active") flags.push(`<span class="pill pill--off">${esc(d.lifecycle_state)}</span>`);
    b.innerHTML = `<span class="lib-doc-name">${esc(d.title || d.path)}</span>${flags.length ? `<span class="lib-leaf-meta">${flags.join("")}</span>` : ""}`;
    b.addEventListener("click", () => openDoc(d.id));
    return b;
  }

  function markActiveListItem() {
    const id = state.doc && state.doc.id;
    listEl.querySelectorAll(".lib-tree-doc").forEach(b => {
      if (id && b.dataset.docId === id) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
  }

  /* Expand the ancestors of a path so its leaf is visible in the tree. */
  function ensureVisible(path) {
    if (!path) return;
    const parts = String(path).split("/").filter(Boolean);
    parts.pop();
    let prefix = "", changed = false;
    for (const seg of parts) {
      prefix = prefix ? prefix + "/" + seg : seg;
      if (!state.expanded.has(prefix)) { state.expanded.add(prefix); changed = true; }
    }
    if (changed) { saveExpanded(); renderTree(); }
  }

  /* Tree keyboard support: roving tabindex + arrows (disclosure pattern). */
  function visibleTreeButtons() {
    return [...listEl.querySelectorAll(".lib-tree-dir, .lib-tree-doc")].filter(b => b.offsetParent !== null);
  }
  function initRoving() {
    const items = visibleTreeButtons();
    if (!items.length) return;
    items.forEach(b => { b.tabIndex = -1; });
    const current = items.find(b => b.getAttribute("aria-current") === "true");
    (current || items[0]).tabIndex = 0;
  }
  listEl.addEventListener("focusin", (e) => {
    if (!e.target.matches || !e.target.matches(".lib-tree-dir, .lib-tree-doc")) return;
    visibleTreeButtons().forEach(b => { b.tabIndex = b === e.target ? 0 : -1; });
  });
  listEl.addEventListener("keydown", (e) => {
    const t = e.target;
    if (!t.matches || !t.matches(".lib-tree-dir, .lib-tree-doc")) return;
    const items = visibleTreeButtons();
    const idx = items.indexOf(t);
    if (idx < 0) return;
    const focus = (el) => { if (el) { el.tabIndex = 0; t.tabIndex = el === t ? 0 : -1; el.focus(); } };
    const isDir = t.classList.contains("lib-tree-dir");
    if (e.key === "ArrowDown") { e.preventDefault(); focus(items[idx + 1]); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focus(items[idx - 1]); }
    else if (e.key === "Home") { e.preventDefault(); focus(items[0]); }
    else if (e.key === "End") { e.preventDefault(); focus(items[items.length - 1]); }
    else if (e.key === "ArrowRight") {
      e.preventDefault();
      if (isDir && t.getAttribute("aria-expanded") === "false") t.click();
      else focus(items[idx + 1]);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      if (isDir && t.getAttribute("aria-expanded") === "true") t.click();
      else {
        const group = t.closest("ul.lib-tree-group");
        const parentBtn = group && group.parentElement.querySelector(":scope > .lib-tree-dir");
        focus(parentBtn);
      }
    }
    // Enter/Space: native button activation.
  });

  filterEl.addEventListener("input", renderTree);
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
      ensureVisible(doc.path);
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

  function crumbsHtml(path) {
    const parts = String(path || "").split("/").filter(Boolean);
    if (!parts.length) return "";
    const out = ['<nav class="lib-crumbs" aria-label="Document location">'];
    let prefix = "";
    parts.forEach((seg, i) => {
      if (i < parts.length - 1) {
        prefix = prefix ? prefix + "/" + seg : seg;
        out.push(`<button type="button" data-crumb="${esc(prefix)}">${esc(seg)}</button><span class="lib-crumb-sep" aria-hidden="true">/</span>`);
      } else {
        out.push(`<span class="lib-crumb-leaf">${esc(seg)}</span>`);
      }
    });
    out.push("</nav>");
    return out.join("");
  }

  function renderReader(doc, opts = {}) {
    const { versionBanner = "", body = doc.body } = opts;
    const editable = doc.lifecycle_state === "active" || !doc.lifecycle_state;
    const resolveLink = makeResolver(dirOf(doc.path));
    readerEl.innerHTML = `
      ${versionBanner}
      <div class="row" style="justify-content:space-between; align-items:flex-start; gap:.75rem; flex-wrap:wrap;">
        <div style="min-width:0;">
          ${crumbsHtml(doc.path)}
          <h2 class="lib-doc-title">${esc(doc.title || doc.path || "Untitled")}</h2>
          <div class="row" style="flex-wrap:wrap; gap:.35rem;">${metaPills(doc)}</div>
        </div>
        <div class="row" style="gap:.4rem;">
          ${editable && !state.viewingVersion ? '<button class="btn btn--sm" id="libEditBtn" type="button">Edit</button>' : ""}
          ${doc.lifecycle_state !== "tombstoned" && !state.viewingVersion ? '<button class="btn btn--sm" id="libDeleteBtn" type="button">Delete</button>' : ""}
        </div>
      </div>
      <hr style="border:none; border-top:1px solid var(--mu-color-border); margin:1rem 0;" />
      <div class="lib-md-scroll"><div class="lib-md" id="libMd">${body != null ? md(body, { resolveLink }) : `<div class="empty">This snapshot's body is not exposed by the backend — metadata only.</div>`}</div></div>`;
    const editBtn = readerEl.querySelector("#libEditBtn");
    if (editBtn) editBtn.addEventListener("click", () => renderEditor(doc));
    const delBtn = readerEl.querySelector("#libDeleteBtn");
    if (delBtn) delBtn.addEventListener("click", () => deleteDoc(doc));
    readerEl.querySelectorAll("[data-crumb]").forEach(b => b.addEventListener("click", () => {
      filterEl.value = b.dataset.crumb + "/";
      renderTree();
    }));
    buildToc();
  }

  /* Wiki navigation: intercept .wl / .wl-missing anchors (event delegation).
     Inside the live editor preview links are inert so edits are never lost. */
  readerEl.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a.wl, a.wl-missing");
    if (!a || !readerEl.contains(a)) return;
    e.preventDefault();
    if (a.closest("[data-preview]")) return;
    if (a.classList.contains("wl-missing")) {
      startCreate(a.dataset.create || "", a.dataset.wlTarget || "");
    } else if (a.dataset.path) {
      openDoc(a.dataset.path);
    }
  });

  function startCreate(path, targetText) {
    const base = String(path || "").split("/").pop().replace(/\.md$/i, "");
    const title = String(targetText || base.replace(/-/g, " ")).trim();
    renderEditor(null, { path, title });
  }

  async function deleteDoc(doc) {
    const reason = window.prompt(`Deletion reason for ${doc.path}:`, "Retired from governed memory");
    if (!reason || reason.trim().length < 3) return;
    if (!window.confirm("Delete retrievable content and all local projections? This action is recorded.")) return;
    try {
      const data = await api.post("/kb.delete_doc?id=" + encodeURIComponent(doc.id), { reason: reason.trim() });
      const complete = data && data.deletion && data.deletion.complete;
      toast(complete ? "ok" : "warn", complete ? "Document deleted" : "Content deleted; projection cleanup is pending");
      invalidateLinks();
      bus.emit("wiki:links-changed");
      await loadList();
      openDoc(doc.id, { updateHash: false });
    } catch (e) { toast("err", e.message); }
  }

  /* ---------- editor (side-by-side live preview) ---------- */
  function renderEditor(doc, extras = {}) {
    const isNew = !doc;
    const d = doc || {
      path: extras.path || "",
      body: extras.title ? `# ${extras.title}\n\n` : "",
      scope: "personal",
      review_due_at: "",
    };
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
      <div class="lib-ed-split">
        <div class="field" style="margin:0;"><label class="lbl" for="libEdBody">Markdown</label>
          <textarea class="textarea mono" id="libEdBody" style="min-height:320px;">${esc(d.body || "")}</textarea></div>
        <div style="min-width:0;"><span class="lbl" id="libEdPreviewLbl">Preview</span>
          <div class="lib-md lib-ed-preview" id="libEdPreview" data-preview="1" role="region" aria-labelledby="libEdPreviewLbl"></div></div>
      </div>
      <div class="row" style="gap:.5rem; margin-top:.85rem;">
        <button class="btn btn--primary" id="libEdSave" type="button">Save</button>
        <button class="btn" id="libEdCancel" type="button">Cancel</button>
      </div>`;
    const pathInput = readerEl.querySelector("#libEdPath");
    const bodyInput = readerEl.querySelector("#libEdBody");
    const previewEl = readerEl.querySelector("#libEdPreview");
    // Live preview: 150ms debounce, SAME resolver as the reader so wikilinks
    // (and red-links) render exactly as they will after save.
    let previewTimer = 0;
    const renderPreview = () => {
      previewEl.innerHTML = md(bodyInput.value, { resolveLink: makeResolver(dirOf(pathInput.value.trim())) });
    };
    const schedulePreview = () => { clearTimeout(previewTimer); previewTimer = setTimeout(renderPreview, 150); };
    bodyInput.addEventListener("input", schedulePreview);
    pathInput.addEventListener("input", schedulePreview);
    renderPreview();

    readerEl.querySelector("#libEdCancel").addEventListener("click", () => {
      if (isNew) renderHome();
      else openDoc(d.id, { updateHash: false });
    });
    readerEl.querySelector("#libEdSave").addEventListener("click", async () => {
      const path = pathInput.value.trim();
      const body = bodyInput.value;
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
        invalidateLinks();
        bus.emit("wiki:links-changed"); // constellation refreshes link edges lazily
        await loadList();
        openDoc(id);
      } catch (e) {
        toast("err", e.message);
        saveBtn.disabled = false;
      }
    });
    // Red-link create flow prefills path+title and focuses the editor body;
    // a plain +New starts at the path field.
    if (isNew && extras.path) bodyInput.focus();
    else if (isNew) pathInput.focus();
  }

  /* ---------- right rail: TOC + timeline + backlinks + ask ---------- */
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
      <div class="card" id="libLinksCard" style="margin-top:1rem;" aria-live="polite" hidden>
        <span class="kicker">Linked from · <span id="libBlCount">0</span></span>
        <ul class="lib-links" id="libBacklinks"></ul>
        <div id="libWantedWrap" hidden>
          <span class="kicker" style="margin-top:.9rem;">Wanted pages · <span id="libWantedCount">0</span></span>
          <ul class="lib-links" id="libWanted"></ul>
        </div>
      </div>
      <button class="btn" id="libAskBtn" type="button" style="margin-top:1rem; width:100%;">Ask about this doc</button>`;
    railEl.querySelector("#libAskBtn").addEventListener("click", () => {
      const query = `What does "${doc.title || doc.path}" cover, and what decisions does it record?`;
      location.hash = "#ask";
      bus.emit("ask:run", { query, scope: doc.scope || undefined, k: 6 });
    });
    buildToc();
    renderLinksPanel(doc);
    try {
      const { history: hist } = await api.get("/kb.doc_history", { id: doc.id });
      state.history = hist || [];
      renderTimeline(doc);
    } catch (e) {
      const tl = railEl.querySelector("#libTimeline");
      if (tl) tl.innerHTML = `<li class="empty" style="padding:.4rem 0;">${esc(e.message)}</li>`;
    }
  }

  async function renderLinksPanel(doc) {
    const card = railEl.querySelector("#libLinksCard");
    if (!card) return;
    const data = await fetchLinks();
    // rail may have been re-rendered (another doc opened) while we awaited
    if (railEl.querySelector("#libLinksCard") !== card) return;
    if (!data) { card.hidden = true; return; } // /kb.links absent — hide quietly
    const backlinks = data.links.filter(l => l.dst === doc.path);
    const wanted = data.missing.filter(m => m.src === doc.path);
    const titleOf = (p) => {
      const d = state.docs.find(x => x.path === p);
      return d ? (d.title || d.path) : p;
    };
    card.querySelector("#libBlCount").textContent = String(backlinks.length);
    card.querySelector("#libBacklinks").innerHTML = backlinks.length
      ? backlinks.map(l => `<li><button type="button" data-open="${esc(l.src)}" title="${esc(l.src)}">${esc(titleOf(l.src))}</button></li>`).join("")
      : `<li class="empty" style="padding:.4rem 0;">No pages link here yet.</li>`;
    const wrap = card.querySelector("#libWantedWrap");
    if (wanted.length) {
      wrap.hidden = false;
      card.querySelector("#libWantedCount").textContent = String(wanted.length);
      card.querySelector("#libWanted").innerHTML = wanted.map(w =>
        `<li><button type="button" class="lib-wanted-btn" data-create-target="${esc(w.target)}">${esc(w.target)}</button></li>`).join("");
    } else {
      wrap.hidden = true;
    }
    card.hidden = false;
    card.querySelectorAll("[data-open]").forEach(b =>
      b.addEventListener("click", () => openDoc(b.dataset.open)));
    card.querySelectorAll("[data-create-target]").forEach(b =>
      b.addEventListener("click", () => {
        const target = b.dataset.createTarget;
        startCreate(createPathFor(target, dirOf(doc.path)), target);
      }));
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

  /* ---------- wiki home (no doc selected) ---------- */
  async function renderHome() {
    state.doc = null;
    state.viewingVersion = null;
    if (state.spy) { state.spy.disconnect(); state.spy = null; }
    railEl.innerHTML = "";
    markActiveListItem();
    const docs = state.docs.filter(d => !d.lifecycle_state || d.lifecycle_state === "active");
    const start = docs.find(d => /^(index|home)\.md$/i.test(d.path || ""));
    const recent = docs.filter(d => d.updated_at)
      .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
      .slice(0, 8);
    readerEl.innerHTML = `
      <span class="kicker">Wiki home</span>
      <h2 class="lib-doc-title" style="margin-top:.15rem;">Where the reading starts</h2>
      ${start ? `<div class="card card--strong" style="margin-bottom:1.1rem;">
          <span class="kicker">Start here</span>
          <div class="row" style="justify-content:space-between; gap:.75rem; flex-wrap:wrap;">
            <div style="min-width:0;">
              <div class="item__title">${esc(start.title || start.path)}</div>
              <div class="item__sub mono">${esc(start.path)}</div>
            </div>
            <button class="btn btn--sm" id="libStartBtn" type="button">Open</button>
          </div>
        </div>` : ""}
      <div class="lib-home-grid">
        <section aria-label="Recent changes">
          <h2 class="section section--flush" style="margin-bottom:.6rem;">Recent changes</h2>
          <div class="list" id="libRecent">${recent.length ? "" : `<div class="empty">No documents yet — press <strong>+ New document</strong> to add the first.</div>`}</div>
        </section>
        <section aria-label="Wanted pages and orphans" id="libHomeLinks">
          <h2 class="section section--flush" style="margin-bottom:.6rem;">Wanted pages</h2>
          <div class="loading"><span class="spinner"></span> checking links…</div>
        </section>
      </div>`;
    const recentHost = readerEl.querySelector("#libRecent");
    recent.forEach(d => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "item lib-item-btn";
      b.innerHTML = `<div style="min-width:0;text-align:left;">
          <div class="item__title">${esc(d.title || d.path)}</div>
          <div class="item__sub mono">${esc(d.path || "")}</div>
        </div>
        <div class="item__meta"><span class="pill">${esc(relTime(d.updated_at))}</span></div>`;
      b.addEventListener("click", () => openDoc(d.id));
      recentHost.appendChild(b);
    });
    const startBtn = readerEl.querySelector("#libStartBtn");
    if (startBtn) startBtn.addEventListener("click", () => openDoc(start.id));

    const data = await fetchLinks();
    const linksHost = readerEl.querySelector("#libHomeLinks");
    if (!linksHost) return; // navigated away while fetching
    if (!data) { linksHost.hidden = true; return; } // /kb.links absent — hide quietly
    const wantedMap = new Map();
    for (const m of data.missing) {
      if (!m.target) continue;
      if (!wantedMap.has(m.target)) wantedMap.set(m.target, { target: m.target, srcs: [] });
      if (m.src) wantedMap.get(m.target).srcs.push(m.src);
    }
    const wanted = [...wantedMap.values()].sort((a, b) => b.srcs.length - a.srcs.length).slice(0, 12);
    const dstSet = new Set(data.links.map(l => l.dst));
    const orphans = docs
      .filter(d => !dstSet.has(d.path) && (!start || d.id !== start.id))
      .slice(0, 12);
    linksHost.innerHTML = `
      <h2 class="section section--flush" style="margin-bottom:.6rem;">Wanted pages · <span class="mono">${wanted.length}</span></h2>
      ${wanted.length
        ? `<ul class="lib-links">${wanted.map(w =>
            `<li><button type="button" class="lib-wanted-btn" data-target="${esc(w.target)}" data-src="${esc(w.srcs[0] || "")}">${esc(w.target)}${w.srcs.length > 1 ? ` <span class="mono lib-count">×${w.srcs.length}</span>` : ""}</button></li>`).join("")}</ul>`
        : `<p class="dim" style="margin:.2rem 0 1rem;">No broken wikilinks — every reference resolves.</p>`}
      <h2 class="section" style="margin:1.2rem 0 .6rem;">Orphans</h2>
      ${orphans.length
        ? `<ul class="lib-links">${orphans.map(d =>
            `<li><button type="button" data-open="${esc(d.path)}" title="${esc(d.path)}">${esc(d.title || d.path)}</button></li>`).join("")}</ul>`
        : `<p class="dim" style="margin:.2rem 0;">Every page is linked from somewhere.</p>`}`;
    linksHost.querySelectorAll("[data-open]").forEach(b =>
      b.addEventListener("click", () => openDoc(b.dataset.open)));
    linksHost.querySelectorAll(".lib-wanted-btn").forEach(b =>
      b.addEventListener("click", () => {
        startCreate(createPathFor(b.dataset.target, dirOf(b.dataset.src || "")), b.dataset.target);
      }));
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
  // Another surface changed the link graph (or we did) — drop the cache.
  bus.on("wiki:links-changed", invalidateLinks);

  /* ---------- boot ---------- */
  loading(readerEl, "opening the library…");
  loadList().then(() => {
    const path = parseHashPath();
    if (path) openDoc(path, { updateHash: false });
    else renderHome();
  });
}
