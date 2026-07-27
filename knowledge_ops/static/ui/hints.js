/* Knowledge-Ops — hints: one glass tooltip for the whole instrument.
   Consumers add attributes only — zero per-element JS:

     <button data-hint="Shown after a beat of hover, instantly on focus.">
     <span data-hint="Body text." data-hint-title="Bold first line.">

   mountHints(root=document) wires ONE delegated listener pair
   (pointerover/focusin + matching pointerout/focusout) on the root. A single
   module-wide tooltip element serves every anchor, so nested mounts (a view
   mounting its own section while the shell mounts document) never produce a
   second tooltip: show() is idempotent per anchor and mounts are guarded per
   root. Shows after ~250 ms of hover, instantly on focus; hides on
   pointerout / blur / Esc / any scroll. While visible the anchor carries
   aria-describedby -> the tooltip id (previous value preserved + restored).
   Positioned above the anchor (below when there is no room), clamped inside
   the viewport. Glass material rides tokens only; reduced-motion drops the
   transition (base.css also globally disables it).

   hintHtml(text, title?) returns a small focusable info-glyph span with the
   data-hint attributes preset — for sprinkling a "?" beside a label. */

const TIP_ID = "koHintTip";
const SHOW_DELAY = 250;
const GAP = 10; // px between anchor and tooltip
const MARGIN = 8; // px viewport safety margin

let tip = null;
let tipTitle = null;
let tipBody = null;
let anchor = null; // element the tip is shown (or scheduled) for
let timer = 0;
let describedBySet = false;
let prevDescribedBy = null;
let stylesInjected = false;
let globalWired = false;
const mountedRoots = new WeakSet();

function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const st = document.createElement("style");
  /* Glass + typography via --mu-* tokens only; the rest is positioning. */
  st.textContent = `
.ko-hint{
  position:fixed;left:0;top:0;z-index:96;
  max-width:38ch;
  padding:.5rem .75rem;
  background:var(--mu-glass-strong);
  -webkit-backdrop-filter:blur(14px) saturate(1.5);backdrop-filter:blur(14px) saturate(1.5);
  border-radius:var(--mu-radius-md);
  box-shadow:0 0 0 1px var(--mu-hairline),var(--mu-shadow-pane);
  color:var(--mu-color-text);
  font-family:var(--mu-font-mono);font-size:.74rem;line-height:1.55;letter-spacing:.02em;
  pointer-events:none;
  visibility:hidden;opacity:0;translate:0 3px;
  transition:opacity 130ms ease,translate 130ms ease;
}
.ko-hint.is-on{visibility:visible;opacity:1;translate:0 0;}
.ko-hint__title{display:block;font-weight:600;margin:0 0 .18rem;}
.ko-hint__title[hidden]{display:none!important;}
.ko-hint__body{display:block;color:var(--mu-color-muted);}
@media (prefers-reduced-motion: reduce){
  .ko-hint{transition:none;translate:0 0;}
}
/* the optional info glyph emitted by hintHtml() */
.ko-hint-i{
  position:relative;
  display:inline-flex;align-items:center;justify-content:center;
  width:1.05rem;height:1.05rem;
  vertical-align:-.14em;margin:0 .12rem;
  border-radius:50%;
  color:var(--mu-color-dim);
  cursor:help;
}
.ko-hint-i:hover,.ko-hint-i:focus-visible{color:var(--mu-color-accent);}
.ko-hint-i svg{display:block;}
/* coarse pointers get an invisible >=44px touch halo (tap focuses -> instant hint) */
@media (pointer: coarse){
  .ko-hint-i::after{content:"";position:absolute;inset:-14px;}
}
`;
  document.head.appendChild(st);
}

function ensureTip() {
  if (tip && tip.isConnected) return tip;
  injectStyles();
  tip = document.createElement("div");
  tip.id = TIP_ID;
  tip.className = "ko-hint";
  tip.setAttribute("role", "tooltip");
  tip.setAttribute("aria-hidden", "true");
  tipTitle = document.createElement("b");
  tipTitle.className = "ko-hint__title";
  tipBody = document.createElement("span");
  tipBody.className = "ko-hint__body";
  tip.append(tipTitle, tipBody);
  document.body.appendChild(tip);
  return tip;
}

