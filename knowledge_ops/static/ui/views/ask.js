/* Ask — the evidence workbench.
   Federated retrieval (kb.search) and heuristic grounding (kb.ground) rendered
   as staged citation cards. Honesty first: heuristic verdicts are always
   labelled as heuristic, never as verification. */

const REDUCED = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* map a passage source string onto the --mu-src-* token family */
function srcToken(source) {
  const s = String(source || "").toLowerCase();
  const coll = s.startsWith("qdrant:") ? s.slice(7) : s;
  if (coll.includes("wiki")) return "--mu-src-wiki";
  if (coll.includes("org_memory") || coll.includes("org-memory")) return "--mu-src-org-memory";
  if (coll.includes("meeting")) return "--mu-src-meetings";
  if (coll.includes("stable")) return "--mu-src-stable";
  if (coll.includes("file")) return "--mu-src-files";
  if (coll.includes("falkor") || coll.includes("graph")) return "--mu-src-graph";
  if (coll.includes("metric")) return "--mu-src-metrics";
  return "--mu-src-wiki";
}

function srcLabel(source) {
  const s = String(source || "other");
  if (s.startsWith("qdrant:")) {
    return s.slice(7).replace(/^knowledge_ops_/, "").replace(/_/g, " ");
  }
  return s.replace(/^falkordb:/, "graph ").replace(/^rest:/, "api ").replace(/^postgres:/, "sql ").replace(/_/g, " ");
}

function fmtScore(s) {
  return typeof s === "number" ? s.toFixed(2) : String(s == null ? "—" : s);
}

const EXAMPLES = [
  "What is our brand voice?",
  "How does the inference pipeline work?",
  "What is the Majiks launch plan?",
];

const VERDICTS = {
  possible_support: ["pill--ok", "possible support"],
  possible_contradiction: ["", "possible contradiction"],
  possible_conflict: ["", "possible conflict"],
  related_evidence: ["pill--scope", "related evidence"],
  not_enough_evidence: ["pill--off", "not enough evidence"],
};

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  st.textContent = `
.ask-cite{position:relative;padding:0.9rem 1rem;border:1px solid var(--mu-color-border);border-radius:var(--mu-radius-md);background:var(--mu-color-surface);margin-bottom:0.7rem;transition:border-color 140ms cubic-bezier(0.22,1,0.36,1)}
.ask-cite:hover,.ask-cite:focus-within{border-color:var(--mu-color-border-strong)}
.ask-cite--in{opacity:0;transform:translateY(6px);animation:ask-settle 320ms cubic-bezier(0.22,1,0.36,1) forwards}
@keyframes ask-settle{to{opacity:1;transform:none}}
@media (prefers-reduced-motion: reduce){.ask-cite--in{animation:none;opacity:1;transform:none}}
.ask-cite__head{display:flex;flex-wrap:wrap;align-items:center;gap:0.5rem}
.ask-cite__n{display:inline-flex;align-items:center;justify-content:center;min-width:1.6rem;height:1.6rem;padding:0 .3rem;border-radius:var(--mu-radius-sm);font-family:var(--mu-font-mono);font-size:.78rem;color:var(--mu-color-bg);background:var(--mu-color-accent)}
.ask-cite__title{font-weight:600;color:var(--mu-color-text);min-width:0;overflow-wrap:anywhere}
.ask-src{display:inline-flex;align-items:center;gap:.35rem;padding:.15rem .55rem;border-radius:var(--mu-radius-sm);font-family:var(--mu-font-mono);font-size:.72rem;color:var(--mu-color-text);border:1px solid var(--mu-color-border);background:var(--mu-color-surface-strong)}
.ask-src__dot{width:.55rem;height:.55rem;border-radius:50%;flex:none}
.ask-meter{display:flex;align-items:center;gap:.5rem;margin:.55rem 0 .1rem}
.ask-meter__track{flex:1;max-width:14rem;height:3px;border-radius:2px;background:var(--mu-color-border);overflow:hidden}
.ask-meter__fill{height:100%;border-radius:2px;background:var(--mu-gradient-brand)}
.ask-meter__num{font-family:var(--mu-font-mono);font-variant-numeric:tabular-nums;font-size:.75rem;color:var(--mu-color-muted)}
.ask-cite__text{margin-top:.45rem;color:var(--mu-color-muted);line-height:1.55;overflow-wrap:anywhere}
.ask-cite__text--clamp{max-height:7.2em;overflow:hidden;-webkit-mask-image:linear-gradient(to bottom,#000 65%,transparent);mask-image:linear-gradient(to bottom,#000 65%,transparent)}
.ask-cite__foot{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-top:.55rem}
.ask-cite__uri{font-family:var(--mu-font-mono);font-size:.7rem;color:var(--mu-color-dim);overflow-wrap:anywhere;margin-top:.45rem}
.ask-skel{border:1px solid var(--mu-color-border);border-radius:var(--mu-radius-md);background:var(--mu-color-surface);padding:.9rem 1rem;margin-bottom:.7rem}
.ask-skel__bar{height:.8rem;border-radius:var(--mu-radius-sm);background:linear-gradient(90deg,var(--mu-color-surface-strong) 25%,var(--mu-color-border) 50%,var(--mu-color-surface-strong) 75%);background-size:200% 100%;animation:ask-shimmer 1.4s linear infinite;margin-bottom:.55rem}
.ask-skel__bar:last-child{margin-bottom:0;width:60%}
@keyframes ask-shimmer{to{background-position:-200% 0}}
@media (prefers-reduced-motion: reduce){.ask-skel__bar{animation:none;background:var(--mu-color-surface-strong)}}
.ask-examples{display:flex;flex-wrap:wrap;gap:.6rem;justify-content:center;margin-top:.8rem}
.ask-heading{font-family:var(--mu-font-display);font-size:1.5rem;letter-spacing:.01em;color:var(--mu-color-text);margin:0 0 .8rem}
`;
  document.head.appendChild(st);
}

