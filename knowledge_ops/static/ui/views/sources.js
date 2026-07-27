/* Sources view — the federation as an instrument board. Each store is a
   plate: glowing source badge, live mono count (count-up on first paint),
   honest "awaiting population" states, last-sync/detail line. Groups carry
   brass instrument labels; the MCP tool contracts are hairline rows. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

/* Map a store key onto the base.css .src--* glow family by COLLECTION, not
   just prefix, so each qdrant collection keeps its constellation color. */
function srcClass(source) {
  const s = String(source || "").toLowerCase();
  const coll = s.startsWith("qdrant:") ? s.slice(7) : s;
  if (coll.includes("falkor") || coll.includes("graph")) return "src--graph";
  if (coll.includes("wiki")) return "src--wiki";
  if (coll.includes("org_memory") || coll.includes("org-memory")) return "src--org-memory";
  if (coll.includes("meeting")) return "src--meetings";
  if (coll.includes("stable")) return "src--stable";
  if (coll.includes("file")) return "src--files";
  if (coll.includes("metric") || coll.includes("center_deep")) return "src--metrics";
  return "src-other";
}
function sourceLabel(source) {
  return String(source || "other")
    .replace(/^qdrant:/, "Q ").replace(/^falkordb:/, "G ")
    .replace(/^rest:/, "API ").replace(/^postgres:/, "SQL ");
}

const GROUPS = [
  { id: "semantic", title: "Semantic", blurb: "Vector recall over embedded passages", unit: "atoms" },
  { id: "canonical", title: "Canonical", blurb: "Versioned, reviewed wiki truth", unit: "docs" },
  { id: "files", title: "Files", blurb: "Approved roots, chunk-indexed", unit: "chunks" },
  { id: "graph", title: "Graph", blurb: "FalkorDB relationship fabric", unit: "nodes" },
  { id: "metrics", title: "Metrics", blurb: "Service and quality signals", unit: "series" },
];

function groupOf(entry) {
  const k = `${entry.kind || ""} ${entry.key || ""} ${entry.label || ""}`.toLowerCase();
  if (/graph|falkor/.test(k)) return "graph";
  if (/wiki|canonical/.test(k)) return "canonical";
  if (/file/.test(k)) return "files";
  if (/metric/.test(k)) return "metrics";
  if (/qdrant|semantic|memory|meeting|stable|vector|collection/.test(k)) return "semantic";
  return "semantic";
}

/* Count-up on first paint; instant when reduced motion is preferred. */
function countUp(node, target) {
  const n = Number(target) || 0;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || n <= 0) {
    node.textContent = n.toLocaleString();
    return;
  }
  const duration = 750;
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    node.textContent = Math.round(n * eased).toLocaleString();
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function fmtTime(t, esc) {
  if (!t) return "";
  try { return new Date(t).toLocaleString(); } catch { return esc(t); }
}

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  st.textContent = `
  /* layout only — every color rides a --mu-* token */
  #view-sources .src-grouphead { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; margin: 1.8rem 0 .7rem; }
  #view-sources .src-grouphead[hidden] { display: none !important; }
  #view-sources .src-grouphead .kicker { margin: 0; }
  #view-sources .src-grouphead__blurb { font-size: .84rem; }
  /* instrument readout numerals — mono per the brass-instrument language */
  #view-sources .stat__n.mono { font-family: var(--mu-font-mono); font-size: 1.65rem; letter-spacing: -.01em; }
  /* auto-fill keeps lone plates plate-sized instead of stretching the row */
  #view-sources .grid--3 { grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
  #view-sources .src-plate { display: flex; flex-direction: column; gap: .65rem; }
  #view-sources .src-plate[hidden] { display: none !important; }
  #view-sources .src-plate__top { display: flex; align-items: center; justify-content: space-between; gap: .45rem .6rem; flex-wrap: wrap; }
  #view-sources .src-plate__top .src-badge { font-weight: 550; min-width: 0; flex: 1 1 auto; }
  #view-sources .src-badge__t { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #view-sources .src-plate .stat { margin-top: auto; }
  #view-sources .src-plate .item__meta { margin-left: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #view-sources .tool { display: flex; align-items: baseline; gap: .9rem; }
  #view-sources .tool[hidden] { display: none !important; }
  #view-sources .tool > .pill { margin-left: auto; flex: none; }
  #view-sources .tool__main { min-width: 0; }
  `;
  document.head.appendChild(st);
}

