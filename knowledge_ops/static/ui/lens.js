/* Knowledge-Ops shell — the lens.
   The product's primary control: one bottom-center instrument that is the ask
   bar, the command palette, and the jump-to-doc box at once (it absorbs and
   replaces the old modal palette).

   Grammar:  plain text → Ask (always the first, default result)
             >           → commands only (views, actions, themes)
             @           → documents only
   Keyboard: ⌘K / Ctrl+K or "/" focuses; ↑↓ choose; ↵ runs; Esc closes.
   ARIA combobox: focus stays in the input, options addressed via
   aria-activedescendant. Scope (mine+shared / global / personal / admin-all)
   is a segmented control in the sheet foot, persisted in localStorage. */

import { THEMES, setTheme } from "./theme.js";

const DOC_CACHE_MS = 60000;
const MAX_RESULTS = 9;
const SCOPE_KEY = "ko-scope";

function fuzzy(query, text) {
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  if (!q) return 1;
  let ti = 0, score = 0, streak = 0;
  for (const ch of q) {
    const idx = t.indexOf(ch, ti);
    if (idx < 0) return -1;
    streak = idx === ti ? streak + 1 : 1;
    score += streak + (idx === 0 || /[\s/\-_.]/.test(t[idx - 1]) ? 3 : 0);
    ti = idx + 1;
  }
  return score + Math.max(0, 20 - t.length / 8);
}

