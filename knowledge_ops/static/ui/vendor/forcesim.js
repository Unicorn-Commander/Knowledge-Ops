/*
 * forcesim.js — dependency-free 2D force-directed layout for the
 * Knowledge-Ops constellation. Vanilla ES module, zero external URLs.
 *
 * Integration: velocity Verlet (half-step velocity update with the
 * previous tick's acceleration), damped, with alpha annealing.
 * Forces: link springs, many-body repulsion (uniform spatial grid,
 * cutoff radius -> ~O(n·k) per tick), soft centering, and pairwise
 * collision resolution folded into the same grid pass.
 *
 * Comfortable at 800 nodes / 2000 edges on a 60fps budget.
 *
 * Usage:
 *   import { createForceSim } from './vendor/forcesim.js';
 *   const sim = createForceSim({ center: { x: 0, y: 0 } });
 *   sim.setGraph(nodes, edges);   // nodes: [{id, r?, x?, y?, fx?, fy?}]
 *   sim.tick(120);                // edges: [{a, b, w?}] (ids)
 *
 * The sim mutates node objects in place (x, y, vx, vy, ...). Fixed
 * nodes: set fx/fy (clear with null). Reheat with kick(alpha).
 */

const TAU = Math.PI * 2;
const GOLDEN = Math.PI * (3 - Math.sqrt(5)); // golden angle for seed layout

