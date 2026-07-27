/* Ask — the evidence workbench, lens-first.
   The shell's lens is the only place a question is typed; this pane is where
   the evidence lands. Two states:
     IDLE   — a quiet welcome: example asks + a pointer at the lens (⌘K).
     RESULT — the question becomes the pane's Fraunces title; verdict + cited
              passages render as hairline rows with brass numbering.
   Honesty first: heuristic verdicts are always labelled as heuristic, never
   as verification.

   Contracts (KEEP EXACTLY):
     bus.on('ask:run', {query, scope, k})  — executes an ask (lens, examples,
                                             constellation node taps).
     bus.emit('ask:results', {query, passages}) — the sky ignites on this.
     bus.emit('constellation:focus', {ids}) — spotlight on cite hover/focus.
     api.post('/kb.ground' | '/kb.search' | '/kb.inspect_citation').
     ctx.error(el, e) — branches 401 vs 403 (locked). */

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

/* token → the matching .src--* color class from base.css */
function srcClass(source) {
  return "src--" + srcToken(source).slice(9);
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
  "What gates remain before Meeting-Ops launches?",
];

const VERDICTS = {
  possible_support: ["pill--ok", "possible support"],
  possible_contradiction: ["", "possible contradiction"],
  possible_conflict: ["", "possible conflict"],
  related_evidence: ["pill--scope", "related evidence"],
  not_enough_evidence: ["pill--off", "not enough evidence"],
};

const MODES = [
  ["ground", "Assess", "Retrieves evidence, then applies a heuristic verdict over it — supportive, contested, or not enough. A triage of retrieval, never fact verification."],
  ["search", "Passages", "Raw federated retrieval — the top-k passages ranked by relevance, with no verdict layer applied."],
];

