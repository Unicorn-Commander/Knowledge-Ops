/* Sources view — the federation as an observatory roster. Status cards
   grouped by store family, live counts from /kb.stats (count-up on first
   paint), honest "awaiting population" states, and the registered MCP
   tool contracts. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

function srcClass(source) {
  const base = String(source || "other").split(":")[0];
  const known = ["wiki", "falkordb", "qdrant", "file_index", "org_memory", "center_deep", "rest", "postgres"];
  return "src-" + (known.includes(base) ? base : "other");
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

export function init(section, ctx) {
  const { api, esc } = ctx;
  let painted = false;

  section.innerHTML = `
    <p class="kicker">Federation</p>
    <h1 class="title">Sources</h1>
    <p class="lede">The stores this observatory watches — availability, live counts, and the agent access contracts.</p>

    <div id="srcGroups" aria-live="polite">${LOADING}</div>

    <h2 class="section">Registered MCP tools <span class="pill" id="srcToolNs">knowledge</span> <span class="dim" id="srcVersion"></span></h2>
    <div class="list" id="srcTools" aria-live="polite">${LOADING}</div>`;

  const groupsBox = section.querySelector("#srcGroups");
  const toolsBox = section.querySelector("#srcTools");

  function statCard(entry, group, statusMap) {
    const label = entry.label || entry.key;
    const status = statusMap.get(String(entry.key)) || statusMap.get(String(label)) || null;
    const count = Number(entry.count) || 0;
    const isGraph = group.id === "graph";
    let pill;
    if (isGraph && count === 0) {
      pill = `<span class="pill pill--off">configured · awaiting population</span>`;
    } else if (status && !status.enabled) {
      pill = `<span class="pill pill--off">○ ${esc(status.status)}</span>`;
    } else {
      pill = `<span class="pill pill--ok">● ${esc(status ? status.status : "reachable")}</span>`;
    }
    return `<div class="card">
      <div class="row" style="justify-content:space-between;align-items:flex-start;">
        <span class="src-badge ${srcClass(entry.key)}">${esc(sourceLabel(entry.key))}</span>
        ${pill}
      </div>
      <div style="margin-top:.7rem;">
        <span class="mono src-count" data-count="${count}" style="font-variant-numeric:tabular-nums;font-size:1.5rem;font-weight:600;">0</span>
        <span class="dim"> ${esc(entry.unit || group.unit)}</span>
      </div>
      <div class="item__sub mono" style="margin-top:.25rem;word-break:break-all;">${esc(label)}</div>
      ${status && status.detail ? `<div class="dim mono" style="margin-top:.45rem;font-size:.72rem;word-break:break-all;">${esc(status.detail)}</div>` : ""}
    </div>`;
  }

  function plainCard(s) {
    const pill = s.enabled
      ? `<span class="pill pill--ok">● ${esc(s.status)}</span>`
      : `<span class="pill pill--off">○ ${esc(s.status)}</span>`;
    return `<div class="card">
      <div class="row" style="justify-content:space-between;"><span class="src-badge ${srcClass(s.name)}">${esc(sourceLabel(s.name))}</span>${pill}</div>
      <div class="dim mono" style="margin-top:.6rem;font-size:.74rem;word-break:break-all;">${esc(s.detail || "")}</div>
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
        html += `<div class="section-row" style="margin-top:1.1rem;">
            <div><h2 class="section section--flush">${esc(group.title)}</h2><p class="dim" style="margin:.15rem 0 0;">${esc(group.blurb)}</p></div>
          </div>
          <div class="grid grid--3">${entries.map((entry) => statCard(entry, group, statusMap)).join("")}</div>`;
      });
      const leftovers = (sources || []).filter((s) => !claimed.has(String(s.name)));
      if (leftovers.length) {
        html += `<h2 class="section">Other federated sources</h2><div class="grid grid--3">${leftovers.map(plainCard).join("")}</div>`;
      }
    } else {
      /* /kb.stats not deployed yet — fall back to the availability roster. */
      html = `<h2 class="section section--flush">Federated sources</h2><div class="grid grid--3">${sources.map(plainCard).join("")}</div>`;
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
          <div>
            <div><code class="mono">${esc(man.tool_namespace)}.${esc(t.name)}</code>
              ${t.read_only
                ? '<span class="pill pill--ok" style="margin-left:.3rem;">read-only</span>'
                : '<span class="pill" style="margin-left:.3rem;">write</span>'}
            </div>
            <div class="dim" style="margin-top:.25rem;font-size:.84rem;">${esc(t.description || "")}</div>
          </div>
        </div>`).join("") : `<div class="empty">No tools published in the manifest.</div>`;
    } catch (e) { ctx.error(toolsBox, e); }
  }

  loadFederation().catch((e) => ctx.error(groupsBox, e));
  loadTools();

  ctx.bus.on("nav", ({ view }) => {
    if (view === "sources") loadFederation().catch((e) => ctx.error(groupsBox, e));
  });
}
