/* Files view — search the governed file index, deep-read a file's chunks
   in mono with query-term highlighting, and (admin) index a new root. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

function fmtScore(s, esc) { return (typeof s === "number") ? s.toFixed(2) : esc(s); }

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

export function init(section, ctx) {
  const { api, esc, toast } = ctx;
  const isAdmin = ctx.me ? Boolean(ctx.me.is_admin) : null;

  section.innerHTML = `
    <p class="kicker">Indexed</p>
    <h1 class="title">Files</h1>
    <p class="lede">Search content inside approved roots, then deep-read the exact chunks the index holds.</p>

    <div class="card card--strong">
      <div class="row">
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
        <button type="button" class="btn btn--primary field-action" id="flBtn">Search</button>
      </div>
    </div>

    <div id="flResult" aria-live="polite" style="margin-top:1.2rem;"></div>
    <div id="flDeep" aria-live="polite" style="margin-top:1.2rem;"></div>
    <div id="flAdmin" style="margin-top:1.6rem;"></div>`;

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
    resultBox.innerHTML = `<div class="card">${LOADING}</div>`;
    try {
      const scope = scopeEl.value;
      const data = await api.get(`/kb.find_file?query=${encodeURIComponent(q)}&k=12${scope ? "&scope=" + encodeURIComponent(scope) : ""}`);
      const passages = data.passages || [];
      if (!passages.length) {
        resultBox.innerHTML = `<div class="empty"><div class="big" aria-hidden="true">⌕</div>No files matched. The index may be empty on this host — an administrator can index a root below.</div>`;
        return;
      }
      resultBox.innerHTML = `<h2 class="section">Matches <span class="dim">· ${passages.length}</span></h2>
        <div class="list">${passages.map((p) => {
          const path = p.uri || (p.metadata && p.metadata.path) || "";
          return `<button type="button" class="item fl-hit" data-path="${esc(path)}">
            <div style="min-width:0;text-align:left;">
              <div class="item__title">${highlight(p.title, q, esc)}</div>
              <div class="item__sub mono">${esc(path)}</div>
            </div>
            <div class="item__meta"><span class="pill pill--scope">${esc(p.scope)}</span><span class="pill dim mono" style="font-variant-numeric:tabular-nums;">score ${fmtScore(p.score, esc)}</span><span class="pill">Deep-read →</span></div>
          </button>`;
        }).join("")}</div>`;
      resultBox.querySelectorAll(".fl-hit").forEach((hit) => hit.addEventListener("click", () => deepRead(hit.dataset.path, q)));
    } catch (e) { ctx.error(resultBox, e); }
    finally { searchBtn.disabled = false; }
  }

  /* ---------- deep read ---------- */
  async function deepRead(path, query) {
    if (!path) { toast("err", "No path for this result"); return; }
    deepBox.innerHTML = `<div class="card">${LOADING}</div>`;
    try {
      const data = await api.get(`/kb.deep_read?path=${encodeURIComponent(path)}&query=${encodeURIComponent(query || "")}&k=20`);
      const passages = data.passages || [];
      deepBox.innerHTML = `<h2 class="section">Deep read <span class="dim mono">· ${esc(path)}</span></h2>` +
        (passages.length ? `<div class="card">${passages.map((p, i) => `
          <div class="cite">
            <div class="cite__head">
              <span class="cite__n">${i + 1}</span>
              <span class="cite__title">${esc(p.title || p.source_id || "chunk")}</span>
              ${p.scope ? `<span class="pill pill--scope">${esc(p.scope)}</span>` : ""}
              <span class="cite__meta mono" style="font-variant-numeric:tabular-nums;">score ${fmtScore(p.score, esc)}</span>
            </div>
            <pre class="mono" style="white-space:pre-wrap;word-break:break-word;overflow-x:auto;margin:.4rem 0 0;font-family:var(--mu-font-mono);">${highlight(p.text, query, esc)}</pre>
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
  if (isAdmin === false) {
    const card = document.createElement("div");
    card.className = "card";
    adminBox.appendChild(card);
    ctx.locked(card, "file indexing");
    const kicker = document.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = "Indexing";
    adminBox.prepend(kicker);
    return;
  }

  adminBox.innerHTML = `
    <p class="kicker">Indexing</p>
    <h2 class="section section--flush">Index a root</h2>
    <div class="card">
      <div class="row">
        <div class="field field--grow">
          <label class="lbl" for="flRoot">Approved root path</label>
          <input class="input mono" id="flRoot" placeholder="/srv/knowledge/approved-root" />
        </div>
        <button type="button" class="btn field-action" id="flIndexBtn">Index files</button>
      </div>
      <div id="flIndexStatus" aria-live="polite"></div>
    </div>`;

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
        const card = document.createElement("div");
        statusBox.appendChild(card);
        ctx.locked(card, "file indexing");
      } else { ctx.error(statusBox, e); }
    } finally { indexBtn.disabled = false; }
  });
}