export function mountLens(hostEl, ctx) {
  const { api, bus, esc } = ctx;
  const nav = (view) => bus.emit("nav", { view });

  const viewItems = [
    { kind: "View", label: "Home", hint: "the constellation", haystack: "home constellation overview", go: () => nav("home") },
    { kind: "View", label: "Ask", hint: "evidence-cited answers", haystack: "ask question ground evidence search", go: () => nav("ask") },
    { kind: "View", label: "Library", hint: "canonical documents", haystack: "library wiki docs canonical documents", go: () => nav("library") },
    { kind: "View", label: "Review", hint: "imports, staleness, conflicts", haystack: "review queue imports staleness conflicts approve", go: () => nav("review") },
    { kind: "View", label: "Files", hint: "indexed file search", haystack: "files search index deep read", go: () => nav("files") },
    { kind: "View", label: "Sources", hint: "federation status", haystack: "sources federation adapters status mcp tools", go: () => nav("sources") },
    { kind: "View", label: "Graph", hint: "explore the memory graph", haystack: "graph constellation nodes edges explore", go: () => nav("graph") },
    { kind: "View", label: "Operations", hint: "jobs, audit, worker health", haystack: "operations ops metrics audit refresh jobs worker deletion", go: () => nav("ops") },
  ];
  const actionItems = [
    ...THEMES.map((t) => ({
      kind: "Theme", label: t.label, hint: t.hint,
      haystack: `theme ${t.name} ${t.label} ${t.hint}`,
      go: () => setTheme(t.name, bus),
    })),
    {
      kind: "Action", label: "New document", hint: "draft in the Library",
      haystack: "new document create draft write wiki library",
      go: () => { nav("library"); bus.emit("library:new", {}); },
    },
  ];

  let docItems = [];
  let docsAt = 0;
  let docsLoading = false;

  let scope = "";
  try { scope = localStorage.getItem(SCOPE_KEY) || ""; } catch { /* private mode */ }

  /* ---- DOM ------------------------------------------------------------- */

  hostEl.className = "lens";
  hostEl.innerHTML = `
    <div class="lens__sheet" id="koLensSheet" hidden>
      <div class="lens__list" id="koLensList" role="listbox" aria-label="Results"></div>
      <div class="lens__foot">
        <span aria-hidden="true">↵ ask · &gt; commands · @ documents</span>
        <span class="seg" role="group" aria-label="Search scope"></span>
      </div>
      <div class="visually-hidden" aria-live="polite" id="koLensCount"></div>
    </div>
    <div class="lens__pill">
      <span class="lens__glyph" aria-hidden="true">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
          <circle cx="10.5" cy="10.5" r="6.25" stroke="currentColor" stroke-width="1.7"/>
          <path d="M15.3 15.3l4.2 4.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
        </svg>
      </span>
      <input class="lens__input" id="koLensInput" type="text" autocomplete="off" spellcheck="false"
             role="combobox" aria-expanded="false" aria-controls="koLensList" aria-autocomplete="list"
             aria-label="Ask your organization, or jump anywhere"
             placeholder="Ask your organization anything…" />
      <span class="lens__kbd" aria-hidden="true">⌘K</span>
      <button type="button" class="lens__go" id="koLensGo" aria-label="Ask">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M5 12h13M13 6.5l5.5 5.5-5.5 5.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
    </div>`;

  const input = hostEl.querySelector("#koLensInput");
  const goBtn = hostEl.querySelector("#koLensGo");
  const sheet = hostEl.querySelector("#koLensSheet");
  const listEl = hostEl.querySelector("#koLensList");
  const countEl = hostEl.querySelector("#koLensCount");
  const segEl = hostEl.querySelector(".seg");

  /* ---- scope segmented control ------------------------------------------ */

  function scopePairs() {
    const pairs = [["", "Mine+shared"], ["global", "Global"], ["personal", "Personal"]];
    if (ctx.me && ctx.me.is_admin) pairs.push(["all", "All"]);
    return pairs;
  }
  function renderScope() {
    segEl.innerHTML = scopePairs().map(([v, label]) => `
      <button type="button" class="seg__opt" data-scope="${v}" aria-pressed="${v === scope}">${esc(label)}</button>`).join("");
    segEl.querySelectorAll(".seg__opt").forEach((b) => b.addEventListener("click", () => {
      scope = b.dataset.scope;
      try { localStorage.setItem(SCOPE_KEY, scope); } catch { /* private mode */ }
      renderScope();
      input.focus();
    }));
  }
  renderScope();

  /* ---- results ---------------------------------------------------------- */

  let results = [];
  let active = 0;

  function runAsk(query) {
    if (!query) { input.focus(); return; }
    bus.emit("ask:run", { query, scope: scope || null, k: 8 });
    nav("ask");
    closeSheet();
    input.blur();
  }

  function compute() {
    const raw = input.value;
    const q = raw.trim();
    let pool, queryText = q, askFirst = false;
    if (q.startsWith(">")) { pool = [...viewItems, ...actionItems]; queryText = q.slice(1).trim(); }
    else if (q.startsWith("@")) { pool = docItems; queryText = q.slice(1).trim(); }
    else if (!q) { pool = viewItems; }
    else { pool = [...viewItems, ...actionItems, ...docItems]; askFirst = true; }

    const matched = pool
      .map((item) => ({ item, score: fuzzy(queryText, item.haystack || item.label) }))
      .filter((r) => r.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_RESULTS)
      .map((r) => r.item);

    results = askFirst
      ? [{ kind: "Ask", label: `Ask: “${q}”`, hint: "evidence-cited answer", go: () => runAsk(q) }, ...matched]
      : matched;
    active = 0;
    render();
  }

  function render() {
    if (!results.length) {
      listEl.innerHTML = `<div class="empty">No matches — plain text asks the constellation.</div>`;
      input.removeAttribute("aria-activedescendant");
      countEl.textContent = "No results";
      return;
    }
    listEl.innerHTML = results.map((item, i) => `
      <div class="lens__opt" role="option" id="ko-lens-opt-${i}" aria-selected="${i === active}">
        <span class="pill">${esc(item.kind)}</span>
        <span class="lens__label">${esc(item.label)}</span>
        ${item.hint ? `<span class="lens__detail">${esc(item.hint)}</span>` : ""}
      </div>`).join("");
    input.setAttribute("aria-activedescendant", `ko-lens-opt-${active}`);
    countEl.textContent = `${results.length} result${results.length === 1 ? "" : "s"}`;
    Array.from(listEl.children).forEach((el, i) => {
      if (el.getAttribute("role") !== "option") return;
      el.addEventListener("pointermove", () => { if (active !== i) { active = i; markActive(); } });
      el.addEventListener("click", () => select(i));
    });
  }

  function markActive() {
    Array.from(listEl.querySelectorAll("[role=option]")).forEach((el, i) => {
      el.setAttribute("aria-selected", String(i === active));
    });
    input.setAttribute("aria-activedescendant", `ko-lens-opt-${active}`);
    const el = listEl.querySelector(`#ko-lens-opt-${active}`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function select(i) {
    const item = results[i];
    if (!item) return;
    if (item.kind !== "Ask") { closeSheet(); input.value = ""; input.blur(); }
    try { item.go(); } catch (e) { console.warn("[ko:lens] action failed", e); }
  }

  /* ---- docs (lazy, cached) ---------------------------------------------- */

  function refreshDocs() {
    if (docsLoading || Date.now() - docsAt < DOC_CACHE_MS) return;
    docsLoading = true;
    api.get("/kb.docs").then((data) => {
      docItems = (data.docs || []).map((d) => ({
        kind: "Doc",
        label: d.title || d.path || String(d.id),
        hint: d.path || "",
        haystack: `${d.title || ""} ${d.path || ""}`,
        go: () => { nav("library"); bus.emit("library:open", { id: d.id, path: d.path, title: d.title }); },
      }));
      docsAt = Date.now();
      if (!sheet.hidden) compute();
    }).catch(() => { /* not signed in / not entitled — the lens still works */ })
      .finally(() => { docsLoading = false; });
  }

  /* ---- open / close ------------------------------------------------------ */

  function openSheet() {
    if (!sheet.hidden) return;
    sheet.hidden = false;
    hostEl.classList.add("lens--open");
    input.setAttribute("aria-expanded", "true");
    compute();
    refreshDocs();
  }
  function closeSheet() {
    if (sheet.hidden) return;
    sheet.hidden = true;
    hostEl.classList.remove("lens--open");
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  input.addEventListener("focus", openSheet);
  input.addEventListener("input", () => { openSheet(); compute(); });
  goBtn.addEventListener("click", () => runAsk(input.value.trim()));

  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); openSheet(); if (results.length) { active = (active + 1) % results.length; markActive(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (results.length) { active = (active - 1 + results.length) % results.length; markActive(); } }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (!sheet.hidden && results.length) select(active);
      else runAsk(input.value.trim());
    } else if (e.key === "Escape") { e.preventDefault(); closeSheet(); input.blur(); }
  });

  document.addEventListener("pointerdown", (e) => {
    if (!hostEl.contains(e.target)) closeSheet();
  });

  document.addEventListener("keydown", (e) => {
    const dlgOpen = document.querySelector("dialog[open]");
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
      if (dlgOpen) return; // a modal's top layer would trap it
      e.preventDefault();
      if (e.repeat) return;
      input.focus();
      input.select();
    }
    // "/" reaches the lens from anywhere outside a text field
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !dlgOpen) {
      const t = e.target;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (!typing) { e.preventDefault(); input.focus(); input.select(); }
    }
  });

  return {
    focus() { input.focus(); input.select(); },
    close: closeSheet,
  };
}
