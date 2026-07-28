/*
 * constellation.js — the Knowledge-Ops Constellation.
 * "Watch your organization think."
 *
 * Exports:
 *   initHome(sectionEl, ctx)  — full-bleed hero constellation + center ask bar
 *   initGraph(sectionEl, ctx) — the same engine with chrome: legend, filters,
 *                               search-to-focus, node inspector.
 *
 * Contract notes:
 *   - Vanilla ES module. Zero external URLs. Colors ONLY via --mu-* tokens
 *     (read through getComputedStyle; re-read on the 'theme' bus event).
 *   - Data: GET /kb.graph; graceful fallback to /kb.docs + /kb.stats; if even
 *     those fail, an ambient decorative field (unlabeled — we never fabricate
 *     data) so the view NEVER looks broken.
 *   - prefers-reduced-motion: static settled layout, instant highlights.
 *   - Communicates only via ctx/bus. Never imports view modules.
 */

import { createForceSim } from './vendor/forcesim.js';

/* ------------------------------------------------------------------------ */
/* constants                                                                */
/* ------------------------------------------------------------------------ */

const SRC_ORDER = ['wiki', 'org-memory', 'meetings', 'stable', 'files', 'graph', 'metrics'];

const SRC_LABEL = {
  'wiki': 'Wiki',
  'org-memory': 'Org memory',
  'meetings': 'Meetings',
  'stable': 'Stable',
  'files': 'Files',
  'graph': 'Graph',
  'metrics': 'Metrics',
  'other': 'Other',
};

const SRC_TOKEN = {
  'wiki': '--mu-src-wiki',
  'org-memory': '--mu-src-org-memory',
  'meetings': '--mu-src-meetings',
  'stable': '--mu-src-stable',
  'files': '--mu-src-files',
  'graph': '--mu-src-graph',
  'metrics': '--mu-src-metrics',
};

/* Shape channel: every source carries a glyph as well as a color, so the
 * map survives color-blindness, monochrome screenshots, and tiny sizes. */
const SRC_GLYPH = {
  'wiki': 'circle',
  'org-memory': 'diamond',
  'meetings': 'triangle',
  'stable': 'square',
  'files': 'penta',
  'graph': 'star',
  'metrics': 'hexa',
  'other': 'cross',
};

function traceGlyph(g, shape, x, y, r) {
  const poly = (sides, rot, rad) => {
    for (let i = 0; i <= sides; i++) {
      const a = rot + (i / sides) * Math.PI * 2;
      const px = x + Math.cos(a) * rad, py = y + Math.sin(a) * rad;
      if (i) g.lineTo(px, py); else g.moveTo(px, py);
    }
  };
  g.beginPath();
  switch (shape) {
    case 'diamond': poly(4, -Math.PI / 2, r * 1.22); break;
    case 'triangle': poly(3, -Math.PI / 2, r * 1.28); break;
    case 'square': poly(4, -Math.PI / 4, r * 1.08); break;
    case 'penta': poly(5, -Math.PI / 2, r * 1.16); break;
    case 'hexa': poly(6, 0, r * 1.1); break;
    case 'star': {
      for (let i = 0; i < 10; i++) {
        const a = -Math.PI / 2 + (i / 10) * Math.PI * 2;
        const rad = i % 2 ? r * 0.58 : r * 1.38;
        const px = x + Math.cos(a) * rad, py = y + Math.sin(a) * rad;
        if (i) g.lineTo(px, py); else g.moveTo(px, py);
      }
      g.closePath(); break;
    }
    case 'cross': {
      const t = r * 0.44, R = r * 1.16;
      g.moveTo(x - t, y - R); g.lineTo(x + t, y - R); g.lineTo(x + t, y - t); g.lineTo(x + R, y - t);
      g.lineTo(x + R, y + t); g.lineTo(x + t, y + t); g.lineTo(x + t, y + R); g.lineTo(x - t, y + R);
      g.lineTo(x - t, y + t); g.lineTo(x - R, y + t); g.lineTo(x - R, y - t); g.lineTo(x - t, y - t);
      g.closePath(); break;
    }
    default: g.arc(x, y, r, 0, Math.PI * 2);
  }
}

/* The same glyphs as 12px inline SVG for legends, pills, and tooltips. */
export function glyphSvg(srcKey) {
  const shape = SRC_GLYPH[srcKey] || 'circle';
  const P = {
    circle: '<circle cx="6" cy="6" r="4.4"/>',
    diamond: '<polygon points="6,0.8 11.2,6 6,11.2 0.8,6"/>',
    triangle: '<polygon points="6,1 11.2,10.4 0.8,10.4"/>',
    square: '<rect x="1.8" y="1.8" width="8.4" height="8.4"/>',
    penta: '<polygon points="6,0.8 11,4.6 9.1,10.6 2.9,10.6 1,4.6"/>',
    hexa: '<polygon points="10.8,6 8.4,10.2 3.6,10.2 1.2,6 3.6,1.8 8.4,1.8"/>',
    star: '<polygon points="6,0.6 7.4,4.2 11.4,4.4 8.3,6.9 9.4,10.8 6,8.6 2.6,10.8 3.7,6.9 0.6,4.4 4.6,4.2"/>',
    cross: '<path d="M4.4 1h3.2v3.4H11v3.2H7.6V11H4.4V7.6H1V4.4h3.4z"/>',
  };
  return `<svg class="ko-glyph" width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">${P[shape] || P.circle}</svg>`;
}

// Real collection counts (only used to shape the last-resort ambient field —
// decorative, unlabeled; we never present fabricated nodes as data).
const AMBIENT_SHAPE = [
  ['meetings', 1734],
  ['org-memory', 396],
  ['wiki', 326],
  ['stable', 62],
];

const IGNITE_STAGGER = 60;   // ms between node ignitions
const IGNITE_FLARE = 300;    // ms flare ease-out
const IGNITE_AFTERGLOW = 4200;
const BEAM_LIFE = 2600;      // ms evidence-beam visibility
const CAMERA_EASE = 0.085;

/* ------------------------------------------------------------------------ */
/* small helpers                                                            */
/* ------------------------------------------------------------------------ */

function srcKey(source) {
  const s = String(source || '').toLowerCase();
  if (s.includes('wiki')) return 'wiki';
  if (s.includes('org_memory') || s.includes('org-memory') || s.includes('memory')) return 'org-memory';
  if (s.includes('meeting') || s.includes('transcript')) return 'meetings';
  if (s.includes('stable')) return 'stable';
  if (s.includes('file')) return 'files';
  if (s.includes('graph') || s.includes('falkor')) return 'graph';
  if (s.includes('metric')) return 'metrics';
  return 'other';
}

function hashStr(s) {
  let h = 2166136261 >>> 0;
  const str = String(s);
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

function normText(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

/** Fuzzy passage->node title match: containment or >=60% token overlap. */
function titleMatches(a, b) {
  const na = normText(a), nb = normText(b);
  if (!na || !nb) return false;
  if (na === nb || na.includes(nb) || nb.includes(na)) return true;
  const ta = new Set(na.split(' ')), tb = nb.split(' ');
  let hit = 0;
  for (const t of tb) if (t.length > 2 && ta.has(t)) hit++;
  const denom = Math.min(ta.size, tb.length) || 1;
  return hit / denom >= 0.6;
}

function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/* ------------------------------------------------------------------------ */
/* theme (all colors come from tokens; re-read on 'theme' bus event)        */
/* ------------------------------------------------------------------------ */

function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name, fb) => (cs.getPropertyValue(name) || '').trim() || fb;
  const src = {};
  for (const k of SRC_ORDER) src[k] = v(SRC_TOKEN[k], v('--mu-canvas-star', '#cfd6ff'));
  return {
    canvasBg: v('--mu-canvas-bg', v('--mu-color-bg', '#05060f')),
    star: v('--mu-canvas-star', '#cfd6ff'),
    edge: v('--mu-canvas-edge', 'rgba(140,150,200,0.22)'),
    label: v('--mu-canvas-label', '#d9a96c'),
    text: v('--mu-color-text', '#e8eaf6'),
    muted: v('--mu-color-muted', '#9aa0b8'),
    primary: v('--mu-color-primary', '#9b7cf6'),
    accent: v('--mu-color-accent', '#46c2b3'),
    glow: clamp(parseFloat(v('--mu-glow', '0.6')) || 0, 0, 1),
    fontMono: v('--mu-font-mono', 'ui-monospace, SFMono-Regular, Menlo, monospace'),
    src,
  };
}

/* ------------------------------------------------------------------------ */
/* one-time layout CSS (positions only; every color is a token var)         */
/* ------------------------------------------------------------------------ */

