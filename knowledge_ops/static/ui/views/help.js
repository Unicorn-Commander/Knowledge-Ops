/* Help — the Help Center + the Guide.
   Three zones:
     ARTICLES — in-file markdown manual (ctx.md) with a quiet filter and a
                contents rail. Deep-linkable: #help?a=<article-id>.
     GUIDE    — "Ask the Guide", a Brigade-backed chat panel. Contract:
                GET /kb.guide.info -> {configured}; GET /kb.guide.agents ->
                [{id,name,description}]; POST /kb.guide.chat
                {agent_id, messages:[{role,content},...]} -> {reply}.
                Anything 404ing (mock, backend not landed) renders the honest
                offline note. 401 -> ctx.error session card; 403 -> locked.
     '?' KEY  — document-level: '?' outside inputs/textareas/contenteditable/
                dialogs jumps to #help and scrolls to the shortcuts article.
   Uses ../hints.js (data-hint attributes; mountHints is idempotent, so the
   shell mounting it globally as well is a no-op) and glyphSvg from
   ../constellation.js for the graph legend. View-local CSS is layout only —
   every color rides a --mu-* token. */

import { glyphSvg } from "../constellation.js";
import { mountHints, hintHtml } from "../hints.js";

/* ------------------------------------------------------------------ articles
   Written to be read: concrete, honest, short. md() renders each body. */

