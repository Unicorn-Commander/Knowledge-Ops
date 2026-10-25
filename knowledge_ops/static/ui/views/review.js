/* Review view — OpenWiki import queue with a governance checklist
   (provenance / access / privacy / content) gating approval, plus the
   memory-quality scan (staleness + conflicts). Evidence-first: nothing
   is promoted into governed memory without a human sign-off.

   v2 pane idiom: the pane IS the surface — the queue is hairline rows,
   the inspection zone sits beside it behind a hairline rule, and the
   governance gate is a quiet ruled zone rather than a boxed card. */

const LOADING = `<div class="loading"><span class="spinner"></span> loading…</div>`;

function fmtTime(t, esc) {
  if (!t) return "";
  try { return new Date(t).toLocaleString(); } catch { return esc(t); }
}

/* compact stamp for hairline rows: "Jul 26, 10:30 AM" */
function fmtShort(t, esc) {
  if (!t) return "";
  try {
    const d = new Date(t);
    return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  } catch { return esc(t); }
}

const CHECKLIST = [
  { id: "provenance", label: "Provenance — origin and author trail are recorded and plausible",
    hint: "Check the trail: which system this came from, who authored it, and how it was staged — recorded and believable." },
  { id: "access", label: "Access — visibility scope matches who should retrieve this",
    hint: "Check the declared scope (personal / global) against who should actually be able to retrieve and cite this content." },
  { id: "privacy", label: "Privacy — no unexpected PII, secrets, or client-confidential spill",
    hint: "Look for names, contact details, credentials, or client-confidential content. Intentional PII also needs the acceptance box below." },
  { id: "content", label: "Content — accurate, current, and safe to cite as evidence",
    hint: "Check the material is accurate and current — once approved, agents and people will cite it as evidence." },
];

/* import state → pill flavor (honest: approved is the only green) */
function statePill(state, esc) {
  const s = String(state || "staged");
  const cls = s === "approved" ? "pill--ok" : (s === "rejected" ? "pill--off" : "");
  return `<span class="pill ${cls}" data-hint-title="Import state"
    data-hint="staged = waiting for a human decision · approved = promoted into governed, citable memory · rejected = kept out. Approval is the only way in.">${esc(s)}</span>`;
}

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  st.textContent = `
  /* layout only — every color rides a --mu-* token */
  #view-review .rv-split { display: grid; grid-template-columns: minmax(15rem, 5fr) minmax(0, 7fr); gap: 0 1.7rem; align-items: start; margin-top: .3rem; }
  #view-review .rv-split[hidden] { display: none !important; }
  #view-review .rv-inspect { border-left: 1px solid var(--mu-hairline); padding-left: 1.7rem; min-height: 12rem; }
  @media (max-width: 960px) {
    #view-review .rv-split { grid-template-columns: minmax(0, 1fr); }
    #view-review .rv-inspect { border-left: 0; padding-left: 0; border-top: 1px solid var(--mu-hairline); padding-top: 1.1rem; margin-top: .9rem; }
  }
  #view-review .review-item { align-items: flex-start; }
  #view-review button.review-item[aria-current="true"] {
    background: color-mix(in srgb, var(--mu-color-surface-strong) 55%, transparent);
    box-shadow: inset 2px 0 0 var(--mu-color-primary);
    border-radius: 0 var(--mu-radius-sm) var(--mu-radius-sm) 0;
  }
  #view-review .rv-meta { display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; justify-content: flex-end; }
  #view-review .rv-meta[hidden] { display: none !important; }
  #view-review .rv-gatezone { border-top: 1px solid var(--mu-hairline); margin-top: 1.2rem; padding-top: 1rem; }
  #view-review .rv-checks { display: flex; flex-direction: column; gap: .5rem; margin: .4rem 0 .9rem; }
  #view-review .rv-checks[hidden] { display: none !important; }
  #view-review .rv-zone { border-top: 1px solid var(--mu-hairline); margin-top: 1.9rem; padding-top: 1.3rem; }
  #view-review .rv-about { font-size: .8rem; margin: -.5rem 0 1.1rem; }
  `;
  document.head.appendChild(st);
}