function ensureCss() {
  if (document.getElementById('ko-constellation-css')) return;
  const st = document.createElement('style');
  st.id = 'ko-constellation-css';
  st.textContent = `
/* v2: the canvas lives in the shell's #sky substrate — these styles cover the
   overlays (hero copy, graph chrome) and the shared tooltip. */
.ko-canvas--grabbable { cursor: grab; } .ko-canvas--grabbing { cursor: grabbing; }
.ko-tooltip { position: absolute; z-index: 30; max-width: 34ch; padding: .35rem .65rem; border-radius: var(--mu-radius-sm); background: var(--mu-glass-strong); -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px); box-shadow: 0 0 0 1px var(--mu-hairline); color: var(--mu-color-text); font-family: var(--mu-font-mono); font-size: .76rem; pointer-events: none; transform: translate(-50%, calc(-100% - 14px)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* home hero — copy floats directly on the sky, no card, no box */
.ko-hero2 { position: relative; min-height: calc(100vh - var(--mu-bar-h) - 1.5rem); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .35rem; padding: 0 1.2rem 26vh; text-align: center; pointer-events: none; }
.ko-hero2::before { content: ""; position: absolute; left: 50%; top: 50%; translate: -50% -54%; width: min(66rem, 96vw); height: 24rem; border-radius: 50%; background: radial-gradient(closest-side, color-mix(in srgb, var(--mu-color-bg) 62%, transparent) 0%, transparent 74%); pointer-events: none; }
.ko-hero2 > * { pointer-events: auto; position: relative; }
.ko-hero2 h1 { font-family: var(--mu-font-display); font-size: var(--mu-type-hero); font-weight: 550; line-height: .98; letter-spacing: -0.022em; margin: .5rem 0 1rem; text-wrap: balance; text-shadow: 0 0 calc(34px * var(--mu-glow, .6)) color-mix(in srgb, var(--mu-color-primary) 40%, transparent); }
.ko-hero2 .lede { margin: 0 auto; }
.ko-hero-cta { margin-top: 1.2rem; display: flex; gap: .7rem; align-items: center; flex-wrap: wrap; justify-content: center; }
@media (min-width: 881px) and (min-height: 720px) {
  /* the lens is fixed mid-page on home — pin the doors right beneath it */
  html[data-route="home"] .ko-hero-cta { position: fixed; left: 50%; top: calc(56.5% + 4.7rem); translate: -50% 0; margin: 0; z-index: 12; }
}
.ko-hero-cta kbd { font-family: var(--mu-font-mono); font-size: .72em; opacity: .75; margin-left: .35em; }
.ko-hero-status { position: fixed; right: 1.35rem; left: calc(50% + var(--mu-lens-w) / 2 + 1.2rem); bottom: 1.35rem; z-index: 12; pointer-events: none; font-family: var(--mu-font-mono); font-size: .72rem; font-variant-numeric: tabular-nums; letter-spacing: .04em; color: var(--mu-color-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: right; }
html:not([data-route="home"]) .ko-hero-status { display: none; }

/* graph chrome — instrument panels floating over the immersive sky */
.ko-float { position: fixed; top: calc(var(--mu-bar-h) + 1rem); z-index: 20; display: flex; flex-direction: column; gap: .65rem; width: min(300px, 86vw); max-height: calc(100vh - var(--mu-bar-h) - 3rem); overflow: auto; padding: 1rem 1.1rem 1.15rem; }
.ko-float--tools { left: calc(var(--mu-dock-w) + 1.6rem); }
.ko-float--inspector { right: 1.2rem; width: min(370px, 90vw); }
.ko-float[hidden] { display: none !important; }
.ko-legend { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.ko-legend .pill { position: relative; display: inline-flex; align-items: center; gap: .45rem; min-height: 40px; padding-inline: .75rem; cursor: pointer; user-select: none; }
.ko-legend .pill input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; margin: 0; cursor: pointer; }
.ko-legend .pill:has(input:focus-visible) { outline: 2px solid var(--mu-color-primary); outline-offset: 2px; }
.ko-legend .pill[data-off="1"] { opacity: .45; }
.ko-dot { width: .6rem; height: .6rem; border-radius: 50%; flex: none; box-shadow: 0 0 8px color-mix(in srgb, currentColor 60%, transparent); background: currentColor; }
.ko-glyphwrap { display: inline-grid; place-items: center; flex: none; filter: drop-shadow(0 0 5px color-mix(in srgb, currentColor 65%, transparent)); }
.ko-orbit-hint { position: fixed; right: 1.2rem; bottom: 5.6rem; z-index: 20; pointer-events: none; font-family: var(--mu-font-mono); font-size: .7rem; letter-spacing: .06em; color: var(--mu-color-dim); }
.ko-edgekey { display: flex; gap: .9rem; align-items: center; flex-wrap: wrap; margin-top: .4rem; font-family: var(--mu-font-mono); font-size: .72rem; color: var(--mu-color-dim); }
.ko-edgekey__item { display: inline-flex; align-items: center; gap: .4rem; }
.ko-edgeline { width: 1.4rem; height: 0; border-top: 1px solid var(--mu-canvas-edge); opacity: .5; flex: none; }
.ko-edgeline--link { opacity: 1; }
.ko-linksline { font-family: var(--mu-font-mono); font-size: .76rem; font-variant-numeric: tabular-nums; color: var(--mu-color-muted); margin: 0; }
.ko-float .ko-pills { display: flex; flex-wrap: wrap; gap: .4rem; }
.ko-snippet { font-size: .85rem; line-height: 1.55; color: var(--mu-color-muted); overflow-wrap: anywhere; }
.ko-neighbors { display: flex; flex-direction: column; gap: .25rem; }
.ko-neighbors button { text-align: left; }
.ko-float .ko-actions { display: flex; gap: .5rem; flex-wrap: wrap; }
.ko-float .ko-close { margin-left: auto; }
.ko-inspector-head { display: flex; align-items: flex-start; gap: .5rem; }
.ko-searchcount { font-family: var(--mu-font-mono); font-size: .73rem; color: var(--mu-color-dim); min-height: 1.1em; }
@media (max-width: 880px) {
  .ko-float--tools { left: .8rem; right: .8rem; width: auto; max-height: 34vh; }
  .ko-float--inspector { left: .8rem; right: .8rem; width: auto; top: auto; bottom: 9.2rem; max-height: 38vh; }
  .ko-hero-status { display: none; }
  .ko-hero2 { padding-bottom: 22vh; }
}
@media (prefers-reduced-motion: reduce) {
  .ko-hero2 h1 { text-shadow: none; }
}
`;
  document.head.appendChild(st);
}

/* ------------------------------------------------------------------------ */
/* data loading + honest fallbacks                                          */
/* ------------------------------------------------------------------------ */

function normalizeGraph(g) {
  const nodes = (g.nodes || []).map((n) => ({
    id: String(n.id),
    label: n.label || String(n.id),
    source: srcKey(n.source || n.collection),
    rawSource: n.source || '',
    collection: n.collection || '',
    scope: n.scope || 'global',
    size: Number(n.size) || 1,
    ref: n.ref || null,
    r: clamp(3.4 + 1.5 * Math.log2(1 + (Number(n.size) || 1)), 3.4, 13),
  }));
  // Edge kinds: 'link' = real wiki links (solid, brighter, stronger spring so
  // linked docs cluster truthfully); anything else — including edges with no
  // kind at all — is 'semantic' (embedding neighbors, current subtle look).
  const edges = (g.edges || []).map((e) => {
    const kind = e.kind === 'link' ? 'link' : 'semantic';
    const w = Number(e.w) || 1;
    return { a: String(e.a), b: String(e.b), w: kind === 'link' ? w * 1.6 : w, kind };
  });
  return { nodes, edges, stats: g.stats || null, kind: 'graph' };
}

function dirOf(path) {
  const p = String(path || '');
  const i = p.lastIndexOf('/');
  return i > 0 ? p.slice(0, i) : '';
}

/** Ambient constellation from real docs + stats — used when /kb.graph is absent. */
function buildAmbient(docs, statsSources) {
  const nodes = [];
  const edges = [];

  const srcCounts = [];
  if (Array.isArray(statsSources) && statsSources.length) {
    for (const s of statsSources) srcCounts.push([srcKey(s.key || s.label), Number(s.count) || 0, s.label]);
  } else {
    for (const [k, c] of AMBIENT_SHAPE) srcCounts.push([k, c, SRC_LABEL[k]]);
  }

  const hubs = new Map();
  for (const [key, count, label] of srcCounts) {
    if (hubs.has(key)) continue;
    const hub = {
      id: 'hub:' + key,
      label: (label || SRC_LABEL[key] || key) + ' · ' + count.toLocaleString(),
      source: key, scope: 'global', size: count, ref: null, hub: true,
      r: clamp(6 + 1.4 * Math.log2(1 + count), 7, 15),
    };
    hubs.set(key, hub);
    nodes.push(hub);
  }

  // Real wiki docs, chained by directory, first-in-dir linked to the wiki hub.
  const wikiHub = hubs.get('wiki');
  const byDir = new Map();
  for (const d of docs || []) {
    if (d.tombstoned_at) continue;
    const n = {
      id: 'doc:' + (d.path || d.id),
      label: d.title || d.path || 'Untitled',
      source: 'wiki', scope: d.scope || 'global', size: 3,
      ref: d.path || d.id, r: 4.6,
    };
    nodes.push(n);
    const dir = dirOf(d.path);
    if (!byDir.has(dir)) byDir.set(dir, []);
    byDir.get(dir).push(n);
  }
  for (const group of byDir.values()) {
    for (let i = 1; i < group.length; i++) edges.push({ a: group[i - 1].id, b: group[i].id, w: 1 });
    if (wikiHub && group.length) edges.push({ a: wikiHub.id, b: group[0].id, w: 0.6 });
  }

  // Decorative satellites for non-wiki hubs — sized by real counts but
  // explicitly unlabeled dust (deco: true). We never invent titles.
  for (const [key, count] of srcCounts) {
    if (key === 'wiki' && byDir.size) continue;
    const hub = hubs.get(key);
    if (!hub) continue;
    const n = clamp(Math.round(4 + Math.log2(1 + count) * 2.2), 5, 26);
    let prev = hub.id;
    for (let i = 0; i < n; i++) {
      const id = 'dust:' + key + ':' + i;
      nodes.push({ id, label: null, source: key, scope: 'global', size: 1, ref: null, deco: true, r: 2.4 + (hashStr(id) % 20) / 10 });
      edges.push({ a: hub.id, b: id, w: 0.35 });
      if (i % 3 === 2) edges.push({ a: prev, b: id, w: 0.25 });
      prev = id;
    }
  }

  // Loose ring between hubs so the field reads as one organism.
  const hubList = [...hubs.values()];
  for (let i = 0; i < hubList.length; i++) {
    edges.push({ a: hubList[i].id, b: hubList[(i + 1) % hubList.length].id, w: 0.2 });
  }

  return { nodes, edges, stats: null, kind: docs && docs.length ? 'ambient' : 'deco' };
}