const ARTICLES = [
  {
    id: "what",
    toc: "What is Knowledge-Ops",
    title: "What is Knowledge-Ops",
    body: `
Knowledge-Ops is the organization's governed memory — a read layer across the stores where the org already thinks: the canonical wiki, agent org-memory, meeting transcripts, Stable rooms, indexed files, the relationship graph, and service metrics.

Three promises shape everything here:

- **Evidence, not eloquence.** Answers are retrieved passages with source-level citations. Nothing is generated and then presented as fact.
- **Governance is visible.** Every atom of knowledge carries a scope; you only ever search what you are allowed to read, and locked areas say so plainly instead of pretending to be empty.
- **The federation stays honest.** Sources report real counts and real availability. An empty store says *awaiting population* — never a decorative number.

The constellation behind this page **is** the memory: each star is a knowledge atom, colored and shaped by its source. Ask a question and the stars that answered it ignite.`,
  },
  {
    id: "lens",
    toc: "The lens",
    title: "The lens — ask, > commands, @ docs, scopes",
    body: `
The lens is the floating instrument at the bottom of every page (center-stage on Home). It is three controls in one, chosen by the first character you type:

- **Plain text** — an ask. Press ↵ and the Ask pane opens with cited evidence.
- \`>\` — commands only: jump to any view, switch theme, start a new document.
- \`@\` — documents only: fuzzy-jump straight to a Library doc.

The sheet's footer holds the **search scope**: *Mine + shared* (the default), *Global*, *Personal* — and *All* if you are a knowledge-admin. The choice persists on this device and applies to every ask you run.

Reach the lens from anywhere with **⌘K** (Ctrl+K on other keyboards) or \`/\`. Arrow keys choose, ↵ runs, Esc closes.`,
  },
  {
    id: "ask",
    toc: "Ask & evidence",
    title: "Ask & evidence — what the verdicts mean",
    body: `
Ask runs your question across the federated stores, merges and re-ranks the passages that come back, then labels the result. Two modes sit at the top of every answer:

- **Assess** — retrieval plus a heuristic verdict over the evidence.
- **Passages** — the raw ranked evidence, no verdict.

The verdicts mean exactly this — and no more:

| Verdict | Meaning |
| --- | --- |
| possible support | retrieved wording leans toward your claim |
| possible contradiction | retrieved wording leans against it |
| possible conflict | retrieved sources disagree with each other |
| related evidence | on-topic passages with no clear lean |
| not enough evidence | nothing surfaced that is worth labelling |

Every verdict wears the **heuristic, not verified** pill because that is what it is: lexical and semantic agreement between your words and stored text. It is a reading aid, not fact-checking — retrieval, never verified fact. When the answer matters, do what the instrument is built for: press **Inspect span** on a citation and read the exact source characters it points at.`,
  },
  {
    id: "library",
    toc: "Library & wikilinks",
    title: "Library & wikilinks — red links, backlinks, versions",
    body: `
The Library is the canonical, versioned wiki: the store the org deliberately writes, distinct from everything the observatory merely watches.

- **Wikilinks** — write \`[[Target]]\` or \`[[Target|shown label]]\` in any doc; links resolve across the whole library.
- **Red links** — a wikilink to a doc that does not exist yet renders red. Click it to create the missing page on the spot; the *wanted pages* panel keeps that backlog honest.
- **Backlinks** — every doc lists the docs that cite it, so the library grows as a web instead of a pile.
- **Versions** — every save is kept. The version timeline shows a doc's full history, and any prior version can be opened and read.

Docs are markdown. Links are internal only — this instrument does not reach out to the public internet.`,
  },
  {
    id: "graph",
    toc: "The 3D graph",
    title: "The 3D graph — orbit, pan, zoom, glyphs",
    body: `
The Graph view is the constellation at full brightness — the same substrate that lives behind every page, opened up as a true 3D instrument. Sources sit in their own depth bands, so the memory reads as layered space rather than a flat scatter.

- **Drag** orbits the camera around the field.
- **Shift + drag** pans.
- **Wheel** zooms.
- **Search** — type in the graph's search box; ↵ cycles through the matches.
- On Home the camera auto-orbits gently until you touch it.

Every source carries a **glyph** as well as a color, so the map survives monochrome screenshots and color-blindness:`,
    extra: "GLYPH_LEGEND",
    after: `Stars are real atoms from the federated stores. The faint ambient background field is decoration and is never labelled or counted as data.`,
  },
  {
    id: "scopes",
    toc: "Scopes & governance",
    title: "Scopes & governance — and what locked means",
    body: `
Every knowledge atom carries a scope:

- **global** — readable by the whole organization.
- **personal** — yours alone.
- **restricted** — shared with a defined audience, invisible to everyone else.

Retrieval is governed at the source: an ask only searches atoms your session may read, and the scope control in the lens narrows that further. Results wear scope pills on anything that is not global.

**What locked means.** Some areas — Review, Operations, destructive document actions — require the *knowledge-admin* role. Without it you see a quiet locked card. That is not an error and not a session problem: your sign-in and your evidence access are untouched; the area simply belongs to administrators.`,
  },
  {
    id: "sources",
    toc: "Sources & federation",
    title: "Sources & federation",
    body: `
Knowledge-Ops owns almost none of its knowledge. It federates stores that already exist and answers only from what they actually hold:

- **Semantic** — vector collections: org-memory, meetings, Stable rooms, wiki embeddings.
- **Canonical** — the versioned wiki you read in the Library.
- **Files** — chunk-indexed documents under approved roots.
- **Graph** — the relationship fabric of entities and links.
- **Metrics** — service and quality signals.

The Sources view is the instrument board for all of this: live counts, availability, last-sync times, and the registered MCP tool contracts. A configured store with nothing in it reports **awaiting population** — the observatory does not invent numbers to look busy.`,
  },
  {
    id: "admin",
    toc: "Operations & Review",
    title: "Operations & Review (admin)",
    body: `
Two views belong to knowledge administrators; everyone else sees the locked card there.

**Review** is the intake airlock. Staged imports, staleness flags, and conflicting-passage findings queue here, and an admin approves or rejects each item with the findings in view. Nothing enters the canon silently.

**Operations** is the engine room: refresh jobs and their runs, worker health, the audit trail, the deletion outbox, and service metrics.

Every consequential act — approvals, rejections, deletions, imports — lands in the audit log with an actor and a timestamp.`,
  },
  {
    id: "themes",
    toc: "Themes",
    title: "Themes — five skies + ?theme= deep link",
    body: `
Five skies, one instrument. Switch from the theme menu in the top bar, or through the lens with \`>\`:

- **Aurora** — the deep-space default: indigo air, luminous data, brass fittings.
- **Daylight** — the chart room: ink and gold on warm paper.
- **Porcelain** — the gallery: cool white light, pewter fittings.
- **Nebula** — the demo reel: saturated space, maximum glow.
- **Midnight** — lights out: true-black OLED quiet.

Deep-link any of them with \`?theme=\` in the URL — for example \`/ui/?theme=porcelain\` — and the choice persists on that device. First visits follow your system's light or dark preference.`,
  },
  {
    id: "shortcuts",
    toc: "Keyboard shortcuts",
    title: "Keyboard shortcuts",
    body: `
| Keys | Does |
| --- | --- |
| ⌘K / Ctrl+K | focus the lens from anywhere |
| \`/\` | focus the lens (outside a text field) |
| ↑ ↓ | choose in the lens sheet |
| ↵ | run the selection — or ask |
| Esc | close the lens sheet, dialogs, and hints |
| \`>\` | lens: commands only |
| \`@\` | lens: documents only |
| ↵ in graph search | cycle through matches |
| \`?\` | open this help |

In the Library's document tree, ↑ ↓ walk the docs, ← → collapse and expand folders, Home and End jump to the edges.`,
  },
  {
    id: "api",
    toc: "MCP & API",
    title: "Automate it: MCP & API",
    body: `
Everything this UI does rides a governed HTTP surface you can automate against directly.

**MCP** — \`POST /mcp\` is a JSON-RPC 2.0 endpoint (initialize, tools/list, tools/call). The manifest at \`/.well-known/mcps.json\` advertises the catalog under the \`knowledge\` namespace: search and ground (evidence with citations), the document tools (list, get, upsert, history), file search and deep-read, citation inspection, and the graph / links / stats read surface. The **Sources** view renders the live manifest tool-by-tool — that page is always the authoritative list for your deployment.

**REST** — the same \`/kb.*\` routes the UI calls speak plain JSON behind your session: \`/kb.search\`, \`/kb.ground\`, \`/kb.docs\`, \`/kb.graph\`, \`/kb.links\`, \`/kb.stats\`, and friends.

Two things never change across either surface: your identity's scopes govern every result, and answers are retrieval with citations — an agent gets exactly the same honesty a human does.`,
  },
];