const VALID_SCOPES = ["global", "personal", "all"];

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  /* Layout/motion only — every color and family rides a --mu-* token or a
     base.css class. */
  st.textContent = `
/* idle state */
.ask-try{margin:1.5rem 0 .1rem}
.ask-ex{min-height:2.75rem}
.ask-ex .cite__n{flex:none}
.ask-ex .item__title{font-weight:500}
.ask-hint{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;margin-top:1.5rem}
.ask-hint .btn{min-height:2.75rem}
.ask-kbd{font-family:var(--mu-font-mono);font-size:.68rem;letter-spacing:.06em;color:var(--mu-color-dim);padding:.14rem .48rem;border-radius:var(--mu-radius-pill);box-shadow:inset 0 0 0 1px var(--mu-hairline)}
/* result header */
.ask-q{overflow-wrap:anywhere}
.ask-meta{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.5rem 0 .9rem}
.ask-meta__pills{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;margin-right:auto}
.ask-meta .btn{min-height:2.75rem}
.ask-seg{display:inline-flex;gap:.1rem;padding:.16rem;border-radius:var(--mu-radius-pill);box-shadow:inset 0 0 0 1px var(--mu-hairline)}
.ask-seg .seg__opt{min-height:2.45rem;padding:.3rem .85rem}
/* answer + verdict */
.ask-verdict{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;margin:.2rem 0 .5rem}
.ask-answer{max-width:70ch;margin:.2rem 0 .4rem}
/* citation rows (base .cite hairline vocabulary) */
.ask-cites .cite:last-child{border-bottom:0}
.ask-cites .cite{scroll-margin-top:5rem}
.ask-cites .cite__text{overflow-wrap:anywhere}
.ask-src{font-family:var(--mu-font-mono);font-size:.72rem;letter-spacing:.05em}
.ask-score{margin-left:auto;flex:none;display:inline-flex;align-items:center;gap:.45rem}
.ask-score__track{width:3.4rem;height:3px;border-radius:2px;background:var(--mu-color-border);overflow:hidden}
.ask-score__fill{display:block;height:100%;border-radius:2px;background:var(--mu-gradient-brand)}
.ask-score__n{font-family:var(--mu-font-mono);font-variant-numeric:tabular-nums;font-size:.74rem;color:var(--mu-color-dim)}
.ask-cite-foot{display:flex;align-items:center;gap:.6rem;margin-top:.45rem}
.ask-cite-foot .ask-uri{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ask-cite-foot .btn{flex:none}
.ask-more[hidden]{display:none!important}
.ask-uri{font-family:var(--mu-font-mono);font-size:.7rem;color:var(--mu-color-dim);overflow-wrap:anywhere}
.ask-hint__gram{font-size:.75rem;letter-spacing:.05em}
.ask-about{font-size:.8rem;margin:-.5rem 0 0}
/* settle-in (guarded by a REDUCED() check before the class is added) */
.ask-in{opacity:0;transform:translateY(6px);animation:ask-settle 320ms var(--mu-ease-settle) forwards}
@keyframes ask-settle{to{opacity:1;transform:none}}
@media (prefers-reduced-motion: reduce){.ask-in{animation:none;opacity:1;transform:none}}
/* loading skeleton — hairline rows, echoing the cite list to come */
.ask-skel{padding:.85rem .2rem;border-bottom:1px solid var(--mu-hairline)}
.ask-skel__bar{height:.72rem;border-radius:var(--mu-radius-sm);background:linear-gradient(90deg,var(--mu-color-surface-strong) 25%,var(--mu-color-border) 50%,var(--mu-color-surface-strong) 75%);background-size:200% 100%;animation:ask-shimmer 1.4s linear infinite;margin-bottom:.5rem}
.ask-skel__bar:last-child{margin-bottom:0;width:60%}
@keyframes ask-shimmer{to{background-position:-200% 0}}
@media (prefers-reduced-motion: reduce){.ask-skel__bar{animation:none;background:var(--mu-color-surface-strong)}}
/* result-empty example chips */
.ask-examples{display:flex;flex-wrap:wrap;gap:.6rem;justify-content:center;margin-top:.9rem}
.ask-examples .btn{min-height:2.75rem}
/* inspect dialog excerpt */
.ask-excerpt{white-space:pre-wrap;overflow-wrap:anywhere;font-family:var(--mu-font-mono);font-size:.82rem;line-height:1.55;background:var(--mu-field-bg);box-shadow:inset 0 0 0 1px var(--mu-hairline);border-radius:var(--mu-radius-md);padding:.9rem;max-height:50vh;overflow:auto;margin:0}
`;
  document.head.appendChild(st);
}

