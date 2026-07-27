/* Files view — search the governed file index, deep-read a file's chunks
   in mono with query-term highlighting, and (admin) index a new root.

   v2 pane idiom: the search sits in a quiet ruled row (no boxed toolbar),
   matches are hairline .item rows carrying a glowing source badge, and
   deep reads are .cite blocks laid straight on the pane. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

function fmtScore(s, esc) { return (typeof s === "number") ? s.toFixed(2) : esc(s); }

/* map a source string onto the src-badge color classes from base.css */
function srcClass(source) {
  const base = String(source || "other").split(":")[0];
  const known = ["wiki", "falkordb", "qdrant", "file_index", "org_memory", "center_deep", "rest", "postgres"];
  return "src-" + (known.includes(base) ? base : "other");
}
function srcLabel(source) {
  const s = String(source || "files");
  if (s === "file_index") return "files";
  return s.replace(/^qdrant:/, "").replace(/^falkordb:/, "graph ").replace(/_/g, " ");
}

/* Escape-first highlighting: split the RAW text on query tokens, escape every
   part, wrap matches in <mark>. Tokens under 2 chars are skipped. */
function highlight(text, query, esc) {
  const raw = String(text || "");
  const tokens = String(query || "").split(/\s+/).map((t) => t.trim()).filter((t) => t.length >= 2);
  if (!tokens.length) return esc(raw);
  const pattern = tokens
    .sort((a, b) => b.length - a.length)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  let re;
  try { re = new RegExp(`(${pattern})`, "gi"); } catch { return esc(raw); }
  return raw.split(re).map((part, i) => (i % 2 === 1 ? `<mark>${esc(part)}</mark>` : esc(part))).join("");
}

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  st.textContent = `
  /* layout only — every color rides a --mu-* token */
  #view-files .fl-search { display: flex; gap: .8rem; align-items: flex-end; flex-wrap: wrap; margin: .2rem 0 .4rem; }
  #view-files .fl-search[hidden] { display: none !important; }
  #view-files .fl-search .field { margin-bottom: 0; }
  #view-files .fl-hit .item__meta { display: flex; align-items: center; gap: .55rem; }
  #view-files .fl-hit mark, #view-files .fl-pre mark {
    background: color-mix(in srgb, var(--mu-color-accent) 24%, transparent);
    color: inherit; border-radius: 3px; padding: 0 .15em;
  }
  #view-files .fl-pre {
    white-space: pre-wrap; word-break: break-word; overflow-x: auto;
    margin: .5rem 0 0; padding: 0;
    font-family: var(--mu-font-mono); font-size: .82rem; line-height: 1.6;
    color: var(--mu-color-muted);
  }
  #view-files .fl-zone { border-top: 1px solid var(--mu-hairline); margin-top: 2rem; padding-top: 1.3rem; }
  #view-files .fl-arrow { color: var(--mu-color-dim); font-size: .78rem; font-family: var(--mu-font-mono); white-space: nowrap; }
  @media (max-width: 700px) { #view-files .fl-hit .item__meta { flex-wrap: wrap; justify-content: flex-end; } }
  `;
  document.head.appendChild(st);
}