const GLYPH_LEGEND = [
  ["wiki", "circle", "wiki"],
  ["org-memory", "diamond", "org-memory"],
  ["meetings", "triangle", "meetings"],
  ["stable", "square", "stable"],
  ["files", "pentagon", "files"],
  ["graph", "star", "graph entities"],
  ["metrics", "hexagon", "metrics"],
];

/* ------------------------------------------------------------------ styles */

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  /* layout only — every color is a --mu-* token or a base.css class */
  st.textContent = `
#view-help [hidden]{display:none!important}
#view-help .help-grid{display:grid;grid-template-columns:14.5rem minmax(0,1fr);gap:1.9rem;align-items:start;margin-top:1.1rem}
#view-help .help-side{position:sticky;top:calc(var(--mu-bar-h) + 1.2rem);display:flex;flex-direction:column;gap:.75rem;min-width:0}
#view-help .help-toc{display:flex;flex-direction:column;gap:.1rem}
#view-help .help-toc a{display:flex;align-items:center;min-height:44px;padding:.3rem .6rem;border-radius:var(--mu-radius-sm);color:var(--mu-color-muted);font-size:.88rem;line-height:1.25}
#view-help .help-toc a:hover{background:var(--mu-color-surface-strong);color:var(--mu-color-text);text-decoration:none}
#view-help .help-toc a.help-toc__guide{color:var(--mu-color-accent);border-top:1px solid var(--mu-hairline);border-radius:0 0 var(--mu-radius-sm) var(--mu-radius-sm);margin-top:.35rem;padding-top:.5rem}
#view-help .help-body{min-width:0}
#view-help .help-art{scroll-margin-top:calc(var(--mu-bar-h) + 1.3rem);padding:0 0 1.5rem;border-bottom:1px solid var(--mu-hairline);margin:0 0 1.5rem}
#view-help .help-art:last-of-type{border-bottom:0}
#view-help .help-art__h{margin-top:0;scroll-margin-top:calc(var(--mu-bar-h) + 1.3rem)}
#view-help .help-art .md{max-width:66ch}
#view-help .help-legend{display:flex;flex-wrap:wrap;gap:.45rem;margin:.9rem 0 .3rem}
#view-help .help-legend .pill{min-height:2rem}
#view-help .help-legend__g{display:inline-flex;flex:none}
/* guide */
#view-help .help-guide{scroll-margin-top:calc(var(--mu-bar-h) + 1.3rem);margin-top:.4rem}
#view-help .help-guide__head{display:flex;align-items:baseline;gap:.55rem;flex-wrap:wrap;margin-bottom:.15rem}
#view-help .help-agents{display:flex;gap:.7rem;align-items:center;flex-wrap:wrap;margin:.7rem 0 .2rem}
#view-help .help-agents .select{width:auto;min-width:12rem;flex:0 1 auto}
#view-help .help-agents__desc{font-size:.84rem;min-width:0}
#view-help .help-log{display:flex;flex-direction:column;gap:.7rem;max-height:24rem;overflow:auto;padding:.85rem .15rem;margin:.4rem 0}
#view-help .help-bubble{max-width:56ch;padding:.55rem .85rem;border-radius:var(--mu-radius-md);overflow-wrap:anywhere}
#view-help .help-bubble--user{align-self:flex-end;white-space:pre-wrap;background:color-mix(in srgb, var(--mu-color-primary) 15%, transparent);box-shadow:inset 0 0 0 1px color-mix(in srgb, var(--mu-color-primary) 30%, transparent)}
#view-help .help-bubble--assistant{align-self:flex-start;background:var(--mu-color-surface);box-shadow:inset 0 0 0 1px var(--mu-hairline)}
#view-help .help-bubble .md{max-width:none}
#view-help .help-bubble .md > :first-child{margin-top:0}
#view-help .help-bubble .md > :last-child{margin-bottom:0}
#view-help .help-hello{padding:.4rem .15rem}
#view-help .help-err:empty{display:none}
#view-help .help-err{margin:.4rem 0}
#view-help .help-sendrow{display:flex;gap:.6rem;align-items:flex-end;margin-top:.5rem}
#view-help .help-sendrow .textarea{flex:1;min-height:44px;height:44px;max-height:9rem;resize:vertical;line-height:1.45}
#view-help .help-sendrow .btn{flex:none}
@media (max-width:880px){
  #view-help .help-grid{display:block}
  #view-help .help-side{position:static;margin:0 0 1.3rem}
  #view-help .help-toc{flex-direction:row;flex-wrap:wrap;gap:.4rem}
  #view-help .help-toc a{box-shadow:inset 0 0 0 1px var(--mu-hairline);border-radius:var(--mu-radius-pill);padding:.3rem .85rem}
  #view-help .help-toc a.help-toc__guide{border-top:0;margin-top:0;padding-top:.3rem;border-radius:var(--mu-radius-pill)}
  #view-help .help-log{max-height:19rem}
}`;
  document.head.appendChild(st);
}

