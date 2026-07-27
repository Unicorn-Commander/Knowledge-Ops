/* Operations view — service metrics (everyone) + admin console
   (refresh jobs/runs, scheduler worker, deletion propagation, audit).
   Non-admins see ONE locked card for the whole admin area.

   v2 pane idiom: numbers are Fraunces .stat blocks, jobs/runs/audit are
   hairline instrument tables inside .tablewrap, and every form is a quiet
   ruled row — no boxed toolbars, no card-in-card. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

function fmtTime(t, esc) {
  if (!t) return "";
  try { return new Date(t).toLocaleString(); } catch { return esc(t); }
}

/* Server-rendered chart SVG is untrusted markup: parse it as XML, reject
   anything that isn't a clean <svg>, and strip active content (script,
   foreignObject, on* handlers, javascript: hrefs) before it touches the live
   DOM. Returns a detached, importable <svg> node — or null on rejection, in
   which case the caller falls back to the local sparkline. */
function sanitizeSvg(markup) {
  if (typeof markup !== "string" || !markup.includes("<svg")) return null;
  let doc;
  try { doc = new DOMParser().parseFromString(markup, "image/svg+xml"); }
  catch { return null; }
  if (!doc || doc.querySelector("parsererror")) return null;
  const root = doc.documentElement;
  if (!root || root.localName !== "svg") return null;
  for (const el of [root, ...root.querySelectorAll("*")]) {
    if (el !== root && (el.localName || "").toLowerCase().match(/^(script|foreignobject)$/)) {
      el.remove();
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) { el.removeAttribute(attr.name); continue; }
      if ((name === "href" || name === "xlink:href" || name.endsWith(":href")) &&
          !/^\s*#/.test(attr.value)) {
        el.removeAttribute(attr.name);
      }
    }
  }
  return document.importNode(root, true);
}