export function init(section, ctx) {
  const { api, esc, toast } = ctx;
  const isAdmin = ctx.me ? Boolean(ctx.me.is_admin) : null;
  injectStyles();

  section.innerHTML = `
    <p class="kicker">Indexed</p>
    <h1 class="title">Files</h1>
    <p class="lede">Search content inside approved roots, then deep-read the exact chunks the index holds.</p>

    <div class="fl-search" role="search" aria-label="File search">
      <div class="field field--grow">
        <label class="lbl" for="flQuery">File search</label>
        <input class="input" id="flQuery" placeholder="e.g. brand bible, meeting-ops inference pipeline" />
      </div>
      <div class="field">
        <label class="lbl" for="flScope">Visibility</label>
        <select class="select" id="flScope">
          <option value="">default</option>
          <option value="personal">personal</option>
          <option value="global">global</option>
          <option value="all">all</option>
        </select>
      </div>
      <button type="button" class="btn btn--primary field-action" id="flBtn" style="min-height:44px;">Search</button>
    </div>

    <div id="flResult" aria-live="polite" style="margin-top:1.1rem;"></div>
    <div id="flDeep" aria-live="polite" style="margin-top:1.1rem;"></div>
    <div id="flAdmin" class="fl-zone" hidden></div>`;

  const queryEl = section.querySelector("#flQuery");
  const scopeEl = section.querySelector("#flScope");
  const searchBtn = section.querySelector("#flBtn");
  const resultBox = section.querySelector("#flResult");
  const deepBox = section.querySelector("#flDeep");
  const adminBox = section.querySelector("#flAdmin");

  /* ---------- search ---------- */
  async function doSearch() {
    const q = queryEl.value.trim();
    deepBox.innerHTML = "";
    if (!q) { resultBox.innerHTML = `<div class="error">Enter a search query.</div>`; return; }
    searchBtn.disabled = true;
    resultBox.innerHTML = LOADING;
    try {
      const scope = scopeEl.value;
      const data = await api.get(`/kb.find_file?query=${encodeURIComponent(q)}&k=12${scope ? "&scope=" + encodeURIComponent(scope) : ""}`);
      const passages = data.passages || [];
      if (!passages.length) {
        resultBox.innerHTML = `<div class="empty"><span class="big"><span aria-hidden="true">⌕ </span>Nothing surfaced</span>No files matched. The index may be empty on this host — an administrator can index a root below.</div>`;
        return;
      }
      resultBox.innerHTML = `<div class="section-row"><h2 class="section section--flush">Matches <span class="dim">· ${passages.length}</span></h2></div>
        <div class="list">${passages.map((p) => {
          const path = p.uri || (p.metadata && p.metadata.path) || "";
          return `<button type="button" class="item fl-hit" data-path="${esc(path)}">
            <div style="min-width:0;text-align:left;">
              <div class="item__title">${highlight(p.title, q, esc)}</div>
              <div class="item__sub">${esc(path)}</div>
            </div>
            <div class="item__meta">
              <span class="src-badge ${srcClass(p.source)}">${esc(srcLabel(p.source))}</span>
              ${p.scope ? `<span class="pill pill--scope">${esc(p.scope)}</span>` : ""}
              <span class="mono">${fmtScore(p.score, esc)}</span>
              <span class="fl-arrow">deep-read →</span>
            </div>
          </button>`;
        }).join("")}</div>`;
      resultBox.querySelectorAll(".fl-hit").forEach((hit) => hit.addEventListener("click", () => deepRead(hit.dataset.path, q)));
    } catch (e) { ctx.error(resultBox, e); }
    finally { searchBtn.disabled = false; }
  }

  /* ---------- deep read ---------- */
  async function deepRead(path, query) {
    if (!path) { toast("err", "No path for this result"); return; }
    deepBox.innerHTML = LOADING;
    try {
      const data = await api.get(`/kb.deep_read?path=${encodeURIComponent(path)}&query=${encodeURIComponent(query || "")}&k=20`);
      const passages = data.passages || [];
      deepBox.innerHTML = `<div class="section-row"><h2 class="section section--flush">Deep read</h2><span class="dim mono">${esc(path)}</span></div>` +
        (passages.length ? `<div class="list">${passages.map((p, i) => `
          <div class="cite">
            <div class="cite__head">
              <span class="cite__n">${i + 1}</span>
              <span class="cite__title">${esc(p.title || p.source_id || "chunk")}</span>
              ${p.scope ? `<span class="pill pill--scope">${esc(p.scope)}</span>` : ""}
              <span class="cite__meta">score ${fmtScore(p.score, esc)}</span>
            </div>
            <pre class="fl-pre">${highlight(p.text, query, esc)}</pre>
          </div>`).join("")}</div>` : `<div class="empty">No chunks indexed for this file.</div>`);
      deepBox.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "nearest",
      });
    } catch (e) { ctx.error(deepBox, e); }
  }

  searchBtn.addEventListener("click", doSearch);
  queryEl.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

  /* ---------- index a root (admin) ---------- */
  adminBox.hidden = false;
  if (isAdmin === false) {
    adminBox.innerHTML = `<p class="kicker">Indexing</p>`;
    const box = document.createElement("div");
    adminBox.appendChild(box);
    ctx.locked(box, "file indexing");
    return;
  }

  adminBox.innerHTML = `
    <p class="kicker">Indexing</p>
    <h2 class="section section--flush" style="margin-bottom:.7rem;">Index a root</h2>
    <div class="fl-search">
      <div class="field field--grow">
        <label class="lbl" for="flRoot">Approved root path</label>
        <input class="input mono" id="flRoot" placeholder="/srv/knowledge/approved-root" />
      </div>
      <button type="button" class="btn field-action" id="flIndexBtn">Index files</button>
    </div>
    <div id="flIndexStatus" aria-live="polite"></div>`;

  const indexBtn = adminBox.querySelector("#flIndexBtn");
  const statusBox = adminBox.querySelector("#flIndexStatus");
  indexBtn.addEventListener("click", async () => {
    const root = adminBox.querySelector("#flRoot").value.trim();
    if (!root) { toast("err", "Enter a root path"); return; }
    if (!window.confirm(`Index files under ${root}? Only approved roots are accepted.`)) return;
    indexBtn.disabled = true;
    statusBox.innerHTML = LOADING;
    try {
      const data = await api.post(`/kb.index_files?root=${encodeURIComponent(root)}`, {});
      const n = data.chunks != null ? data.chunks : (data.indexed != null ? data.indexed : (data.count != null ? data.count : ""));
      statusBox.innerHTML = `<div class="row" style="margin-top:.6rem;"><span class="pill pill--ok">indexed${n !== "" ? ` · ${esc(n)} chunks` : ""}</span><span class="dim mono">${esc(root)}</span></div>`;
      toast("ok", "Indexing complete");
    } catch (e) {
      statusBox.innerHTML = "";
      if (e && e.status === 403) {
        const box = document.createElement("div");
        statusBox.appendChild(box);
        ctx.locked(box, "file indexing");
      } else { ctx.error(statusBox, e); }
    } finally { indexBtn.disabled = false; }
  });
}