export function init(section, ctx) {
  const { api, esc } = ctx;
  let painted = false;
  injectStyles();

  section.innerHTML = `
    <p class="kicker">Federation</p>
    <h1 class="title">Sources</h1>
    <p class="lede">The stores this observatory watches — availability, live counts, and the agent access contracts.</p>

    <div id="srcGroups" aria-live="polite">${LOADING}</div>

    <h2 class="section">Registered MCP tools <span class="pill" id="srcToolNs">knowledge</span> <span class="dim" id="srcVersion"></span></h2>
    <div class="list" id="srcTools" aria-live="polite">${LOADING}</div>`;

  const groupsBox = section.querySelector("#srcGroups");
  const toolsBox = section.querySelector("#srcTools");

  function statusPill(entry, group, status) {
    const count = Number(entry.count) || 0;
    if (group && group.id === "graph" && count === 0) {
      return `<span class="pill pill--off">configured · awaiting population</span>`;
    }
    if (status && !status.enabled) {
      return `<span class="pill pill--off">○ ${esc(status.status)}</span>`;
    }
    return `<span class="pill pill--ok">● ${esc(status ? status.status : "reachable")}</span>`;
  }

  /* one instrument plate per store: badge · count · last-sync/detail */
  function statPlate(entry, group, statusMap) {
    const label = entry.label || entry.key;
    const status = statusMap.get(String(entry.key)) || statusMap.get(String(label)) || null;
    const count = Number(entry.count) || 0;
    const sync = entry.last_sync || entry.synced_at || entry.last_synced_at
      || (status && (status.last_sync || status.synced_at)) || null;
    const foot = sync ? `sync ${fmtTime(sync, esc)}` : ((status && status.detail) || String(entry.key));
    return `<div class="card src-plate">
      <div class="src-plate__top">
        <span class="src-badge ${srcClass(entry.key)}" title="${esc(label)}"><span class="src-badge__t">${esc(label)}</span></span>
        ${statusPill(entry, group, status)}
      </div>
      <div class="stat">
        <span class="stat__n mono src-count" data-count="${count}">0</span>
        <span class="stat__l">${esc(entry.unit || group.unit)}</span>
      </div>
      <div class="item__meta" title="${esc(foot)}">${esc(foot)}</div>
    </div>`;
  }

  /* availability-only plate for sources without a stats entry */
  function plainPlate(s) {
    const pill = s.enabled
      ? `<span class="pill pill--ok">● ${esc(s.status)}</span>`
      : `<span class="pill pill--off">○ ${esc(s.status)}</span>`;
    return `<div class="card src-plate">
      <div class="src-plate__top">
        <span class="src-badge ${srcClass(s.name)}" title="${esc(sourceLabel(s.name))}"><span class="src-badge__t">${esc(sourceLabel(s.name))}</span></span>
        ${pill}
      </div>
      <div class="item__meta" title="${esc(s.detail || "")}">${esc(s.detail || "no detail reported")}</div>
    </div>`;
  }

  async function loadFederation() {
    const [statsRes, sourcesRes] = await Promise.allSettled([
      api.get("/kb.stats"),
      api.get("/kb.list_sources"),
    ]);
    const stats = statsRes.status === "fulfilled" ? (statsRes.value.sources || []) : null;
    const sources = sourcesRes.status === "fulfilled" ? (sourcesRes.value.sources || []) : null;

    if (!stats && !sources) {
      ctx.error(groupsBox, sourcesRes.reason || statsRes.reason);
      return;
    }

    const statusMap = new Map();
    (sources || []).forEach((s) => statusMap.set(String(s.name), s));

    let html = "";
    if (stats) {
      const claimed = new Set();
      GROUPS.forEach((group) => {
        const entries = stats.filter((entry) => groupOf(entry) === group.id);
        if (!entries.length) return;
        entries.forEach((entry) => {
          claimed.add(String(entry.key));
          if (entry.label) claimed.add(String(entry.label));
        });
        html += `<div class="src-grouphead">
            <span class="kicker">${esc(group.title)}</span>
            <span class="src-grouphead__blurb dim">${esc(group.blurb)}</span>
          </div>
          <div class="grid grid--3">${entries.map((entry) => statPlate(entry, group, statusMap)).join("")}</div>`;
      });
      const leftovers = (sources || []).filter((s) => !claimed.has(String(s.name)));
      if (leftovers.length) {
        html += `<div class="src-grouphead">
            <span class="kicker">Other federated</span>
            <span class="src-grouphead__blurb dim">Reachable stores without live counts</span>
          </div>
          <div class="grid grid--3">${leftovers.map(plainPlate).join("")}</div>`;
      }
    } else {
      /* /kb.stats not deployed yet — fall back to the availability roster. */
      html = `<div class="src-grouphead">
          <span class="kicker">Federated sources</span>
          <span class="src-grouphead__blurb dim">Availability roster — counts unavailable on this host</span>
        </div>
        <div class="grid grid--3">${sources.map(plainPlate).join("")}</div>`;
    }
    groupsBox.innerHTML = html;

    const animate = !painted;
    painted = true;
    groupsBox.querySelectorAll(".src-count").forEach((node) => {
      const n = Number(node.dataset.count) || 0;
      if (animate) countUp(node, n);
      else node.textContent = n.toLocaleString();
    });
  }

  async function loadTools() {
    try {
      const man = await api.get("/.well-known/mcps.json");
      section.querySelector("#srcToolNs").textContent = man.tool_namespace || "knowledge";
      section.querySelector("#srcVersion").textContent = man.version ? "v" + man.version : "";
      const tools = man.tools || [];
      toolsBox.innerHTML = tools.length ? tools.map((t) => `
        <div class="tool">
          <div class="tool__main">
            <code class="mono">${esc(man.tool_namespace)}.${esc(t.name)}</code>
            <div class="dim" style="margin-top:.25rem;font-size:.84rem;">${esc(t.description || "")}</div>
          </div>
          ${t.read_only
            ? '<span class="pill pill--ok">read-only</span>'
            : '<span class="pill">write</span>'}
        </div>`).join("") : `<div class="empty">No tools published in the manifest.</div>`;
    } catch (e) { ctx.error(toolsBox, e); }
  }

  loadFederation().catch((e) => ctx.error(groupsBox, e));
  loadTools();

  ctx.bus.on("nav", ({ view }) => {
    if (view === "sources") loadFederation().catch((e) => ctx.error(groupsBox, e));
  });
}