function place() {
  const r = anchor.getBoundingClientRect();
  const tw = tip.offsetWidth;
  const th = tip.offsetHeight;
  let x = r.left + r.width / 2 - tw / 2;
  x = Math.max(MARGIN, Math.min(x, window.innerWidth - tw - MARGIN));
  let y = r.top - th - GAP; // preferred: above the anchor
  if (y < MARGIN) y = r.bottom + GAP; // no room above -> below
  y = Math.max(MARGIN, Math.min(y, window.innerHeight - th - MARGIN));
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

function reveal() {
  timer = 0;
  if (!anchor || !anchor.isConnected) { anchor = null; return; }
  const text = anchor.getAttribute("data-hint");
  if (!text) { anchor = null; return; }
  ensureTip();
  const title = anchor.getAttribute("data-hint-title") || "";
  tipTitle.textContent = title; // textContent — hint text can never inject HTML
  tipTitle.hidden = !title;
  tipBody.textContent = text;
  prevDescribedBy = anchor.getAttribute("aria-describedby");
  describedBySet = true;
  anchor.setAttribute(
    "aria-describedby",
    prevDescribedBy && prevDescribedBy.split(/\s+/).indexOf(TIP_ID) < 0
      ? prevDescribedBy + " " + TIP_ID
      : TIP_ID,
  );
  tip.setAttribute("aria-hidden", "false");
  place(); // measure while visibility:hidden (layout exists), then show
  tip.classList.add("is-on");
}

function hide() {
  if (timer) { clearTimeout(timer); timer = 0; }
  if (anchor && describedBySet) {
    if (prevDescribedBy != null) anchor.setAttribute("aria-describedby", prevDescribedBy);
    else anchor.removeAttribute("aria-describedby");
  }
  describedBySet = false;
  prevDescribedBy = null;
  anchor = null;
  if (tip) {
    tip.classList.remove("is-on");
    tip.setAttribute("aria-hidden", "true");
  }
}

function show(el, instant) {
  if (el === anchor) {
    // Already scheduled or visible for this anchor; a focus can only upgrade
    // a pending hover timer to instant. Never restart — keeps nested-mount
    // double delivery (section + document listeners) a no-op.
    if (instant && timer) { clearTimeout(timer); timer = 0; reveal(); }
    return;
  }
  hide();
  if (!el.getAttribute("data-hint")) return;
  anchor = el;
  if (instant) reveal();
  else timer = setTimeout(reveal, SHOW_DELAY);
}

function anchorOf(e) {
  const t = e.target;
  return t && t.closest ? t.closest("[data-hint]") : null;
}

/* leaving = the event concerns the current anchor AND the destination is
   outside it (and not the tooltip itself, which is pointer-events:none). */
function leaving(e) {
  if (!anchor) return false;
  const from = e.target;
  if (!(from === anchor || (anchor.contains && anchor.contains(from)))) return false;
  const to = e.relatedTarget;
  return !(to && anchor.contains && anchor.contains(to));
}

function onPointerOver(e) {
  const el = anchorOf(e);
  if (el) show(el, false);
}
function onPointerOut(e) {
  if (leaving(e)) hide();
}
function onFocusIn(e) {
  const el = anchorOf(e);
  if (el) show(el, true);
}
function onFocusOut(e) {
  if (leaving(e)) hide();
}

function wireGlobal() {
  if (globalWired) return;
  globalWired = true;
  // capture: hide even when a view stops propagation; never preventDefault.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hide();
  }, true);
  window.addEventListener("scroll", () => {
    if (anchor || timer) hide();
  }, { capture: true, passive: true });
}

/** Wire delegated hint handling on root (default: the whole document).
 *  Idempotent per root; nested roots coexist without double tooltips. */
export function mountHints(root = document) {
  if (!root || mountedRoots.has(root)) return;
  mountedRoots.add(root);
  injectStyles();
  wireGlobal();
  root.addEventListener("pointerover", onPointerOver);
  root.addEventListener("pointerout", onPointerOut);
  root.addEventListener("focusin", onFocusIn);
  root.addEventListener("focusout", onFocusOut);
}

const escAttr = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

/** A small focusable info glyph carrying the hint — drop into any HTML string. */
export function hintHtml(text, title) {
  return `<span class="ko-hint-i" tabindex="0" role="img" aria-label="More information"
    data-hint="${escAttr(text)}"${title ? ` data-hint-title="${escAttr(title)}"` : ""}>
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <circle cx="6" cy="6" r="5.1" stroke="currentColor" stroke-width="1.3"/>
      <path d="M6 5.4v3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      <circle cx="6" cy="3.4" r=".8" fill="currentColor"/>
    </svg></span>`;
}