/* ------------------------------------------------- global '?' (added once) */

let helpKeyWired = false;
let gotoArticleRef = null; // bound by init; the key handler goes through it

function wireGlobalHelpKey() {
  if (helpKeyWired) return;
  helpKeyWired = true;
  document.addEventListener("keydown", (e) => {
    if (e.key !== "?" || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if (document.querySelector("dialog[open]")) return;
    e.preventDefault();
    if (gotoArticleRef) gotoArticleRef("shortcuts");
    else location.hash = "#help";
  });
}

/* ---------------------------------------------------------------- the view */

export function init(section, ctx) {
  const { api, esc, md, error } = ctx;
  injectStyles();
  mountHints(); // idempotent — a no-op once the shell mounts hints globally

  const REDUCED = () =>
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- static shell ---- */

  const legendHtml = `
    <div class="help-legend" role="img"
         aria-label="Glyph legend: circle wiki, diamond org-memory, triangle meetings, square stable, pentagon files, star graph entities, hexagon metrics">
      ${GLYPH_LEGEND.map(([key, shape, label]) => `
        <span class="pill" data-hint="${esc(`${shape} = ${label}`)}"><span class="help-legend__g src--${esc(key)}" aria-hidden="true">${glyphSvg(key)}</span>${esc(label)}</span>`).join("")}
    </div>`;

  const articleHtml = (a) => `
    <article class="help-art" id="help-art-${esc(a.id)}" data-art="${esc(a.id)}">
      <h2 class="section help-art__h" tabindex="-1">${esc(a.title)}</h2>
      <div class="md">${md(a.body)}</div>
      ${a.extra === "GLYPH_LEGEND" ? legendHtml : ""}
      ${a.after ? `<div class="md">${md(a.after)}</div>` : ""}
    </article>`;

  section.innerHTML = `
    <p class="kicker">Help</p>
    <h1 class="title">The observatory, explained.</h1>
    <p class="lede">Short, honest articles about every instrument here — and a Guide you can ask when reading is not enough. Press <span class="mono">?</span> anywhere to come back.</p>

    <div class="help-grid">
      <aside class="help-side" aria-label="Help contents">
        <input class="input" id="helpFilter" type="search" autocomplete="off" spellcheck="false"
               aria-label="Filter help articles" placeholder="Filter articles…"
               data-hint="Type to narrow the articles — matching runs over titles and full text." data-hint-title="Filter" />
        <nav class="help-toc" id="helpToc" aria-label="Articles">
          ${ARTICLES.map((a) => `<a href="#help?a=${esc(a.id)}" data-goto="${esc(a.id)}">${esc(a.toc)}</a>`).join("")}
          <a href="#help?a=guide" data-goto="guide" class="help-toc__guide">Ask the Guide</a>
        </nav>
        <div class="visually-hidden" role="status" aria-live="polite" id="helpFilterCount"></div>
      </aside>

      <div class="help-body">
        <div id="helpArticles">${ARTICLES.map(articleHtml).join("")}</div>
        <div class="empty" id="helpNoMatch" hidden>
          <span class="big">No article matches.</span>
          <p style="margin:0;">Clear the filter — or put the question to the Guide below.</p>
        </div>

        <section class="help-guide card card--strong" id="help-art-guide" aria-label="Ask the Guide">
          <p class="kicker">Guide</p>
          <div class="help-guide__head">
            <h2 class="section section--flush help-art__h" tabindex="-1">Ask the Guide</h2>
            <span class="dim">— powered by Unicorn Brigade</span>
            ${hintHtml("The Guide is a Brigade agent that answers questions about this deployment. Same governance as any ask: it only sees what your session can read.", "Ask the Guide")}
          </div>
          <div id="helpGuideBody" aria-live="polite" aria-busy="true">
            <div class="loading"><span class="spinner" aria-hidden="true"></span> checking for the Guide…</div>
          </div>
        </section>
      </div>
    </div>`;

  const filterEl = section.querySelector("#helpFilter");
  const tocEl = section.querySelector("#helpToc");
  const countEl = section.querySelector("#helpFilterCount");
  const noMatchEl = section.querySelector("#helpNoMatch");
  const guideBody = section.querySelector("#helpGuideBody");

  /* ---- filter ---- */

  const haystacks = new Map(
    ARTICLES.map((a) => [a.id, `${a.toc} ${a.title} ${a.body} ${a.after || ""}`.toLowerCase()]),
  );

  function applyFilter() {
    const q = filterEl.value.trim().toLowerCase();
    let shown = 0;
    for (const a of ARTICLES) {
      const hit = !q || haystacks.get(a.id).includes(q);
      const art = section.querySelector(`#help-art-${a.id}`);
      const link = tocEl.querySelector(`a[data-goto="${a.id}"]`);
      if (art) art.hidden = !hit;
      if (link) link.hidden = !hit;
      if (hit) shown += 1;
    }
    noMatchEl.hidden = shown > 0;
    countEl.textContent = q
      ? `${shown} of ${ARTICLES.length} articles match`
      : `${ARTICLES.length} articles`;
  }
  filterEl.addEventListener("input", applyFilter);
  applyFilter();

  /* ---- article navigation (#help?a=<id> deep links) ---- */

  let lastA = null;

  function scrollToArticle(id, tries = 0) {
    const el = section.querySelector(`#help-art-${CSS.escape(String(id))}`);
    if (!el) return;
    if (section.hidden) {
      // route transition still settling — retry briefly, then give up quietly
      if (tries < 20) setTimeout(() => scrollToArticle(id, tries + 1), 60);
      return;
    }
    if (el.hidden) { filterEl.value = ""; applyFilter(); } // filtered out -> unfilter
    el.scrollIntoView({ behavior: REDUCED() ? "auto" : "smooth", block: "start" });
    const h = el.querySelector(".help-art__h");
    if (h) h.focus({ preventScroll: true });
  }

  function gotoArticle(id) {
    lastA = id;
    const want = "#help?a=" + encodeURIComponent(id);
    if (location.hash !== want) location.hash = want; // route stays "help"
    scrollToArticle(id);
  }
  gotoArticleRef = gotoArticle;

  function articleFromHash() {
    const m = /^#help\?(.*)$/.exec(location.hash || "");
    if (!m) return null;
    try { return new URLSearchParams(m[1]).get("a"); } catch { return null; }
  }

  tocEl.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[data-goto]");
    if (!a) return;
    e.preventDefault();
    gotoArticle(a.dataset.goto);
  });

  window.addEventListener("hashchange", () => {
    const a = articleFromHash();
    if (a && a !== lastA) { lastA = a; scrollToArticle(a); }
    if (!a) lastA = null;
  });

  const a0 = articleFromHash();
  if (a0) { lastA = a0; scrollToArticle(a0); }

  wireGlobalHelpKey();

  /* ---- the Guide ---- */

  const OFFLINE_NOTE =
    "The Guide is not connected on this deployment yet — the articles above cover everything.";

  function renderOffline() {
    guideBody.setAttribute("aria-busy", "false");
    guideBody.innerHTML = `
      <p class="dim" style="margin:.5rem 0 .8rem; max-width:60ch;">${esc(OFFLINE_NOTE)}</p>
      <div class="help-sendrow">
        <textarea class="textarea" rows="1" disabled aria-label="Message the Guide (unavailable)"
                  placeholder="Guide offline on this deployment"></textarea>
        <button type="button" class="btn" disabled>Send</button>
      </div>`;
  }

  function renderChat(agents) {
    guideBody.setAttribute("aria-busy", "false");
    if (!agents.length) { renderOffline(); return; }
    guideBody.innerHTML = `
      <div class="help-agents">
        <label class="lbl" for="helpAgentSel">Agent</label>
        <select class="select" id="helpAgentSel" data-hint="Pick which Brigade agent answers — each is briefed for a different depth of question.">
          ${agents.map((a) => `<option value="${esc(String(a.id))}">${esc(a.name || String(a.id))}</option>`).join("")}
        </select>
        <span class="dim help-agents__desc" id="helpAgentDesc"></span>
      </div>
      <div class="help-log" id="helpLog" role="log" aria-live="polite" aria-label="Guide conversation">
        <p class="dim help-hello" style="margin:0;">Ask anything about the observatory — the Guide answers from this deployment's docs and manifest.</p>
      </div>
      <div class="help-err" id="helpGuideErr"></div>
      <div class="help-sendrow">
        <textarea class="textarea" id="helpMsg" rows="1" aria-label="Message the Guide"
                  placeholder="Ask the Guide…  (↵ sends · Shift+↵ new line)"></textarea>
        <button type="button" class="btn btn--primary" id="helpSend">Send</button>
      </div>`;

    const sel = guideBody.querySelector("#helpAgentSel");
    const desc = guideBody.querySelector("#helpAgentDesc");
    const log = guideBody.querySelector("#helpLog");
    const errBox = guideBody.querySelector("#helpGuideErr");
    const msgEl = guideBody.querySelector("#helpMsg");
    const sendBtn = guideBody.querySelector("#helpSend");

    const syncDesc = () => {
      const a = agents.find((x) => String(x.id) === sel.value);
      desc.textContent = (a && a.description) || "";
    };
    sel.addEventListener("change", syncDesc);
    syncDesc();

    const messages = []; // full [{role, content}] history, sent on every turn
    let pending = false;

    function renderLog(thinking) {
      log.innerHTML = messages.map((m) => m.role === "user"
        ? `<div class="help-bubble help-bubble--user">${esc(m.content)}</div>`
        : `<div class="help-bubble help-bubble--assistant"><div class="md">${md(m.content)}</div></div>`,
      ).join("") + (thinking
        ? `<div class="help-bubble help-bubble--assistant"><span class="loading"><span class="spinner" aria-hidden="true"></span> thinking…</span></div>`
        : "");
      log.scrollTop = log.scrollHeight;
    }

    async function send() {
      const text = msgEl.value.trim();
      if (!text || pending) return;
      pending = true;
      sendBtn.disabled = true;
      errBox.innerHTML = "";
      msgEl.value = "";
      messages.push({ role: "user", content: text });
      renderLog(true);
      try {
        const data = await api.post("/kb.guide.chat", { agent_id: sel.value, messages });
        messages.push({ role: "assistant", content: String((data && data.reply) || "") });
        renderLog(false);
      } catch (e) {
        // confirm on success only: roll the turn back, restore the draft
        messages.pop();
        msgEl.value = text;
        renderLog(false);
        error(errBox, e);
      } finally {
        pending = false;
        sendBtn.disabled = false;
        msgEl.focus();
      }
    }

    sendBtn.addEventListener("click", send);
    msgEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
  }

  async function initGuide() {
    let info = null;
    try {
      info = await api.get("/kb.guide.info");
    } catch (e) {
      if (e && (e.status === 401 || e.status === 403)) {
        guideBody.setAttribute("aria-busy", "false");
        error(guideBody, e); // 401 session card / 403 locked, per shell contract
        return;
      }
      renderOffline(); // 404 (backend not landed / mock) or unreachable
      return;
    }
    if (!info || !info.configured) { renderOffline(); return; }

    try {
      const res = await api.get("/kb.guide.agents");
      const agents = Array.isArray(res) ? res : (res && res.agents) || [];
      renderChat(agents);
    } catch (e) {
      if (e && e.status === 404) { renderOffline(); return; }
      guideBody.setAttribute("aria-busy", "false");
      error(guideBody, e);
    }
  }

  initGuide();
}
