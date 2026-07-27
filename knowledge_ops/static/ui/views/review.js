/* Review view — OpenWiki import queue with a governance checklist
   (provenance / access / privacy / content) gating approval, plus the
   memory-quality scan (staleness + conflicts). Evidence-first: nothing
   is promoted into governed memory without a human sign-off. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

function fmtTime(t, esc) {
  if (!t) return "";
  try { return new Date(t).toLocaleString(); } catch { return esc(t); }
}

const CHECKLIST = [
  { id: "provenance", label: "Provenance — origin and author trail are recorded and plausible" },
  { id: "access", label: "Access — visibility scope matches who should retrieve this" },
  { id: "privacy", label: "Privacy — no unexpected PII, secrets, or client-confidential spill" },
  { id: "content", label: "Content — accurate, current, and safe to cite as evidence" },
];

export function init(section, ctx) {
  const { api, esc, toast } = ctx;

  section.innerHTML = `
    <p class="kicker">OpenWiki</p>
    <h1 class="title">Review queue</h1>
    <p class="lede">Every import passes a human governance gate before it can be cited. Nothing sneaks into memory.</p>

    <div class="grid grid--2 review-layout" style="align-items:start;">
      <div class="card">
        <div class="row" style="justify-content:space-between;margin-bottom:.7rem;">
          <h2 class="section" style="margin:0;">Imports</h2>
          <button type="button" class="btn btn--sm" id="rvRefresh">Refresh</button>
        </div>
        <div class="list" id="rvList" aria-live="polite">${LOADING}</div>
      </div>
      <div class="card card--strong" id="rvPane" aria-live="polite">
        <div class="empty">Select an import to inspect its findings and documents.</div>
      </div>
    </div>

    <div class="section-row">
      <div>
        <p class="kicker">Memory quality</p>
        <h2 class="section section--flush">Staleness and conflicts</h2>
      </div>
      <button type="button" class="btn btn--sm" id="rvScanBtn">Scan now</button>
    </div>
    <div class="list" id="rvMemoryList" aria-live="polite">${LOADING}</div>`;

  const list = section.querySelector("#rvList");
  const pane = section.querySelector("#rvPane");
  const memoryList = section.querySelector("#rvMemoryList");

  /* ---------- import queue ---------- */
  async function loadQueue() {
    list.innerHTML = LOADING;
    try {
      const data = await api.get("/kb.openwiki_imports");
      const imports = data.imports || [];
      if (!imports.length) {
        list.innerHTML = `<div class="empty">Queue is clear — every staged import has been reviewed.</div>`;
        return;
      }
      list.innerHTML = "";
      imports.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "item";
        const blocking = (item.findings || []).filter((f) => f.severity === "blocking").length;
        button.innerHTML = `
          <div style="min-width:0;text-align:left;">
            <div class="item__title">${esc(item.import_id)}</div>
            <div class="item__sub">${esc(item.document_count)} docs · ${fmtTime(item.staged_at, esc)}</div>
          </div>
          <div class="item__meta"><span class="pill ${blocking ? "" : "pill--ok"}">${esc(item.state)}</span>${blocking ? `<span class="pill run--failed">${blocking} blocking</span>` : ""}</div>`;
        button.addEventListener("click", () => viewImport(item.import_id));
        list.appendChild(button);
      });
    } catch (e) { ctx.error(list, e); }
  }

  async function viewImport(id) {
    pane.innerHTML = LOADING;
    try {
      const data = await api.get(`/kb.openwiki_import?id=${encodeURIComponent(id)}`);
      const item = data.import;
      const findings = item.findings || [];
      const documents = item.documents || [];
      const decided = ["approved", "rejected"].includes(item.state);

      const checklistHtml = CHECKLIST.map((c) => `
        <label class="check">
          <input type="checkbox" class="rv-gate" id="rvGate-${c.id}" />
          ${esc(c.label)}
        </label>`).join("");

      pane.innerHTML = `
        <div class="row" style="justify-content:space-between;align-items:flex-start;">
          <div><div class="item__sub mono">${esc(item.import_id)}</div><h2 class="section" style="margin:.2rem 0;">${esc(item.document_count)} documents</h2></div>
          <span class="pill">${esc(item.state)}</span>
        </div>
        <h3 class="subhead">Findings</h3>
        <div class="list">${findings.length ? findings.map((f) => `
          <div class="finding finding--${esc(f.severity)}"><strong>${esc(f.category)}</strong><span>${esc(f.path)} · ${esc(f.detail)}</span></div>`).join("") : `<div class="empty">No findings.</div>`}</div>
        <h3 class="subhead">Documents</h3>
        <div class="list">${documents.map((d) => `
          <div class="item">
            <div><div class="item__title">${esc(d.path)}</div><div class="item__sub">${esc(d.okf_type || "missing OKF type")}</div></div>
            <div class="item__meta"><span class="pill pill--scope">${esc(d.scope)}</span></div>
          </div>`).join("")}</div>
        ${decided
          ? `<p class="dim">Reviewed ${fmtTime(item.reviewed_at, esc)} by ${esc(item.reviewed_by || "")}</p>`
          : `<div class="review-actions">
              <h3 class="subhead">Governance checklist</h3>
              <div class="field" role="group" aria-label="Approval checklist">${checklistHtml}</div>
              <label class="check"><input type="checkbox" id="rvAcceptPii" /> PII is expected and approved for this visibility</label>
              <div class="field">
                <label class="lbl" for="rvNote">Review note</label>
                <textarea class="textarea" id="rvNote" rows="3" placeholder="What did you verify, and why does this belong in governed memory?"></textarea>
              </div>
              <div class="row">
                <button type="button" class="btn btn--primary" id="rvApprove" disabled>Approve</button>
                <button type="button" class="btn" id="rvReject">Reject</button>
              </div>
              <p class="dim" id="rvGateHint">Approval unlocks when all four checks pass and a note is written.</p>
            </div>`}`;

      if (decided) return;

      const approveBtn = pane.querySelector("#rvApprove");
      const noteEl = pane.querySelector("#rvNote");
      const gates = Array.from(pane.querySelectorAll(".rv-gate"));
      const gateState = () => gates.every((g) => g.checked) && noteEl.value.trim().length >= 3;
      const syncGate = () => { approveBtn.disabled = !gateState(); };
      gates.forEach((g) => g.addEventListener("change", syncGate));
      noteEl.addEventListener("input", syncGate);

      approveBtn.addEventListener("click", () => decide(id, true, noteEl));
      pane.querySelector("#rvReject").addEventListener("click", () => decide(id, false, noteEl));
    } catch (e) { ctx.error(pane, e); }
  }

  async function decide(id, approve, noteEl) {
    const note = noteEl.value.trim();
    if (note.length < 3) { toast("err", "Add a review note"); return; }
    const verb = approve ? "Approve" : "Reject";
    if (!window.confirm(`${verb} import ${id}? This decision is recorded in the audit trail.`)) return;
    try {
      const acceptPii = pane.querySelector("#rvAcceptPii");
      await api.post(
        `${approve ? "/kb.openwiki_approve" : "/kb.openwiki_reject"}?id=${encodeURIComponent(id)}`,
        { note, accept_pii: Boolean(acceptPii && acceptPii.checked) },
      );
      toast("ok", approve ? "Import approved" : "Import rejected");
      await loadQueue();
      await viewImport(id);
    } catch (e) { toast("err", e.message); }
  }

  /* ---------- memory-quality scan ---------- */
  async function loadMemory() {
    memoryList.innerHTML = LOADING;
    try {
      const data = await api.get("/kb.review_items?status=open&limit=200");
      const items = data.items || [];
      if (!items.length) {
        memoryList.innerHTML = `<div class="empty">No staleness or conflict findings — memory is holding its shape.</div>`;
        return;
      }
      memoryList.innerHTML = items.map((item) => {
        const details = item.details || {};
        const locator = details.paths ? details.paths.join(" · ") : (details.path || item.resource_id);
        return `<div class="item review-item">
          <div><div class="item__title">${esc(item.reason)}</div><div class="item__sub mono">${esc(locator)}</div></div>
          <div class="item__meta"><span class="pill">${esc(item.kind)}</span>${item.due_at ? `<span class="pill">due ${fmtTime(item.due_at, esc)}</span>` : ""}<button type="button" class="btn btn--sm rv-resolve" data-id="${esc(item.id)}">Resolve</button></div>
        </div>`;
      }).join("");
      memoryList.querySelectorAll(".rv-resolve").forEach((button) => button.addEventListener("click", async () => {
        const note = window.prompt("Resolution note:");
        if (!note || note.trim().length < 3) return;
        try {
          await api.post(`/kb.review_resolve?id=${encodeURIComponent(button.dataset.id)}`, { note: note.trim() });
          toast("ok", "Review item resolved");
          loadMemory();
        } catch (e) { toast("err", e.message); }
      }));
    } catch (e) { ctx.error(memoryList, e); }
  }

  section.querySelector("#rvScanBtn").addEventListener("click", async () => {
    const btn = section.querySelector("#rvScanBtn");
    btn.disabled = true;
    try {
      const result = await api.post("/kb.review_scan", {});
      toast("ok", `${result.detected} findings detected`);
      await loadMemory();
    } catch (e) { toast("err", e.message); }
    finally { btn.disabled = false; }
  });

  section.querySelector("#rvRefresh").addEventListener("click", () => { loadQueue(); loadMemory(); });

  loadQueue();
  loadMemory();
}
