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
.ko-stage { position: relative; min-height: max(62vh, 460px); height: calc(100vh - 8rem); overflow: hidden; border-radius: var(--mu-radius-lg); background: var(--mu-canvas-bg); }
.ko-stage--hero { height: calc(100vh - 5.5rem); min-height: 520px; border-radius: 0 0 var(--mu-radius-lg) var(--mu-radius-lg); }
.ko-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.ko-canvas--grabbable { cursor: grab; } .ko-canvas--grabbing { cursor: grabbing; }
.ko-hero { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .75rem; padding: 1.25rem; text-align: center; pointer-events: none; }
.ko-hero > * { pointer-events: auto; }
.ko-hero .title { text-shadow: 0 0 calc(28px * var(--mu-glow, .6)) color-mix(in srgb, var(--mu-color-primary) 45%, transparent); }
.ko-askbar { width: min(680px, calc(100% - 2rem)); margin-top: .5rem; }
.ko-askbar .row { display: flex; gap: .5rem; align-items: stretch; flex-wrap: wrap; }
.ko-askbar .input { flex: 1 1 16rem; min-height: 44px; }
.ko-askbar .select { flex: 0 1 auto; min-height: 44px; }
.ko-askbar .btn { min-height: 44px; }
.ko-hint { max-width: 52ch; }
.ko-status { position: absolute; left: 1rem; bottom: .85rem; right: 1rem; display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; pointer-events: none; font-family: var(--mu-font-mono); font-size: .78rem; color: var(--mu-color-dim); }
.ko-tooltip { position: absolute; z-index: 4; max-width: 34ch; padding: .35rem .6rem; border-radius: var(--mu-radius-sm); border: 1px solid var(--mu-color-border); background: var(--mu-color-surface-strong); color: var(--mu-color-text); font-family: var(--mu-font-mono); font-size: .78rem; pointer-events: none; transform: translate(-50%, calc(-100% - 12px)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ko-chrome { position: absolute; inset: 0; pointer-events: none; display: flex; align-items: stretch; justify-content: space-between; padding: 1rem; gap: 1rem; }
.ko-toolbar { pointer-events: auto; align-self: flex-start; display: flex; flex-direction: column; gap: .65rem; width: min(300px, 86vw); max-height: calc(100% - 1rem); overflow: auto; }
.ko-legend { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.ko-legend .pill { position: relative; display: inline-flex; align-items: center; gap: .45rem; min-height: 44px; padding-inline: .8rem; cursor: pointer; user-select: none; }
.ko-legend .pill input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; margin: 0; cursor: pointer; }
.ko-legend .pill:has(input:focus-visible) { outline: 2px solid var(--mu-color-primary); outline-offset: 2px; }
.ko-legend .pill[data-off="1"] { opacity: .45; }
.ko-dot { width: .65rem; height: .65rem; border-radius: 50%; flex: none; }
.ko-edgekey { display: flex; gap: .9rem; align-items: center; flex-wrap: wrap; margin-top: .4rem; font-family: var(--mu-font-mono); font-size: .75rem; color: var(--mu-color-dim); }
.ko-edgekey__item { display: inline-flex; align-items: center; gap: .4rem; }
.ko-edgeline { width: 1.4rem; height: 0; border-top: 1px solid var(--mu-canvas-edge); opacity: .5; flex: none; }
.ko-edgeline--link { opacity: 1; }
.ko-linksline { font-family: var(--mu-font-mono); font-size: .78rem; font-variant-numeric: tabular-nums; color: var(--mu-color-muted); margin: 0; }
.ko-inspector { pointer-events: auto; align-self: flex-start; margin-left: auto; width: min(360px, 90vw); max-height: calc(100% - 1rem); overflow: auto; display: flex; flex-direction: column; gap: .6rem; }
.ko-inspector[hidden] { display: none; }
.ko-inspector .ko-pills { display: flex; flex-wrap: wrap; gap: .4rem; }
.ko-snippet { font-size: .85rem; line-height: 1.5; color: var(--mu-color-muted); max-width: 72ch; overflow-wrap: anywhere; }
.ko-neighbors { display: flex; flex-direction: column; gap: .25rem; }
.ko-neighbors button { text-align: left; }
.ko-inspector .ko-actions { display: flex; gap: .5rem; flex-wrap: wrap; }
.ko-inspector .ko-close { margin-left: auto; }
.ko-inspector-head { display: flex; align-items: flex-start; gap: .5rem; }
.ko-searchcount { font-family: var(--mu-font-mono); font-size: .75rem; color: var(--mu-color-dim); min-height: 1.1em; }
@media (max-width: 760px) {
  .ko-chrome { flex-direction: column; overflow: auto; }
  .ko-inspector { margin-left: 0; width: 100%; }
  .ko-stage { height: calc(100vh - 6rem); }
}
@media (prefers-reduced-motion: reduce) {
  .ko-hero .title { text-shadow: none; }
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

function createEngine(stageEl, canvas, ctx, opts = {}) {
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

  const cam = { x: 0, y: 0, z: 1, tx: 0, ty: 0, tz: 1 };
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

  function setGraph(data) {
    sim.setGraph(data.nodes, data.edges);
    tagLinkKinds(data.edges);
    rebuildIndexes();
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
      if (old) { n.x = old.x; n.y = old.y; n.vx = old.vx || 0; n.vy = old.vy || 0; }
    }
    sim.setGraph(data.nodes, data.edges);
    tagLinkKinds(data.edges);
    rebuildIndexes();
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

  function worldToScreen(x, y) { return [(x - cam.x) * cam.z + w / 2, (y - cam.y) * cam.z + h / 2]; }
  function screenToWorld(sx, sy) { return [(sx - w / 2) / cam.z + cam.x, (sy - h / 2) / cam.z + cam.y]; }

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
    cam.tz = clamp(Math.min(w / bw, h / bh), 0.28, 2.4);
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
    g2d.fillStyle = theme.canvasBg;
    g2d.fillRect(0, 0, w, h);

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
      return worldToScreen(n.x + ox, n.y + oy);
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

    // ---- nodes
    for (const n of sim.nodes) {
      if (!visible(n) || (n.temp && !n.sat)) continue;
      const [x, y] = posOf(n);
      if (x < -30 || y < -30 || x > w + 30 || y > h + 30) continue;
      const ig = igniteState(n.id, now);
      const isHover = n === hover;
      const isSel = n === selected;
      const inSpot = !spot || spot.has(n.id) || isHover || isSel || (hoverAdj && hoverAdj.has(n.id));
      const nearHover = !hover || isHover || (hoverAdj && hoverAdj.has(n.id));
      const dim = (!inSpot || !nearHover);

      let r = n.r * clamp(cam.z, 0.7, 1.6);
      let alpha = n.deco ? 0.5 : 0.95;
      if (dim) alpha *= 0.22;
      const color = nodeColor(n);

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

      g2d.globalAlpha = alpha;
      g2d.fillStyle = color;
      g2d.beginPath(); g2d.arc(x, y, r, 0, TAU2); g2d.fill();

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
    cam.x += (cam.tx - cam.x) * CAMERA_EASE;
    cam.y += (cam.ty - cam.y) * CAMERA_EASE;
    cam.z += (cam.tz - cam.z) * CAMERA_EASE;
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
      const [x, y] = screenToWorld(sx, sy);
      return sim.find(x, y, 12 / cam.z, (n) => visible(n) && !n.deco && !(n.temp && !n.sat));
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
/* HOME — the hero                                                          */
/* ------------------------------------------------------------------------ */

export function initHome(sectionEl, ctx) {
  ensureCss();
  const { api, bus, esc } = ctx;

  sectionEl.innerHTML = `
    <div class="ko-stage ko-stage--hero">
      <canvas class="ko-canvas" aria-hidden="true"></canvas>
      <div class="ko-hero">
        <span class="kicker">Knowledge-Ops · Constellation</span>
        <h1 class="title">Watch your organization think.</h1>
        <p class="lede">Every light is something your organization knows — a doc, a memory, a meeting. Ask, and the evidence lights up.</p>
        <form class="ko-askbar card card--strong" novalidate>
          <div class="field">
            <label class="lbl" for="ko-home-q">Ask your organization anything</label>
            <div class="row">
              <input id="ko-home-q" class="input" type="text" name="q" autocomplete="off"
                     placeholder="Ask your organization anything…" />
              <select id="ko-home-scope" class="select" aria-label="Search scope"></select>
              <button class="btn btn--primary" type="submit">Ask</button>
            </div>
          </div>
        </form>
        <p class="ko-hint dim">Answers cite their sources. Constellation links are related evidence — heuristic, not verification.</p>
      </div>
      <div class="ko-status" role="status" aria-live="polite"></div>
    </div>
  `;

  const stage = sectionEl.querySelector('.ko-stage');
  const canvas = sectionEl.querySelector('.ko-canvas');
  const form = sectionEl.querySelector('form');
  const input = sectionEl.querySelector('#ko-home-q');
  const scopeSel = sectionEl.querySelector('#ko-home-scope');
  const status = sectionEl.querySelector('.ko-status');

  fillSelect(scopeSel, scopeOptions(ctx.me), '');

  const engine = createEngine(stage, canvas, ctx);
  engine.setFocal(0.5, 0.44);

  // pointer parallax only (home is not pannable — the ask bar owns the stage)
  stage.addEventListener('pointermove', (e) => {
    const r = stage.getBoundingClientRect();
    engine.pointer.x = e.clientX - r.left;
    engine.pointer.y = e.clientY - r.top;
    engine.pointer.in = true;
  });
  stage.addEventListener('pointerleave', () => { engine.pointer.in = false; });

  status.innerHTML = `<span class="loading"><span class="spinner" aria-hidden="true"></span> Charting the constellation…</span>`;

  loadConstellation(api).then(({ data, err }) => {
    engine.setGraph(data);
    const real = data.nodes.filter((n) => !n.deco).length;
    const srcs = new Set(data.nodes.map((n) => n.source)).size;
    if (data.kind === 'graph') {
      status.textContent = `${real.toLocaleString()} points of light · ${srcs} sources`;
    } else if (data.kind === 'ambient') {
      status.textContent = `${real.toLocaleString()} documents charted · graph service warming up`;
    } else {
      status.textContent = 'Ambient sky — the constellation will light up once sources connect.';
    }
    if (err) {
      const errBox = document.createElement('div');
      status.appendChild(errBox);
      ctx.error(errBox, err);
    }
  }).catch((err) => {
    engine.setGraph(buildAmbient([], null));
    status.textContent = '';
    ctx.error(status, err);
  });

  // Ask: hand off to the ask view; it executes and emits 'ask:results'.
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) { input.focus(); return; }
    bus.emit('ask:run', { query, scope: scopeSel.value || null, k: 8 });
    location.hash = '#ask';
  });

  // The jaw-drop: results come back, evidence ignites.
  bus.on('ask:results', ({ passages } = {}) => {
    engine.ignite(passages || []);
    const n = (passages || []).length;
    status.textContent = n
      ? `${n} evidence passage${n === 1 ? '' : 's'} lit — related constellation links are heuristic, not verification.`
      : 'No matching evidence found for that ask.';
  });

  bus.on('constellation:focus', ({ ids } = {}) => engine.spotlight(ids || []));
  bus.on('theme', () => engine.setTheme());

  // A doc save changed wiki links: refetch the graph in the background and
  // morph — existing nodes keep their positions, no full reset.
  let homeRefreshing = false;
  bus.on('wiki:links-changed', async () => {
    invalidateLinksIndex();
    if (homeRefreshing) return;
    homeRefreshing = true;
    try {
      const g = await api.get('/kb.graph');
      if (g && Array.isArray(g.nodes) && g.nodes.length) engine.morphTo(normalizeGraph(g));
    } catch { /* keep the current sky */ }
    finally { homeRefreshing = false; }
  });

  // Activity: run only while #home (or empty hash) is the route.
  const routeCheck = () => {
    const hash = (location.hash || '#home').replace('#', '');
    engine.setActive(hash === 'home' || hash === '');
  };
  window.addEventListener('hashchange', routeCheck);
  bus.on('nav', () => routeCheck());
  routeCheck();
}

/* ------------------------------------------------------------------------ */
/* GRAPH — same engine, chrome on                                           */
/* ------------------------------------------------------------------------ */

export function initGraph(sectionEl, ctx) {
  ensureCss();
  const { api, bus, esc } = ctx;

  sectionEl.innerHTML = `
    <div class="ko-stage">
      <canvas class="ko-canvas ko-canvas--grabbable" aria-hidden="true"></canvas>
      <div class="ko-chrome">
        <div class="ko-toolbar card">
          <span class="kicker">Constellation · Graph</span>
          <div class="field">
            <label class="lbl" for="ko-g-search">Find a node</label>
            <input id="ko-g-search" class="input" type="search" autocomplete="off"
                   placeholder="Search, Enter cycles matches" />
            <p class="ko-searchcount" role="status" aria-live="polite"></p>
          </div>
          <div class="field">
            <label class="lbl" for="ko-g-scope">Scope</label>
            <select id="ko-g-scope" class="select"></select>
          </div>
          <fieldset class="ko-legend-wrap field">
            <legend class="lbl">Sources</legend>
            <div class="ko-legend"></div>
          </fieldset>
          <div class="ko-graph-status" role="status" aria-live="polite"></div>
        </div>
        <aside class="ko-inspector card card--strong" hidden aria-live="polite" aria-label="Node inspector"></aside>
      </div>
      <div class="ko-tooltip" hidden aria-hidden="true"></div>
    </div>
  `;

  const stage = sectionEl.querySelector('.ko-stage');
  const canvas = sectionEl.querySelector('.ko-canvas');
  const searchInput = sectionEl.querySelector('#ko-g-search');
  const searchCount = sectionEl.querySelector('.ko-searchcount');
  const scopeSel = sectionEl.querySelector('#ko-g-scope');
  const legendEl = sectionEl.querySelector('.ko-legend');
  const statusEl = sectionEl.querySelector('.ko-graph-status');
  const inspector = sectionEl.querySelector('.ko-inspector');
  const tooltip = sectionEl.querySelector('.ko-tooltip');

  const engine = createEngine(stage, canvas, ctx);
  engine.setFocal(0.5, 0.4);

  // filter state
  const sourceOff = new Set();
  let scopeFilter = '';
  engine.setHiddenPred((n) => {
    if (sourceOff.has(n.source)) return true;
    if (scopeFilter && (n.scope || 'global') !== scopeFilter) return true;
    return false;
  });

  /* ---- legend + scope chrome ------------------------------------------- */

  // Edge-type key: shown only when the graph actually carries real wiki-link
  // edges, so the legend never describes something that isn't on the canvas.
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
    // sources present in the data, canonical order first
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
        <input type="checkbox" checked />
        <span class="ko-dot" aria-hidden="true"></span>
        <span>${esc(SRC_LABEL[key] || key)}</span>
        <span class="dim">${count.toLocaleString()}</span>`;
      const dot = label.querySelector('.ko-dot');
      dot.style.color = `var(${SRC_TOKEN[key] || '--mu-canvas-star'})`;
      dot.style.background = `var(${SRC_TOKEN[key] || '--mu-canvas-star'})`;
      const cb = label.querySelector('input');
      cb.addEventListener('change', () => {
        if (cb.checked) sourceOff.delete(key); else sourceOff.add(key);
        label.dataset.off = cb.checked ? '' : '1';
        engine.setHiddenPred((n) => sourceOff.has(n.source) || (scopeFilter && (n.scope || 'global') !== scopeFilter));
        announce();
      });
      legendEl.appendChild(label);
    }
    ensureEdgeKey(data.edges);

    const scopes = [...new Set(data.nodes.map((n) => n.scope || 'global'))].sort();
    const pairs = [['', 'All visible scopes']];
    for (const s of scopes) pairs.push([s, s]);
    fillSelect(scopeSel, pairs, '');
  }

  scopeSel.addEventListener('change', () => {
    scopeFilter = scopeSel.value;
    engine.setHiddenPred((n) => sourceOff.has(n.source) || (scopeFilter && (n.scope || 'global') !== scopeFilter));
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
        <h2 class="section">${esc(node.label || node.id)}</h2>
        <button type="button" class="btn btn--sm ko-close" aria-label="Close inspector">Close</button>
      </div>
      <div class="ko-pills">
        <span class="pill"><span class="ko-dot" aria-hidden="true"></span>${esc(SRC_LABEL[key] || key)}</span>
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
    const dot = inspector.querySelector('.ko-dot');
    dot.style.color = `var(${SRC_TOKEN[key] || '--mu-canvas-star'})`;
    dot.style.background = `var(${SRC_TOKEN[key] || '--mu-canvas-star'})`;

    const closeBtn = inspector.querySelector('.ko-close');
    closeBtn.addEventListener('click', closeInspector);
    // Move focus into the inspector: Escape works after mouse-only canvas
    // clicks (focus would otherwise sit on <body>) and screen readers land
    // on labelled context (the panel is aria-live). Exception: when opened by
    // Enter-cycling from the search input, focus stays there so "Enter cycles
    // matches" keeps working — Escape still closes via the document handler.
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

  /* ---- pointer: hover, tooltip, click, pan, zoom ------------------------ */

  let dragging = false;
  let dragMoved = false;
  let last = { x: 0, y: 0 };

  canvas.addEventListener('pointerdown', (e) => {
    dragging = true; dragMoved = false;
    last = { x: e.clientX, y: e.clientY };
    canvas.classList.add('ko-canvas--grabbing');
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    const r = stage.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    engine.pointer.x = sx; engine.pointer.y = sy; engine.pointer.in = true;
    if (dragging) {
      const dx = e.clientX - last.x, dy = e.clientY - last.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) dragMoved = true;
      last = { x: e.clientX, y: e.clientY };
      engine.cam.x -= dx / engine.cam.z; engine.cam.tx = engine.cam.x;
      engine.cam.y -= dy / engine.cam.z; engine.cam.ty = engine.cam.y;
      if (engine.reduced) engine.draw();
      return;
    }
    const hit = engine.hitTest(sx, sy);
    engine.setHover(hit);
    if (hit && hit.label) {
      tooltip.hidden = false;
      tooltip.textContent = hit.label;
      const [nx, ny] = engine.worldToScreen(hit.x, hit.y);
      tooltip.style.left = nx + 'px';
      tooltip.style.top = ny + 'px';
      canvas.style.cursor = 'pointer';
    } else {
      tooltip.hidden = true;
      canvas.style.cursor = '';
    }
  });
  canvas.addEventListener('pointerup', (e) => {
    canvas.classList.remove('ko-canvas--grabbing');
    if (dragging && !dragMoved) {
      const r = stage.getBoundingClientRect();
      const hit = engine.hitTest(e.clientX - r.left, e.clientY - r.top);
      if (hit) openInspector(hit);
      else closeInspector();
    }
    dragging = false;
  });
  canvas.addEventListener('pointerleave', () => {
    engine.pointer.in = false;
    engine.setHover(null);
    tooltip.hidden = true;
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.0012);
    engine.cam.tz = clamp(engine.cam.tz * factor, 0.2, 3);
    if (engine.reduced) { engine.cam.z = engine.cam.tz; engine.draw(); }
  }, { passive: false });

  // Escape closes the inspector wherever focus sits (canvas clicks leave it
  // on <body>, outside sectionEl). Document-level, added ONCE at init — the
  // guards keep it inert unless the graph view and inspector are both
  // visible, so there is nothing to unbind and no per-open leak.
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

  /* ---- data ------------------------------------------------------------- */

  statusEl.innerHTML = `<span class="loading"><span class="spinner" aria-hidden="true"></span> Loading graph…</span>`;
  loadConstellation(api).then(({ data, err }) => {
    engine.setGraph(data);
    buildChrome(data);
    announce();
    if (err) { const box = document.createElement('div'); statusEl.appendChild(box); ctx.error(box, err); }
  }).catch((err) => {
    engine.setGraph(buildAmbient([], null));
    buildChrome({ nodes: engine.sim.nodes });
    statusEl.textContent = '';
    ctx.error(statusEl, err);
  });

  /* ---- bus -------------------------------------------------------------- */

  bus.on('constellation:focus', ({ ids } = {}) => engine.spotlight(ids || []));
  bus.on('ask:results', ({ passages } = {}) => { engine.ignite(passages || []); });
  bus.on('theme', () => engine.setTheme());

  // A doc save changed wiki links: drop the /kb.links cache, refetch the
  // graph in the background, and morph in place (positions reused by id).
  let graphRefreshing = false;
  bus.on('wiki:links-changed', async () => {
    invalidateLinksIndex();
    if (graphRefreshing) return;
    graphRefreshing = true;
    try {
      const g = await api.get('/kb.graph');
      if (g && Array.isArray(g.nodes) && g.nodes.length) {
        const data = normalizeGraph(g);
        engine.morphTo(data);
        ensureEdgeKey(data.edges);
        announce();
      }
    } catch { /* keep the current sky */ }
    finally { graphRefreshing = false; }
  });

  const routeCheck = () => {
    engine.setActive((location.hash || '').replace('#', '') === 'graph');
  };
  window.addEventListener('hashchange', routeCheck);
  bus.on('nav', () => routeCheck());
  routeCheck();
}
