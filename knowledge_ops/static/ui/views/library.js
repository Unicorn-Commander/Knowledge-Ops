/* Library — the encyclopedia.
   Wikipedia grammar in the Observatory skin: a full-width, LEFT-anchored
   article grid — [tree rail 240px | article | infobox rail 260px] — instead of
   a centered app pane. The article column reads like an encyclopedia entry:
   Fraunces H1 over a hairline title rule, a mono byline (path · edited ·
   versions), left-anchored 78ch prose. The right rail is an infobox plate
   (scope/owner/links/versions) + TOC scrollspy + version timeline + backlinks.
   The wiki home is a portal page: lead zone, recent changes as a dated
   changelog, two-column wanted/orphan panels. Mobile (<900px) is one column —
   article first, the tree behind a "Contents" disclosure in the masthead.
   All classic behaviors survive: filter, +New, edit (split live preview with
   [[wikilink]] autocomplete), delete, history snapshots, deep-links
   #library?path=..., red-link create flow, backlinks/wanted from /kb.links
   (tolerates its absence), bus contracts library:open / library:new / nav /
   wiki:links-changed. Hints ride as [data-hint] attributes only (ui/hints.js
   owns the tooltip runtime; nothing here imports it). */

const KNOWN_GROUPS = ["ai-platform", "apps", "engineering", "infrastructure", "marketing"];
const EXPANDED_KEY = "ko.lib.expanded"; // sessionStorage: expanded dir paths
const LINKS_TTL_MS = 60000;             // /kb.links cache window

