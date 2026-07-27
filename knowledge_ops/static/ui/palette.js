/* Knowledge-Ops shell — command palette (Cmd+K / Ctrl+K).
   Fuzzy search across views, actions (theme switch, new doc), and library
   documents (lazy kb.docs fetch, cached 60s). ARIA combobox pattern: focus
   stays in the input; options are addressed via aria-activedescendant. */

import { THEMES, setTheme } from "./theme.js";

const DOC_CACHE_MS = 60000;
const MAX_RESULTS = 14;

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

export function createPalette(ctx) {
  const { api, bus, esc } = ctx;
  const nav = (view) => bus.emit("nav", { view });

  const staticItems = [
    { kind: "View", label: "Home", hint: "the constellation", haystack: "home constellation overview", go: () => nav("home") },
    { kind: "View", label: "Ask", hint: "evidence-cited answers", haystack: "ask question ground evidence search", go: () => nav("ask") },
    { kind: "View", label: "Library", hint: "canonical documents", haystack: "library wiki docs canonical documents", go: () => nav("library") },
    { kind: "View", label: "Review", hint: "imports, staleness, conflicts", haystack: "review queue imports staleness conflicts approve", go: () => nav("review") },
    { kind: "View", label: "Files", hint: "indexed file search", haystack: "files search index deep read", go: () => nav("files") },
    { kind: "View", label: "Sources", hint: "federation status", haystack: "sources federation adapters status mcp tools", go: () => nav("sources") },
    { kind: "View", label: "Graph", hint: "explore the memory graph", haystack: "graph constellation nodes edges explore", go: () => nav("graph") },
    { kind: "View", label: "Operations", hint: "jobs, audit, worker health", haystack: "operations ops metrics audit refresh jobs worker deletion", go: () => nav("ops") },
    ...THEMES.map((t) => ({
      kind: "Action", label: `Theme: ${t.label}`, hint: t.hint,
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

  // ---- DOM ------------------------------------------------------------
  const root = document.createElement("div");
  root.className = "ko-palette";
  root.hidden = true;
  root.innerHTML = `
    <div class="ko-palette__scrim"></div>
    <div class="ko-palette__panel card card--strong" role="dialog" aria-modal="true" aria-label="Command palette">
      <div class="field" style="margin:0;">
        <label class="lbl" for="koPaletteInput">Jump to a view, action, or document</label>
        <input class="input" id="koPaletteInput" type="text" autocomplete="off" spellcheck="false"
               role="combobox" aria-expanded="true" aria-controls="koPaletteList" aria-autocomplete="list"
               placeholder="Type to search… (Esc to close)" />
      </div>
      <div class="list ko-palette__list" id="koPaletteList" role="listbox" aria-label="Results"></div>
      <div class="visually-hidden" aria-live="polite" id="koPaletteCount"></div>
    </div>`;
  document.body.appendChild(root);

  const input = root.querySelector("#koPaletteInput");
  const listEl = root.querySelector("#koPaletteList");
  const countEl = root.querySelector("#koPaletteCount");
  const scrim = root.querySelector(".ko-palette__scrim");

  let results = [];
  let active = 0;
  let lastFocus = null;

  // ---- rendering ------------------------------------------------------
  function compute() {
    const q = input.value.trim();
    const pool = q ? [...staticItems, ...docItems] : staticItems;
    results = pool
      .map((item) => ({ item, score: fuzzy(q, item.haystack || item.label) }))
      .filter((r) => r.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_RESULTS)
      .map((r) => r.item);
    active = 0;
    render();
  }

  function render() {
    if (!results.length) {
      listEl.innerHTML = `<div class="empty">No matches — try a view name or a document title.</div>`;
      input.removeAttribute("aria-activedescendant");
      countEl.textContent = "No results";
      return;
    }
    listEl.innerHTML = results.map((item, i) => `
      <div class="item ko-palette__opt" role="option" id="ko-opt-${i}" aria-selected="${i === active}">
        <span class="pill">${esc(item.kind)}</span>
        <span class="ko-palette__label">${esc(item.label)}</span>
        ${item.hint ? `<span class="ko-palette__detail">${esc(item.hint)}</span>` : ""}
      </div>`).join("");
    input.setAttribute("aria-activedescendant", `ko-opt-${active}`);
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
    input.setAttribute("aria-activedescendant", `ko-opt-${active}`);
    const el = listEl.querySelector(`#ko-opt-${active}`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function select(i) {
    const item = results[i];
    if (!item) return;
    close();
    try { item.go(); } catch (e) { console.warn("[ko:palette] action failed", e); }
  }

  // ---- docs (lazy, cached) --------------------------------------------
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
      if (!root.hidden) compute();
    }).catch(() => { /* not signed in / not entitled — palette still works */ })
      .finally(() => { docsLoading = false; });
  }

  // ---- open / close ---------------------------------------------------
  function open() {
    if (!root.hidden) return;
    lastFocus = document.activeElement;
    root.hidden = false;
    input.value = "";
    compute();
    refreshDocs();
    input.focus();
  }
  function close() {
    if (root.hidden) return;
    root.hidden = true;
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    lastFocus = null;
  }
  function toggle() { root.hidden ? open() : close(); }

  // ---- keyboard -------------------------------------------------------
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
      // A showModal() dialog's top layer would make the palette inert — don't
      // open it underneath (input.focus() would fail and Esc couldn't reach it).
      if (document.querySelector("dialog[open]")) return;
      e.preventDefault();
      if (!e.repeat) toggle();
    }
    // Escape closes the palette wherever focus landed (panel padding, list
    // scrollbar, body) — the input's own handler covers the common path first.
    if (e.key === "Escape" && !root.hidden) {
      e.preventDefault();
      close();
    }
  });

  input.addEventListener("input", compute);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (results.length) { active = (active + 1) % results.length; markActive(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (results.length) { active = (active - 1 + results.length) % results.length; markActive(); } }
    else if (e.key === "Home" && !input.value) { e.preventDefault(); active = 0; markActive(); }
    else if (e.key === "End" && !input.value) { e.preventDefault(); active = Math.max(0, results.length - 1); markActive(); }
    else if (e.key === "Enter") { e.preventDefault(); select(active); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") { e.preventDefault(); /* combobox keeps focus in the input */ }
  });

  scrim.addEventListener("click", close);

  return { open, close, toggle };
}