export function init(section, ctx) {
  const { api, esc, toast } = ctx;
  injectStyles();

  section.innerHTML = `
    <p class="kicker">OpenWiki · governance</p>
    <h1 class="title">Review queue</h1>
    <p class="lede">Every import passes a human governance gate before it can be cited. Nothing sneaks into memory.</p>
    <p class="dim rv-about">About this view — the human gate: staged imports need a governance sign-off before they become citable memory, and scans flag memory going stale. <a href="#help">Learn more</a></p>

    <div class="section-row">
      <h2 class="section section--flush">Imports <span class="dim" id="rvCount"></span></h2>
      <button type="button" class="btn btn--sm" id="rvRefresh">Refresh</button>
    </div>
    <div class="rv-split">
      <div class="list" id="rvList" aria-live="polite" aria-label="Staged imports">${LOADING}</div>
      <div class="rv-inspect" id="rvPane" aria-live="polite" aria-label="Import inspection">
        <div class="empty"><span class="big">Nothing under the glass</span>Select an import to inspect its findings and documents.</div>
      </div>
    </div>

    <div class="rv-zone">
      <div class="section-row">
        <div>
          <p class="kicker">Memory quality</p>
          <h2 class="section section--flush">Staleness and conflicts</h2>
        </div>
        <button type="button" class="btn btn--sm" id="rvScanBtn"
          data-hint="Runs the staleness and conflict scan across governed memory right now. It only detects and lists findings — nothing is changed.">Scan now</button>
      </div>
      <div class="list" id="rvMemoryList" aria-live="polite" aria-label="Memory quality findings">${LOADING}</div>
    </div>`;

  const list = section.querySelector("#rvList");
  const pane = section.querySelector("#rvPane");
  const memoryList = section.querySelector("#rvMemoryList");
  const memoryZone = section.querySelector(".rv-zone");
  const countEl = section.querySelector("#rvCount");
  let selectedId = null;

  function hideMemoryReviewOnForbidden(err) {
    if (err && err.status === 403) {
      memoryZone.hidden = true;
      return true;
    }
    return false;
  }

  /* ---------- import queue ---------- */
  function markSelected() {
    list.querySelectorAll(".review-item").forEach((row) => {
      if (row.dataset.id === selectedId) row.setAttribute("aria-current", "true");
      else row.removeAttribute("aria-current");
    });
  }

  async function loadQueue() {
    list.innerHTML = LOADING;
    countEl.textContent = "";
    try {
      const data = await api.get("/kb.openwiki_imports");
      const imports = data.imports || [];
      countEl.textContent = imports.length ? `· ${imports.length}` : "";
      if (!imports.length) {
        list.innerHTML = `<div class="empty"><span class="big">Queue is clear</span>Every staged import has been reviewed.</div>`;
        return;
      }
      list.innerHTML = "";
      imports.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "item review-item";
        button.dataset.id = item.import_id;
        const blocking = (item.findings || []).filter((f) => f.severity === "blocking").length;
        button.innerHTML = `
          <div style="min-width:0;text-align:left;">
            <div class="item__title mono">${esc(item.import_id)}</div>
            <div class="item__sub">${esc(item.document_count)} docs · ${fmtShort(item.staged_at, esc)}</div>
          </div>
          <div class="item__meta rv-meta">${statePill(item.state, esc)}${blocking ? `<span class="pill run--failed" data-hint="Number of findings flagged blocking — review them in the inspection pane before approving this import.">${blocking} blocking</span>` : ""}</div>`;
        button.addEventListener("click", () => viewImport(item.import_id));
        list.appendChild(button);
      });
      markSelected();
    } catch (e) { ctx.error(list, e); }
  }

  async function viewImport(id) {
    selectedId = id;
    markSelected();
    pane.innerHTML = LOADING;
    try {
      const data = await api.get(`/kb.openwiki_import?id=${encodeURIComponent(id)}`);
      const item = data.import;
      const findings = item.findings || [];
      const documents = item.documents || [];
      const decided = ["approved", "rejected"].includes(item.state);

      const checklistHtml = CHECKLIST.map((c) => `
        <label class="check" data-hint="${esc(c.hint)}">
          <input type="checkbox" class="rv-gate" id="rvGate-${c.id}" />
          ${esc(c.label)}
        </label>`).join("");

      pane.innerHTML = `
        <div class="section-row" style="align-items:flex-start;">
          <div style="min-width:0;">
            <p class="kicker" style="margin-bottom:.15rem;">Inspection</p>
            <h2 class="section section--flush">${esc(item.document_count)} documents</h2>
            <div class="item__sub" style="margin-top:.2rem;">${esc(item.import_id)} · staged ${fmtTime(item.staged_at, esc)}</div>
          </div>
          ${statePill(item.state, esc)}
        </div>
        <h3 class="subhead">Findings</h3>
        <div class="list">${findings.length ? findings.map((f) => `
          <div class="finding finding--${esc(f.severity)}"><span class="pill ${f.severity === "blocking" ? "run--failed" : ""}" data-hint="Finding severity — blocking should stop approval until it is reviewed; lower severities are advisory.">${esc(f.severity)}</span><strong>${esc(f.category)}</strong><span>${esc(f.path)} · ${esc(f.detail)}</span></div>`).join("") : `<div class="empty">No findings.</div>`}</div>
        <h3 class="subhead">Documents</h3>
        <div class="list">${documents.map((d) => `
          <div class="item">
            <div style="min-width:0;"><div class="item__title">${esc(d.path)}</div><div class="item__sub">${esc(d.okf_type || "missing OKF type")}</div></div>
            <div class="item__meta"><span class="pill pill--scope">${esc(d.scope)}</span></div>
          </div>`).join("")}</div>
        ${decided
          ? `<p class="dim">Reviewed ${fmtTime(item.reviewed_at, esc)} by ${esc(item.reviewed_by || "")}</p>`
          : `<div class="rv-gatezone">
              <p class="kicker" style="margin-bottom:.4rem;">Governance checklist</p>
              <div class="rv-checks" role="group" aria-label="Approval checklist">${checklistHtml}</div>
              <label class="check" data-hint="Records an explicit decision in the audit trail: you saw personal data in this import and accept it at the declared visibility."><input type="checkbox" id="rvAcceptPii" /> PII is expected and approved for this visibility</label>
              <div class="field" style="margin-top:.9rem;">
                <label class="lbl" for="rvNote">Review note</label>
                <textarea class="textarea" id="rvNote" rows="3" style="min-height:5.4rem;" data-hint="Required. Stored with your decision in the audit trail — say what you verified and why it belongs in governed memory." placeholder="What did you verify, and why does this belong in governed memory?"></textarea>
              </div>
              <div class="review-actions">
                <button type="button" class="btn btn--primary" id="rvApprove" disabled data-hint="Promotes every document in this import into governed, citable memory. Unlocks after all four checks and a note; the decision is audited.">Approve</button>
                <button type="button" class="btn" id="rvReject" data-hint="Keeps this import out of governed memory. Your note records why; the decision lands in the audit trail.">Reject</button>
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
    memoryZone.hidden = false;
    memoryList.innerHTML = LOADING;
    try {
      const data = await api.get("/kb.review_items?status=open&limit=200");
      const items = data.items || [];
      if (!items.length) {
        memoryList.innerHTML = `<div class="empty"><span class="big">Memory is holding its shape</span>No staleness or conflict findings.</div>`;
        return;
      }
      memoryList.innerHTML = items.map((item) => {
        const details = item.details || {};
        /* tolerate both the API shape (reason/resource_id/details) and older
           flat rows (title/path/detail) without inventing data */
        const reason = item.reason || item.detail || item.title || item.kind;
        const locator = (details.paths && details.paths.join(" · "))
          || details.path || item.path || item.resource_id || item.doc_id || "";
        return `<div class="item review-item">
          <div style="min-width:0;"><div class="item__title">${esc(reason)}</div><div class="item__sub">${esc(locator)}</div></div>
          <div class="item__meta rv-meta"><span class="pill ${item.kind === "conflict" ? "run--failed" : ""}" data-hint="Finding kind — staleness means a memory passed its review horizon; conflict means two memories disagree.">${esc(item.kind)}</span>${item.severity ? `<span class="pill" data-hint="How urgent the scan judged this finding to be.">${esc(item.severity)}</span>` : ""}${item.due_at ? `<span class="pill" data-hint="Review-by date derived from the memory's staleness policy.">due ${fmtShort(item.due_at, esc)}</span>` : ""}<button type="button" class="btn btn--sm rv-resolve" data-id="${esc(item.id)}" aria-label="Resolve finding: ${esc(reason)}" data-hint="Closes this finding with a short note (prompted next) — you are asserting it is handled. A later scan can raise it again if it persists.">Resolve</button></div>
        </div>`;
      }).join("");
      memoryList.querySelectorAll(".rv-resolve").forEach((button) => button.addEventListener("click", async () => {
        const note = window.prompt("Resolution note:");
        if (!note || note.trim().length < 3) return;
        try {
          await api.post(`/kb.review_resolve?id=${encodeURIComponent(button.dataset.id)}`, { note: note.trim() });
          toast("ok", "Review item resolved");
          loadMemory();
        } catch (e) {
          if (!hideMemoryReviewOnForbidden(e)) toast("err", e.message);
        }
      }));
    } catch (e) {
      if (!hideMemoryReviewOnForbidden(e)) ctx.error(memoryList, e);
    }
  }

  section.querySelector("#rvScanBtn").addEventListener("click", async () => {
    const btn = section.querySelector("#rvScanBtn");
    btn.disabled = true;
    try {
      const result = await api.post("/kb.review_scan", {});
      toast("ok", `${result.detected} findings detected`);
      await loadMemory();
    } catch (e) {
      if (!hideMemoryReviewOnForbidden(e)) toast("err", e.message);
    }
    finally { btn.disabled = false; }
  });

  section.querySelector("#rvRefresh").addEventListener("click", () => { loadQueue(); loadMemory(); });

  loadQueue();
  loadMemory();
}