/* ---- /kb.links — lazy, cached once, invalidated on 'wiki:links-changed'.
 * Resolves to a Map(docKey -> {in, out}) or null when the endpoint is absent
 * (404 → the inspector simply omits its Links line). Shape-tolerant. */
let _linksIndexPromise = null;

function invalidateLinksIndex() { _linksIndexPromise = null; }

function normalizeLinksIndex(res) {
  const items = Array.isArray(res) ? res : (res && (res.links || res.edges)) || [];
  const idx = new Map();
  const bump = (key, dir) => {
    const k = String(key);
    if (!k) return;
    const c = idx.get(k) || { in: 0, out: 0 };
    c[dir]++;
    idx.set(k, c);
  };
  for (const l of items) {
    if (!l) continue;
    // Backend shape (graph_api.py /kb.links) is from_path/to_path; the
    // remaining aliases keep older/mocked shapes readable.
    const from = l.from_path ?? l.from ?? l.source ?? l.a ?? l.src;
    const to = l.to_path ?? l.to ?? l.target ?? l.b ?? l.dst;
    if (from != null) bump(from, 'out');
    if (to != null) bump(to, 'in');
  }
  return idx;
}

function fetchLinksIndex(api) {
  if (!_linksIndexPromise) {
    _linksIndexPromise = api.get('/kb.links').then(normalizeLinksIndex).catch((e) => {
      // 404 = feature not there; cache the absence. Transient errors: allow retry.
      if (!(e && e.status === 404)) _linksIndexPromise = null;
      return null;
    });
  }
  return _linksIndexPromise;
}

async function loadConstellation(api) {
  let firstErr = null;
  try {
    const g = await api.get('/kb.graph');
    if (g && Array.isArray(g.nodes) && g.nodes.length) return { data: normalizeGraph(g), err: null };
  } catch (e) { firstErr = e; }

  let docs = [];
  let stats = null;
  let err = null;
  try {
    const d = await api.get('/kb.docs');
    docs = (d && d.docs) || [];
  } catch (e) { err = e; }
  try {
    const s = await api.get('/kb.stats');
    stats = (s && s.sources) || null;
  } catch (e) { /* stats are optional */ }

  // Surface auth problems (401/403) even though we still render ambience.
  const authErr = [firstErr, err].find((e) => e && (e.status === 401 || e.status === 403)) || null;
  return { data: buildAmbient(docs, stats), err: authErr };
}

/* ------------------------------------------------------------------------ */
/* the engine — canvas, camera, starfield, ignition choreography            */
/* ------------------------------------------------------------------------ */