export function init(sectionEl, ctx) {
  const { api, bus, esc, toast, error } = ctx;
  injectStyles();

  sectionEl.innerHTML = `
    <header id="askHead"></header>
    <div id="askStage" aria-live="polite" aria-busy="false"></div>
  `;

  const head = sectionEl.querySelector("#askHead");
  const stage = sectionEl.querySelector("#askStage");

  /* session state — the lens (or an example / constellation tap) sets these
     through 'ask:run'; the mode seg re-runs the current query. */
  let currentQuery = "";
  let currentScope = null; // null = mine + shared (the API default)
  let currentK = 8;
  let currentMode = "ground";
  let runToken = 0;

  const normScope = (v) => (VALID_SCOPES.includes(v) ? v : null);
  const scopeLabel = () => currentScope || "mine+shared";

  function focusLens() {
    if (ctx.lens && typeof ctx.lens.focus === "function") ctx.lens.focus();
  }

  /* ---------- idle state ---------- */

  function exampleRow(q, i) {
    return `<button type="button" class="item ask-ex" data-q="${esc(q)}">
      <span class="cite__n" aria-hidden="true">${String(i + 1).padStart(2, "0")}</span>
      <span class="item__title">${esc(q)}</span>
      <span class="item__meta" aria-hidden="true">→</span>
    </button>`;
  }

  function bindExamples(root) {
    root.querySelectorAll("[data-q]").forEach((b) =>
      b.addEventListener("click", () =>
        bus.emit("ask:run", { query: b.dataset.q, scope: null, k: 8 })));
  }

  function renderIdle() {
    head.innerHTML = `
      <p class="kicker">Evidence workbench</p>
      <h1 class="title">Ask the constellation.</h1>
      <p class="lede">Answers arrive as cited evidence — heuristic retrieval, never presented as verified fact.</p>
      <p class="dim ask-about">About this view — questions typed into the lens land here as cited passages pulled from every federated store. <a href="#help">Learn more</a></p>`;
    stage.innerHTML = `
      <p class="lbl ask-try">Try an ask</p>
      <div class="list" role="group" aria-label="Example asks">
        ${EXAMPLES.map(exampleRow).join("")}
      </div>
      <div class="ask-hint">
        <button type="button" class="btn" id="askLensCta"
          data-hint-title="The lens"
          data-hint="Jumps to the lens — the single field where questions are typed. Press Enter to ask; the evidence lands on this pane.">Ask from anywhere <span class="ask-kbd" aria-hidden="true">⌘K</span></button>
        <span class="dim mono ask-hint__gram" aria-hidden="true"
          data-hint="Lens grammar — plain text asks the memory, a leading &gt; runs commands, and @ references a document by name.">↵ ask · &gt; commands · @ documents</span>
      </div>`;
    bindExamples(stage);
    stage.querySelector("#askLensCta").addEventListener("click", focusLens);
  }

  /* ---------- result header ---------- */

  function renderRunHead(phase, ms) {
    let timing = "";
    if (phase === "running") timing = `<span class="pill pill--off" data-hint="Query in flight — every federated store is being asked in parallel.">running…</span>`;
    else if (phase === "done" && typeof ms === "number") {
      timing = `<span class="pill" data-hint="Wall-clock time for the whole run — every store queried, results merged and re-ranked.">${(ms / 1000).toFixed(2)} s</span>`;
    }
    head.innerHTML = `
      <p class="kicker">Evidence workbench</p>
      <h1 class="title ask-q">“${esc(currentQuery)}”</h1>
      <div class="ask-meta">
        <div class="ask-meta__pills">
          <span class="pill pill--scope" data-hint-title="Scope"
            data-hint="Which memory was searched — mine+shared is the default; personal is only yours; global is org-wide; all is everything you can access.">${esc(scopeLabel())}</span>
          <span class="pill" data-hint-title="Retrieval budget"
            data-hint="k is how many passages the federation returns after merging and re-ranking. Set it from the lens; default 8, max 50.">k ${esc(String(currentK))}</span>
          ${timing}
        </div>
        <div class="ask-seg" role="group" aria-label="Answer mode">
          ${MODES.map(([m, label, hint]) => `<button type="button" class="seg__opt" data-mode="${m}"
            data-hint-title="${label} mode" data-hint="${hint}"
            aria-pressed="${String(m === currentMode)}">${label}</button>`).join("")}
        </div>
        <button type="button" class="btn" id="askAgain">Ask again <span class="ask-kbd" aria-hidden="true">⌘K</span></button>
      </div>`;
    head.querySelectorAll(".ask-seg .seg__opt").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.dataset.mode === currentMode) return;
        currentMode = b.dataset.mode;
        run({}); // re-run the CURRENT query in the new mode
      }));
    head.querySelector("#askAgain").addEventListener("click", focusLens);
  }

  /* ---------- result rendering ---------- */

  function skeletons(n) {
    let html = "";
    for (let i = 0; i < n; i++) {
      html += `<div class="ask-skel" aria-hidden="true"><div class="ask-skel__bar" style="width:38%;"></div><div class="ask-skel__bar"></div><div class="ask-skel__bar"></div></div>`;
    }
    return html;
  }

  function citeCard(p, i) {
    const score = typeof p.score === "number" ? Math.max(0, Math.min(1, p.score)) : 0;
    const text = String(p.text || "");
    const scope = p.scope && p.scope !== "global"
      ? `<span class="pill pill--scope" data-hint="Visibility of this passage — personal and team passages are only retrievable by people with that access.">${esc(p.scope)}</span>` : "";
    const relation = p.metadata && p.metadata.evidence_relation
      ? `<span class="pill" data-hint="How the heuristic related this passage to your question — e.g. supports, contradicts, or related background.">${esc(String(p.metadata.evidence_relation).replace(/_/g, " "))}</span>` : "";
    return `<article class="cite" data-i="${i}" tabindex="0"
      data-node-id="${esc(p.ref || p.source_id || "")}">
      <div class="cite__head">
        <span class="cite__n" aria-label="citation ${i}">${String(i).padStart(2, "0")}</span>
        <span class="cite__title">${esc(p.title || p.source_id || "untitled")}</span>
        <span class="src-badge ${srcClass(p.source)} ask-src" data-hint-title="Source"
          data-hint="Federated store this passage came from: ${esc(srcLabel(p.source))}. Color and glyph match its band in the constellation.">${esc(srcLabel(p.source))}</span>
        ${scope}${relation}
        <span class="ask-score" role="img" aria-label="relevance score ${esc(fmtScore(p.score))}"
          data-hint-title="Relevance"
          data-hint="Similarity between your question and this passage, 0–1, used for ranking. High relevance is not endorsement or truth.">
          <span class="ask-score__track"><span class="ask-score__fill" style="width:${(score * 100).toFixed(0)}%;"></span></span>
          <span class="ask-score__n">${esc(fmtScore(p.score))}${p.owner ? ` · ${esc(p.owner)}` : ""}</span>
        </span>
      </div>
      <p class="cite__text" id="askCiteText${i}">${esc(text) || `<span class="dim">(no snippet)</span>`}</p>
      <div class="ask-cite-foot">
        <span class="ask-uri">${esc(p.uri || "")}</span>
        <button type="button" class="btn btn--sm ask-more" hidden aria-expanded="false" aria-controls="askCiteText${i}">More</button>
        <button type="button" class="btn btn--sm cite__inspect ask-inspect"
          data-hint="Opens the stored source behind this citation — the exact excerpt with the matched span highlighted, plus its lifecycle state."
          data-source="${esc(String(p.source || ""))}"
          data-source-id="${esc(String(p.source_id || ""))}">Inspect span</button>
      </div>
    </article>`;
  }

  function bindCard(card, query) {
    const more = card.querySelector(".ask-more");
    const textEl = card.querySelector(".cite__text");
    if (more && textEl) {
      more.addEventListener("click", () => {
        const open = textEl.classList.toggle("expanded");
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

  /* reveal More only where the 3-line clamp actually hides text */
  function revealMoreButtons(token) {
    requestAnimationFrame(() => {
      if (token !== runToken) return;
      stage.querySelectorAll(".cite").forEach((card) => {
        const t = card.querySelector(".cite__text");
        const more = card.querySelector(".ask-more");
        if (t && more && t.scrollHeight > t.clientHeight + 1) more.hidden = false;
      });
    });
  }

  function verdictBlock(data) {
    const [cls, label] = VERDICTS[data.verdict] || ["pill--off", "unassessed"];
    return `<div class="ask-verdict">
        <span class="pill ${cls}" data-hint-title="Heuristic verdict"
          data-hint="Heuristic triage of the retrieved evidence — supportive, contested, or unclear. It judges what retrieval surfaced; it is not verification.">${esc(label)}</span>
        <span class="pill pill--off"
          data-hint="A permanent honesty label — verdicts come from retrieval heuristics, not from human review or a fact-checker.">heuristic, not verified</span>
      </div>
      ${data.answer ? `<p class="answer ask-answer">${esc(data.answer)}</p>` : ""}`;
  }

  function emptyState() {
    return `<div class="empty">
      <span class="big">Nothing surfaced.</span>
      <p style="margin:0 0 .3rem;">No accessible evidence answered that yet.</p>
      <p style="margin:0;" class="dim">Try one of these to watch the memory answer:</p>
      <div class="ask-examples" role="group" aria-label="example questions">
        ${EXAMPLES.map((q) => `<button type="button" class="btn btn--sm" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
      </div>
    </div>`;
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
        ? `<span class="pill" data-hint="Where the cited span sits inside the source text, counted in Unicode codepoints (start–end).">codepoints ${esc(String(span.start))}–${esc(String(span.end))}</span>`
        : "";
      content.innerHTML = `
        <div class="row" style="margin-bottom:.6rem;">
          <span class="src-badge ${srcClass(c.source)} ask-src">${esc(srcLabel(c.source))}</span>
          ${spanPill}
          ${c.lifecycle ? `<span class="pill" data-hint="Lifecycle state of the source document in governed memory — e.g. active, stale, or tombstoned.">${esc(c.lifecycle.state)}</span>` : ""}
        </div>
        <div class="ask-uri" style="margin:0 0 .6rem;">${esc(c.source_path || c.source_id || "")}</div>
        <pre class="ask-excerpt source-excerpt">${highlight(c.excerpt, c.match_ranges)}</pre>
        <div class="dim" style="font-size:.78rem;margin-top:.5rem;">Claim ${esc(String(c.claim_id || ""))} · Citation ${esc(String(c.citation_id || ""))}</div>`;
    } catch (e) {
      error(content, e);
    }
  }

  /* ---------- run ---------- */

  async function run(opts = {}) {
    if (opts.query != null) currentQuery = String(opts.query).trim();
    if ("scope" in opts) currentScope = normScope(opts.scope);
    if (opts.k != null) {
      const kk = parseInt(opts.k, 10);
      if (Number.isFinite(kk) && kk > 0) currentK = Math.min(kk, 50);
    }
    if (!currentQuery) { renderIdle(); return; }

    const token = ++runToken;
    const query = currentQuery;
    const mode = currentMode;
    stage.setAttribute("aria-busy", "true");
    renderRunHead("running");
    stage.innerHTML = `
      <div class="loading" style="margin-bottom:.6rem;"><span class="spinner"></span> Federating stores, merging &amp; re-ranking evidence…</div>
      ${skeletons(Math.min(currentK, 4))}`;
    const t0 = performance.now();
    try {
      const data = await api.post(mode === "search" ? "/kb.search" : "/kb.ground", {
        query,
        scope: currentScope || undefined,
        k: currentK,
      });
      if (token !== runToken) return; // superseded
      renderRunHead("done", performance.now() - t0);
      const passages = data.passages || [];
      let html = "";
      if (mode === "ground") {
        html += verdictBlock(data);
      } else if (data.answer) {
        html += `<p class="answer ask-answer">${esc(String(data.answer).split(/\n\nCitations:/)[0])}</p>`;
      }
      if (passages.length) {
        const srcs = [...new Set(passages.map((p) => srcLabel(p.source)))];
        html += `<h2 class="section">Evidence <span class="dim" style="font-weight:400;">· ${passages.length} passage${passages.length === 1 ? "" : "s"} from ${srcs.length} source${srcs.length === 1 ? "" : "s"}</span></h2>`;
        html += `<div class="ask-cites" id="askCites">${passages.map((p, i) => citeCard(p, i + 1)).join("")}</div>`;
      } else {
        html += emptyState();
      }
      stage.innerHTML = html;
      bindExamples(stage);
      const cards = Array.from(stage.querySelectorAll(".cite"));
      const reduced = REDUCED();
      cards.forEach((card, i) => {
        bindCard(card, query);
        if (!reduced) {
          card.classList.add("ask-in");
          card.style.animationDelay = (i * 40) + "ms";
        }
      });
      revealMoreButtons(token);
      bus.emit("ask:results", { query, passages });
    } catch (e) {
      if (token !== runToken) return;
      renderRunHead("error");
      error(stage, e);
    } finally {
      if (token === runToken) stage.setAttribute("aria-busy", "false");
    }
  }

  bus.on("ask:run", (payload) => run(payload || {}));

  renderIdle();

  /* deep-link: /ui/?ask=…#ask runs the ask on arrival (demos, shared links) */
  try {
    const preset = new URLSearchParams(location.search).get("ask");
    const onAsk = (location.hash || "").replace(/^#/, "").split("?")[0] === "ask";
    if (preset && preset.trim() && onAsk) run({ query: preset.trim() });
  } catch { /* no URL API — never blocks boot */ }
}