function hashish(str) {
  let h = 2166136261 >>> 0;
  const s = String(str);
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

const DEFAULTS = {
  linkDistance: 62,      // spring rest length (shrinks a little with weight)
  linkStrength: 0.055,   // spring constant
  repulsion: 950,        // many-body charge magnitude (repulsive)
  repulsionCutoff: 230,  // px; beyond this, repulsion is ignored (grid cell size)
  centerStrength: 0.011, // pull toward opts.center
  collidePadding: 3,     // extra spacing on top of node radii
  collideStrength: 0.55, // positional overlap correction factor
  dt: 1,                 // timestep (unitless)
  velocityDecay: 0.8,    // per-tick velocity damping
  maxVelocity: 26,       // clamp; keeps cold starts from exploding
  maxForce: 8,           // clamp on any single pairwise repulsion
  alpha: 1,
  alphaDecay: 0.026,
  alphaMin: 0.004,
  alphaTarget: 0,
  seedRadius: 240,       // radius of the initial golden-spiral placement
};

export function createForceSim(options = {}) {
  const opt = Object.assign({}, DEFAULTS, options);
  const center = Object.assign({ x: 0, y: 0 }, options.center);

  let nodes = [];
  let links = []; // resolved: {s, t, w, rest}
  let alpha = opt.alpha;

  // ---- spatial grid ------------------------------------------------------
  // Rebuilt each tick. Cell size = repulsion cutoff so any interacting pair
  // is found in the 3x3 neighborhood of a cell.
  const grid = new Map();
  const cellOf = (v, size) => Math.floor(v / size);
  const gridKey = (cx, cy) => (cx + 32768) * 65536 + (cy + 32768);

  function buildGrid(size) {
    grid.clear();
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const k = gridKey(cellOf(n.x, size), cellOf(n.y, size));
      const bucket = grid.get(k);
      if (bucket) bucket.push(i);
      else grid.set(k, [i]);
    }
  }

  // ---- node prep ---------------------------------------------------------
  function prepNode(n, i, total) {
    if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) {
      // Deterministic-ish golden spiral seeded by id hash: stable layouts
      // across reloads, no clumped random starts.
      const h = hashish(n.id != null ? n.id : i);
      const t = i + (h % 97) / 97;
      const rad = opt.seedRadius * Math.sqrt((t + 1) / (total + 1));
      const ang = t * GOLDEN + (h % 360) * (TAU / 360) * 0.01;
      n.x = center.x + Math.cos(ang) * rad;
      n.y = center.y + Math.sin(ang) * rad;
    }
    if (!Number.isFinite(n.vx)) n.vx = 0;
    if (!Number.isFinite(n.vy)) n.vy = 0;
    n.ax = 0; n.ay = 0;
    n.pax = 0; n.pay = 0;
    if (!Number.isFinite(n.r)) n.r = 5;
    return n;
  }

  // ---- forces ------------------------------------------------------------
  function applyCentering(a) {
    const k = opt.centerStrength * a;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      n.ax += (center.x - n.x) * k;
      n.ay += (center.y - n.y) * k;
    }
  }

  function applySprings(a) {
    for (let i = 0; i < links.length; i++) {
      const l = links[i];
      const s = l.s, t = l.t;
      let dx = t.x - s.x, dy = t.y - s.y;
      let d = Math.sqrt(dx * dx + dy * dy) || 1e-4;
      const f = opt.linkStrength * l.w * (d - l.rest) * a;
      dx /= d; dy /= d;
      s.ax += dx * f; s.ay += dy * f;
      t.ax -= dx * f; t.ay -= dy * f;
    }
  }

  function applyRepulsionAndCollide(a) {
    const cutoff = opt.repulsionCutoff;
    const cutoff2 = cutoff * cutoff;
    buildGrid(cutoff);
    const rep = opt.repulsion * a;
    const maxF = opt.maxForce;
    const pad = opt.collidePadding;
    const cStr = opt.collideStrength;

    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const cx = cellOf(n.x, cutoff);
      const cy = cellOf(n.y, cutoff);
      for (let gx = cx - 1; gx <= cx + 1; gx++) {
        for (let gy = cy - 1; gy <= cy + 1; gy++) {
          const bucket = grid.get(gridKey(gx, gy));
          if (!bucket) continue;
          for (let b = 0; b < bucket.length; b++) {
            const j = bucket[b];
            if (j <= i) continue; // symmetric pair once
            const m = nodes[j];
            let dx = n.x - m.x, dy = n.y - m.y;
            let d2 = dx * dx + dy * dy;
            if (d2 >= cutoff2) continue;
            if (d2 < 1e-6) {
              // Coincident points: nudge apart deterministically.
              const jig = ((i * 7919 + j * 104729) % 628) / 100;
              dx = Math.cos(jig) * 0.5; dy = Math.sin(jig) * 0.5;
              d2 = 0.25;
            }
            const d = Math.sqrt(d2);
            const ux = dx / d, uy = dy / d;

            // Many-body repulsion (inverse-square, clamped).
            let f = rep / d2;
            if (f > maxF) f = maxF;
            n.ax += ux * f; n.ay += uy * f;
            m.ax -= ux * f; m.ay -= uy * f;

            // Collision: positional correction (stable at high density).
            const minD = n.r + m.r + pad;
            if (d < minD) {
              const push = (minD - d) * cStr * 0.5;
              if (n.fx == null) { n.x += ux * push; }
              if (n.fy == null) { n.y += uy * push; }
              if (m.fx == null) { m.x -= ux * push; }
              if (m.fy == null) { m.y -= uy * push; }
            }
          }
        }
      }
    }
  }

  // ---- integration: velocity Verlet -------------------------------------
  function integrate() {
    const dt = opt.dt;
    const dt2 = 0.5 * dt * dt;
    const vd = opt.velocityDecay;
    const vMax = opt.maxVelocity;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.fx != null) { n.x = n.fx; n.vx = 0; }
      if (n.fy != null) { n.y = n.fy; n.vy = 0; }
      if (n.fx != null && n.fy != null) { n.pax = 0; n.pay = 0; continue; }

      // v(t+dt) = v(t) + ((a_prev + a_now) / 2) * dt   (half-step average)
      n.vx = (n.vx + 0.5 * (n.pax + n.ax) * dt) * vd;
      n.vy = (n.vy + 0.5 * (n.pay + n.ay) * dt) * vd;
      const sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy);
      if (sp > vMax) { const s = vMax / sp; n.vx *= s; n.vy *= s; }

      // x(t+dt) = x(t) + v*dt + a*dt^2/2
      if (n.fx == null) n.x += n.vx * dt + n.ax * dt2;
      if (n.fy == null) n.y += n.vy * dt + n.ay * dt2;

      n.pax = n.ax; n.pay = n.ay;
      n.ax = 0; n.ay = 0;
    }
  }

  // ---- public api --------------------------------------------------------
  const sim = {
    /** Replace the graph. Node objects are adopted + mutated in place. */
    setGraph(nextNodes, nextEdges) {
      nodes = (nextNodes || []).slice();
      const total = nodes.length;
      const byId = new Map();
      for (let i = 0; i < nodes.length; i++) {
        prepNode(nodes[i], i, total);
        byId.set(nodes[i].id, nodes[i]);
      }
      links = [];
      for (const e of nextEdges || []) {
        const s = byId.get(e.a);
        const t = byId.get(e.b);
        if (!s || !t || s === t) continue; // silently drop unresolvable edges
        const w = Math.max(0.05, Math.min(Number(e.w) || 1, 4));
        links.push({ s, t, w, rest: opt.linkDistance / (0.6 + 0.4 * w) });
      }
      alpha = Math.max(alpha, 0.9);
      return sim;
    },

    /** Add nodes/edges without disturbing existing positions. */
    addGraph(extraNodes, extraEdges) {
      const startTotal = nodes.length + (extraNodes ? extraNodes.length : 0);
      const byId = new Map(nodes.map((n) => [n.id, n]));
      for (const n of extraNodes || []) {
        if (byId.has(n.id)) continue;
        prepNode(n, nodes.length, startTotal);
        nodes.push(n);
        byId.set(n.id, n);
      }
      for (const e of extraEdges || []) {
        const s = byId.get(e.a);
        const t = byId.get(e.b);
        if (!s || !t || s === t) continue;
        const w = Math.max(0.05, Math.min(Number(e.w) || 1, 4));
        links.push({ s, t, w, rest: opt.linkDistance / (0.6 + 0.4 * w) });
      }
      return sim;
    },

    /** Remove nodes by predicate; prunes their links too. */
    removeNodes(pred) {
      const dead = new Set(nodes.filter(pred));
      if (!dead.size) return sim;
      nodes = nodes.filter((n) => !dead.has(n));
      links = links.filter((l) => !dead.has(l.s) && !dead.has(l.t));
      return sim;
    },

    /** Advance the simulation n steps. Returns current alpha. */
    tick(steps = 1) {
      for (let s = 0; s < steps; s++) {
        if (alpha < opt.alphaMin && opt.alphaTarget < opt.alphaMin) break;
        const a = alpha;
        applyCentering(a);
        applySprings(a);
        applyRepulsionAndCollide(a);
        integrate();
        alpha += (opt.alphaTarget - alpha) * opt.alphaDecay;
      }
      return alpha;
    },

    /** Reheat (e.g. after adding nodes or dragging). */
    kick(a = 0.5) {
      alpha = Math.max(alpha, Math.min(a, 1));
      return sim;
    },

    /** Run to rest synchronously (bounded). For reduced-motion layouts. */
    settle(maxTicks = 500) {
      for (let i = 0; i < maxTicks; i++) {
        if (alpha < opt.alphaMin) break;
        sim.tick(1);
      }
      return sim;
    },

    /** Nearest node to world (x, y) within radius + node radius; optional filter(n). */
    find(x, y, radius = 16, filter) {
      let best = null;
      let bestD2 = Infinity;
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (filter && !filter(n)) continue;
        const dx = n.x - x, dy = n.y - y;
        const d2 = dx * dx + dy * dy;
        const reach = radius + n.r;
        if (d2 < reach * reach && d2 < bestD2) { best = n; bestD2 = d2; }
      }
      return best;
    },

    setCenter(x, y) { center.x = x; center.y = y; return sim; },

    get nodes() { return nodes; },
    get links() { return links; },
    get alpha() { return alpha; },
    set alpha(v) { alpha = Math.max(0, Math.min(Number(v) || 0, 1)); },
    get settled() { return alpha < opt.alphaMin && opt.alphaTarget < opt.alphaMin; },
    get options() { return opt; },
  };

  return sim;
}

export default createForceSim;