export function createEngine(stageEl, canvas, ctx, opts = {}) {
  const g2d = canvas.getContext('2d');
  let theme = readTheme();
  let reduced = prefersReducedMotion();
  let dpr = Math.min(window.devicePixelRatio || 1, 2);
  let w = 0, h = 0;
  let active = false;
  let raf = 0;
  let dirty = true;
  let visCheck = 0;

  const sim = createForceSim({ center: { x: 0, y: 0 } });
  let adjacency = new Map(); // id -> Set(id)
  let nodeById = new Map();

  // 3D camera: x/y = world anchor, z = zoom (dolly), yaw/pitch = orbit.
  // Rest pose is a gentle three-quarter angle so depth reads instantly.
  const cam = { x: 0, y: 0, z: 1, yaw: -0.34, pitch: 0.18, tx: 0, ty: 0, tz: 1, tyaw: -0.34, tpitch: 0.18 };
  const PERSP = 1050;         // perspective distance (world units)
  let autoOrbit = false;      // hero mode: the sky turns, slowly
  const pointer = { x: 0, y: 0, in: false };

  // interaction state
  let hover = null;
  let selected = null;
  let highlightIds = null;   // Set of node ids to spotlight (dims the rest)
  let ignitions = new Map(); // id -> {t0}
  let beams = [];            // {node, t0}
  let igniteTimer = 0;       // releases the spotlight dim after afterglow
  let focalScreen = { fx: 0.5, fy: 0.42 };
  let hiddenPred = null;     // node -> bool (filters, graph view)
  let framed = false;        // first fit done
  let grade = 'full';        // 'full' | 'dim' — substrate quality behind panes

  /* Bloom sprites: pre-rendered radial glows per color — real luminosity
   * without per-node gradient allocation. Cache cleared on theme change. */
  const spriteCache = new Map();
  function glowSprite(color) {
    let s = spriteCache.get(color);
    if (s) return s;
    s = document.createElement('canvas');
    s.width = s.height = 64;
    const x = s.getContext('2d');
    let grad = x.createRadialGradient(32, 32, 1.5, 32, 32, 32);
    grad.addColorStop(0, color);
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    x.fillStyle = grad;
    x.globalAlpha = 0.55;
    x.fillRect(0, 0, 64, 64);
    grad = x.createRadialGradient(32, 32, 1, 32, 32, 14);
    grad.addColorStop(0, color);
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    x.fillStyle = grad;
    x.globalAlpha = 0.9;
    x.fillRect(0, 0, 64, 64);
    spriteCache.set(color, s);
    return s;
  }

  // starfield dust (drawn once per resize/theme)
  let dust = null;
  function paintDust() {
    if (w < 4 || h < 4) return;
    dust = document.createElement('canvas');
    dust.width = Math.ceil(w * dpr);
    dust.height = Math.ceil(h * dpr);
    const d = dust.getContext('2d');
    d.scale(dpr, dpr);
    const n = Math.round((w * h) / 3800);
    const rnd = (seed) => { let s = seed >>> 0; return () => ((s = Math.imul(s ^ (s >>> 15), 2246822519) >>> 0) % 10000) / 10000; };
    const r = rnd(hashStr(theme.canvasBg) + 7);
    d.fillStyle = theme.star;
    for (let i = 0; i < n; i++) {
      const x = r() * w, y = r() * h;
      const rad = r() * 1.1 + 0.2;
      d.globalAlpha = (0.05 + r() * 0.22) * (0.35 + theme.glow * 0.65);
      d.beginPath(); d.arc(x, y, rad, 0, Math.PI * 2); d.fill();
    }
    d.globalAlpha = 1;
  }

  function resize() {
    const rect = stageEl.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = rect.width; h = rect.height;
    canvas.width = Math.ceil(w * dpr);
    canvas.height = Math.ceil(h * dpr);
    paintDust();
    if (!framed && sim.nodes.length) fitAll(true);
    dirty = true;
    if (reduced) draw();
  }

  const ro = new ResizeObserver(() => resize());
  ro.observe(stageEl);

  function setTheme() {
    theme = readTheme();
    spriteCache.clear();
    paintDust();
    dirty = true;
    if (reduced) draw();
  }

  if (window.matchMedia) {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onMq = () => {
      reduced = mq.matches;
      if (reduced) { sim.settle(400); stopLoop(); draw(); }
      else if (active) startLoop();
    };
    if (mq.addEventListener) mq.addEventListener('change', onMq);
  }

  /* ---- graph plumbing --------------------------------------------------- */

  function rebuildIndexes() {
    nodeById = new Map(sim.nodes.map((n) => [n.id, n]));
    adjacency = new Map();
    for (const l of sim.links) {
      if (!adjacency.has(l.s.id)) adjacency.set(l.s.id, new Set());
      if (!adjacency.has(l.t.id)) adjacency.set(l.t.id, new Set());
      adjacency.get(l.s.id).add(l.t.id);
      adjacency.get(l.t.id).add(l.s.id);
    }
  }

  // The sim builds its own link records ({s,t,w,rest}); carry each edge's
  // kind over so the renderer can tell real wiki links from semantic ones.
  // Edges added later without a kind (temp satellites) stay untagged → semantic.
  function tagLinkKinds(edges) {
    const km = new Set();
    for (const e of edges || []) {
      if (e.kind === 'link') { km.add(e.a + '\u0001' + e.b); km.add(e.b + '\u0001' + e.a); }
    }
    for (const l of sim.links) {
      l.kind = km.has(l.s.id + '\u0001' + l.t.id) ? 'link' : 'semantic';
    }
  }

  /* Depth: each source gets a band on the z axis (the constellation is a
   * layered volume, not a disc), every node a stable hash jitter inside it.
   * Dust spreads much deeper — it is what makes the parallax read as space. */
  function assignDepth() {
    for (const n of sim.nodes) {
      if (typeof n.z === 'number') continue;
      const band = SRC_ORDER.indexOf(n.source);
      const base = (band < 0 ? 3.5 : band) - (SRC_ORDER.length - 1) / 2;
      const jitter = (((hashStr(n.id) >> 6) % 1000) / 1000 - 0.5);
      n.z = base * 46 + jitter * (n.deco ? 340 : 120);
    }
  }

  function setGraph(data) {
    sim.setGraph(data.nodes, data.edges);
    tagLinkKinds(data.edges);
    rebuildIndexes();
    assignDepth();
    // Warm-up so first paint is a constellation, not an explosion.
    sim.tick(reduced ? 500 : 140);
    if (reduced) sim.settle(400);
    framed = false;
    if (w > 2) { fitAll(true); }
    dirty = true;
    if (reduced) draw();
  }

  /** Background refresh: adopt new data but keep positions for ids we already
   * have, so the layout morphs instead of resetting. Camera stays put. */
  function morphTo(data) {
    const prev = nodeById;
    for (const n of data.nodes) {
      const old = prev.get(n.id);
      if (old) { n.x = old.x; n.y = old.y; n.z = old.z; n.vx = old.vx || 0; n.vy = old.vy || 0; }
    }
    sim.setGraph(data.nodes, data.edges);
    tagLinkKinds(data.edges);
    rebuildIndexes();
    assignDepth();
    // Re-point interaction state at the adopted node objects.
    if (selected) selected = nodeById.get(selected.id) || null;
    if (hover) hover = nodeById.get(hover.id) || null;
    if (reduced) {
      sim.settle(400);
      draw();
    } else {
      // setGraph reheats to 0.9; calm it down so the morph is a drift, not a bang.
      sim.alpha = 0.25;
    }
    dirty = true;
  }

  function visible(n) { return !(hiddenPred && hiddenPred(n)); }

  /* ---- camera ----------------------------------------------------------- */

  /* 3D projection: yaw about the vertical axis, pitch about the horizontal,
   * then perspective divide. Returns [sx, sy, f] where f is the perspective
   * scale (≈1 at the focal plane, >1 near, <1 far) — callers use it for
   * size/alpha depth attenuation and painter sorting. */
  function worldToScreen(x, y, z = 0) {
    const dx = x - cam.x, dy = y - cam.y, dz = z;
    const cy0 = Math.cos(cam.yaw), sy0 = Math.sin(cam.yaw);
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const rx = dx * cy0 + dz * sy0;
    const z1 = -dx * sy0 + dz * cy0;
    const ry = dy * cp - z1 * sp;
    const rz = dy * sp + z1 * cp;
    const f = clamp(PERSP / (PERSP - rz), 0.42, 2.3);
    return [w / 2 + rx * cam.z * f, h / 2 + ry * cam.z * f, f];
  }
  /* Approximate inverse (focal plane only) — used for pan math. */
  function screenToWorld(sx, sy) {
    const cy0 = Math.cos(cam.yaw), cp = Math.cos(cam.pitch);
    return [(sx - w / 2) / (cam.z * (Math.abs(cy0) < 0.25 ? 0.25 * Math.sign(cy0 || 1) : cy0)) + cam.x,
            (sy - h / 2) / (cam.z * cp) + cam.y];
  }
  /* Pan by a screen-space delta: invert the projection's linear part so the
   * field follows the pointer at any orbit angle. */
  function panBy(dxs, dys) {
    const cy0 = Math.cos(cam.yaw), sy0 = Math.sin(cam.yaw);
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const gy = Math.abs(cy0) < 0.25 ? 0.25 * Math.sign(cy0 || 1) : cy0;
    const dcx = -dxs / (cam.z * gy);
    const dcy = (dxs * sy0 * sp - dys * gy) / (cam.z * gy * cp) * -1;
    cam.x += dcx; cam.tx = cam.x;
    cam.y -= dcy; cam.ty = cam.y;
    dirty = true;
  }

  function fitNodes(list, snap = false, pad = 90) {
    const vis = list.filter(visible);
    if (!vis.length || w < 2) return;
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    for (const n of vis) {
      if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
    }
    const bw = Math.max(maxX - minX, 40) + pad * 2;
    const bh = Math.max(maxY - minY, 40) + pad * 2;
    cam.tx = (minX + maxX) / 2;
    cam.ty = (minY + maxY) / 2;
    // 0.86: leave air for the perspective spread of near-plane nodes.
    cam.tz = clamp(Math.min(w / bw, h / bh) * 0.86, 0.28, 2.4);
    if (snap || reduced) { cam.x = cam.tx; cam.y = cam.ty; cam.z = cam.tz; }
    framed = true;
    dirty = true;
  }

  function fitAll(snap = false) { fitNodes(sim.nodes, snap); }

  function focusNode(n, zoom = 1.35) {
    if (!n) return;
    cam.tx = n.x; cam.ty = n.y; cam.tz = Math.max(cam.z, zoom);
    if (reduced) { cam.x = cam.tx; cam.y = cam.ty; cam.z = cam.tz; draw(); }
    dirty = true;
  }

  /* ---- ignition choreography ("the jaw-drop moment") -------------------- */

  function clearTemporaries() {
    sim.removeNodes((n) => n.temp);
    rebuildIndexes();
    ignitions = new Map();
    beams = [];
  }

  function matchPassages(passages) {
    const matched = [];
    const unmatched = [];
    for (const p of passages || []) {
      let node = null;
      const keys = [p.uri, p.source_id, p.title].filter(Boolean);
      for (const k of keys) {
        node = nodeById.get(String(k)) || nodeById.get('doc:' + k) || null;
        if (node) break;
      }
      if (!node) {
        for (const n of sim.nodes) {
          if (n.deco || n.temp || !n.label) continue;
          if ((n.ref && (n.ref === p.uri || n.ref === p.source_id)) || titleMatches(n.label, p.title)) { node = n; break; }
        }
      }
      if (node) matched.push(node); else unmatched.push(p);
    }
    return { matched: [...new Set(matched)], unmatched };
  }

  function ignite(passages) {
    clearTemporaries();
    const { matched, unmatched } = matchPassages(passages);
    const now = performance.now();

    // Centroid of the evidence — satellites and beams organize around it.
    let cx = cam.x, cy = cam.y;
    if (matched.length) {
      cx = matched.reduce((s, n) => s + n.x, 0) / matched.length;
      cy = matched.reduce((s, n) => s + n.y, 0) / matched.length;
    }

    // Temporary satellites for passages with no home in the constellation.
    const sats = [];
    const satEdges = [];
    if (unmatched.length) {
      const anchor = { id: 'temp:anchor', label: null, source: 'other', temp: true, deco: true, r: 0.1, fx: cx, fy: cy, x: cx, y: cy };
      sats.push(anchor);
      unmatched.forEach((p, i) => {
        const ang = (i / unmatched.length) * Math.PI * 2;
        const id = 'temp:sat:' + i;
        sats.push({
          id, label: p.title || SRC_LABEL[srcKey(p.source)] || 'evidence',
          source: srcKey(p.source), scope: p.scope || 'global', size: 2, ref: p.uri || null,
          temp: true, sat: true, r: 4.2,
          x: cx + Math.cos(ang) * 120, y: cy + Math.sin(ang) * 120,
        });
        satEdges.push({ a: 'temp:anchor', b: id, w: 0.5 });
      });
      sim.addGraph(sats, satEdges);
      rebuildIndexes();
      sim.kick(0.28);
    }

    const lit = [...matched, ...sats.filter((s) => s.sat)];
    highlightIds = new Set(lit.map((n) => n.id));

    if (reduced) {
      // No flare, no stagger, no beams — instant highlight and frame.
      sim.settle(240);
      for (const n of lit) ignitions.set(n.id, { t0: now - IGNITE_FLARE, static: true });
      if (lit.length) fitNodes(lit, true, 120);
      draw();
      return lit.length;
    }

    lit.forEach((n, i) => { ignitions.set(n.id, { t0: now + i * IGNITE_STAGGER }); });
    beams = lit.map((n, i) => ({ node: n, t0: now + i * IGNITE_STAGGER + IGNITE_FLARE * 0.4 }));
    if (lit.length) fitNodes(lit, false, 130);
    // After the afterglow fades, let the rest of the constellation breathe again.
    if (igniteTimer) clearTimeout(igniteTimer);
    igniteTimer = setTimeout(() => {
      highlightIds = null;
      dirty = true;
    }, lit.length * IGNITE_STAGGER + IGNITE_FLARE + IGNITE_AFTERGLOW);
    dirty = true;
    return lit.length;
  }

  function spotlight(ids) {
    const set = new Set((ids || []).map(String));
    const nodes = sim.nodes.filter((n) => set.has(n.id) || (n.ref && set.has(String(n.ref))));
    highlightIds = nodes.length ? new Set(nodes.map((n) => n.id)) : null;
    const now = performance.now();
    nodes.forEach((n, i) => ignitions.set(n.id, { t0: reduced ? now - IGNITE_FLARE : now + i * IGNITE_STAGGER, static: reduced }));
    if (nodes.length) fitNodes(nodes, reduced, 120);
    dirty = true;
    if (reduced) draw();
  }

  /* ---- drawing ---------------------------------------------------------- */

  const TAU2 = Math.PI * 2;

  function nodeColor(n) { return theme.src[n.source] || theme.star; }

  function igniteState(id, now) {
    const ig = ignitions.get(id);
    if (!ig) return null;
    const dt = now - ig.t0;
    if (dt < 0) return { phase: 'wait', p: 0, glow: 0 };
    if (dt < IGNITE_FLARE) { const p = easeOutCubic(dt / IGNITE_FLARE); return { phase: 'flare', p, glow: p }; }
    const after = dt - IGNITE_FLARE;
    if (after < IGNITE_AFTERGLOW) return { phase: 'after', p: 1, glow: 1 - (after / IGNITE_AFTERGLOW) * 0.75 };
    if (ig.static) return { phase: 'after', p: 1, glow: 0.3 };
    ignitions.delete(id);
    return null;
  }

  function driftOffset(n, t) {
    if (reduced) return [0, 0];
    const ph = (hashStr(n.id) % 6283) / 1000;
    const s = n.hub ? 0.4 : 1;
    return [Math.sin(t * 0.00021 + ph) * 1.6 * s, Math.cos(t * 0.00017 + ph * 1.7) * 1.6 * s];
  }

  function draw() {
    if (w < 2 || h < 2) return;
    const now = performance.now();
    g2d.setTransform(dpr, 0, 0, dpr, 0, 0);
    // Transparent canvas: the page's themed nebula gradient shows through.
    g2d.clearRect(0, 0, w, h);

    // dust with gentle parallax
    if (dust) {
      const px = pointer.in && !reduced ? (pointer.x / w - 0.5) * -10 : 0;
      const py = pointer.in && !reduced ? (pointer.y / h - 0.5) * -10 : 0;
      g2d.drawImage(dust, px * dpr, py * dpr, dust.width, dust.height, 0, 0, w, h);
    }

    const hoverAdj = hover ? adjacency.get(hover.id) : null;
    const spot = highlightIds;
    const dimStrength = 0.16;

    const posOf = (n) => {
      const [ox, oy] = driftOffset(n, now);
      return worldToScreen(n.x + ox, n.y + oy, n.z || 0);
    };

    // ---- edges (three batches: semantic dim, link dim, bright)
    // Real wiki links draw solid at higher alpha; semantic (embedding)
    // neighbors keep the current subtle treatment. Same token color, no glow.
    g2d.lineWidth = 1;
    const brightEdges = [];
    const linkEdges = [];
    g2d.beginPath();
    for (const l of sim.links) {
      if (!visible(l.s) || !visible(l.t)) continue;
      const isBright =
        (hover && (l.s === hover || l.t === hover)) ||
        (spot && spot.has(l.s.id) && spot.has(l.t.id));
      if (isBright) { brightEdges.push(l); continue; }
      if (l.kind === 'link') { linkEdges.push(l); continue; }
      const [x1, y1] = posOf(l.s);
      const [x2, y2] = posOf(l.t);
      g2d.moveTo(x1, y1); g2d.lineTo(x2, y2);
    }
    const baseEdgeAlpha = clamp(0.5 + theme.glow * 0.2, 0.4, 0.8);
    g2d.globalAlpha = (hover || spot) ? dimStrength : baseEdgeAlpha;
    g2d.strokeStyle = theme.edge;
    g2d.stroke();
    if (linkEdges.length) {
      g2d.beginPath();
      for (const l of linkEdges) {
        const [x1, y1] = posOf(l.s);
        const [x2, y2] = posOf(l.t);
        g2d.moveTo(x1, y1); g2d.lineTo(x2, y2);
      }
      g2d.globalAlpha = (hover || spot) ? dimStrength : clamp(baseEdgeAlpha + 0.25, 0, 0.95);
      g2d.strokeStyle = theme.edge;
      g2d.stroke();
    }
    if (brightEdges.length) {
      g2d.beginPath();
      for (const l of brightEdges) {
        const [x1, y1] = posOf(l.s);
        const [x2, y2] = posOf(l.t);
        g2d.moveTo(x1, y1); g2d.lineTo(x2, y2);
      }
      g2d.globalAlpha = 0.9;
      g2d.strokeStyle = theme.accent;
      g2d.lineWidth = 1.25;
      g2d.stroke();
      g2d.lineWidth = 1;
    }
    g2d.globalAlpha = 1;

    // ---- ambient thought: motes drifting along edges (the org "thinking")
    if (grade === 'full' && !reduced && theme.glow > 0.2 && !(hover || spot)) {
      const stride = sim.links.length > 380 ? 3 : 1;
      g2d.save();
      for (let i = 0; i < sim.links.length; i += stride) {
        const l = sim.links[i];
        if (!visible(l.s) || !visible(l.t)) continue;
        const ph = hashStr(l.s.id + l.t.id);
        const t = ((now * 0.000045 * (0.55 + (ph % 7) / 9)) + ph / 9973) % 1;
        const [x1, y1] = posOf(l.s);
        const [x2, y2] = posOf(l.t);
        const mx = x1 + (x2 - x1) * t, my = y1 + (y2 - y1) * t;
        if (mx < -10 || my < -10 || mx > w + 10 || my > h + 10) continue;
        g2d.globalAlpha = 0.38 * theme.glow * Math.sin(t * Math.PI);
        g2d.fillStyle = nodeColor(l.s);
        g2d.beginPath(); g2d.arc(mx, my, 1.15, 0, TAU2); g2d.fill();
      }
      g2d.restore();
    }

    // ---- evidence beams toward the focal point
    if (beams.length && !reduced) {
      const fx = w * focalScreen.fx, fy = h * focalScreen.fy;
      const alive = [];
      for (const b of beams) {
        const dt = now - b.t0;
        if (dt < 0) { alive.push(b); continue; }
        if (dt > BEAM_LIFE) continue;
        alive.push(b);
        const [x1, y1] = posOf(b.node);
        const fadeIn = clamp(dt / 260, 0, 1);
        const fadeOut = clamp(1 - (dt - BEAM_LIFE * 0.6) / (BEAM_LIFE * 0.4), 0, 1);
        const a = fadeIn * fadeOut * 0.65;
        if (a <= 0.01) continue;
        const grad = g2d.createLinearGradient(x1, y1, fx, fy);
        grad.addColorStop(0, nodeColor(b.node));
        grad.addColorStop(1, theme.accent);
        g2d.save();
        g2d.globalAlpha = a;
        g2d.strokeStyle = grad;
        g2d.lineWidth = 1.4;
        g2d.setLineDash([5, 9]);
        g2d.lineDashOffset = -dt * 0.09;
        g2d.beginPath(); g2d.moveTo(x1, y1); g2d.lineTo(fx, fy); g2d.stroke();
        g2d.restore();
      }
      beams = alive;
    }

    // ---- nodes (painter-sorted: far stars first, near stars over them)
    const drawList = [];
    for (const n of sim.nodes) {
      if (!visible(n) || (n.temp && !n.sat)) continue;
      const [x, y, f] = posOf(n);
      if (x < -40 || y < -40 || x > w + 40 || y > h + 40) continue;
      drawList.push([f, x, y, n]);
    }
    drawList.sort((a, b) => a[0] - b[0]);
    for (const [f, x, y, n] of drawList) {
      const depthFade = clamp(0.34 + 0.72 * (f - 0.42) / 0.9, 0.34, 1.08);
      const ig = igniteState(n.id, now);
      const isHover = n === hover;
      const isSel = n === selected;
      const inSpot = !spot || spot.has(n.id) || isHover || isSel || (hoverAdj && hoverAdj.has(n.id));
      const nearHover = !hover || isHover || (hoverAdj && hoverAdj.has(n.id));
      const dim = (!inSpot || !nearHover);

      let r = n.r * clamp(cam.z, 0.7, 1.6) * f;
      let alpha = (n.deco ? 0.5 : 0.95) * depthFade;
      if (dim) alpha *= 0.22;
      const color = nodeColor(n);
      // Stars have magnitude: a stable per-node brightness class so the field
      // reads as a sky (varied, luminous), not confetti (uniform, flat).
      const mag = n.deco ? 0.4 : 0.55 + 0.45 * (((hashStr(n.id) >> 4) % 100) / 100);

      if (ig && ig.phase !== 'wait') {
        const flare = ig.phase === 'flare' ? ig.p : 1;
        const glowA = ig.glow * (0.35 + theme.glow * 0.65);
        r *= 1 + 0.8 * flare * ig.glow;
        // radial flare halo
        const halo = g2d.createRadialGradient(x, y, r * 0.4, x, y, r * 5.2);
        halo.addColorStop(0, color);
        halo.addColorStop(1, 'rgba(0,0,0,0)');
        g2d.save();
        g2d.globalAlpha = 0.4 * glowA;
        g2d.fillStyle = halo;
        g2d.beginPath(); g2d.arc(x, y, r * 5.2, 0, TAU2); g2d.fill();
        g2d.restore();
        alpha = 1;
      } else if ((isHover || isSel) && theme.glow > 0.05) {
        const halo = g2d.createRadialGradient(x, y, r * 0.4, x, y, r * 3.4);
        halo.addColorStop(0, color);
        halo.addColorStop(1, 'rgba(0,0,0,0)');
        g2d.save();
        g2d.globalAlpha = 0.3 * theme.glow;
        g2d.fillStyle = halo;
        g2d.beginPath(); g2d.arc(x, y, r * 3.4, 0, TAU2); g2d.fill();
        g2d.restore();
      }

      // bloom halo (sprite blit — cheap) + a small hot core, sized by
      // magnitude: stars are luminous points, not opaque buttons.
      if (theme.glow > 0.05 && !n.deco) {
        const sp = glowSprite(color);
        const gr = r * (6 + 5 * mag) * (0.4 + theme.glow * 0.6);
        g2d.globalAlpha = alpha * 0.46 * theme.glow * mag;
        g2d.drawImage(sp, x - gr, y - gr, gr * 2, gr * 2);
      }
      g2d.globalAlpha = alpha * (0.5 + 0.34 * mag);
      g2d.fillStyle = color;
      if (n.deco) { g2d.beginPath(); g2d.arc(x, y, r * (0.44 + 0.26 * mag), 0, TAU2); }
      else traceGlyph(g2d, SRC_GLYPH[n.source] || 'circle', x, y, r * (0.44 + 0.26 * mag));
      g2d.fill();

      if (isSel) {
        g2d.globalAlpha = 0.95;
        g2d.strokeStyle = theme.text;
        g2d.lineWidth = 1.4;
        g2d.beginPath(); g2d.arc(x, y, r + 3.5, 0, TAU2); g2d.stroke();
        g2d.lineWidth = 1;
      }
      g2d.globalAlpha = 1;

      // labels: hubs at readable zoom, hovered/selected/ignited always
      const showLabel = n.label && !n.deco &&
        (isHover || isSel || (ig && ig.phase !== 'wait' && ig.glow > 0.25) || (n.hub && cam.z > 0.55));
      if (showLabel) {
        g2d.font = '500 11px ' + theme.fontMono;
        const text = n.label.length > 44 ? n.label.slice(0, 43) + '…' : n.label;
        g2d.textAlign = 'center';
        g2d.textBaseline = 'top';
        g2d.lineWidth = 3;
        g2d.strokeStyle = theme.canvasBg;
        g2d.globalAlpha = 0.85;
        g2d.strokeText(text, x, y + r + 4);
        g2d.globalAlpha = dim ? 0.5 : 0.95;
        g2d.fillStyle = (isHover || isSel) ? theme.text : theme.muted;
        g2d.fillText(text, x, y + r + 4);
        g2d.globalAlpha = 1;
        g2d.lineWidth = 1;
      }
    }

    // ---- constellation names: zoomed out, each source cluster gets its
    // star-chart label — brass small-caps, like an engraved instrument plate.
    if (grade === 'full' && cam.z < 0.62 && !spot) {
      const cent = new Map(); // source -> {x,y,c}
      for (const n of sim.nodes) {
        if (n.deco || !visible(n)) continue;
        const c = cent.get(n.source) || { x: 0, y: 0, z: 0, c: 0 };
        c.x += n.x; c.y += n.y; c.z += (n.z || 0); c.c++;
        cent.set(n.source, c);
      }
      g2d.save();
      g2d.font = '500 10px ' + theme.fontMono;
      try { g2d.letterSpacing = '0.22em'; } catch { /* older engines */ }
      g2d.textAlign = 'center';
      g2d.textBaseline = 'middle';
      for (const [src, c] of cent) {
        if (c.c < 4) continue;
        const [x, y] = worldToScreen(c.x / c.c, c.y / c.c, c.z / c.c);
        if (x < -60 || y < -30 || x > w + 60 || y > h + 30) continue;
        const text = (SRC_LABEL[src] || src).toUpperCase();
        const fade = clamp((0.62 - cam.z) / 0.2, 0, 1);
        g2d.globalAlpha = 0.28 * fade;
        g2d.strokeStyle = theme.canvasBg;
        g2d.lineWidth = 3;
        g2d.strokeText(text, x, y);
        g2d.globalAlpha = 0.85 * fade;
        g2d.fillStyle = theme.label;
        g2d.fillText(text, x, y);
        const tw = g2d.measureText(text).width;
        g2d.globalAlpha = 0.4 * fade;
        g2d.strokeStyle = theme.label;
        g2d.lineWidth = 1;
        g2d.beginPath();
        g2d.moveTo(x - tw / 2 - 14, y); g2d.lineTo(x - tw / 2 - 5, y);
        g2d.moveTo(x + tw / 2 + 5, y); g2d.lineTo(x + tw / 2 + 14, y);
        g2d.stroke();
      }
      g2d.restore();
    }
  }
  /* ---- loop ------------------------------------------------------------- */

  let stageHidden = false;

  function frame() {
    raf = 0;
    if (!active) return;
    // pause work while our section is hidden (re-checked every ~15 frames)
    if (visCheck++ % 15 === 0) {
      stageHidden = stageEl.offsetParent === null && !stageEl.getClientRects().length;
    }
    if (stageHidden) {
      raf = requestAnimationFrame(frame);
      return;
    }
    if (!sim.settled) sim.tick(1);
    if (autoOrbit) cam.tyaw += 0.00021 * 16; // the observatory turns, slowly
    cam.x += (cam.tx - cam.x) * CAMERA_EASE;
    cam.y += (cam.ty - cam.y) * CAMERA_EASE;
    cam.z += (cam.tz - cam.z) * CAMERA_EASE;
    cam.yaw += (cam.tyaw - cam.yaw) * CAMERA_EASE;
    cam.pitch += (cam.tpitch - cam.pitch) * CAMERA_EASE;
    draw();
    raf = requestAnimationFrame(frame);
  }

  function startLoop() {
    if (reduced) { draw(); return; }
    if (!raf) raf = requestAnimationFrame(frame);
  }
  function stopLoop() { if (raf) { cancelAnimationFrame(raf); raf = 0; } }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopLoop();
    else if (active && !reduced) startLoop();
  });

  return {
    sim,
    cam,
    pointer,
    get theme() { return theme; },
    get reduced() { return reduced; },
    get adjacency() { return adjacency; },
    get nodeById() { return nodeById; },
    setGraph,
    morphTo,
    setTheme,
    resize,
    draw,
    ignite,
    spotlight,
    fitAll,
    fitNodes,
    focusNode,
    clearTemporaries,
    worldToScreen,
    screenToWorld,
    visibleOf: visible,
    setHiddenPred(p) { hiddenPred = p; dirty = true; if (reduced) draw(); },
    setGrade(g) { grade = g; dirty = true; if (reduced) draw(); },
    setAutoOrbit(on) { autoOrbit = !!on && !reduced; },
    orbitBy(dyaw, dpitch) {
      cam.tyaw += dyaw;
      cam.tpitch = clamp(cam.tpitch + dpitch, -1.15, 1.15);
      if (reduced) { cam.yaw = cam.tyaw; cam.pitch = cam.tpitch; draw(); }
      dirty = true;
    },
    panBy,
    setHover(n) { if (hover !== n) { hover = n; if (reduced) draw(); } },
    getHover() { return hover; },
    setSelected(n) { selected = n; if (reduced) draw(); },
    getSelected() { return selected; },
    clearSpotlight() { highlightIds = null; if (reduced) draw(); },
    setFocal(fx, fy) { focalScreen = { fx, fy }; },
    setActive(on) {
      active = on;
      if (on) { resize(); startLoop(); }
      else stopLoop();
    },
    hitTest(sx, sy) {
      let best = null, bd = Infinity;
      for (const n of sim.nodes) {
        if (!visible(n) || n.deco || (n.temp && !n.sat)) continue;
        const [px, py, f] = worldToScreen(n.x, n.y, n.z || 0);
        const rr = Math.max(11, n.r * clamp(cam.z, 0.7, 1.6) * f + 5);
        const d = (px - sx) * (px - sx) + (py - sy) * (py - sy);
        if (d < rr * rr && d < bd) { bd = d; best = n; }
      }
      return best;
    },
    dims() { return [w, h]; },
  };
}

