/* Knowledge-Ops shell — boots the Constellation UI.
   Builds ctx {api, bus, esc, md, toast, locked, error, me}, routes on hash,
   and hands each view its <section>. A view that fails to load or throws on
   init never takes down the shell. Vanilla ES modules; no build step. */

import { api, KoErr } from "./api.js";
import { createBus } from "./bus.js";
import { initTheme, mountThemeMenu } from "./theme.js";
import { createPalette } from "./palette.js";
import { md, esc } from "./md.js";

initTheme(); // apply persisted / preferred theme before anything renders

const bus = createBus();
const VIEWS = ["home", "ask", "library", "review", "files", "sources", "graph", "ops"];
const $ = (sel, root = document) => root.querySelector(sel);

/* ---------- toast ---------- */
function toast(kind, msg) {
  if (msg == null) { msg = kind; kind = ""; } // tolerate toast(msg)
  const host = $("#toast");
  if (!host) return;
  const t = document.createElement("div");
  t.className = "toast__item" + (kind ? ` ${kind} toast__item--${kind}` : "");
  t.textContent = String(msg);
  host.appendChild(t);
  setTimeout(() => { t.classList.add("is-leaving"); setTimeout(() => t.remove(), 300); }, 3600);
}

/* ---------- 403 locked state (quiet — never a session problem, never red) */
function locked(el, what) {
  el.innerHTML = `
    <div class="card locked" role="note"
         style="border:1px dashed var(--mu-color-border-strong); background:transparent;">
      <div class="row" style="align-items:flex-start; flex-wrap:nowrap;">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"
             style="flex:none; margin-top:.15rem; color:var(--mu-color-dim);">
          <rect x="4.75" y="10.75" width="14.5" height="9.5" rx="2" stroke="currentColor" stroke-width="1.5"/>
          <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" stroke="currentColor" stroke-width="1.5"/>
          <circle cx="12" cy="15.5" r="1.4" fill="currentColor"/>
        </svg>
        <div style="min-width:0;">
          <div class="row" style="margin:0 0 .3rem;"><strong>Requires knowledge-admin</strong><span class="pill">locked</span></div>
          <p class="dim" style="margin:0;">${esc(what || "This area")} is reserved for knowledge administrators — your session and evidence access are unaffected.</p>
        </div>
      </div>
    </div>`;
}

/* ---------- error rendering (branches 401 vs 403 vs other) ---------- */
function error(el, err) {
  const status = err instanceof KoErr ? err.status : err && err.status;
  if (status === 401) {
    el.innerHTML = `
      <div class="card" role="alert">
        <strong>Session expired</strong>
        <p class="dim" style="margin:.35rem 0 .7rem;">Your sign-in lapsed. Reload to go back through the gateway.</p>
        <button type="button" class="btn">Reload</button>
      </div>`;
    el.querySelector("button").addEventListener("click", () => location.reload());
    return;
  }
  if (status === 403) { locked(el, err && err.what); return; }
  el.innerHTML = `<div class="error">${esc((err && err.message) || "Something went wrong.")}</div>`;
}

/* ---------- ctx ---------- */
const ctx = { api, bus, esc, md, toast, locked, error, me: null };

/* ---------- hash router ---------- */
let activeView = null;
function currentView() {
  // Views may carry a query suffix (e.g. #library?path=...) — route on the bare name.
  const h = (location.hash || "#home").slice(1).split("?")[0];
  return VIEWS.includes(h) ? h : "home";
}
function route() {
  const name = currentView();
  if (name === activeView) return;
  activeView = name;
  document.documentElement.dataset.route = name; // design hook: chromeless home
  for (const v of VIEWS) {
    const section = document.getElementById("view-" + v);
    if (section) section.hidden = v !== name;
  }
  document.querySelectorAll(".side__btn").forEach((a) => {
    const on = a.dataset.view === name;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  bus.emit("nav", { view: name });
}

/* ---------- status pulse (/ready every 30s) ---------- */
async function pulse() {
  const dot = $("#statusDot"), txt = $("#statusText");
  if (!dot || !txt) return;
  try {
    await api.get("/ready");
    dot.className = "dot dot--ok";
    txt.textContent = "Ready";
  } catch (e) {
    dot.className = "dot dot--bad";
    txt.textContent = e && e.status === 401 ? "Signed out" : "Offline";
  }
}

/* ---------- view loading (resilient: one broken view never sinks boot) */
const LOADERS = {
  home: () => import("./constellation.js").then((m) => m.initHome),
  graph: () => import("./constellation.js").then((m) => m.initGraph),
  ask: () => import("./views/ask.js").then((m) => m.init),
  library: () => import("./views/library.js").then((m) => m.init),
  review: () => import("./views/review.js").then((m) => m.init),
  files: () => import("./views/files.js").then((m) => m.init),
  sources: () => import("./views/sources.js").then((m) => m.init),
  ops: () => import("./views/ops.js").then((m) => m.init),
};

async function bootView(name) {
  const section = document.getElementById("view-" + name);
  if (!section) return;
  try {
    const init = await LOADERS[name]();
    if (typeof init !== "function") throw new Error(`view "${name}" exports no init()`);
    await init(section, ctx);
  } catch (e) {
    console.warn(`[ko] view "${name}" failed to boot`, e);
    section.innerHTML = `<div class="error">This view failed to load — see the browser console for details.</div>`;
  }
}

/* ---------- boot ---------- */
async function boot() {
  // Who am I? Optional endpoint — degrade to null on 404/401/anything.
  try { ctx.me = await api.get("/kb.me"); } catch { ctx.me = null; }

  // Header chrome
  const palette = createPalette(ctx);
  const searchBtn = $("#globalSearchBtn");
  if (searchBtn) searchBtn.addEventListener("click", () => palette.open());
  const themeHost = $("#themeMenuHost");
  if (themeHost) mountThemeMenu(themeHost, bus);

  // Citation dialog (shared by ask/files views)
  const dlg = $("#citationDialog");
  if (dlg) {
    const closeBtn = $("#citationClose");
    if (closeBtn) closeBtn.addEventListener("click", () => dlg.close());
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  }

  // Routing: show the initial view immediately, then wire hashchange and the
  // bus 'nav' event (emitting nav with a new view navigates there).
  route();
  window.addEventListener("hashchange", route);
  bus.on("nav", ({ view }) => {
    if (VIEWS.includes(view) && view !== currentView()) location.hash = "#" + view;
  });

  // Boot every view in parallel; each is isolated.
  await Promise.all(VIEWS.map(bootView));

  // Service heartbeat
  pulse();
  setInterval(pulse, 30000);
}

boot().catch((e) => {
  console.error("[ko] shell boot failed", e);
  toast("err", "The shell failed to boot — see the console.");
});
