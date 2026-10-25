/* ==================================================================
   Ambient constellation — the living ground of the page.
   ~110 decorative points colored by the source palette, slow drift,
   bloom via cached radial-gradient sprites, hairline edges between
   near neighbors, gentle pointer parallax by depth.
   prefers-reduced-motion -> a single static frame, no animation.
   Self-contained: no product modules, no external URLs.
   ================================================================== */
(function () {
  'use strict';

  /* Smooth scrolling only for in-page clicks (motion-permitting) — deep links
     land instantly, so arriving at /landing/#how never animates from the top. */
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var tgt = document.getElementById(a.getAttribute('href').slice(1));
    if (!tgt) return;
    e.preventDefault();
    var smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    tgt.scrollIntoView(smooth ? { behavior: 'smooth', block: 'start' } : { block: 'start' });
    if (history.pushState) history.pushState(null, '', a.getAttribute('href'));
  });

  var canvas = document.getElementById('sky');
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;

  var mqMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  var mqHover = window.matchMedia('(hover: hover)');

  var STAR = '#d7d2f0';
  var EDGE = '163,156,219';
  var SRC = ['#c9b6ff', '#7fe3d2', '#92b5f7', '#efc48e', '#e3a6cf', '#a3e8ae'];
  var N = 110;
  var LINK = 128;

  var nodes = [], W = 0, H = 0, DPR = 1;
  var px = 0, py = 0, tpx = 0, tpy = 0;
  var raf = 0, last = 0;
  var sprites = {};

  /* soft bloom sprite per color — drawn once, blitted every frame */
  function sprite(color) {
    if (sprites[color]) return sprites[color];
    var S = 64, c = document.createElement('canvas');
    c.width = S; c.height = S;
    var g = c.getContext('2d');
    var grad = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
    grad.addColorStop(0, color);
    grad.addColorStop(0.25, color);
    grad.addColorStop(1, 'rgba(7,5,18,0)');
    g.globalAlpha = 0.85;
    g.fillStyle = grad;
    g.fillRect(0, 0, S, S);
    sprites[color] = c;
    return c;
  }

  function seed() {
    nodes.length = 0;
    for (var i = 0; i < N; i++) {
      var z = 0.3 + Math.random() * 0.7;              /* depth 0.3 far .. 1 near */
      var bright = Math.random() < 0.42;               /* source-colored vs faint star */
      var a = Math.random() * Math.PI * 2;
      var speed = (2 + Math.random() * 2.8) * z;       /* px/s — slow drift */
      nodes.push({
        x: Math.random() * W,
        y: Math.random() * H,
        z: z,
        r: (bright ? 1.1 + Math.random() * 1.4 : 0.6 + Math.random() * 0.9) * z,
        vx: Math.cos(a) * speed,
        vy: Math.sin(a) * speed,
        c: bright ? SRC[(Math.random() * SRC.length) | 0] : STAR,
        bloom: bright && z > 0.62,
        ph: Math.random() * Math.PI * 2
      });
    }
  }

  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    var w = Math.max(canvas.clientWidth, 1);
    var h = Math.max(canvas.clientHeight, 1);
    if (nodes.length && W > 0 && H > 0 && (w !== W || h !== H)) {
      var sx = w / W, sy = h / H;
      for (var i = 0; i < nodes.length; i++) { nodes[i].x *= sx; nodes[i].y *= sy; }
    }
    W = w; H = h;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    if (!nodes.length) seed();
    if (mqMotion.matches) draw(0);
  }

  var BUCKETS = 6;
  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    var i, j, a, b, n;

    /* hairline edges between near neighbors — bucketed by alpha so the
       whole pass is BUCKETS stroke calls, not one per edge */
    var paths = [], k;
    for (k = 0; k < BUCKETS; k++) paths.push(new Path2D());
    for (i = 0; i < N; i++) {
      a = nodes[i];
      var ax = a.x + px * a.z, ay = a.y + py * a.z;
      for (j = i + 1; j < N; j++) {
        b = nodes[j];
        var max = LINK * Math.min(a.z, b.z);
        var bx = b.x + px * b.z, by = b.y + py * b.z;
        var dx = ax - bx, dy = ay - by;
        var d2 = dx * dx + dy * dy;
        if (d2 < max * max) {
          var f = 1 - Math.sqrt(d2) / max;           /* 0..1 proximity */
          k = Math.min((f * BUCKETS) | 0, BUCKETS - 1);
          paths[k].moveTo(ax, ay);
          paths[k].lineTo(bx, by);
        }
      }
    }
    ctx.lineWidth = 0.6;
    for (k = 0; k < BUCKETS; k++) {
      var al = 0.3 * ((k + 0.5) / BUCKETS);
      ctx.strokeStyle = 'rgba(' + EDGE + ',' + al.toFixed(3) + ')';
      ctx.stroke(paths[k]);
    }

    /* points, brightest with bloom */
    for (i = 0; i < N; i++) {
      n = nodes[i];
      var x = n.x + px * n.z, y = n.y + py * n.z;
      var tw = mqMotion.matches ? 0.9 : 0.68 + 0.32 * Math.sin(t / 1000 * 0.7 + n.ph);
      if (n.bloom) {
        var R = n.r * 9;
        ctx.globalAlpha = 0.34 * tw;
        ctx.drawImage(sprite(n.c), x - R, y - R, R * 2, R * 2);
      }
      ctx.globalAlpha = tw * (0.45 + n.z * 0.55);
      ctx.fillStyle = n.c;
      ctx.beginPath();
      ctx.arc(x, y, n.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  var lastDraw = -1e9;
  function tick(t) {
    raf = requestAnimationFrame(tick);
    var dt = Math.min((t - last) / 1000, 0.05);
    last = t;
    px += (tpx - px) * Math.min(dt * 2.5, 1);
    py += (tpy - py) * Math.min(dt * 2.5, 1);
    var m = 26;
    for (var i = 0; i < N; i++) {
      var n = nodes[i];
      n.x += n.vx * dt;
      n.y += n.vy * dt;
      if (n.x < -m) n.x = W + m; else if (n.x > W + m) n.x = -m;
      if (n.y < -m) n.y = H + m; else if (n.y > H + m) n.y = -m;
    }
    /* the drift is slow — ~30fps is indistinguishable and halves the work */
    if (t - lastDraw >= 30) { lastDraw = t; draw(t); }
  }

  function start() {
    cancelAnimationFrame(raf);
    if (mqMotion.matches) { px = py = 0; draw(0); return; }
    last = performance.now();
    raf = requestAnimationFrame(tick);
  }

  if (mqHover.matches) {
    window.addEventListener('pointermove', function (e) {
      if (mqMotion.matches) return;
      tpx = (e.clientX / Math.max(W, 1) - 0.5) * 24;
      tpy = (e.clientY / Math.max(H, 1) - 0.5) * 16;
    }, { passive: true });
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) cancelAnimationFrame(raf);
    else start();
  });

  var onMotion = function () { start(); };
  if (mqMotion.addEventListener) mqMotion.addEventListener('change', onMotion);
  else if (mqMotion.addListener) mqMotion.addListener(onMotion);
  window.addEventListener('resize', resize);

  resize();
  start();
})();