/* ------------------------------------------------------------------------ */
/* shared: scope select options                                             */
/* ------------------------------------------------------------------------ */

function scopeOptions(me) {
  const opts = [
    ['', 'Mine + shared'],
    ['global', 'Global'],
    ['personal', 'Personal'],
  ];
  if (me && me.is_admin) opts.push(['all', 'All (admin)']);
  return opts;
}

function fillSelect(sel, pairs, value) {
  sel.textContent = '';
  for (const [v, label] of pairs) {
    const o = document.createElement('option');
    o.value = v; o.textContent = label;
    sel.appendChild(o);
  }
  if (value != null) sel.value = value;
}

/* ------------------------------------------------------------------------ */
/* SKY — the substrate.                                                     */
/* One engine, owned by the shell, alive on every route. Views talk to it   */
/* through ctx.sky and bus events:                                          */
/*   emits  'sky:data' {data, err} · 'sky:node' {node} · 'sky:background'   */
/*          'sky:ignited' {count, total}                                    */
/*   modes  hero (home) · immersive (graph: pan/zoom/hover/click)           */
/*          recede (reading routes — dimmed by CSS, ambience dropped)       */
/* ------------------------------------------------------------------------ */

export function initSky(hostEl, ctx) {
  ensureCss();
  const { api, bus } = ctx;

  const canvas = document.createElement('canvas');
  hostEl.appendChild(canvas);
  const tooltip = document.createElement('div');
  tooltip.className = 'ko-tooltip';
  tooltip.hidden = true;
  hostEl.appendChild(tooltip);

  const engine = createEngine(hostEl, canvas, ctx);
  let mode = 'hero';
  const sky = { engine, data: null, err: null, get mode() { return mode; }, setMode, refresh };

  function setMode(next) {
    if (mode === next) return;
    mode = next;
    engine.setGrade(next === 'recede' ? 'dim' : 'full');
    canvas.classList.toggle('ko-canvas--grabbable', next === 'immersive' || next === 'hero');
    if (next !== 'immersive') { tooltip.hidden = true; engine.setHover(null); }
    engine.setAutoOrbit(next === 'hero');
    if (next === 'hero') { engine.setFocal(0.5, 0.44); engine.fitAll(); }
    if (next === 'immersive') { engine.setFocal(0.5, 0.42); }
  }

  let refreshing = false;
  async function refresh() { // background morph (wiki links changed, etc.)
    if (refreshing) return;
    refreshing = true;
    try {
      const g = await api.get('/kb.graph');
      if (g && Array.isArray(g.nodes) && g.nodes.length) {
        const data = normalizeGraph(g);
        engine.morphTo(data);
        sky.data = data;
        bus.emit('sky:data', { data, err: null });
      }
    } catch { /* keep the current sky */ }
    finally { refreshing = false; }
  }

  loadConstellation(api).then(({ data, err }) => {
    engine.setGraph(data);
    sky.data = data; sky.err = err;
    bus.emit('sky:data', { data, err });
  }).catch((err) => {
    const data = buildAmbient([], null);
    engine.setGraph(data);
    sky.data = data; sky.err = err;
    bus.emit('sky:data', { data, err });
  });

  /* ---- pointer: hero = parallax; immersive = pan/zoom/hover/click ------- */

  window.addEventListener('pointermove', (e) => {
    if (mode !== 'hero') return;
    engine.pointer.x = e.clientX; engine.pointer.y = e.clientY; engine.pointer.in = true;
  });

  const handsOn = () => mode === 'immersive' || mode === 'hero';
  let dragging = false, dragMoved = false, last = { x: 0, y: 0 };
  canvas.addEventListener('pointerdown', (e) => {
    if (!handsOn()) return;
    dragging = true; dragMoved = false;
    last = { x: e.clientX, y: e.clientY };
    canvas.classList.add('ko-canvas--grabbing');
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!handsOn()) return;
    const sx = e.clientX, sy = e.clientY; // the host IS the viewport
    engine.pointer.x = sx; engine.pointer.y = sy; engine.pointer.in = true;
    if (dragging) {
      const dx = e.clientX - last.x, dy = e.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) dragMoved = true;
      last = { x: e.clientX, y: e.clientY };
      // Drag orbits the volume (the 3D gesture); hold Shift to pan instead.
      if (e.shiftKey) engine.panBy(dx, dy);
      else engine.orbitBy(-dx * 0.0042, dy * 0.0034);
      if (engine.reduced) engine.draw();
      return;
    }
    const hit = engine.hitTest(sx, sy);
    engine.setHover(hit);
    if (hit && hit.label && mode === 'immersive') {
      tooltip.hidden = false;
      tooltip.textContent = hit.label;
      const [nx, ny] = engine.worldToScreen(hit.x, hit.y, hit.z || 0);
      tooltip.style.left = nx + 'px';
      tooltip.style.top = ny + 'px';
      canvas.style.cursor = 'pointer';
    } else {
      tooltip.hidden = true;
      canvas.style.cursor = '';
    }
  });
  canvas.addEventListener('pointerup', (e) => {
    if (!handsOn()) { dragging = false; return; }
    canvas.classList.remove('ko-canvas--grabbing');
    if (dragging && !dragMoved) {
      const hit = engine.hitTest(e.clientX, e.clientY);
      if (mode === 'immersive') {
        if (hit) bus.emit('sky:node', { node: hit });
        else bus.emit('sky:background', {});
      } else if (hit) {
        // Home: touching a star takes you into the graph, focused on it.
        location.hash = '#graph';
        setTimeout(() => { engine.focusNode(hit, 1.4); bus.emit('sky:node', { node: hit }); }, 480);
      }
    }
    dragging = false;
  });
  canvas.addEventListener('pointerleave', () => {
    engine.pointer.in = false;
    if (mode === 'immersive') { engine.setHover(null); tooltip.hidden = true; }
  });
  canvas.addEventListener('wheel', (e) => {
    if (!handsOn()) return;
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.0012);
    engine.cam.tz = clamp(engine.cam.tz * factor, 0.2, 3);
    if (engine.reduced) { engine.cam.z = engine.cam.tz; engine.draw(); }
  }, { passive: false });

  /* ---- choreography ----------------------------------------------------- */

  let igniteLift = 0;
  bus.on('ask:results', ({ passages } = {}) => {
    const count = engine.ignite(passages || []);
    // Reading routes dim the sky — lift the veil while the evidence burns.
    hostEl.classList.add('sky--ignited');
    clearTimeout(igniteLift);
    igniteLift = setTimeout(() => hostEl.classList.remove('sky--ignited'), 6500);
    bus.emit('sky:ignited', { count, total: (passages || []).length });
  });
  bus.on('constellation:focus', ({ ids } = {}) => engine.spotlight(ids || []));
  bus.on('theme', () => engine.setTheme());
  bus.on('wiki:links-changed', () => { invalidateLinksIndex(); refresh(); });

  engine.setAutoOrbit(true); // boots on home
  engine.setActive(true);
  return sky;
}