export function init(sectionEl, ctx) {
  const { api, bus, esc, toast, error } = ctx;
  injectStyles();

  sectionEl.innerHTML = `
    <div class="card card--strong">
      <p class="kicker">Evidence workbench</p>
      <h2 class="ask-heading">Ask knowledge</h2>
      <div class="field">
        <label class="lbl" for="askQuery">Question or claim</label>
        <textarea class="textarea" id="askQuery" rows="3"
          placeholder="Ask a question, or state a claim to check against the organization's memory…"></textarea>
      </div>
      <div class="row" style="align-items:flex-end;">
        <div>
          <label class="lbl" for="askMode">Mode</label>
          <select class="select" id="askMode">
            <option value="ground" selected>Assess evidence</option>
            <option value="search">Search (raw passages)</option>
          </select>
        </div>
        <div>
          <label class="lbl" for="askScope">Visibility</label>
          <select class="select" id="askScope">
            <option value="">mine + shared</option>
            <option value="global">global</option>
            <option value="personal">personal</option>
            <option value="all">all (administrator)</option>
          </select>
        </div>
        <div>
          <label class="lbl" for="askK">Evidence (k)</label>
          <select class="select" id="askK">
            <option>4</option><option selected>6</option><option>8</option><option>12</option>
          </select>
        </div>
        <button type="button" class="btn btn--primary" id="askBtn" style="min-height:44px;">Gather evidence</button>
      </div>
    </div>
    <div id="askStage" aria-live="polite" aria-busy="false" style="margin-top:1rem;"></div>
  `;

  const $ = (sel) => sectionEl.querySelector(sel);
  const stage = $("#askStage");
  const queryEl = $("#askQuery");
  const modeEl = $("#askMode");
  const scopeEl = $("#askScope");
  const kEl = $("#askK");
  const btn = $("#askBtn");
  let runToken = 0;

  /* ---------- rendering ---------- */

  function skeletons(n) {
    let html = "";
    for (let i = 0; i < n; i++) {
      html += `<div class="ask-skel" aria-hidden="true"><div class="ask-skel__bar" style="width:38%;"></div><div class="ask-skel__bar"></div><div class="ask-skel__bar"></div></div>`;
    }
    return html;
  }

  function citeCard(p, i) {
    const token = srcToken(p.source);
    const score = typeof p.score === "number" ? Math.max(0, Math.min(1, p.score)) : 0;
    const text = String(p.text || "");
    const long = text.length > 420;
    const scope = p.scope ? `<span class="pill pill--scope">${esc(p.scope)}</span>` : "";
    const relation = p.metadata && p.metadata.evidence_relation
      ? `<span class="pill">${esc(String(p.metadata.evidence_relation).replace(/_/g, " "))}</span>` : "";
    const uri = p.uri ? `<div class="ask-cite__uri">${esc(p.uri)}</div>` : "";
    return `<article class="ask-cite" data-i="${i}" tabindex="0"
      data-node-id="${esc(p.ref || p.source_id || "")}">
      <div class="ask-cite__head">
        <span class="ask-cite__n" aria-label="citation ${i}">${i}</span>
        <span class="ask-src"><span class="ask-src__dot" style="background:var(${token});"></span>${esc(srcLabel(p.source))}</span>
        <span class="ask-cite__title">${esc(p.title || p.source_id || "untitled")}</span>
        ${scope}${relation}
      </div>
      <div class="ask-meter">
        <div class="ask-meter__track" role="img" aria-label="relevance score ${esc(fmtScore(p.score))}">
          <div class="ask-meter__fill" style="width:${(score * 100).toFixed(0)}%;"></div>
        </div>
        <span class="ask-meter__num">${esc(fmtScore(p.score))}</span>
        ${p.owner ? `<span class="ask-meter__num">· ${esc(p.owner)}</span>` : ""}
      </div>
      <div class="ask-cite__text ${long ? "ask-cite__text--clamp" : ""}" id="askCiteText${i}">${esc(text) || `<span style="color:var(--mu-color-dim);">(no snippet)</span>`}</div>
      <div class="ask-cite__foot">
        ${long ? `<button type="button" class="btn btn--sm ask-more" aria-expanded="false" aria-controls="askCiteText${i}">More</button>` : ""}
        <button type="button" class="btn btn--sm ask-inspect"
          data-source="${esc(String(p.source || ""))}"
          data-source-id="${esc(String(p.source_id || ""))}">Inspect span</button>
      </div>
      ${uri}
    </article>`;
  }

  function bindCard(card, query) {
    const more = card.querySelector(".ask-more");
    if (more) {
      more.addEventListener("click", () => {
        const t = card.querySelector(".ask-cite__text");
        const open = t.classList.toggle("ask-cite__text--clamp") === false;
        more.setAttribute("aria-expanded", String(open));
        more.textContent = open ? "Less" : "More";
      });
    }
    const inspect = card.querySelector(".ask-inspect");
    inspect.addEventListener("click", () => inspectSpan(inspect, query));
    const focusOut = () => bus.emit("constellation:focus", { ids: [] });
    const focusIn = () => {
      const id = card.dataset.nodeId;
      bus.emit("constellation:focus", { ids: id ? [id] : [] });
    };
    card.addEventListener("mouseenter", focusIn);
    card.addEventListener("focusin", focusIn);
    card.addEventListener("mouseleave", focusOut);
    card.addEventListener("focusout", (e) => { if (!card.contains(e.relatedTarget)) focusOut(); });
  }

  function verdictHeader(data) {
    const [cls, label] = VERDICTS[data.verdict] || ["pill--off", "unassessed"];
    return `<div class="card card--strong" style="margin-bottom:1rem;">
      <div class="row" style="margin-bottom:.5rem;">
        <span class="pill ${cls}">${esc(label)}</span>
        <span class="pill pill--off">heuristic, not verified</span>
      </div>
      <div>${esc(data.answer || "")}</div>
    </div>`;
  }

  function emptyState() {
    return `<div class="empty">
      <p style="margin:0 0 .3rem;">No accessible evidence found for that yet.</p>
      <p style="margin:0;color:var(--mu-color-dim);">Try one of these to watch the memory answer:</p>
      <div class="ask-examples" role="group" aria-label="example questions">
        ${EXAMPLES.map((q) => `<button type="button" class="btn btn--sm ask-example" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
      </div>
    </div>`;
  }

  function bindExamples(root) {
    root.querySelectorAll(".ask-example").forEach((b) =>
      b.addEventListener("click", () => run({ query: b.dataset.q })));
  }

  /* ---------- inspect span dialog ---------- */

  function highlight(excerpt, ranges) {
    const pts = Array.from(String(excerpt || ""));
    const valid = (ranges || [])
      .filter((r) => Number.isInteger(r.start) && Number.isInteger(r.end) && r.start >= 0 && r.end > r.start && r.end <= pts.length)
      .sort((a, b) => a.start - b.start || a.end - b.end);
    const merged = [];
    valid.forEach((r) => {
      const last = merged[merged.length - 1];
      if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
      else merged.push({ start: r.start, end: r.end });
    });
    let cur = 0, html = "";
    merged.forEach((r) => {
      html += esc(pts.slice(cur, r.start).join(""));
      html += `<mark>${esc(pts.slice(r.start, r.end).join(""))}</mark>`;
      cur = r.end;
    });
    return html + esc(pts.slice(cur).join(""));
  }

  async function inspectSpan(btnEl, claim) {
    const dialog = document.getElementById("citationDialog");
    const content = document.getElementById("citationContent");
    if (!dialog || !content) { toast("err", "Citation dialog unavailable in this build."); return; }
    content.innerHTML = `<div class="loading"><span class="spinner"></span> loading source span…</div>`;
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    try {
      const data = await api.post("/kb.inspect_citation", {
        source: btnEl.dataset.source,
        source_id: btnEl.dataset.sourceId,
        claim: claim || "evidence",
      });
      const c = (data && data.citation) || {};
      if (!c.inspection_available) {
        content.innerHTML = `<div class="empty">${esc(c.reason || "Source inspection is unavailable for this passage.")}</div>`;
        return;
      }
      const span = c.source_span || {};
      const spanPill = Number.isInteger(span.start) && Number.isInteger(span.end)
        ? `<span class="pill">codepoints ${esc(String(span.start))}–${esc(String(span.end))}</span>`
        : "";
      const token = srcToken(c.source);
      content.innerHTML = `
        <div class="row" style="margin-bottom:.6rem;">
          <span class="ask-src"><span class="ask-src__dot" style="background:var(${token});"></span>${esc(srcLabel(c.source))}</span>
          ${spanPill}
          ${c.lifecycle ? `<span class="pill">${esc(c.lifecycle.state)}</span>` : ""}
        </div>
        <div style="font-family:var(--mu-font-mono);font-size:.78rem;color:var(--mu-color-muted);overflow-wrap:anywhere;margin-bottom:.6rem;">${esc(c.source_path || c.source_id || "")}</div>
        <pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--mu-font-mono);font-size:.82rem;line-height:1.55;background:var(--mu-color-surface);border:1px solid var(--mu-color-border);border-radius:var(--mu-radius-md);padding:.9rem;max-height:50vh;overflow:auto;">${highlight(c.excerpt, c.match_ranges)}</pre>
        <div style="color:var(--mu-color-dim);font-size:.78rem;margin-top:.5rem;">Claim ${esc(String(c.claim_id || ""))} · Citation ${esc(String(c.citation_id || ""))}</div>`;
    } catch (e) {
      error(content, e);
    }
  }

  /* ---------- run ---------- */

  async function run(opts = {}) {
    if (opts.query != null) queryEl.value = opts.query;
    if (opts.scope != null) {
      const has = Array.from(scopeEl.options).some((o) => o.value === opts.scope);
      scopeEl.value = has ? opts.scope : "";
    }
    if (opts.k != null && Array.from(kEl.options).some((o) => o.value === String(opts.k))) {
      kEl.value = String(opts.k);
    }
    const query = queryEl.value.trim();
    if (!query) {
      stage.innerHTML = `<div class="empty">
        <p style="margin:0 0 .3rem;">State a question or a claim above.</p>
        <div class="ask-examples" role="group" aria-label="example questions">
          ${EXAMPLES.map((q) => `<button type="button" class="btn btn--sm ask-example" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
        </div>
      </div>`;
      bindExamples(stage);
      return;
    }
    const token = ++runToken;
    const mode = modeEl.value;
    const scope = scopeEl.value || undefined;
    const k = parseInt(kEl.value, 10) || 6;
    btn.disabled = true;
    stage.setAttribute("aria-busy", "true");
    stage.innerHTML = `
      <div class="loading" style="margin-bottom:.8rem;"><span class="spinner"></span> Federating stores, merging &amp; re-ranking evidence…</div>
      ${skeletons(Math.min(k, 4))}`;
    try {
      const data = await api.post(mode === "search" ? "/kb.search" : "/kb.ground", { query, scope, k });
      if (token !== runToken) return; // superseded
      const passages = data.passages || [];
      let html = "";
      if (mode === "ground") {
        html += verdictHeader(data);
      } else if (data.answer) {
        html += `<div class="card card--strong" style="margin-bottom:1rem;">${esc(String(data.answer).split(/\n\nCitations:/)[0])}</div>`;
      }
      if (passages.length) {
        const srcs = [...new Set(passages.map((p) => String(p.source).split(":")[0]))];
        html += `<h2 class="section">Evidence <span style="color:var(--mu-color-dim);font-weight:400;">· ${passages.length} passage${passages.length === 1 ? "" : "s"} from ${srcs.length} source${srcs.length === 1 ? "" : "s"}</span></h2>`;
        html += `<div id="askCites">${passages.map((p, i) => citeCard(p, i + 1)).join("")}</div>`;
      } else {
        html += emptyState();
      }
      stage.innerHTML = html;
      bindExamples(stage);
      const cards = Array.from(stage.querySelectorAll(".ask-cite"));
      const reduced = REDUCED();
      cards.forEach((card, i) => {
        bindCard(card, query);
        if (!reduced) {
          card.classList.add("ask-cite--in");
          card.style.animationDelay = (i * 40) + "ms";
        }
      });
      bus.emit("ask:results", { query, passages });
    } catch (e) {
      if (token !== runToken) return;
      error(stage, e);
    } finally {
      if (token === runToken) {
        btn.disabled = false;
        stage.setAttribute("aria-busy", "false");
      }
    }
  }

  btn.addEventListener("click", () => run());
  queryEl.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); run(); }
  });
  bus.on("ask:run", (payload) => run(payload || {}));

  /* initial idle state: invite with example chips */
  stage.innerHTML = `<div class="empty">
    <p style="margin:0 0 .3rem;">Ask the organization what it remembers.</p>
    <p style="margin:0;color:var(--mu-color-dim);">Answers arrive as cited evidence — heuristic retrieval, never presented as verified fact.</p>
    <div class="ask-examples" role="group" aria-label="example questions">
      ${EXAMPLES.map((q) => `<button type="button" class="btn btn--sm ask-example" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
    </div>
  </div>`;
  bindExamples(stage);
}