/* Tiny inline sparkline drawn from metric values; stroke uses tokens only. */
function sparklineSvg(values, w = 240, h = 44) {
  const nums = values.map(Number).filter((n) => Number.isFinite(n));
  if (nums.length < 2) return "";
  const min = Math.min(...nums), max = Math.max(...nums);
  const span = max - min || 1;
  const pad = 3;
  const pts = nums.map((v, i) => {
    const x = pad + (i * (w - pad * 2)) / (nums.length - 1);
    const y = h - pad - ((v - min) * (h - pad * 2)) / span;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = pts.split(" ").pop();
  return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="trend of last ${nums.length} values" preserveAspectRatio="none" style="max-width:100%;display:block;">
    <polyline points="${pts}" fill="none" stroke="var(--mu-color-accent)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"></polyline>
    <circle cx="${last.split(",")[0]}" cy="${last.split(",")[1]}" r="2.5" fill="var(--mu-color-primary)"></circle>
  </svg>`;
}

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  st.textContent = `
  /* layout only — every color rides a --mu-* token */
  #view-ops .ops-quiet { display: flex; gap: .8rem; align-items: flex-end; flex-wrap: wrap; margin: .2rem 0 .4rem; }
  #view-ops .ops-quiet[hidden] { display: none !important; }
  #view-ops .ops-quiet .field { margin-bottom: 0; }
  #view-ops .ops-form { margin: .6rem 0 1.1rem; }
  #view-ops .ops-form .lbl { display: block; margin-bottom: .35rem; }
  #view-ops .ops-zone { border-top: 1px solid var(--mu-hairline); margin-top: 1.9rem; padding-top: 1.2rem; }
  #view-ops .ops-stats { margin: .4rem 0 .4rem; }
  #view-ops .ops-table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  #view-ops .ops-table th {
    text-align: left; font-family: var(--mu-font-mono); font-size: .66rem;
    letter-spacing: .11em; text-transform: uppercase; font-weight: 500;
    color: var(--mu-color-dim); padding: .4rem .8rem .4rem 0; white-space: nowrap;
  }
  #view-ops .ops-table td { padding: .5rem .8rem .5rem 0; border-top: 1px solid var(--mu-hairline); vertical-align: baseline; }
  #view-ops .ops-table td.mono, #view-ops .ops-table .mono { font-size: .8rem; }
  #view-ops .tablewrap { margin: .3rem 0; }
  #view-ops .ops-metricrow { display: flex; align-items: center; gap: 1.4rem; flex-wrap: wrap; }
  #view-ops .ops-metricrow[hidden] { display: none !important; }
  `;
  document.head.appendChild(st);
}

export function init(section, ctx) {
  const { api, esc, toast } = ctx;
  const isAdmin = ctx.me ? Boolean(ctx.me.is_admin) : null; // null = unknown, probe at load time
  injectStyles();

  section.innerHTML = `
    <p class="kicker">Service</p>
    <h1 class="title">Operations</h1>
    <p class="lede">Watch the memory machinery: metrics, refresh cadence, deletion propagation, and the audit trail.</p>

    <div class="ops-quiet" role="search" aria-label="Metric explorer">
      <div class="field field--grow">
        <label class="lbl" for="opsMetricName">Metric name</label>
        <input class="input mono" id="opsMetricName" placeholder="e.g. qwen.orchestrator.plan_seconds" />
      </div>
      <button type="button" class="btn btn--primary field-action" id="opsMetricBtn" style="min-height:44px;">Render</button>
    </div>
    <div id="opsMetricResult" aria-live="polite" style="margin-top:.9rem;"></div>

    <div id="opsAdminArea" class="ops-zone"></div>`;

  const metricBox = section.querySelector("#opsMetricResult");
  const metricInput = section.querySelector("#opsMetricName");
  const metricBtn = section.querySelector("#opsMetricBtn");
  const adminArea = section.querySelector("#opsAdminArea");

  /* ---------- metric explorer (open to everyone) ---------- */
  async function doMetric() {
    const name = metricInput.value.trim();
    if (!name) { metricBox.innerHTML = `<div class="error">Enter a metric name.</div>`; return; }
    metricBtn.disabled = true;
    metricBox.innerHTML = LOADING;
    try {
      const rows = await api.get(`/kb.metrics?name=${encodeURIComponent(name)}&limit=120`);
      const metrics = (rows && rows.metrics) || [];
      let chartNode = null;
      try {
        const svg = await api.get(`/kb.get_metric_chart?name=${encodeURIComponent(name)}&limit=120`);
        chartNode = sanitizeSvg(svg); // null on rejection → local sparkline below
      } catch { /* server chart unavailable — the sparkline below still tells the story */ }
      const mountChart = () => {
        if (!chartNode) return;
        const wrap = document.createElement("div");
        wrap.className = "chartwrap";
        wrap.style.overflowX = "auto";
        wrap.appendChild(chartNode);
        metricBox.prepend(wrap);
      };
      if (!metrics.length) {
        metricBox.innerHTML = chartNode ? "" : `<div class="empty">No recorded values for <b>${esc(name)}</b> yet.</div>`;
        mountChart();
        return;
      }
      const spark = sparklineSvg(metrics.map((m) => m.value));
      const latest = metrics[metrics.length - 1];
      metricBox.innerHTML = `
        <div class="ops-metricrow">
          <div class="stat">
            <span class="stat__n">${esc(Number(latest.value).toFixed(3))}</span>
            <span class="stat__l">latest · ${metrics.length} points</span>
          </div>
          ${spark}
        </div>
        <h2 class="section">Recent values</h2>
        <div class="tablewrap"><table class="ops-table">
          <thead><tr><th>Value</th><th>Tags</th><th>Recorded</th></tr></thead>
          <tbody>${metrics.slice(-20).reverse().map((m) => `
            <tr>
              <td class="mono">${esc(Number(m.value).toFixed(3))}</td>
              <td class="mono dim">${esc(JSON.stringify(m.tags || {}))}</td>
              <td class="dim">${fmtTime(m.recorded_at, esc)}</td>
            </tr>`).join("")}</tbody>
        </table></div>`;
      mountChart();
    } catch (e) { ctx.error(metricBox, e); }
    finally { metricBtn.disabled = false; }
  }
  metricBtn.addEventListener("click", doMetric);
  metricInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doMetric(); });

  /* ---------- admin console ---------- */
  function renderLockedOnce() {
    const box = document.createElement("div");
    adminArea.innerHTML = "";
    adminArea.appendChild(box);
    ctx.locked(box, "the operations console");
  }

  if (isAdmin === false) { renderLockedOnce(); return; }

  adminArea.innerHTML = `
    <p class="kicker">Machinery</p>
    <div class="grid grid--3 ops-stats" id="opsStats" aria-live="polite">${LOADING}</div>

    <div class="section-row" style="margin-top:1.4rem;">
      <div>
        <p class="kicker">Refresh</p>
        <h2 class="section section--flush">Jobs and runs</h2>
      </div>
      <button type="button" class="btn btn--sm" id="opsRefreshDue">Run due</button>
    </div>
    <div class="ops-form">
      <div><label class="lbl" for="opsJobId">Job ID</label><input class="input mono" id="opsJobId" value="wiki-review" /></div>
      <div><label class="lbl" for="opsJobKind">Kind</label><select class="select" id="opsJobKind"><option value="review_scan">Review scan</option><option value="connector_refresh">Connector refresh</option></select></div>
      <div><label class="lbl" for="opsJobSource">Source</label><input class="input mono" id="opsJobSource" value="wiki" /></div>
      <div><label class="lbl" for="opsJobInterval">Interval (seconds)</label><input class="input" id="opsJobInterval" type="number" min="60" max="31536000" step="60" value="86400" /></div>
      <button type="button" class="btn btn--primary ops-form__action" id="opsJobSave">Save job</button>
    </div>
    <div class="grid grid--2 ops-grid">
      <div><h3 class="subhead">Jobs</h3><div id="opsJobs" aria-live="polite">${LOADING}</div></div>
      <div><h3 class="subhead">Recent runs</h3><div id="opsRuns" aria-live="polite">${LOADING}</div></div>
    </div>
    <div class="grid grid--2 ops-grid" style="margin-top:1rem;">
      <div><h3 class="subhead">Scheduler worker</h3><div class="list" id="opsWorker" aria-live="polite">${LOADING}</div></div>
      <div><h3 class="subhead">Deletion propagation</h3><div class="list" id="opsOutbox" aria-live="polite">${LOADING}</div></div>
    </div>
    <h2 class="section">Audit trail</h2>
    <div id="opsAudit" aria-live="polite">${LOADING}</div>`;

  const statsBox = adminArea.querySelector("#opsStats");
  const jobsBox = adminArea.querySelector("#opsJobs");
  const runsBox = adminArea.querySelector("#opsRuns");
  const workerBox = adminArea.querySelector("#opsWorker");
  const outboxBox = adminArea.querySelector("#opsOutbox");
  const auditBox = adminArea.querySelector("#opsAudit");

  let lockShown = false;
  function handleAdminError(box, e) {
    /* One locked card for the whole console — never a wall of red boxes. */
    if (e && e.status === 403) { if (!lockShown) { lockShown = true; renderLockedOnce(); } return true; }
    ctx.error(box, e);
    return false;
  }

  const runPill = (status) => `<span class="pill run--${esc(String(status || "unknown"))}">${esc(status || "unknown")}</span>`;

  const stat = (n, label) =>
    `<div class="stat"><span class="stat__n">${esc(Number(n).toLocaleString())}</span><span class="stat__l">${esc(label)}</span></div>`;

  async function loadRefresh() {
    try {
      const data = await api.get("/kb.refresh_jobs");
      const jobs = data.jobs || [], runs = data.runs || [];
      jobsBox.innerHTML = jobs.length ? `<div class="tablewrap"><table class="ops-table">
        <thead><tr><th>Job</th><th>Kind · source</th><th>Cadence</th><th>State</th></tr></thead>
        <tbody>${jobs.map((job) => `
          <tr>
            <td class="mono">${esc(job.job_id)}</td>
            <td class="mono dim">${esc(job.kind)} · ${esc(job.source_ref)}</td>
            <td class="mono">${esc(job.interval_seconds)}s</td>
            <td><span class="pill ${job.enabled ? "pill--ok" : "pill--off"}">${job.enabled ? "enabled" : "paused"}</span></td>
          </tr>`).join("")}</tbody>
      </table></div>` : `<div class="empty">No refresh jobs yet — the machinery is idle by choice, not neglect.</div>`;
      runsBox.innerHTML = runs.length ? `<div class="tablewrap"><table class="ops-table">
        <thead><tr><th>Run</th><th>Job</th><th>Status</th><th>Scheduled</th></tr></thead>
        <tbody>${runs.map((run) => `
          <tr>
            <td class="mono dim">${esc(run.run_id)}</td>
            <td class="mono">${esc(run.job_id)}</td>
            <td>${runPill(run.status)}</td>
            <td class="dim">${fmtTime(run.scheduled_for, esc)}</td>
          </tr>`).join("")}</tbody>
      </table></div>` : `<div class="empty">No runs recorded.</div>`;
    } catch (e) { if (!handleAdminError(jobsBox, e)) runsBox.innerHTML = `<div class="empty">—</div>`; }
  }

  async function loadOperational() {
    try {
      const data = await api.get("/kb.operations");
      const readiness = data.worker || {}, worker = readiness.worker;

      /* machinery at a glance — Fraunces numbers, honest fields only */
      const refresh = data.refresh || {};
      const outbox = data.deletion_outbox || {};
      const stats = [];
      if (Number.isFinite(Number(refresh.succeeded))) stats.push(stat(refresh.succeeded, "runs succeeded"));
      if (Number.isFinite(Number(refresh.failed))) stats.push(stat(refresh.failed, "runs failed"));
      if (Number.isFinite(Number(outbox.pending))) stats.push(stat(outbox.pending, "deletions pending"));
      if (Number.isFinite(Number(outbox.failed))) stats.push(stat(outbox.failed, "deletions failed"));
      if (Number.isFinite(Number(outbox.succeeded))) stats.push(stat(outbox.succeeded, "deletions propagated"));
      if (Number.isFinite(Number(readiness.fresh_running_workers))) stats.push(stat(readiness.fresh_running_workers, "workers active"));
      statsBox.innerHTML = stats.length ? stats.join("") : `<div class="empty">No operational counters reported.</div>`;

      workerBox.innerHTML = worker ? `
        <div class="item">
          <div style="min-width:0;"><div class="item__title">${esc(worker.worker_kind)}</div><div class="item__sub">heartbeat ${fmtTime(worker.heartbeat_at, esc)}</div></div>
          <div class="item__meta"><span class="pill ${readiness.ready ? "pill--ok" : "run--failed"}">${readiness.ready ? "ready" : esc(worker.status)}</span><span class="pill">${esc(readiness.fresh_running_workers)} active</span></div>
        </div>` : `<div class="empty">No worker heartbeat recorded.</div>`;
      const tasks = data.deletion_tasks || [];
      outboxBox.innerHTML = tasks.length ? tasks.map((task) => `
        <div class="item">
          <div style="min-width:0;"><div class="item__title">${esc(task.adapter)}</div><div class="item__sub">${esc(task.source_ref)}</div></div>
          <div class="item__meta">${runPill(task.status)}<span class="dim mono" style="margin:0 .4rem;">${esc(task.attempt_count)}/${esc(task.max_attempts)}</span>${task.status === "failed" ? `<button type="button" class="btn btn--sm ops-redrive" data-id="${esc(task.task_id)}">Retry</button>` : ""}</div>
        </div>`).join("") : `<div class="empty">No source-owner deletion tasks — every tombstone has propagated.</div>`;
      outboxBox.querySelectorAll(".ops-redrive").forEach((button) => button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api.post(`/kb.deletion_outbox/${encodeURIComponent(button.dataset.id)}/redrive`, {});
          toast("ok", "Deletion task queued");
          await loadOperational();
          loadAudit();
        } catch (e) { toast("err", e.message); button.disabled = false; }
      }));
    } catch (e) {
      if (!handleAdminError(workerBox, e)) {
        statsBox.innerHTML = `<div class="empty">—</div>`;
        outboxBox.innerHTML = `<div class="empty">—</div>`;
      }
    }
  }

  async function loadAudit() {
    try {
      const data = await api.get("/kb.audit?limit=50");
      const events = data.events || [];
      auditBox.innerHTML = events.length ? `<div class="tablewrap"><table class="ops-table">
        <thead><tr><th>Action</th><th>Actor</th><th>Resource</th><th>Outcome</th><th>When</th></tr></thead>
        <tbody>${events.map((event) => `
          <tr>
            <td class="mono">${esc(event.action)}</td>
            <td>${esc(event.actor)}</td>
            <td class="mono dim">${esc(event.resource)}</td>
            <td><span class="pill ${event.outcome === "success" || event.outcome === "approved" ? "pill--ok" : ""}">${esc(event.outcome)}</span></td>
            <td class="dim">${fmtTime(event.recorded_at, esc)}</td>
          </tr>`).join("")}</tbody>
      </table></div>` : `<div class="empty">No recorded changes.</div>`;
    } catch (e) { handleAdminError(auditBox, e); }
  }

  adminArea.querySelector("#opsJobSave").addEventListener("click", async () => {
    const btn = adminArea.querySelector("#opsJobSave");
    btn.disabled = true;
    try {
      await api.post("/kb.refresh_job", {
        job_id: adminArea.querySelector("#opsJobId").value.trim(),
        kind: adminArea.querySelector("#opsJobKind").value,
        source_ref: adminArea.querySelector("#opsJobSource").value.trim(),
        interval_seconds: Number(adminArea.querySelector("#opsJobInterval").value),
        enabled: true,
      });
      toast("ok", "Refresh job saved");
      loadRefresh();
    } catch (e) { toast("err", e.message); }
    finally { btn.disabled = false; }
  });

  adminArea.querySelector("#opsRefreshDue").addEventListener("click", async () => {
    const btn = adminArea.querySelector("#opsRefreshDue");
    btn.disabled = true;
    try {
      const data = await api.post("/kb.refresh_due", {});
      toast("ok", `${(data.runs || []).length} due runs dispatched`);
      await loadRefresh();
      loadAudit();
    } catch (e) { toast("err", e.message); }
    finally { btn.disabled = false; }
  });

  function loadAll() { loadRefresh(); loadOperational(); loadAudit(); }
  loadAll();
  ctx.bus.on("nav", ({ view }) => { if (view === "ops" && !lockShown) loadAll(); });
}