/* ------------------------------------------------------------------------ */
/* HOME — copy floating on the living sky. The lens is the ask surface.     */
/* ------------------------------------------------------------------------ */

export function initHome(sectionEl, ctx) {
  ensureCss();
  const { bus } = ctx;

  sectionEl.innerHTML = `
    <div class="ko-hero2">
      <span class="kicker">Magic Unicorn · The Observatory</span>
      <h1>Watch your organization think.</h1>
      <p class="lede">Every light is something your organization knows — a doc, a memory, a meeting, a decision. Ask, and the evidence ignites.</p>
      <div class="ko-hero-cta">
        <a class="btn btn--sm" href="#graph">Enter the graph</a>
        <a class="btn btn--sm" href="#library">Read the wiki</a>
        <a class="btn btn--sm" href="#help">Help &amp; guide</a>
      </div>
    </div>
    <p class="ko-hero-status" role="status" aria-live="polite"></p>`;

  const status = sectionEl.querySelector('.ko-hero-status');
  const heroAsk = sectionEl.querySelector('#koHeroAsk');
  if (heroAsk) heroAsk.addEventListener('click', () => { if (ctx.lens) ctx.lens.focus(); });

  const renderStats = () => {
    const data = ctx.sky && ctx.sky.data;
    if (!data) {
      status.innerHTML = `<span class="loading"><span class="spinner" aria-hidden="true"></span> charting the constellation…</span>`;
      return;
    }
    const real = data.nodes.filter((n) => !n.deco).length;
    const srcs = new Set(data.nodes.filter((n) => !n.deco).map((n) => n.source)).size;
    const links = (data.edges || []).filter((e) => e.kind === 'link').length;
    if (data.kind === 'graph') {
      status.textContent = `${real.toLocaleString()} points of light · ${srcs} sources${links ? ` · ${links} wiki links` : ''}`;
    } else if (data.kind === 'ambient') {
      status.textContent = `${real.toLocaleString()} documents charted · graph service warming up`;
    } else {
      status.textContent = 'ambient sky — the constellation lights up once sources connect';
    }
    if (ctx.sky && ctx.sky.err) {
      const errBox = document.createElement('span');
      status.appendChild(errBox);
      ctx.error(errBox, ctx.sky.err);
    }
  };
  renderStats();
  bus.on('sky:data', renderStats);
  bus.on('sky:ignited', ({ count } = {}) => {
    status.textContent = count
      ? `${count} evidence passage${count === 1 ? '' : 's'} lit — constellation links are related evidence, heuristic, not verification`
      : 'no matching evidence found for that ask';
  });
}

