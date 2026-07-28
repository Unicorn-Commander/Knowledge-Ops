/* Knowledge-Ops shell v2 — boots the Observatory.
   Owns the sky (one constellation substrate behind every route), the lens,
   the bar readout, and the router. Builds ctx {api, bus, esc, md, toast,
   locked, error, me, sky, lens} and hands each view its <section>.
   Route changes ride the View Transitions API when available; the sky's
   camera and grade are choreographed per route. A view that fails to load
   or throws on init never takes down the shell. Vanilla ES modules. */

import { api, KoErr } from "./api.js";
import { createBus } from "./bus.js";
import { initTheme, mountThemeMenu } from "./theme.js";
import { initSky, initHome, initGraph } from "./constellation.js";
import { mountLens } from "./lens.js";
import { md, esc } from "./md.js";

initTheme(); // apply persisted / preferred / ?theme= before anything renders

const bus = createBus();
const VIEWS = ["home", "ask", "library", "review", "files", "sources", "graph", "ops", "help"];
const VIEW_LABEL = {
  home: "", ask: "Ask", library: "Library", review: "Review",
  files: "Files", sources: "Sources", graph: "Graph", ops: "Operations", help: "Help",
};
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
         style="border:1px dashed var(--mu-color-border-strong); background:transparent; box-shadow:none;">
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
const ctx = { api, bus, esc, md, toast, locked, error, me: null, sky: null, lens: null };

/* ---------- hash router (view transitions + sky choreography) ---------- */
let activeView = null;

function currentView() {
  // Views may carry a query suffix (e.g. #library?path=...) — route on the bare name.
  const h = (location.hash || "#home").slice(1).split("?")[0];
  return VIEWS.includes(h) ? h : "home";
}

function skyModeFor(name) {
  if (name === "home") return "hero";
  if (name === "graph") return "immersive";
  return "recede";
}

function applyRoute(name) {
  document.documentElement.dataset.route = name;
  const bc = $("#barContext");
  if (bc) bc.textContent = VIEW_LABEL[name] || "";
  for (const v of VIEWS) {
    const section = document.getElementById("view-" + v);
    if (section) section.hidden = v !== name;
  }
  document.querySelectorAll(".dock__btn").forEach((a) => {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function route() {
  const name = currentView();
  if (name === activeView) return;
  const first = activeView === null;
  activeView = name;
  if (ctx.sky) ctx.sky.setMode(skyModeFor(name));
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!first && !reduced && document.startViewTransition) {
    document.startViewTransition(() => applyRoute(name));
  } else {
    applyRoute(name);
  }
  bus.emit("nav", { view: name });
}

/* ---------- bar readout: live sky stats + service pulse ---------- */
function wireReadout() {
  const statsEl = $("#readoutStats");
  if (!statsEl) return;
  bus.on("sky:data", ({ data } = {}) => {
    if (!data || data.kind !== "graph") { statsEl.textContent = ""; return; }
    const real = data.nodes.filter((n) => !n.deco).length;
    const srcs = new Set(data.nodes.filter((n) => !n.deco).map((n) => n.source)).size;
    statsEl.innerHTML = `<b>${real.toLocaleString()}</b> atoms · <b>${srcs}</b> sources`;
  });
}

async function pulse() {
  const dot = $("#statusDot"), txt = $("#statusText");
  if (!dot || !txt) return;
  try {
    await api.get("/ready");
    dot.className = "dot dot--ok";
    txt.textContent = "Live";
  } catch (e) {
    dot.className = "dot dot--bad";
    txt.textContent = e && e.status === 401 ? "Signed out" : "Offline";
  }
}

/* ---------- view loading (resilient: one broken view never sinks boot) */
const LOADERS = {
  home: () => Promise.resolve(initHome),
  graph: () => Promise.resolve(initGraph),
  ask: () => import("./views/ask.js").then((m) => m.init),
  library: () => import("./views/library.js").then((m) => m.init),
  review: () => import("./views/review.js").then((m) => m.init),
  files: () => import("./views/files.js").then((m) => m.init),
  sources: () => import("./views/sources.js").then((m) => m.init),
  ops: () => import("./views/ops.js").then((m) => m.init),
  help: () => import("./views/help.js").then((m) => m.init),
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

  // The sky first (views read ctx.sky at init), then the lens, then chrome.
  ctx.sky = initSky($("#sky"), ctx);
  ctx.lens = mountLens($("#lensHost"), ctx);
  // Hints: one delegated tooltip layer for every [data-hint] in the app.
  import("./hints.js").then((m) => m.mountHints(document)).catch(() => { /* optional */ });
  const themeHost = $("#themeMenuHost");
  if (themeHost) mountThemeMenu(themeHost, bus);
  wireReadout();

  // Dock expand/collapse (persisted) — icons-only or icons+labels.
  const dock = $("#dock"), dockToggle = $("#dockToggle");
  if (dock && dockToggle) {
    const apply = (on) => {
      dock.classList.toggle("dock--expanded", on);
      document.documentElement.classList.toggle("dock-open", on); // content shifts, not overlaps
      dockToggle.setAttribute("aria-expanded", String(on));
      dockToggle.setAttribute("aria-label", on ? "Collapse menu" : "Expand menu");
      const lbl = dockToggle.querySelector(".dock__lbl");
      if (lbl) lbl.textContent = on ? "Collapse" : "Expand";
    };
    let on = false;
    try { on = localStorage.getItem("ko-dock") === "1"; } catch { /* private mode */ }
    apply(on);
    dockToggle.addEventListener("click", () => {
      on = !dock.classList.contains("dock--expanded");
      apply(on);
      try { localStorage.setItem("ko-dock", on ? "1" : "0"); } catch { /* private mode */ }
    });
  }

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