let stylesInjected = false;
function injectStyles() {
  if (stylesInjected) return;
  stylesInjected = true;
  const css = `
  /* Blanket [hidden] restatement — view CSS authors display on several
     hidden-able elements (rail sections, tree groups, wanted wrap, panes). */
  #view-library [hidden] { display: none !important; }
  /* The encyclopedia claims the reading width — this pane alone widens. */
  #view-library { max-width: min(1500px, 100%); }
  @media (min-width: 900px) {
    #view-library { padding: 1.45rem 2.2rem 2.6rem; }
  }

  /* ---- masthead: slim site line over the article grid ---- */
  #view-library .lib-mast {
    display: flex; align-items: center; gap: .75rem; flex-wrap: wrap;
    padding: 0 0 .7rem;
    border-bottom: 1px solid var(--mu-hairline);
  }
  #view-library .lib-mast .kicker { margin: 0; }
  #view-library .lib-mast-note {
    font-family: var(--mu-font-mono); font-size: .72rem;
    color: var(--mu-color-dim); font-variant-numeric: tabular-nums;
  }
  #view-library .lib-mast-actions { margin-left: auto; display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
  #view-library .lib-tree-toggle { display: none; }

  /* ---- the article grid: left-anchored, Wikipedia bones ---- */
  #view-library .lib-layout {
    display: grid;
    grid-template-columns: 240px minmax(0, 1fr) 260px;
    align-items: start;
  }
  #view-library .lib-list-pane {
    min-width: 0;
    display: flex; flex-direction: column;
    padding: 1.05rem 1.05rem 0 0;
    position: sticky;
    top: calc(var(--mu-bar-h) + .9rem);
    max-height: calc(100vh - var(--mu-bar-h) - 2rem);
  }
  #view-library .lib-filter-row { flex: none; padding: 0 0 .6rem; }
  #view-library .lib-tree-scroll {
    flex: 1 1 auto; min-height: 0;
    overflow-y: auto; overscroll-behavior: contain;
    padding-bottom: .9rem;
  }
  #view-library .lib-reader {
    min-width: 0;
    padding: 1.1rem 2rem 0;
    border-left: 1px solid var(--mu-hairline);
    border-right: 1px solid var(--mu-hairline);
  }
  #view-library .lib-rail {
    min-width: 0;
    position: sticky;
    top: calc(var(--mu-bar-h) + .9rem);
    max-height: calc(100vh - var(--mu-bar-h) - 2rem);
    overflow-y: auto; overscroll-behavior: contain;
    padding: 1.1rem .1rem .9rem 1.3rem;
  }
  /* home portal + editor run without the infobox rail */
  #view-library .lib-layout--wide .lib-reader { grid-column: 2 / -1; border-right: 0; }
  #view-library .lib-layout--wide .lib-rail { display: none; }

  @media (max-width: 1099px) {
    #view-library .lib-layout { grid-template-columns: 232px minmax(0, 1fr); }
    #view-library .lib-reader { border-right: 0; padding-right: .4rem; }
    #view-library .lib-layout--wide .lib-reader { grid-column: 2; }
    #view-library .lib-rail {
      grid-column: 2;
      position: static; max-height: none; overflow: visible;
      border-top: 1px solid var(--mu-hairline);
      margin-top: 1.5rem;
      padding: 1.1rem 0 0 2rem;
    }
  }
  @media (max-width: 899px) {
    #view-library .lib-layout { grid-template-columns: minmax(0, 1fr); }
    /* article first; the tree lives behind the masthead's Contents button
       and, when opened, surfaces ABOVE the article where the button sits */
    #view-library .lib-list-pane {
      order: 0; display: none;
      position: static; max-height: none;
      padding: .9rem 0 0;
    }
    #view-library .lib-layout--tree-open .lib-list-pane { display: flex; }
    #view-library .lib-tree-scroll { overflow-y: visible; padding-bottom: .3rem; border-bottom: 1px solid var(--mu-hairline); }
    #view-library .lib-reader { order: 1; border-left: 0; padding: 1rem 0 0; }
    #view-library .lib-layout--wide .lib-reader { grid-column: auto; }
    #view-library .lib-rail {
      order: 2; grid-column: auto;
      position: static; max-height: none; overflow: visible;
      border-top: 1px solid var(--mu-hairline);
      margin-top: 1.5rem;
      padding: 1.1rem 0 0;
    }
    #view-library .lib-tree-toggle { display: inline-flex; }
    /* the byline already carries the full path — drop the crumb leaf's echo */
    #view-library .lib-crumb-leaf,
    #view-library .lib-crumbs .lib-crumb-sep:has(+ .lib-crumb-leaf) { display: none; }
  }

  /* ---- quiet filter (underline, not a box) ---- */
  #view-library .lib-filter {
    width: 100%;
    min-height: 44px;
    padding: .35rem .1rem;
    background: transparent;
    box-shadow: none;
    border-radius: 0;
    border-bottom: 1px solid var(--mu-hairline);
    transition: border-color var(--mu-dur-quick) ease;
  }
  #view-library .lib-filter:hover { border-bottom-color: var(--mu-color-border-strong); }
  #view-library .lib-filter:focus {
    background: transparent;
    box-shadow: none;
    border-bottom-color: var(--mu-color-primary);
  }

  /* ---- article head: title rule + byline (the Wikipedia grammar) ---- */
  #view-library .lib-art-topline {
    display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
    min-height: 2rem;
  }
  #view-library .lib-art-actions { margin-left: auto; display: flex; gap: .4rem; flex: none; }
  #view-library .lib-art-title {
    font-family: var(--mu-font-display);
    font-size: clamp(1.55rem, 2.6vw, 2.2rem);
    font-weight: 550;
    line-height: 1.12;
    letter-spacing: -0.014em;
    color: var(--mu-color-text);
    margin: .15rem 0 0;
    padding: 0 0 .5rem;
    border-bottom: 1px solid var(--mu-hairline);
    overflow-wrap: break-word;
    text-wrap: balance;
  }
  #view-library .lib-byline {
    display: flex; flex-wrap: wrap; align-items: center; gap: .3rem .6rem;
    font-family: var(--mu-font-mono);
    font-size: .74rem;
    color: var(--mu-color-dim);
    margin: .5rem 0 1.3rem;
  }
  #view-library .lib-byline .pill { font-size: .62rem; }

  /* reader body typography flows from base.css .md — article measure is 78ch,
     anchored LEFT like an encyclopedia column, never centered */
  #view-library .lib-md { max-width: 78ch; }
  #view-library .lib-md h1, #view-library .lib-md h2, #view-library .lib-md h3 {
    scroll-margin-top: calc(var(--mu-bar-h) + 1.4rem);
  }
  #view-library .lib-md .lib-dup-title { display: none; }
  #view-library .lib-md > :first-child,
  #view-library .lib-md > .lib-dup-title + * { margin-top: .25rem; }
  #view-library .lib-md-scroll { overflow-x: auto; }

  /* wiki links — resolved links ride the standard accent anchor styling;
     missing pages are red-links: danger at reduced opacity, dotted underline. */
  #view-library a.wl-missing,
  #view-library button.lib-wanted-btn {
    color: color-mix(in srgb, var(--mu-color-danger) 72%, transparent);
    text-decoration: underline dotted;
    text-underline-offset: 3px;
  }
  #view-library a.wl-missing:hover,
  #view-library button.lib-wanted-btn:hover {
    color: var(--mu-color-danger);
  }

  /* ---- directory tree — hairline rows, mono counts, primary tint active ---- */
  #view-library .lib-tree, #view-library .lib-tree ul { list-style: none; margin: 0; padding: 0; }
  #view-library .lib-tree ul.lib-tree-group {
    margin-left: .5rem;
    padding-left: .7rem;
    border-left: 1px solid var(--mu-hairline);
  }
  #view-library .lib-tree li { margin: 0; }
  #view-library .lib-tree-dir, #view-library .lib-tree-doc {
    display: flex; align-items: center; gap: .5rem;
    width: 100%; min-height: 44px; padding: .55rem .55rem;
    border: 0; border-bottom: 1px solid var(--mu-hairline);
    border-radius: 0;
    background: transparent;
    color: var(--mu-color-muted);
    font: inherit; font-size: .88rem; text-align: left;
    cursor: pointer;
    transition: background var(--mu-dur-quick) ease, color var(--mu-dur-quick) ease;
  }
  #view-library .lib-tree > li:last-child > .lib-tree-dir,
  #view-library .lib-tree > li:last-child > .lib-tree-doc { border-bottom: 0; }
  #view-library .lib-tree-dir:hover, #view-library .lib-tree-doc:hover {
    color: var(--mu-color-text);
    background: color-mix(in srgb, var(--mu-color-surface-strong) 60%, transparent);
    border-radius: var(--mu-radius-sm);
  }
  #view-library .lib-tree-dir { font-weight: 600; }
  #view-library .lib-tree-doc[aria-current="true"] {
    color: var(--mu-color-primary);
    background: color-mix(in srgb, var(--mu-color-primary) 13%, transparent);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--mu-color-primary) 30%, transparent);
    border-radius: var(--mu-radius-sm);
    border-bottom-color: transparent;
  }
  #view-library .lib-caret {
    flex: none;
    width: .5rem; height: .5rem;
    margin-inline: .1rem .05rem;
    border-right: 1.5px solid currentColor;
    border-bottom: 1.5px solid currentColor;
    transform: rotate(-45deg);
    transition: transform var(--mu-dur-quick) var(--mu-ease-settle);
  }
  #view-library .lib-tree-dir[aria-expanded="true"] .lib-caret { transform: rotate(45deg); }
  #view-library .lib-doc-name { min-width: 0; overflow-wrap: anywhere; }
  #view-library .lib-tree-doc .lib-doc-name { font-weight: 550; }
  #view-library .lib-count {
    margin-left: auto;
    flex: none;
    font-size: .72rem;
    color: var(--mu-color-dim);
  }
  #view-library .lib-leaf-meta { margin-left: auto; display: flex; gap: .3rem; }

  /* ---- breadcrumbs (the "you are here" line above the title) ---- */
  #view-library .lib-crumbs {
    display: flex; flex-wrap: wrap; align-items: center; gap: .05rem;
    margin: 0;
    flex: 1 1 auto; min-width: 0;
    font-family: var(--mu-font-mono);
    font-size: .78rem;
  }
  #view-library .lib-crumbs button {
    min-height: 44px; padding: .2rem .3rem;
    background: none; border: none;
    border-radius: var(--mu-radius-sm);
    color: var(--mu-color-accent);
    font: inherit; cursor: pointer;
  }
  #view-library .lib-crumbs button:hover { text-decoration: underline; text-underline-offset: 3px; }
  #view-library .lib-crumb-sep { color: var(--mu-color-dim); }
  #view-library .lib-crumb-leaf { color: var(--mu-color-dim); padding: .2rem .3rem; overflow-wrap: anywhere; }

  /* ---- right rail: infobox plate + kicker-led hairline sections ---- */
  #view-library .lib-infobox { padding: .85rem 1rem .95rem; margin: 0 0 1.15rem; }
  #view-library .lib-infobox-cap {
    font-family: var(--mu-font-display);
    font-weight: 600; font-size: .95rem; line-height: 1.25;
    margin: 0 0 .55rem; padding: 0 0 .45rem;
    border-bottom: 1px solid var(--mu-hairline);
    overflow-wrap: break-word;
  }
  #view-library .lib-facts {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: .34rem .9rem;
    margin: 0;
    font-size: .78rem;
  }
  #view-library .lib-facts dt {
    font-family: var(--mu-font-mono); font-size: .64rem; letter-spacing: .1em;
    text-transform: uppercase; color: var(--mu-color-dim);
    padding-top: .12rem;
  }
  #view-library .lib-facts dd {
    margin: 0; color: var(--mu-color-muted);
    text-align: right; overflow-wrap: anywhere;
    font-variant-numeric: tabular-nums;
  }
  #view-library .lib-rail-sec {
    padding: .1rem 0 1rem;
    margin-bottom: 1rem;
    border-bottom: 1px solid var(--mu-hairline);
  }
  #view-library .lib-rail .empty,
  #view-library .lib-links .empty {
    border: 0; text-align: left; font-size: .85rem;
  }

  /* compact link lists (backlinks / wanted / orphans) */
  #view-library .lib-links { list-style: none; margin: .35rem 0 0; padding: 0; }
  #view-library .lib-links button {
    display: block; width: 100%; min-height: 44px;
    text-align: left;
    background: none; border: none;
    border-radius: var(--mu-radius-sm);
    color: var(--mu-color-muted);
    font: inherit; font-size: .85rem;
    padding: .5rem .4rem;
    cursor: pointer;
    overflow-wrap: anywhere;
  }
  #view-library .lib-links button:hover {
    color: var(--mu-color-text);
    background: color-mix(in srgb, var(--mu-color-surface-strong) 60%, transparent);
  }
  #view-library .lib-links button.lib-wanted-btn:hover { color: var(--mu-color-danger); }

  /* ---- editor: side-by-side live preview + mobile Write/Preview toggle ---- */
  #view-library .lib-ed-toggle { display: none; gap: .4rem; margin: 0 0 .65rem; }
  #view-library .lib-ed-toggle .btn[aria-pressed="true"] {
    color: var(--mu-color-primary);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--mu-color-primary) 55%, transparent);
  }
  #view-library .lib-ed-split {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 1.2rem;
    align-items: start;
    max-width: 160ch;
  }
  @media (max-width: 899px) {
    #view-library .lib-ed-split { grid-template-columns: minmax(0, 1fr); }
    #view-library .lib-ed-toggle { display: flex; }
    #view-library .lib-ed-split[data-mode="write"] .lib-ed-pane-preview { display: none; }
    #view-library .lib-ed-split[data-mode="preview"] .lib-ed-pane-write { display: none; }
  }
  #view-library .lib-ed-preview {
    border: 0;
    border-radius: var(--mu-radius-md);
    background: var(--mu-color-surface);
    box-shadow: inset 0 0 0 1px var(--mu-hairline);
    padding: .75rem 1rem;
    min-height: 320px;
    max-height: 70vh;
    overflow: auto;
    max-width: none;
  }
  /* wikilink autocomplete popover (anchored to the caret) */
  #view-library .lib-ed-wrap { position: relative; }
  #view-library .lib-wl-auto {
    position: absolute; z-index: 30;
    min-width: 15rem; max-width: min(21rem, 92vw);
    max-height: 15rem; overflow-y: auto; overscroll-behavior: contain;
    padding: .3rem;
    background: var(--mu-glass-strong);
    -webkit-backdrop-filter: blur(14px) saturate(1.4);
    backdrop-filter: blur(14px) saturate(1.4);
    border-radius: var(--mu-radius-md);
    box-shadow: var(--mu-shadow-pane);
  }
  #view-library .lib-wl-opt {
    display: block; width: 100%; min-height: 44px;
    padding: .4rem .55rem;
    border-radius: var(--mu-radius-sm);
    cursor: pointer;
    font-size: .84rem; color: var(--mu-color-muted);
  }
  #view-library .lib-wl-opt .lib-wl-path {
    display: block;
    font-family: var(--mu-font-mono); font-size: .68rem;
    color: var(--mu-color-dim);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  #view-library .lib-wl-opt[aria-selected="true"] {
    background: color-mix(in srgb, var(--mu-color-primary) 14%, transparent);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--mu-color-primary) 30%, transparent);
    color: var(--mu-color-text);
  }

  /* ---- wiki home portal ---- */
  #view-library .lib-lead { max-width: 78ch; }
  #view-library .lib-start {
    display: flex; align-items: center; gap: .9rem; flex-wrap: wrap;
    margin: .2rem 0 .3rem;
    padding: .7rem 1rem .75rem;
    border-left: 2px solid var(--mu-brass);
    border-radius: 0 var(--mu-radius-md) var(--mu-radius-md) 0;
    background: color-mix(in srgb, var(--mu-brass) 7%, transparent);
  }
  #view-library .lib-start .kicker { margin: 0 0 .1rem; }
  #view-library .lib-home-grid {
    display: grid; gap: 0 2.4rem;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: start;
    max-width: 110ch;
  }
  @media (max-width: 899px) {
    #view-library .lib-home-grid { grid-template-columns: minmax(0, 1fr); }
  }
  /* recent changes as a proper dated changelog */
  #view-library .lib-rc-wrap { max-width: 110ch; }
  #view-library .lib-rc-day {
    font-family: var(--mu-font-mono); font-size: .68rem; letter-spacing: .14em;
    text-transform: uppercase; color: var(--mu-brass);
    margin: 1.2rem 0 .1rem; padding: 0 0 .3rem;
    border-bottom: 1px solid var(--mu-hairline);
  }
  #view-library .lib-rc { list-style: none; margin: 0; padding: 0; }
  #view-library .lib-rc button {
    display: flex; align-items: baseline; gap: .65rem;
    width: 100%; min-height: 44px; text-align: left;
    background: none; border: 0; border-bottom: 1px solid var(--mu-hairline);
    border-radius: 0;
    padding: .5rem .35rem;
    font: inherit; font-size: .88rem; color: var(--mu-color-muted);
    cursor: pointer;
    transition: background var(--mu-dur-quick) ease, color var(--mu-dur-quick) ease;
  }
  #view-library .lib-rc li:last-child button { border-bottom: 0; }
  #view-library .lib-rc button:hover {
    color: var(--mu-color-text);
    background: color-mix(in srgb, var(--mu-color-surface-strong) 60%, transparent);
    border-radius: var(--mu-radius-sm);
  }
  #view-library .lib-rc-time { flex: none; min-width: 3.1rem; font-size: .72rem; color: var(--mu-color-dim); }
  #view-library .lib-rc-title { font-weight: 550; min-width: 0; overflow-wrap: anywhere; }
  #view-library .lib-rc-path {
    margin-left: auto; flex: 0 1 auto; min-width: 0; max-width: 45%;
    font-size: .72rem; color: var(--mu-color-dim);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  /* ---- TOC + timeline (rail vocabulary, unchanged recipes) ---- */
  #view-library .lib-toc { list-style: none; margin: .4rem 0 0; padding: 0; }
  #view-library .lib-toc button {
    display: block;
    width: 100%;
    min-height: 44px;
    text-align: left;
    background: none;
    border: none;
    border-left: 2px solid var(--mu-color-border);
    color: var(--mu-color-muted);
    font: inherit;
    font-size: .85rem;
    padding: .55rem .6rem;
    cursor: pointer;
    border-radius: 0 var(--mu-radius-sm) var(--mu-radius-sm) 0;
  }
  #view-library .lib-toc button:hover { color: var(--mu-color-text); background: var(--mu-color-surface); }
  #view-library .lib-toc li[data-level="3"] button { padding-left: 1.4rem; }
  #view-library .lib-toc button[aria-current="true"] {
    color: var(--mu-color-primary);
    border-left-color: var(--mu-color-primary);
    background: var(--mu-color-surface);
  }
  #view-library .lib-timeline { list-style: none; margin: .4rem 0 0; padding: 0; position: relative; }
  #view-library .lib-timeline::before {
    content: "";
    position: absolute;
    left: .55rem;
    top: .6rem;
    bottom: .6rem;
    width: 1px;
    background: var(--mu-color-border-strong);
  }
  #view-library .lib-timeline button {
    display: block;
    width: 100%;
    min-height: 44px;
    text-align: left;
    background: none;
    border: none;
    color: var(--mu-color-muted);
    font: inherit;
    font-size: .82rem;
    padding: .5rem .4rem .5rem 1.6rem;
    cursor: pointer;
    position: relative;
    border-radius: var(--mu-radius-sm);
  }
  #view-library .lib-timeline button::before {
    content: "";
    position: absolute;
    left: .3rem;
    top: 50%;
    transform: translateY(-50%);
    width: .55rem;
    height: .55rem;
    border-radius: 50%;
    background: var(--mu-color-surface-strong);
    border: 1px solid var(--mu-color-border-strong);
  }
  #view-library .lib-timeline button:hover { color: var(--mu-color-text); background: var(--mu-color-surface); }
  #view-library .lib-timeline button[aria-current="true"] { color: var(--mu-color-primary); }
  #view-library .lib-timeline button[aria-current="true"]::before {
    background: var(--mu-color-primary);
    border-color: var(--mu-color-primary);
  }
  #view-library .lib-version-banner {
    border: 0;
    border-radius: var(--mu-radius-md);
    padding: .6rem .9rem;
    margin-bottom: 1rem;
    color: var(--mu-color-text);
    background: color-mix(in srgb, var(--mu-color-warning) 8%, transparent);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--mu-color-warning) 32%, transparent);
    display: flex;
    flex-wrap: wrap;
    gap: .6rem;
    align-items: center;
    justify-content: space-between;
    max-width: 78ch;
  }
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
}

function relTime(value) {
  if (!value) return "";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const delta = Math.round((Date.now() - then) / 1000);
  const s = Math.abs(delta);
  if (s < 45) return "just now";
  const span =
    s < 3600 ? Math.round(s / 60) + "m" :
    s < 86400 ? Math.round(s / 3600) + "h" :
    s < 604800 ? Math.round(s / 86400) + "d" :
    s < 2629800 ? Math.round(s / 604800) + "w" :
    s < 31557600 ? Math.round(s / 2629800) + "mo" :
    Math.round(s / 31557600) + "y";
  return delta < 0 ? "in " + span : span + " ago"; // review-due dates are future
}
function fmtTime(t) { if (!t) return ""; try { return new Date(t).toLocaleString(); } catch { return String(t); } }
function dayStamp(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}
function dayLabel(value) {
  const stamp = dayStamp(value);
  if (stamp == null) return String(value || "");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  if (stamp === today.getTime()) return "Today";
  if (stamp === today.getTime() - 86400000) return "Yesterday";
  const d = new Date(value);
  const opts = { weekday: "short", month: "short", day: "numeric" };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString(undefined, opts);
}
function timeLabel(value) {
  try { return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  catch { return ""; }
}
function localDateValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}
function toIso(value) { return value ? new Date(value).toISOString() : undefined; }
function dirOf(path) {
  const i = String(path || "").lastIndexOf("/");
  return i > 0 ? String(path).slice(0, i) : "";
}
function parseHashPath() {
  const h = location.hash || "";
  if (!h.startsWith("#library")) return null;
  const q = h.indexOf("?");
  if (q < 0) return null;
  const params = new URLSearchParams(h.slice(q + 1));
  return params.get("path");
}
function slugSeg(s) {
  return String(s).trim()
    .replace(/\s+/g, "-")
    .replace(/[^A-Za-z0-9._-]/g, "")
    .replace(/-{2,}/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
}
/* Create path for a missing [[target]]: explicit paths are slugged per
   segment from the root; bare names are slugged under the current doc's dir. */
function createPathFor(target, dir) {
  const clean = String(target || "").trim().replace(/\.md$/i, "");
  if (clean.includes("/")) {
    const p = clean.split("/").map(slugSeg).filter(Boolean).join("/");
    return (p || "untitled") + ".md";
  }
  const base = slugSeg(clean) || "untitled";
  return (dir ? dir + "/" : "") + base + ".md";
}
function loadExpanded() {
  try { return new Set(JSON.parse(sessionStorage.getItem(EXPANDED_KEY) || "[]")); }
  catch { return new Set(); }
}
/* Red links get their hint attributes here (ui/hints.js reads them; the md
   renderer is shared and stays hint-agnostic). */
function hintRedLinks(host) {
  if (!host) return;
  host.querySelectorAll("a.wl-missing").forEach(a => {
    a.setAttribute("data-hint-title", "Red link");
    a.setAttribute("data-hint", "No page here yet — click to draft " + (a.dataset.create || "it") + ".");
  });
}

/* Tolerant /kb.links normalizer -> {links: [{src,dst}], missing: [{src,target}]}.
   Accepts key aliases and rows flagged exists:false in the main array. */
function normalizeLinks(raw) {
  const links = [], missing = [];
  const pick = (r, keys) => {
    for (const k of keys) { const v = r[k]; if (v != null && v !== "") return String(v); }
    return null;
  };
  const rows = (raw && (raw.links || raw.edges || raw.rows)) || (Array.isArray(raw) ? raw : []);
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const src = pick(r, ["src", "src_path", "source", "from", "from_path"]);
    const dst = pick(r, ["dst", "dst_path", "to", "to_path", "target_path", "path"]);
    const target = pick(r, ["target", "title", "label"]);
    if (r.exists === false || r.resolved === false || (!dst && target)) {
      if (src || target) missing.push({ src: src || "", target: target || dst || "" });
    } else if (src && dst) {
      links.push({ src, dst });
    }
  }
  for (const r of ((raw && (raw.missing || raw.wanted)) || [])) {
    if (!r) continue;
    if (typeof r === "string") { missing.push({ src: "", target: r }); continue; }
    const src = pick(r, ["src", "src_path", "source", "from", "from_path"]) || "";
    const target = pick(r, ["target", "dst", "title", "label"]) || "";
    if (target) missing.push({ src, target });
  }
  return { links, missing };
}

export function init(sectionEl, ctx) {
  injectStyles();
  const { api, bus, esc, md, toast, error } = ctx;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  sectionEl.innerHTML = `
    <header class="lib-mast">
      <span class="kicker">Library</span>
      <span class="lib-mast-note" id="libMastCount"></span>
      <div class="lib-mast-actions">
        <button class="btn btn--sm lib-tree-toggle" id="libTreeToggle" type="button"
                aria-expanded="false" aria-controls="libListPane">Contents</button>
        <button class="btn btn--sm" id="libNewBtn" type="button"
                data-hint="Draft a governed page — versioned from its very first save.">+ New document</button>
      </div>
    </header>
    <div class="lib-layout">
      <aside class="lib-list-pane" id="libListPane" aria-label="Document tree">
        <div class="lib-filter-row">
          <input class="input lib-filter" id="libFilter" type="search" placeholder="Filter the shelves…"
                 autocomplete="off" aria-label="Filter documents by title or path"
                 data-hint="Type to filter by title or path — matching branches expand as you go." />
        </div>
        <div class="lib-tree-scroll">
          <div class="list" id="libList" aria-live="polite" aria-busy="false"></div>
        </div>
      </aside>
      <main class="lib-reader" aria-label="Article">
        <div id="libReader" aria-live="polite" aria-busy="false"></div>
      </main>
      <aside class="lib-rail" aria-label="About this page">
        <div id="libRail"></div>
      </aside>
    </div>`;

  const listEl = sectionEl.querySelector("#libList");
  const readerEl = sectionEl.querySelector("#libReader");
  const railEl = sectionEl.querySelector("#libRail");
  const filterEl = sectionEl.querySelector("#libFilter");
  const layoutEl = sectionEl.querySelector(".lib-layout");
  const treeToggle = sectionEl.querySelector("#libTreeToggle");
  // home + editor render without the infobox rail — the article spans it
  const setWide = (on) => layoutEl.classList.toggle("lib-layout--wide", !!on);

  const state = {
    docs: [],
    doc: null,          // currently open full doc
    history: null,      // version rows for the open doc (null until loaded)
    viewingVersion: null, // version row when previewing an old snapshot
    spy: null,          // IntersectionObserver for scrollspy
    expanded: loadExpanded(), // expanded dir paths (sessionStorage-backed)
    links: { at: 0, data: null, unavailable: false, inflight: null },
  };

  const loading = (el, msg) => { el.innerHTML = `<div class="loading"><span class="spinner"></span> ${esc(msg)}</div>`; };
  const saveExpanded = () => {
    try { sessionStorage.setItem(EXPANDED_KEY, JSON.stringify([...state.expanded])); } catch { /* private mode */ }
  };

  /* mobile "Contents" disclosure — the tree hides behind the masthead button */
  const mobileMq = window.matchMedia("(max-width: 899px)");
  const setTreeOpen = (open) => {
    layoutEl.classList.toggle("lib-layout--tree-open", !!open);
    treeToggle.setAttribute("aria-expanded", String(!!open));
  };
  treeToggle.addEventListener("click", () =>
    setTreeOpen(!layoutEl.classList.contains("lib-layout--tree-open")));
  const closeMobileTree = () => {
    if (!mobileMq.matches) return;
    if (layoutEl.classList.contains("lib-layout--tree-open")) {
      setTreeOpen(false);
      window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
    }
  };

  /* byline/infobox version + link counters */
  const verCountText = () => state.history
    ? `${state.history.length + 1} version${state.history.length ? "s" : ""}`
    : "…";
  const setVerTexts = (text) => {
    const t = text != null ? text : verCountText();
    const a = readerEl.querySelector("#libVerCount");
    if (a) a.textContent = t;
    const b = railEl.querySelector("#libFactVer");
    if (b) b.textContent = t;
  };
  const updateLinkFacts = (doc, data) => {
    const inEl = railEl.querySelector("#libFactIn");
    const outEl = railEl.querySelector("#libFactOut");
    if (!inEl || !outEl) return;
    if (!data) { inEl.textContent = "—"; outEl.textContent = "—"; return; }
    const nIn = data.links.filter(l => l.dst === doc.path).length;
    const nOut = data.links.filter(l => l.src === doc.path).length;
    const nRed = data.missing.filter(m => m.src === doc.path).length;
    inEl.textContent = String(nIn);
    outEl.textContent = nRed ? `${nOut} + ${nRed} red` : String(nOut);
  };

  /* ---------- wikilink resolver (per CONTRACT resolution order) ---------- */
  function makeResolver(currentDir) {
    const docs = state.docs.filter(d => d.path && (!d.lifecycle_state || d.lifecycle_state === "active"));
    const byTitle = new Map(), byPath = new Map(), bySuffix = new Map();
    for (const d of docs) {
      const t = String(d.title || "").trim().toLowerCase();
      if (t && !byTitle.has(t)) byTitle.set(t, d.path);
      byPath.set(d.path, d.path);
      const noExt = d.path.replace(/\.md$/i, "").toLowerCase();
      const base = noExt.split("/").pop();
      if (!bySuffix.has(base)) bySuffix.set(base, []);
      bySuffix.get(base).push({ noExt, path: d.path });
    }
    const found = (path) => ({ href: "#library?path=" + encodeURIComponent(path), exists: true, path });
    return (target) => {
      const t = String(target || "").trim().replace(/^(\.\/|\/)+/, "");
      if (!t) return null;
      const lower = t.toLowerCase();
      if (byTitle.has(lower)) return found(byTitle.get(lower));          // 1. exact title (ci)
      if (byPath.has(t)) return found(byPath.get(t));                    // 2. exact path
      const noExt = lower.replace(/\.md$/, "");
      const base = noExt.split("/").pop();
      const cands = (bySuffix.get(base) || [])
        .filter(c => c.noExt === noExt || c.noExt.endsWith("/" + noExt));
      if (cands.length === 1) return found(cands[0].path);               // 3. unique suffix
      return { exists: false, path: createPathFor(t, currentDir) };      // MISSING (ambiguous too)
    };
  }

  /* ---------- /kb.links (cached, tolerant of a backend without it) ------- */
  function fetchLinks() {
    if (state.links.inflight) return state.links.inflight;
    if (Date.now() - state.links.at < LINKS_TTL_MS && (state.links.data || state.links.unavailable)) {
      return Promise.resolve(state.links.data);
    }
    state.links.inflight = (async () => {
      try {
        const raw = await api.get("/kb.links");
        state.links.data = normalizeLinks(raw);
        state.links.unavailable = false;
      } catch {
        // 404 (endpoint not shipped) or transient failure — hide the panels quietly.
        state.links.data = null;
        state.links.unavailable = true;
      }
      state.links.at = Date.now();
      state.links.inflight = null;
      return state.links.data;
    })();
    return state.links.inflight;
  }
  const invalidateLinks = () => { state.links.at = 0; state.links.data = null; state.links.unavailable = false; };

  /* ---------- directory tree ---------- */
  async function loadList() {
    listEl.setAttribute("aria-busy", "true");
    loading(listEl, "loading library…");
    try {
      const data = await api.get("/kb.docs");
      state.docs = data.docs || [];
      const mast = sectionEl.querySelector("#libMastCount");
      if (mast) mast.textContent = state.docs.length
        ? `${state.docs.length} page${state.docs.length === 1 ? "" : "s"}`
        : "";
      renderTree();
    } catch (e) {
      error(listEl, e);
    } finally {
      listEl.setAttribute("aria-busy", "false");
    }
  }

  function buildTreeModel(docs) {
    const root = { dirs: new Map(), docs: [] };
    for (const d of docs) {
      const parts = String(d.path || "").split("/").filter(Boolean);
      let node = root;
      for (let i = 0; i < parts.length - 1; i++) {
        const seg = parts[i];
        if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), docs: [] });
        node = node.dirs.get(seg);
      }
      node.docs.push(d);
    }
    return root;
  }
  function countDocs(node) {
    let n = node.docs.length;
    for (const child of node.dirs.values()) n += countDocs(child);
    return n;
  }

  function renderTree() {
    const filter = (filterEl.value || "").trim().toLowerCase();
    const docs = state.docs.filter(d => !filter || ((d.title || "") + " " + (d.path || "")).toLowerCase().includes(filter));
    if (!docs.length) {
      listEl.innerHTML = `<div class="empty">${state.docs.length
        ? "No documents match the filter."
        : `<span class="big">An empty library</span>Start with your first governed doc — a brand bible, a runbook, an eval report.`}</div>`;
      return;
    }
    const root = buildTreeModel(docs);
    const tree = document.createElement("ul");
    tree.className = "lib-tree";
    renderNode(root, tree, "", !!filter); // filter -> auto-expand all matches
    listEl.innerHTML = "";
    listEl.appendChild(tree);
    markActiveListItem();
    initRoving();
  }

  function renderNode(node, ul, prefix, forceOpen) {
    const names = [...node.dirs.keys()];
    const ordered = prefix === ""
      ? [...KNOWN_GROUPS.filter(g => node.dirs.has(g)), ...names.filter(g => !KNOWN_GROUPS.includes(g)).sort()]
      : names.sort();
    for (const name of ordered) {
      const dirPath = prefix ? prefix + "/" + name : name;
      const child = node.dirs.get(name);
      const open = forceOpen || state.expanded.has(dirPath);
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "lib-tree-dir";
      btn.dataset.dir = dirPath;
      btn.setAttribute("aria-expanded", String(open));
      btn.innerHTML = `<span class="lib-caret" aria-hidden="true"></span><span class="lib-doc-name">${esc(name)}/</span><span class="lib-count mono">${countDocs(child)}</span>`;
      const group = document.createElement("ul");
      group.className = "lib-tree-group";
      group.hidden = !open;
      btn.addEventListener("click", () => {
        const isOpen = btn.getAttribute("aria-expanded") === "true";
        btn.setAttribute("aria-expanded", String(!isOpen));
        group.hidden = isOpen;
        if (isOpen) state.expanded.delete(dirPath); else state.expanded.add(dirPath);
        saveExpanded();
      });
      li.appendChild(btn);
      li.appendChild(group);
      ul.appendChild(li);
      renderNode(child, group, dirPath, forceOpen);
    }
    const docsSorted = [...node.docs].sort((a, b) =>
      String(a.title || a.path || "").localeCompare(String(b.title || b.path || "")));
    for (const d of docsSorted) {
      const li = document.createElement("li");
      li.appendChild(docButton(d));
      ul.appendChild(li);
    }
  }

  function docButton(d) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "lib-tree-doc";
    b.dataset.docId = d.id;
    b.dataset.docPath = d.path || "";
    b.title = d.path || "";
    const flags = [];
    if (d.scope === "personal") flags.push('<span class="pill pill--scope">personal</span>');
    if (d.lifecycle_state && d.lifecycle_state !== "active") flags.push(`<span class="pill pill--off">${esc(d.lifecycle_state)}</span>`);
    b.innerHTML = `<span class="lib-doc-name">${esc(d.title || d.path)}</span>${flags.length ? `<span class="lib-leaf-meta">${flags.join("")}</span>` : ""}`;
    b.addEventListener("click", () => openDoc(d.id));
    return b;
  }

  function markActiveListItem() {
    const id = state.doc && state.doc.id;
    listEl.querySelectorAll(".lib-tree-doc").forEach(b => {
      if (id && b.dataset.docId === id) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
  }

  /* Expand the ancestors of a path so its leaf is visible in the tree. */
  function ensureVisible(path) {
    if (!path) return;
    const parts = String(path).split("/").filter(Boolean);
    parts.pop();
    let prefix = "", changed = false;
    for (const seg of parts) {
      prefix = prefix ? prefix + "/" + seg : seg;
      if (!state.expanded.has(prefix)) { state.expanded.add(prefix); changed = true; }
    }
    if (changed) { saveExpanded(); renderTree(); }
  }

  /* Tree keyboard support: roving tabindex + arrows (disclosure pattern). */
  function visibleTreeButtons() {
    return [...listEl.querySelectorAll(".lib-tree-dir, .lib-tree-doc")].filter(b => b.offsetParent !== null);
  }
  function initRoving() {
    const items = visibleTreeButtons();
    if (!items.length) return;
    items.forEach(b => { b.tabIndex = -1; });
    const current = items.find(b => b.getAttribute("aria-current") === "true");
    (current || items[0]).tabIndex = 0;
  }
  listEl.addEventListener("focusin", (e) => {
    if (!e.target.matches || !e.target.matches(".lib-tree-dir, .lib-tree-doc")) return;
    visibleTreeButtons().forEach(b => { b.tabIndex = b === e.target ? 0 : -1; });
  });
  listEl.addEventListener("keydown", (e) => {
    const t = e.target;
    if (!t.matches || !t.matches(".lib-tree-dir, .lib-tree-doc")) return;
    const items = visibleTreeButtons();
    const idx = items.indexOf(t);
    if (idx < 0) return;
    const focus = (el) => { if (el) { el.tabIndex = 0; t.tabIndex = el === t ? 0 : -1; el.focus(); } };
    const isDir = t.classList.contains("lib-tree-dir");
    if (e.key === "ArrowDown") { e.preventDefault(); focus(items[idx + 1]); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focus(items[idx - 1]); }
    else if (e.key === "Home") { e.preventDefault(); focus(items[0]); }
    else if (e.key === "End") { e.preventDefault(); focus(items[items.length - 1]); }
    else if (e.key === "ArrowRight") {
      e.preventDefault();
      if (isDir && t.getAttribute("aria-expanded") === "false") t.click();
      else focus(items[idx + 1]);
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      if (isDir && t.getAttribute("aria-expanded") === "true") t.click();
      else {
        const group = t.closest("ul.lib-tree-group");
        const parentBtn = group && group.parentElement.querySelector(":scope > .lib-tree-dir");
        focus(parentBtn);
      }
    }
    // Enter/Space: native button activation.
  });

  filterEl.addEventListener("input", renderTree);
  sectionEl.querySelector("#libNewBtn").addEventListener("click", () => renderEditor(null));

  /* ---------- reader ---------- */
  async function openDoc(idOrPath, { updateHash = true } = {}) {
    readerEl.setAttribute("aria-busy", "true");
    loading(readerEl, "opening document…");
    railEl.innerHTML = "";
    state.viewingVersion = null;
    state.history = null;
    try {
      const { doc } = await api.get("/kb.get_doc", { id: idOrPath });
      if (!doc) {
        readerEl.innerHTML = `<div class="empty">Document not found${idOrPath ? ` — <span class="mono">${esc(String(idOrPath))}</span>` : ""}.</div>`;
        return;
      }
      state.doc = doc;
      if (updateHash && doc.path) {
        // replaceState does NOT fire hashchange, so no suppression flag is needed.
        history.replaceState(null, "", "#library?path=" + encodeURIComponent(doc.path));
      }
      renderReader(doc);
      ensureVisible(doc.path);
      markActiveListItem();
      loadRail(doc);
      closeMobileTree();
    } catch (e) {
      error(readerEl, e);
    } finally {
      readerEl.setAttribute("aria-busy", "false");
    }
  }

  function crumbsHtml(path) {
    const parts = String(path || "").split("/").filter(Boolean);
    if (!parts.length) return "";
    const out = ['<nav class="lib-crumbs" aria-label="Document location">',
      '<button type="button" data-crumb-home="1">Wiki home</button><span class="lib-crumb-sep" aria-hidden="true">/</span>'];
    let prefix = "";
    parts.forEach((seg, i) => {
      if (i < parts.length - 1) {
        prefix = prefix ? prefix + "/" + seg : seg;
        out.push(`<button type="button" data-crumb="${esc(prefix)}">${esc(seg)}</button><span class="lib-crumb-sep" aria-hidden="true">/</span>`);
      } else {
        out.push(`<span class="lib-crumb-leaf">${esc(seg)}</span>`);
      }
    });
    out.push("</nav>");
    return out.join("");
  }

  function renderReader(doc, opts = {}) {
    setWide(false);
    const { versionBanner = "", body = doc.body } = opts;
    const editable = doc.lifecycle_state === "active" || !doc.lifecycle_state;
    const resolveLink = makeResolver(dirOf(doc.path));
    const sep = '<span aria-hidden="true">·</span>';
    const bylineBits = [
      doc.path ? `<span>${esc(doc.path)}</span>` : "",
      doc.updated_at ? `<span>edited ${esc(relTime(doc.updated_at))}</span>` : "",
      `<span id="libVerCount">${esc(verCountText())}</span>`,
    ].filter(Boolean).join(sep);
    const bylinePills = [
      doc.scope === "personal" ? '<span class="pill pill--scope">personal</span>' : "",
      doc.lifecycle_state && doc.lifecycle_state !== "active"
        ? `<span class="pill pill--off">${esc(doc.lifecycle_state)}</span>` : "",
      doc.review_due_at && new Date(doc.review_due_at).getTime() < Date.now()
        ? '<span class="pill pill--locked">review overdue</span>' : "",
    ].filter(Boolean).join("");
    readerEl.innerHTML = `
      ${versionBanner}
      <div class="lib-art-topline">
        ${crumbsHtml(doc.path)}
        <div class="lib-art-actions">
          ${editable && !state.viewingVersion ? '<button class="btn btn--sm" id="libEditBtn" type="button">Edit</button>' : ""}
          ${doc.lifecycle_state !== "tombstoned" && !state.viewingVersion ? '<button class="btn btn--sm" id="libDeleteBtn" type="button">Delete</button>' : ""}
        </div>
      </div>
      <h1 class="lib-art-title">${esc(doc.title || doc.path || "Untitled")}</h1>
      <div class="lib-byline">${bylineBits}${bylinePills}</div>
      <div class="lib-md-scroll"><div class="md lib-md" id="libMd">${body != null ? md(body, { resolveLink }) : `<div class="empty">This snapshot's body is not exposed by the backend — metadata only.</div>`}</div></div>`;
    // Wiki furniture: the title already heads the page — when the body opens
    // with an identical H1, fold it away (display only; content untouched).
    const firstEl = readerEl.querySelector("#libMd") && readerEl.querySelector("#libMd").firstElementChild;
    if (firstEl && firstEl.tagName === "H1" &&
        firstEl.textContent.trim() === String(doc.title || "").trim()) {
      firstEl.classList.add("lib-dup-title");
    }
    hintRedLinks(readerEl.querySelector("#libMd"));
    const editBtn = readerEl.querySelector("#libEditBtn");
    if (editBtn) editBtn.addEventListener("click", () => renderEditor(doc));
    const delBtn = readerEl.querySelector("#libDeleteBtn");
    if (delBtn) delBtn.addEventListener("click", () => deleteDoc(doc));
    readerEl.querySelectorAll("[data-crumb]").forEach(b => b.addEventListener("click", () => {
      filterEl.value = b.dataset.crumb + "/";
      renderTree();
      if (mobileMq.matches) setTreeOpen(true);
    }));
    const homeCrumb = readerEl.querySelector("[data-crumb-home]");
    if (homeCrumb) homeCrumb.addEventListener("click", () => {
      try { history.replaceState(null, "", "#library"); } catch { /* sandboxed */ }
      renderHome();
    });
    buildToc();
  }

  /* Wiki navigation: intercept .wl / .wl-missing anchors (event delegation).
     Inside the live editor preview links are inert so edits are never lost. */
  readerEl.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a.wl, a.wl-missing");
    if (!a || !readerEl.contains(a)) return;
    e.preventDefault();
    if (a.closest("[data-preview]")) return;
    if (a.classList.contains("wl-missing")) {
      startCreate(a.dataset.create || "", a.dataset.wlTarget || "");
    } else if (a.dataset.path) {
      openDoc(a.dataset.path);
    }
  });

  function startCreate(path, targetText) {
    const base = String(path || "").split("/").pop().replace(/\.md$/i, "");
    const title = String(targetText || base.replace(/-/g, " ")).trim();
    renderEditor(null, { path, title });
  }

  async function deleteDoc(doc) {
    const reason = window.prompt(`Deletion reason for ${doc.path}:`, "Retired from governed memory");
    if (!reason || reason.trim().length < 3) return;
    if (!window.confirm("Delete retrievable content and all local projections? This action is recorded.")) return;
    try {
      const data = await api.post("/kb.delete_doc?id=" + encodeURIComponent(doc.id), { reason: reason.trim() });
      const complete = data && data.deletion && data.deletion.complete;
      toast(complete ? "ok" : "warn", complete ? "Document deleted" : "Content deleted; projection cleanup is pending");
      invalidateLinks();
      bus.emit("wiki:links-changed");
      await loadList();
      openDoc(doc.id, { updateHash: false });
    } catch (e) { toast("err", e.message); }
  }

  /* ---------- editor (side-by-side live preview + [[ autocomplete) ------- */
  function renderEditor(doc, extras = {}) {
    setWide(true);
    const isNew = !doc;
    const d = doc || {
      path: extras.path || "",
      body: extras.title ? `# ${extras.title}\n\n` : "",
      scope: "personal",
      review_due_at: "",
    };
    railEl.innerHTML = "";
    readerEl.innerHTML = `
      <h1 class="lib-art-title" style="max-width:78ch;">${isNew ? "New document" : "Edit document"}</h1>
      <div class="lib-byline"><span>markdown</span><span aria-hidden="true">·</span><span>[[ autocompletes a wikilink</span><span aria-hidden="true">·</span><span>preview renders live</span></div>
      <div class="field" style="max-width:44rem;"><label class="lbl" for="libEdPath">Path</label>
        <input class="input mono" id="libEdPath" value="${esc(d.path)}" placeholder="engineering/title.md" ${isNew ? "" : "readonly"} /></div>
      <div class="row" style="margin-bottom:.8rem; flex-wrap:wrap; gap:.6rem; align-items:flex-start;">
        <div class="field" style="margin:0; flex:1 1 9rem; max-width:12rem;"><label class="lbl" for="libEdScope">Visibility</label>
          <select class="select" id="libEdScope">
            <option ${d.scope === "personal" ? "selected" : ""}>personal</option>
            <option ${d.scope === "global" ? "selected" : ""}>global</option>
          </select></div>
        ${isNew ? `
        <div class="field" style="margin:0; flex:1 1 11rem; max-width:14rem;"><label class="lbl" for="libEdValidFrom">Valid from</label>
          <input class="input" id="libEdValidFrom" type="datetime-local" /></div>
        <div class="field" style="margin:0; flex:1 1 11rem; max-width:14rem;"><label class="lbl" for="libEdSupersedes">Supersedes ID</label>
          <input class="input mono" id="libEdSupersedes" /></div>` : ""}
        <div class="field" style="margin:0; flex:1 1 11rem; max-width:14rem;"><label class="lbl" for="libEdReviewDue">Review due</label>
          <input class="input" id="libEdReviewDue" type="datetime-local" value="${esc(localDateValue(d.review_due_at))}" /></div>
      </div>
      <div class="lib-ed-toggle" role="group" aria-label="Editor pane">
        <button class="btn btn--sm" id="libEdModeW" type="button" aria-pressed="true"
                data-hint="Write markdown — type [[ to autocomplete a wikilink to any page.">Write</button>
        <button class="btn btn--sm" id="libEdModeP" type="button" aria-pressed="false"
                data-hint-title="Live preview" data-hint="Rendered exactly as readers will see it — red links mark pages that don't exist yet.">Preview</button>
      </div>
      <div class="lib-ed-split" data-mode="write">
        <div class="field lib-ed-pane-write" style="margin:0;"><label class="lbl" for="libEdBody">Markdown</label>
          <div class="lib-ed-wrap"><textarea class="textarea mono" id="libEdBody" style="min-height:340px;">${esc(d.body || "")}</textarea></div></div>
        <div class="lib-ed-pane-preview" style="min-width:0;"><span class="lbl" id="libEdPreviewLbl">Preview</span>
          <div class="md lib-ed-preview" id="libEdPreview" data-preview="1" role="region" aria-labelledby="libEdPreviewLbl"></div></div>
      </div>
      <div class="row" style="gap:.5rem; margin-top:.85rem;">
        <button class="btn btn--primary" id="libEdSave" type="button">Save</button>
        <button class="btn" id="libEdCancel" type="button">Cancel</button>
      </div>`;
    const pathInput = readerEl.querySelector("#libEdPath");
    const bodyInput = readerEl.querySelector("#libEdBody");
    const previewEl = readerEl.querySelector("#libEdPreview");
    // Live preview: 150ms debounce, SAME resolver as the reader so wikilinks
    // (and red-links) render exactly as they will after save.
    let previewTimer = 0;
    const renderPreview = () => {
      previewEl.innerHTML = md(bodyInput.value, { resolveLink: makeResolver(dirOf(pathInput.value.trim())) });
      hintRedLinks(previewEl);
    };
    const schedulePreview = () => { clearTimeout(previewTimer); previewTimer = setTimeout(renderPreview, 150); };
    bodyInput.addEventListener("input", schedulePreview);
    pathInput.addEventListener("input", schedulePreview);
    renderPreview();

    // Small screens: Write/Preview toggle (desktop shows both side by side).
    const split = readerEl.querySelector(".lib-ed-split");
    const modeW = readerEl.querySelector("#libEdModeW");
    const modeP = readerEl.querySelector("#libEdModeP");
    const setMode = (m) => {
      split.dataset.mode = m;
      modeW.setAttribute("aria-pressed", String(m === "write"));
      modeP.setAttribute("aria-pressed", String(m === "preview"));
      if (m === "preview") renderPreview(); // flush the debounce
    };
    modeW.addEventListener("click", () => setMode("write"));
    modeP.addEventListener("click", () => setMode("preview"));

    attachWikilinkAutocomplete(bodyInput, schedulePreview);

    readerEl.querySelector("#libEdCancel").addEventListener("click", () => {
      if (isNew) renderHome();
      else openDoc(d.id, { updateHash: false });
    });
    readerEl.querySelector("#libEdSave").addEventListener("click", async () => {
      const path = pathInput.value.trim();
      const body = bodyInput.value;
      if (!path) { toast("err", "Path is required"); return; }
      if (!body.trim()) { toast("err", "Body is required"); return; }
      const saveBtn = readerEl.querySelector("#libEdSave");
      saveBtn.disabled = true;
      try {
        const payload = {
          path,
          md: body,
          scope: readerEl.querySelector("#libEdScope").value,
          review_due_at: toIso(readerEl.querySelector("#libEdReviewDue").value),
        };
        if (isNew) {
          payload.valid_from = toIso(readerEl.querySelector("#libEdValidFrom").value);
          payload.supersedes_id = readerEl.querySelector("#libEdSupersedes").value.trim() || undefined;
        }
        const { id } = await api.post("/kb.upsert_doc", payload);
        toast("ok", "Saved");
        invalidateLinks();
        bus.emit("wiki:links-changed"); // constellation refreshes link edges lazily
        await loadList();
        openDoc(id);
      } catch (e) {
        toast("err", e.message);
        saveBtn.disabled = false;
      }
    });
    // Red-link create flow prefills path+title and focuses the editor body;
    // a plain +New starts at the path field.
    if (isNew && extras.path) bodyInput.focus();
    else if (isNew) pathInput.focus();
  }

  /* [[wikilink]] autocomplete: a caret-anchored listbox over the textarea.
     Combobox-pattern a11y (aria-activedescendant), zero deps, closes on
     Esc / blur / scroll. Enter/Tab accept; the closing ]] is added if absent. */
  function attachWikilinkAutocomplete(ta, onAccept) {
    const wrap = ta.closest(".lib-ed-wrap");
    if (!wrap) return;
    let box = null, items = [], active = 0, wl = null;
    ta.setAttribute("aria-autocomplete", "list");
    ta.setAttribute("aria-expanded", "false");
    const close = () => {
      if (box) box.remove();
      box = null; items = []; wl = null;
      ta.removeAttribute("aria-activedescendant");
      ta.setAttribute("aria-expanded", "false");
    };
    const wlContext = () => {
      const caret = ta.selectionStart;
      const before = ta.value.slice(0, caret);
      const open = before.lastIndexOf("[[");
      if (open < 0) return null;
      const frag = before.slice(open + 2);
      if (frag.length > 80 || /[\]\n|]/.test(frag)) return null;
      return { start: open + 2, frag };
    };
    const candidatesFor = (frag) => {
      const q = frag.trim().toLowerCase();
      const docs = state.docs.filter(d => d.path && (!d.lifecycle_state || d.lifecycle_state === "active"));
      const titleCount = new Map();
      for (const d of docs) {
        const t = String(d.title || "").trim().toLowerCase();
        if (t) titleCount.set(t, (titleCount.get(t) || 0) + 1);
      }
      const scored = [];
      for (const d of docs) {
        const title = String(d.title || d.path);
        const tl = title.toLowerCase(), pl = String(d.path).toLowerCase();
        let rank = -1;
        if (!q) rank = 2;
        else if (tl.startsWith(q) || pl.split("/").pop().startsWith(q)) rank = 0;
        else if (tl.includes(q) || pl.includes(q)) rank = 1;
        if (rank < 0) continue;
        const t = String(d.title || "").trim().toLowerCase();
        const insert = (t && titleCount.get(t) === 1) ? String(d.title).trim() : d.path;
        scored.push({ rank, title, path: d.path, insert, upd: d.updated_at || "" });
      }
      scored.sort((a, b) => a.rank - b.rank || String(b.upd).localeCompare(String(a.upd)) || a.title.localeCompare(b.title));
      return scored.slice(0, 8);
    };
    const caretXY = () => {
      const cs = getComputedStyle(ta);
      const mirror = document.createElement("div");
      for (const p of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "tabSize",
                       "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
                       "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "boxSizing"]) {
        mirror.style[p] = cs[p];
      }
      mirror.style.position = "absolute";
      mirror.style.left = "0"; mirror.style.top = "0";
      mirror.style.visibility = "hidden";
      mirror.style.pointerEvents = "none";
      mirror.style.whiteSpace = "pre-wrap";
      mirror.style.overflowWrap = "break-word";
      mirror.style.width = ta.clientWidth + "px";
      mirror.textContent = ta.value.slice(0, ta.selectionStart);
      const mark = document.createElement("span");
      mark.textContent = "​";
      mirror.appendChild(mark);
      wrap.appendChild(mirror);
      const lh = parseFloat(cs.lineHeight) || 22;
      const pos = { top: mark.offsetTop + lh - ta.scrollTop + 4, left: mark.offsetLeft };
      mirror.remove();
      // keep the popover pinned to the textarea's visible box even when the
      // caret sits outside the scrolled viewport
      pos.top = Math.max(8, Math.min(pos.top, ta.clientHeight + 4));
      return pos;
    };
    const accept = (c) => {
      if (!c || !wl) return;
      const caret = ta.selectionStart;
      const after = ta.value.slice(caret);
      const needsClose = !after.startsWith("]]");
      ta.value = ta.value.slice(0, wl.start) + c.insert + (needsClose ? "]]" : "") + after;
      const pos = wl.start + c.insert.length + 2; // land past the ]]
      close();
      ta.setSelectionRange(pos, pos);
      ta.focus();
      onAccept();
    };
    const render = () => {
      if (!items.length) { close(); return; }
      if (!box) {
        box = document.createElement("div");
        box.className = "lib-wl-auto";
        box.id = "libWlAuto";
        box.setAttribute("role", "listbox");
        box.setAttribute("aria-label", "Wikilink suggestions");
        wrap.appendChild(box);
        ta.setAttribute("aria-controls", "libWlAuto");
        ta.setAttribute("aria-expanded", "true");
      }
      box.innerHTML = items.map((c, i) =>
        `<div class="lib-wl-opt" id="libWlOpt${i}" role="option" aria-selected="${i === active}">${esc(c.title)}<span class="lib-wl-path">${esc(c.path)}</span></div>`).join("");
      ta.setAttribute("aria-activedescendant", "libWlOpt" + active);
      box.querySelectorAll(".lib-wl-opt").forEach((el, i) => {
        el.addEventListener("pointerdown", (e) => { e.preventDefault(); accept(items[i]); });
      });
      const p = caretXY();
      box.style.top = p.top + "px";
      box.style.left = "0px";
      const maxLeft = Math.max(0, wrap.clientWidth - box.offsetWidth - 4);
      box.style.left = Math.min(p.left, maxLeft) + "px";
      const sel = box.children[active];
      if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
    };
    const update = () => {
      wl = wlContext();
      if (!wl) { close(); return; }
      items = candidatesFor(wl.frag);
      active = 0;
      render();
    };
    ta.addEventListener("input", update);
    ta.addEventListener("click", () => { if (box) update(); });
    ta.addEventListener("keydown", (e) => {
      if (!box || !items.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
      else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); accept(items[active]); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    });
    ta.addEventListener("blur", () => setTimeout(close, 140));
    ta.addEventListener("scroll", close);
  }

  /* ---------- right rail: infobox + TOC + timeline + backlinks + ask ----- */
  async function loadRail(doc) {
    railEl.innerHTML = `
      <div class="card lib-infobox" id="libInfobox">
        <div class="lib-infobox-cap">${esc(doc.title || doc.path || "Untitled")}</div>
        <dl class="lib-facts">
          <dt>Scope</dt><dd>${esc(doc.scope || "global")}</dd>
          <dt>Owner</dt><dd>${esc(doc.owner || "—")}</dd>
          <dt>State</dt><dd>${esc(doc.lifecycle_state || "active")}</dd>
          <dt>Updated</dt><dd>${esc(relTime(doc.updated_at) || "—")}</dd>
          ${doc.review_due_at ? `<dt>Review due</dt><dd>${esc(relTime(doc.review_due_at))}</dd>` : ""}
          ${doc.provenance ? `<dt>Source</dt><dd>${esc(doc.provenance.source_type)}</dd>` : ""}
          <dt>Links in</dt><dd id="libFactIn">…</dd>
          <dt>Links out</dt><dd id="libFactOut">…</dd>
          <dt>Versions</dt><dd id="libFactVer">${esc(verCountText())}</dd>
        </dl>
      </div>
      <div class="lib-rail-sec">
        <span class="kicker">On this page</span>
        <ul class="lib-toc" id="libToc"><li class="empty" style="padding:.4rem 0;">No headings.</li></ul>
      </div>
      <div class="lib-rail-sec">
        <span class="kicker" data-hint-title="Version timeline" data-hint="Every save is kept. Open any version to read that snapshot — current brings you back.">History</span>
        <ul class="lib-timeline" id="libTimeline" aria-live="polite"><li class="loading"><span class="spinner"></span> loading…</li></ul>
      </div>
      <div class="lib-rail-sec" id="libLinksCard" aria-live="polite" hidden>
        <span class="kicker" data-hint="Reverse citations — every page whose prose links here.">Linked from · <span id="libBlCount">0</span></span>
        <ul class="lib-links" id="libBacklinks"></ul>
        <div id="libWantedWrap" hidden>
          <span class="kicker" style="margin-top:.9rem;" data-hint="Red links on this page — targets that don't exist yet. Click one to create it.">Wanted pages · <span id="libWantedCount">0</span></span>
          <ul class="lib-links" id="libWanted"></ul>
        </div>
      </div>
      <button class="btn" id="libAskBtn" type="button" style="width:100%;">Ask about this doc</button>`;
    railEl.querySelector("#libAskBtn").addEventListener("click", () => {
      const query = `What does "${doc.title || doc.path}" cover, and what decisions does it record?`;
      location.hash = "#ask";
      bus.emit("ask:run", { query, scope: doc.scope || undefined, k: 6 });
    });
    buildToc();
    renderLinksPanel(doc);
    const tlHost = railEl.querySelector("#libTimeline");
    try {
      const { history: hist } = await api.get("/kb.doc_history", { id: doc.id });
      // rail may have been re-rendered (another doc opened) while we awaited
      if (railEl.querySelector("#libTimeline") !== tlHost) return;
      state.history = hist || [];
      renderTimeline(doc);
      setVerTexts();
    } catch (e) {
      if (railEl.querySelector("#libTimeline") !== tlHost) return;
      tlHost.innerHTML = `<li class="empty" style="padding:.4rem 0;">${esc(e.message)}</li>`;
      setVerTexts("—");
    }
  }

  async function renderLinksPanel(doc) {
    const card = railEl.querySelector("#libLinksCard");
    if (!card) return;
    const data = await fetchLinks();
    // rail may have been re-rendered (another doc opened) while we awaited
    if (railEl.querySelector("#libLinksCard") !== card) return;
    updateLinkFacts(doc, data);
    if (!data) { card.hidden = true; return; } // /kb.links absent — hide quietly
    const backlinks = data.links.filter(l => l.dst === doc.path);
    const wanted = data.missing.filter(m => m.src === doc.path);
    const titleOf = (p) => {
      const d = state.docs.find(x => x.path === p);
      return d ? (d.title || d.path) : p;
    };
    card.querySelector("#libBlCount").textContent = String(backlinks.length);
    card.querySelector("#libBacklinks").innerHTML = backlinks.length
      ? backlinks.map(l => `<li><button type="button" data-open="${esc(l.src)}" title="${esc(l.src)}">${esc(titleOf(l.src))}</button></li>`).join("")
      : `<li class="empty" style="padding:.4rem 0;">No pages link here yet.</li>`;
    const wrap = card.querySelector("#libWantedWrap");
    if (wanted.length) {
      wrap.hidden = false;
      card.querySelector("#libWantedCount").textContent = String(wanted.length);
      card.querySelector("#libWanted").innerHTML = wanted.map(w =>
        `<li><button type="button" class="lib-wanted-btn" data-create-target="${esc(w.target)}">${esc(w.target)}</button></li>`).join("");
    } else {
      wrap.hidden = true;
    }
    card.hidden = false;
    card.querySelectorAll("[data-open]").forEach(b =>
      b.addEventListener("click", () => openDoc(b.dataset.open)));
    card.querySelectorAll("[data-create-target]").forEach(b =>
      b.addEventListener("click", () => {
        const target = b.dataset.createTarget;
        startCreate(createPathFor(target, dirOf(doc.path)), target);
      }));
  }

  function renderTimeline(doc) {
    const tl = railEl.querySelector("#libTimeline");
    if (!tl) return;
    const items = [];
    items.push(`<li><button type="button" data-version="current" ${!state.viewingVersion ? 'aria-current="true"' : ""}>
        <strong>current</strong> · ${esc(relTime(doc.updated_at))}
      </button></li>`);
    (state.history || []).forEach(h => {
      const active = state.viewingVersion && state.viewingVersion.version_id === h.version_id;
      items.push(`<li><button type="button" data-version="${esc(String(h.version_id))}" ${active ? 'aria-current="true"' : ""}>
          v${esc(String(h.version_id))} · ${esc(relTime(h.created_at))} <span class="mono" style="font-size:.9em;">(${esc(String(h.bytes))} B)</span>
        </button></li>`);
    });
    if (!(state.history || []).length) {
      items.push(`<li class="empty" style="padding:.4rem 0 .4rem 1.6rem;">First revision — no prior versions.</li>`);
    }
    tl.innerHTML = items.join("");
    tl.querySelectorAll("button[data-version]").forEach(b => {
      b.addEventListener("click", () => {
        const v = b.dataset.version;
        if (v === "current") {
          state.viewingVersion = null;
          renderReader(doc);
        } else {
          previewVersion(doc, (state.history || []).find(h => String(h.version_id) === v));
        }
        renderTimeline(doc);
      });
    });
  }

  async function previewVersion(doc, row) {
    if (!row) return;
    state.viewingVersion = row;
    const banner = `<div class="lib-version-banner" role="status">
        <span>Viewing <strong>v${esc(String(row.version_id))}</strong> · ${esc(fmtTime(row.created_at))} — read-only snapshot</span>
        <button class="btn btn--sm" id="libBackCurrent" type="button">Back to current</button>
      </div>`;
    // Version bodies: prefer an inline body if the backend ever includes one;
    // otherwise try a dedicated endpoint; otherwise degrade to metadata-only.
    let body = row.body != null ? row.body : null;
    if (body == null) {
      try {
        const res = await api.get("/kb.doc_version", { id: doc.id, version_id: row.version_id });
        if (res && res.version && res.version.body != null) body = res.version.body;
      } catch { /* endpoint absent on this backend build — degrade gracefully */ }
    }
    renderReader(doc, { versionBanner: banner, body });
    const back = readerEl.querySelector("#libBackCurrent");
    if (back) back.addEventListener("click", () => {
      state.viewingVersion = null;
      renderReader(doc);
      renderTimeline(doc);
    });
  }

  function buildToc() {
    const tocEl = railEl.querySelector("#libToc");
    const mdEl = readerEl.querySelector("#libMd");
    if (state.spy) { state.spy.disconnect(); state.spy = null; }
    if (!tocEl || !mdEl) return;
    const heads = Array.from(mdEl.querySelectorAll("h2, h3"));
    if (!heads.length) {
      tocEl.innerHTML = `<li class="empty" style="padding:.4rem 0;">No headings.</li>`;
      return;
    }
    heads.forEach((h, i) => { if (!h.id) h.id = "lib-h-" + i; });
    tocEl.innerHTML = heads.map(h =>
      `<li data-level="${h.tagName === "H3" ? 3 : 2}"><button type="button" data-target="${esc(h.id)}">${esc(h.textContent)}</button></li>`
    ).join("");
    const buttons = Array.from(tocEl.querySelectorAll("button[data-target]"));
    buttons.forEach(b => b.addEventListener("click", () => {
      const target = document.getElementById(b.dataset.target);
      if (target) target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    }));
    // scrollspy: highlight the heading nearest the top of the viewport
    const setCurrent = (id) => buttons.forEach(b => {
      if (b.dataset.target === id) b.setAttribute("aria-current", "true");
      else b.removeAttribute("aria-current");
    });
    const visible = new Set();
    state.spy = new IntersectionObserver(entries => {
      entries.forEach(en => {
        if (en.isIntersecting) visible.add(en.target.id);
        else visible.delete(en.target.id);
      });
      if (visible.size) {
        const first = heads.find(h => visible.has(h.id));
        if (first) setCurrent(first.id);
      } else {
        // nothing in the band — highlight the last heading above the viewport
        let last = null;
        for (const h of heads) {
          if (h.getBoundingClientRect().top < 120) last = h; else break;
        }
        if (last) setCurrent(last.id);
      }
    }, { rootMargin: "-10% 0px -70% 0px" });
    heads.forEach(h => state.spy.observe(h));
    setCurrent(heads[0].id);
  }

  /* ---------- wiki home: the portal page ---------- */
  async function renderHome() {
    setWide(true);
    state.doc = null;
    state.viewingVersion = null;
    state.history = null;
    if (state.spy) { state.spy.disconnect(); state.spy = null; }
    railEl.innerHTML = "";
    markActiveListItem();
    const docs = state.docs.filter(d => !d.lifecycle_state || d.lifecycle_state === "active");
    const start = docs.find(d => /^(index|home)\.md$/i.test(d.path || ""));
    const recent = docs.filter(d => d.updated_at)
      .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
      .slice(0, 15);
    const sections = new Set(docs.map(d => {
      const p = String(d.path || "");
      const i = p.indexOf("/");
      return i > 0 ? p.slice(0, i) : null;
    }).filter(Boolean)).size;
    const sep = '<span aria-hidden="true">·</span>';
    const statBits = [
      `<span>${docs.length} page${docs.length === 1 ? "" : "s"}</span>`,
      sections ? `<span>${sections} section${sections === 1 ? "" : "s"}</span>` : "",
      recent.length ? `<span>last edit ${esc(relTime(recent[0].updated_at))}</span>` : "",
    ].filter(Boolean).join(sep);
    readerEl.innerHTML = `
      <div class="lib-lead">
        <span class="kicker">Wiki home</span>
        <h1 class="lib-art-title">The reading room</h1>
        <div class="lib-byline">${statBits}</div>
        <p class="lede" style="margin:.1rem 0 1.1rem;">Governed documents with lineage — every revision kept, every change recorded, every page linked.</p>
        ${start ? `<div class="lib-start">
            <div style="flex:1; min-width:min(100%, 14rem);">
              <span class="kicker">Start here</span>
              <div class="item__title">${esc(start.title || start.path)}</div>
              <div class="item__sub mono">${esc(start.path)}</div>
            </div>
            <button class="btn btn--sm" id="libStartBtn" type="button" style="flex:none;">Open</button>
          </div>` : ""}
      </div>
      <section aria-label="Recent changes" class="lib-rc-wrap">
        <h2 class="section" style="margin:1.7rem 0 .1rem;">Recent changes</h2>
        <div id="libRecent">${recent.length ? "" : `<div class="empty"><span class="big">Nothing on the shelves yet</span>Press <strong>+ New document</strong> to add the first governed page.</div>`}</div>
      </section>
      <section aria-label="Wanted pages and orphans" id="libHomeLinks">
        <div class="loading" style="margin-top:1.4rem;"><span class="spinner"></span> checking links…</div>
      </section>`;
    // recent changes: a dated changelog, newest day first
    const recentHost = readerEl.querySelector("#libRecent");
    let lastDay = null, dayUl = null;
    recent.forEach(d => {
      const k = dayStamp(d.updated_at);
      if (k !== lastDay || !dayUl) {
        lastDay = k;
        const h = document.createElement("div");
        h.className = "lib-rc-day";
        h.textContent = dayLabel(d.updated_at);
        recentHost.appendChild(h);
        dayUl = document.createElement("ul");
        dayUl.className = "lib-rc";
        recentHost.appendChild(dayUl);
      }
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<span class="lib-rc-time mono">${esc(timeLabel(d.updated_at))}</span>
        <span class="lib-rc-title">${esc(d.title || d.path)}</span>
        <span class="lib-rc-path mono">${esc(d.path || "")}</span>`;
      b.addEventListener("click", () => openDoc(d.id));
      li.appendChild(b);
      dayUl.appendChild(li);
    });
    const startBtn = readerEl.querySelector("#libStartBtn");
    if (startBtn) startBtn.addEventListener("click", () => openDoc(start.id));

    const data = await fetchLinks();
    const linksHost = readerEl.querySelector("#libHomeLinks");
    if (!linksHost) return; // navigated away while fetching
    if (!data) { linksHost.hidden = true; return; } // /kb.links absent — hide quietly
    const wantedMap = new Map();
    for (const m of data.missing) {
      if (!m.target) continue;
      if (!wantedMap.has(m.target)) wantedMap.set(m.target, { target: m.target, srcs: [] });
      if (m.src) wantedMap.get(m.target).srcs.push(m.src);
    }
    const wanted = [...wantedMap.values()].sort((a, b) => b.srcs.length - a.srcs.length).slice(0, 12);
    const dstSet = new Set(data.links.map(l => l.dst));
    const orphans = docs
      .filter(d => !dstSet.has(d.path) && (!start || d.id !== start.id))
      .slice(0, 12);
    linksHost.innerHTML = `
      <div class="lib-home-grid">
        <div>
          <h2 class="section" style="margin:1.4rem 0 .1rem;" data-hint-title="Wanted pages" data-hint="Red links collected across the wiki — pages other documents reference that don't exist yet. Click one to draft it.">Wanted pages <span class="pill" style="vertical-align:.18em;">${wanted.length}</span></h2>
          ${wanted.length
            ? `<ul class="lib-links">${wanted.map(w =>
                `<li><button type="button" class="lib-wanted-btn" data-target="${esc(w.target)}" data-src="${esc(w.srcs[0] || "")}">${esc(w.target)}${w.srcs.length > 1 ? ` <span class="mono lib-count">×${w.srcs.length}</span>` : ""}</button></li>`).join("")}</ul>`
            : `<p class="dim" style="margin:.45rem 0 1rem;">No broken wikilinks — every reference resolves.</p>`}
        </div>
        <div>
          <h2 class="section" style="margin:1.4rem 0 .1rem;" data-hint-title="Orphans" data-hint="Pages no other page links to. Give each a home by linking it from a hub page.">Orphans <span class="pill" style="vertical-align:.18em;">${orphans.length}</span></h2>
          ${orphans.length
            ? `<ul class="lib-links">${orphans.map(d =>
                `<li><button type="button" data-open="${esc(d.path)}" title="${esc(d.path)}">${esc(d.title || d.path)}</button></li>`).join("")}</ul>`
            : `<p class="dim" style="margin:.45rem 0;">Every page is linked from somewhere.</p>`}
        </div>
      </div>`;
    linksHost.querySelectorAll("[data-open]").forEach(b =>
      b.addEventListener("click", () => openDoc(b.dataset.open)));
    linksHost.querySelectorAll(".lib-wanted-btn").forEach(b =>
      b.addEventListener("click", () => {
        startCreate(createPathFor(b.dataset.target, dirOf(b.dataset.src || "")), b.dataset.target);
      }));
  }

  /* ---------- deep-link ---------- */
  function handleHash() {
    const path = parseHashPath();
    if (path && (!state.doc || state.doc.path !== path)) {
      openDoc(path, { updateHash: false });
    }
  }
  window.addEventListener("hashchange", handleHash);

  /* ---------- bus wiring (palette + constellation) ---------- */
  // Command palette: doc selected -> open it; "New document" action.
  bus.on("library:open", (p = {}) => { if (p.id || p.path) openDoc(p.id || p.path); });
  bus.on("library:new", () => renderEditor(null));
  // Constellation "Open in Library" emits nav {view:'library', ref, path}.
  bus.on("nav", (p = {}) => {
    if (p.view === "library" && (p.path || p.ref)) openDoc(p.path || p.ref);
  });
  // Another surface changed the link graph (or we did) — drop the cache.
  bus.on("wiki:links-changed", invalidateLinks);

  /* ---------- boot ---------- */
  loading(readerEl, "opening the library…");
  loadList().then(() => {
    const path = parseHashPath();
    if (path) openDoc(path, { updateHash: false });
    else renderHome();
  });
}