/* ------------------------------------------------------------------------ */
/* GRAPH — instrument chrome floating over the immersive sky.               */
/* The canvas + engine belong to ctx.sky; this view is panels only.         */
/* ------------------------------------------------------------------------ */

export function initGraph(sectionEl, ctx) {
  ensureCss();
  const { api, bus, esc } = ctx;
  const engine = ctx.sky.engine;

  sectionEl.innerHTML = `
    <div class="ko-float ko-float--tools card--strong">
      <span class="kicker">Constellation · Graph</span>
      <div class="field" style="margin:0;">
        <label class="lbl" for="ko-g-search">Find a node</label>
        <input id="ko-g-search" class="input" type="search" autocomplete="off"
               placeholder="Search, Enter cycles matches" />
        <p class="ko-searchcount" role="status" aria-live="polite"></p>
      </div>
      <div class="field" style="margin:0;">
        <label class="lbl" for="ko-g-scope">Scope</label>
        <select id="ko-g-scope" class="select"></select>
      </div>
      <fieldset class="ko-legend-wrap field" style="margin:0; border:0; padding:0;">
        <legend class="lbl">Sources</legend>
        <div class="ko-legend"></div>
      </fieldset>
      <div class="ko-graph-status" role="status" aria-live="polite"></div>
      <p class="dim mono" style="margin:0; font-size:.7rem; letter-spacing:.05em;">drag orbit · shift+drag pan · wheel zoom</p>
    </div>
    <aside class="ko-float ko-float--inspector card--strong" hidden aria-live="polite" aria-label="Node inspector"></aside>`;

  const searchInput = sectionEl.querySelector('#ko-g-search');
  const searchCount = sectionEl.querySelector('.ko-searchcount');
  const scopeSel = sectionEl.querySelector('#ko-g-scope');
  const legendEl = sectionEl.querySelector('.ko-legend');
  const statusEl = sectionEl.querySelector('.ko-graph-status');
  const inspector = sectionEl.querySelector('.ko-float--inspector');

  /* ---- filters (applied only while the graph route is active) ----------- */

  const sourceOff = new Set();
  let scopeFilter = '';
  const pred = (n) => {
    if (sourceOff.has(n.source)) return true;
    if (scopeFilter && (n.scope || 'global') !== scopeFilter) return true;
    return false;
  };
  const onGraph = () => (location.hash || '').replace('#', '').split('?')[0] === 'graph';
  const applyPred = () => engine.setHiddenPred(onGraph() ? pred : null);

  /* ---- legend + scope chrome ------------------------------------------- */

  function ensureEdgeKey(edges) {
    const wrap = legendEl.parentElement;
    const existing = wrap.querySelector('.ko-edgekey');
    const hasLinks = (edges || []).some((e) => e.kind === 'link');
    if (!hasLinks) { if (existing) existing.remove(); return; }
    if (existing) return;
    const row = document.createElement('div');
    row.className = 'ko-edgekey';
    row.innerHTML = `
      <span class="ko-edgekey__item"><span class="ko-edgeline ko-edgeline--link" aria-hidden="true"></span>linked</span>
      <span class="ko-edgekey__item"><span class="ko-edgeline" aria-hidden="true"></span>related</span>`;
    wrap.appendChild(row);
  }

  function buildChrome(data) {
    const present = [...new Set(data.nodes.map((n) => n.source))];
    present.sort((a, b) => {
      const ia = SRC_ORDER.indexOf(a), ib = SRC_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    legendEl.textContent = '';
    for (const key of present) {
      const count = data.nodes.filter((n) => n.source === key && !n.deco).length ||
                    data.nodes.filter((n) => n.source === key).length;
      const label = document.createElement('label');
      label.className = 'pill';
      label.innerHTML = `
        <input type="checkbox" ${sourceOff.has(key) ? '' : 'checked'} />
        <span class="ko-glyphwrap" aria-hidden="true">${glyphSvg(key)}</span>
        <span>${esc(SRC_LABEL[key] || key)}</span>
        <span class="dim">${count.toLocaleString()}</span>`;
      const dot = label.querySelector('.ko-glyphwrap');
      dot.style.color = `var(${SRC_TOKEN[key] || '--mu-canvas-star'})`;
      label.dataset.off = sourceOff.has(key) ? '1' : '';
      const cb = label.querySelector('input');
      cb.addEventListener('change', () => {
        if (cb.checked) sourceOff.delete(key); else sourceOff.add(key);
        label.dataset.off = cb.checked ? '' : '1';
        applyPred();
        announce();
      });
      legendEl.appendChild(label);
    }
    ensureEdgeKey(data.edges);

    const scopes = [...new Set(data.nodes.map((n) => n.scope || 'global'))].sort();
    const pairs = [['', 'All visible scopes']];
    for (const s of scopes) pairs.push([s, s]);
    fillSelect(scopeSel, pairs, scopeFilter);
  }

  scopeSel.addEventListener('change', () => {
    scopeFilter = scopeSel.value;
    applyPred();
    announce();
  });

  function announce() {
    const vis = engine.sim.nodes.filter((n) => engine.visibleOf(n) && !n.deco).length;
    statusEl.textContent = `${vis.toLocaleString()} nodes visible`;
  }

  /* ---- inspector -------------------------------------------------------- */

  function closeInspector() {
    inspector.hidden = true;
    engine.setSelected(null);
  }

  async function openInspector(node) {
    engine.setSelected(node);
    inspector.hidden = false;
    const key = node.source;
    inspector.innerHTML = `
      <div class="ko-inspector-head">
        <h2 class="section section--flush">${esc(node.label || node.id)}</h2>
        <button type="button" class="btn btn--sm ko-close" aria-label="Close inspector">Close</button>
      </div>
      <div class="ko-pills">
        <span class="pill"><span class="ko-glyphwrap" aria-hidden="true">${glyphSvg(key)}</span>${esc(SRC_LABEL[key] || key)}</span>
        <span class="pill">${esc(node.scope || 'global')}</span>
        ${node.collection ? `<span class="pill">${esc(node.collection)}</span>` : ''}
      </div>
      <p class="ko-linksline" hidden></p>
      <div class="ko-snippet"><span class="loading"><span class="spinner" aria-hidden="true"></span> Fetching context…</span></div>
      <div class="ko-neighbors-wrap">
        <p class="lbl">Nearby in the constellation <span class="dim">— related evidence, heuristic, not verification</span></p>
        <div class="ko-neighbors"></div>
      </div>
      <div class="ko-actions">
        <button type="button" class="btn btn--sm" data-act="library">Open in Library</button>
        <button type="button" class="btn btn--sm btn--primary" data-act="ask">Ask about this</button>
      </div>
    `;
    const dot = inspector.querySelector('.ko-glyphwrap');
    if (dot) dot.style.color = `var(${SRC_TOKEN[key] || '--mu-canvas-star'})`;

    const closeBtn = inspector.querySelector('.ko-close');
    closeBtn.addEventListener('click', closeInspector);
    // Move focus into the inspector for canvas clicks (Escape + SR context);
    // keep it in the search box when Enter-cycling matches.
    if (document.activeElement !== searchInput) closeBtn.focus({ preventScroll: true });
    inspector.querySelector('[data-act="library"]').addEventListener('click', () => {
      bus.emit('nav', { view: 'library', ref: node.ref || node.id, path: node.ref || null });
      location.hash = '#library';
    });
    inspector.querySelector('[data-act="ask"]').addEventListener('click', () => {
      bus.emit('ask:run', { query: node.label || node.id, scope: null, k: 8 });
      location.hash = '#ask';
    });

    // Wiki link counts (from /kb.links — lazy, cached; 404 → line omitted).
    if (node.source === 'wiki' && !node.hub && !node.deco) {
      fetchLinksIndex(api).then((idx) => {
        if (!idx || inspector.hidden) return;
        const sel = engine.getSelected();
        if (!sel || sel.id !== node.id) return;
        const el = inspector.querySelector('.ko-linksline');
        if (!el) return;
        const keys = [node.ref, node.id, String(node.id).replace(/^doc:/, '')].filter(Boolean);
        let c = null;
        for (const k of keys) { if (idx.has(String(k))) { c = idx.get(String(k)); break; } }
        const cc = c || { in: 0, out: 0 };
        el.textContent = `Links · ${cc.in} in · ${cc.out} out`;
        el.hidden = false;
      });
    }

    // 1-hop neighbors
    const nbWrap = inspector.querySelector('.ko-neighbors');
    const adj = engine.adjacency.get(node.id);
    const neighbors = adj
      ? [...adj].map((id) => engine.nodeById.get(id)).filter((n) => n && n.label && !n.deco).slice(0, 6)
      : [];
    if (!neighbors.length) {
      nbWrap.innerHTML = `<p class="empty">No charted neighbors.</p>`;
    } else {
      for (const nb of neighbors) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn btn--sm';
        b.textContent = nb.label.length > 40 ? nb.label.slice(0, 39) + '…' : nb.label;
        b.addEventListener('click', () => { engine.focusNode(nb); openInspector(nb); });
        nbWrap.appendChild(b);
      }
    }

    // snippet
    const snip = inspector.querySelector('.ko-snippet');
    const looksLikePath = node.ref && /[\w-]+(\/[\w. -]+)+|\.md$/i.test(String(node.ref));
    if (node.source === 'wiki' && looksLikePath) {
      try {
        const res = await api.get('/kb.get_doc', { id: node.ref });
        const doc = res && res.doc;
        if (inspector.hidden || engine.getSelected() !== node) return;
        const body = (doc && (doc.body || doc.md)) || '';
        const text = body.replace(/^#.*$/gm, '').replace(/\s+/g, ' ').trim().slice(0, 380);
        snip.innerHTML = text
          ? `<p>${esc(text)}…</p><p class="dim">${esc(node.ref)}</p>`
          : `<p class="empty">Document found, but it has no readable body.</p>`;
      } catch (err) {
        snip.textContent = '';
        if (err && err.status === 403) ctx.locked(snip, 'this document');
        else ctx.error(snip, err);
      }
    } else {
      const meta = node.rawSource || node.collection || SRC_LABEL[node.source] || node.source;
      snip.innerHTML = `<p class="dim">${esc(String(meta))}${node.size > 1 ? ` · ${node.size.toLocaleString()} items` : ''}</p>`;
    }
  }

  // Node interactions arrive from the sky.
  bus.on('sky:node', ({ node } = {}) => { if (onGraph() && node) openInspector(node); });
  bus.on('sky:background', () => { if (onGraph()) closeInspector(); });

  // Escape closes the inspector wherever focus sits. Document-level, added
  // ONCE at init — guards keep it inert unless graph + inspector are visible.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (sectionEl.hidden || inspector.hidden) return;
    e.preventDefault();
    closeInspector();
    searchInput.focus();
  });

  /* ---- search-to-focus (keyboard path) ---------------------------------- */

  let matches = [];
  let matchIdx = -1;

  function runSearch() {
    const q = normText(searchInput.value);
    matchIdx = -1;
    if (!q) { matches = []; searchCount.textContent = ''; engine.clearSpotlight(); return; }
    matches = engine.sim.nodes.filter((n) =>
      engine.visibleOf(n) && n.label && !n.deco && normText(n.label).includes(q));
    searchCount.textContent = matches.length
      ? `${matches.length} match${matches.length === 1 ? '' : 'es'} — press Enter to cycle`
      : 'No matches in view';
    if (matches.length) engine.spotlight(matches.slice(0, 40).map((n) => n.id));
    else engine.clearSpotlight();
  }

  searchInput.addEventListener('input', runSearch);
  searchInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (!matches.length) return;
    matchIdx = (matchIdx + 1) % matches.length;
    const n = matches[matchIdx];
    engine.focusNode(n, 1.5);
    openInspector(n);
    searchCount.textContent = `${matchIdx + 1} of ${matches.length} — ${n.label}`;
  });

  /* ---- data (shared engine — chrome rebuilds on sky data) --------------- */

  statusEl.innerHTML = `<span class="loading"><span class="spinner" aria-hidden="true"></span> Loading graph…</span>`;
  const onData = ({ data, err } = {}) => {
    if (!data) return;
    buildChrome(data);
    announce();
    if (err) { const box = document.createElement('div'); statusEl.appendChild(box); ctx.error(box, err); }
  };
  if (ctx.sky.data) onData({ data: ctx.sky.data, err: ctx.sky.err });
  bus.on('sky:data', onData);

  /* ---- route: filters + inspector live only inside the graph ------------ */

  const routeCheck = () => {
    applyPred();
    if (!onGraph()) { closeInspector(); engine.clearSpotlight(); }
    else announce();
  };
  window.addEventListener('hashchange', routeCheck);
  bus.on('nav', () => routeCheck());
  routeCheck();
}
