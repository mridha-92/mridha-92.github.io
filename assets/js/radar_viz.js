/* =========================================================================
   radar_viz.js - header threat-intake visualization
   - Canvas particle network: glowing SOURCE nodes (CISA / CERT / NVD /
     MEDIA / OSINT / VENDOR) feeding pulses of light into a central
     INGEST core, over a rotating sweep + range rings.
   - Severity-aware: reads the live feed badges (#radar-feed .sev-*) and
     tints pulses, sweep speed and ambient glow from LOW -> CRITICAL.
   - Zero dependencies, additive only; auto-disables animation for
     prefers-reduced-motion users (renders a single static frame).
   - Public hook: RadarViz.setThreatLevel('low'|'medium'|'high'|'critical'|'auto')
   ========================================================================= */
(function () {
  'use strict';

  var canvas = document.getElementById('radar-viz-canvas');
  if (!canvas || canvas.__radarViz) { return; }
  canvas.__radarViz = true;

  var ctx = canvas.getContext('2d');
  if (!ctx) { return; }

  var REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- palette (mirrors radar.css custom properties) ------------------- */
  var SEV_COLORS = {
    low:      [0, 255, 156],   // --accent
    medium:   [255, 209, 102], // --yellow
    high:     [255, 159, 69],  // --orange
    critical: [255, 77, 106]   // --red
  };
  var CYAN = [34, 211, 238];

  /* ---- sources orbiting the ingest core -------------------------------- */
  var SOURCES = [
    { label: 'CISA',   angle: -90, rate: 0.55 },
    { label: 'CERT',   angle: -30, rate: 0.42 },
    { label: 'NVD',    angle:  30, rate: 0.50 },
    { label: 'MEDIA',  angle:  90, rate: 0.62 },
    { label: 'OSINT',  angle: 150, rate: 0.38 },
    { label: 'VENDOR', angle: 210, rate: 0.33 }
  ];

  /* ---- state ------------------------------------------------------------ */
  var W = 0, H = 0, DPR = 1;
  var CX = 0, CY = 0, R = 0;          // core centre + network radius
  var nodes = [];                     // resolved source node positions
  var pulses = [];                    // packets in flight
  var dust  = [];                     // ambient drifting particles
  var ripples = [];                   // critical alert rings
  var sweep = -Math.PI / 2;           // sweep head angle
  var coreFlash = 0;                  // 0..1 brightness kick on packet arrival
  var lastRipple = 0;

  /* severity profile read from the live feed ----------------------------- */
  var sevCounts = { low: 0, medium: 0, high: 0, critical: 0 };
  var overrideSev = null;             // set via RadarViz.setThreatLevel()
  var T = 0.15;                       // global threat index 0..1

  function computeThreatIndex() {
    if (overrideSev) {
      return { low: 0, medium: 0, high: 0, critical: 0 }[overrideSev] !== undefined
        ? ({ low: 0.05, medium: 0.35, high: 0.65, critical: 1 })[overrideSev]
        : 0.15;
    }
    var weight = { low: 0, medium: 0.25, high: 0.6, critical: 1 };
    var sum = 0, total = 0, k;
    for (k in sevCounts) {
      sum += (weight[k] || 0) * sevCounts[k];
      total += sevCounts[k];
    }
    return total ? Math.min(1, sum / total) : 0.15;
  }

  function sampleSeverity() {
    var total = 0, k;
    for (k in sevCounts) { total += sevCounts[k]; }
    if (!total) {
      // no live data yet: mostly low with a hint of variety
      var r = Math.random();
      return r < 0.78 ? 'low' : r < 0.93 ? 'medium' : 'high';
    }
    var pick = Math.random() * total;
    for (k in sevCounts) {
      pick -= sevCounts[k];
      if (pick < 0) { return k; }
    }
    return 'low';
  }

  function readFeedSeverity() {
    var badges = document.querySelectorAll('#radar-feed .badge[class*="sev-"]');
    for (var i = 0; i < badges.length; i++) {
      var m = /sev-(low|medium|high|critical)/.exec(badges[i].className);
      if (m && m[1] in sevCounts) { sevCounts[m[1]]++; }
    }
    T = computeThreatIndex();
  }

  /* ---- geometry ---------------------------------------------------------- */
  function layout() {
    var rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) { return false; }
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = rect.width; H = rect.height;
    canvas.width  = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

    // keep the title side clean on wide screens: bias centre rightwards
    CX = W < 720 ? W * 0.5 : W * 0.66;
    CY = H * 0.52;
    R  = Math.max(70, Math.min(W * 0.24, H * 0.46));

    nodes = SOURCES.map(function (s, i) {
      var a = (s.angle * Math.PI) / 180;
      var rr = R * (0.92 + 0.08 * Math.sin(i * 2.7));   // slight organic variance
      return {
        src: s,
        x: CX + Math.cos(a) * rr,
        y: CY + Math.sin(a) * rr * 0.86,                // ellipse for depth
        phase: Math.random() * Math.PI * 2,
        spawnAcc: 0
      };
    });

    if (!dust.length) {
      for (var i = 0; i < 36; i++) {
        dust.push({
          a: Math.random() * Math.PI * 2,
          r: R * (0.25 + Math.random() * 1.05),
          v: 0.006 + Math.random() * 0.02,
          s: 0.6 + Math.random() * 1.4,
          o: 0.05 + Math.random() * 0.16
        });
      }
    }
    return true;
  }

  /* ---- drawing helpers ---------------------------------------------------- */
  function rgba(c, a) { return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')'; }

  // quadratic bezier source -> core (same control point used for pulse path)
  function edgeCtrl(n) {
    var mx = (n.x + CX) / 2, my = (n.y + CY) / 2;
    var dx = CX - n.x, dy = CY - n.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    var bend = R * 0.16;
    return { x: mx - (dy / d) * bend, y: my + (dx / d) * bend };
  }
  function qPoint(p0, pc, p1, t) {
    var u = 1 - t;
    return {
      x: u * u * p0.x + 2 * u * t * pc.x + t * t * p1.x,
      y: u * u * p0.y + 2 * u * t * pc.y + t * t * p1.y
    };
  }

  function drawStatic(now) {
    ctx.clearRect(0, 0, W, H);
    drawAmbientGlow();
    drawRings();
    drawSweep(now);
    drawDust();
    nodes.forEach(function (n) { drawEdge(n); });
    drawCore();
    nodes.forEach(function (n) { drawNode(n, now); });
    // a few frozen packets so reduced-motion still shows the concept
    nodes.forEach(function (n, i) {
      var pc = edgeCtrl(n), p = qPoint({ x: n.x, y: n.y }, pc, { x: CX, y: CY }, 0.35 + 0.12 * i);
      drawPacket(p, SEV_COLORS[i % 3 === 0 ? 'low' : i % 3 === 1 ? 'medium' : 'high'], 0.9);
    });
  }

  function drawAmbientGlow() {
    // ambient hue drifts green -> amber -> red as the threat index climbs
    var g = SEV_COLORS.low, mid = SEV_COLORS.medium, hot = SEV_COLORS.critical;
    var c = T < 0.5
      ? mix(g, mid, T * 2)
      : mix(mid, hot, (T - 0.5) * 2);
    var grad = ctx.createRadialGradient(CX, CY, 0, CX, CY, R * 1.7);
    grad.addColorStop(0, rgba(c, 0.10 + 0.10 * T));
    grad.addColorStop(0.55, rgba(c, 0.04));
    grad.addColorStop(1, rgba(c, 0));
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, H);
  }

  function mix(a, b, t) {
    return [
      Math.round(a[0] + (b[0] - a[0]) * t),
      Math.round(a[1] + (b[1] - a[1]) * t),
      Math.round(a[2] + (b[2] - a[2]) * t)
    ];
  }

  function drawRings() {
    var accent = SEV_COLORS.low;
    ctx.save();
    ctx.translate(CX, CY);

    for (var i = 1; i <= 3; i++) {
      ctx.beginPath();
      ctx.arc(0, 0, (R * i) / 3, 0, Math.PI * 2);
      ctx.strokeStyle = rgba(accent, 0.12 - i * 0.028);
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // outer dashed ring + tick marks every 15 degrees
    ctx.setLineDash([2, 7]);
    ctx.beginPath();
    ctx.arc(0, 0, R, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(accent, 0.16);
    ctx.stroke();
    ctx.setLineDash([]);

    var ticks = 24, warn = T > 0.55;
    ctx.strokeStyle = rgba(warn ? SEV_COLORS.high : accent, 0.22);
    for (i = 0; i < ticks; i++) {
      var a = (i / ticks) * Math.PI * 2;
      var long = i % 6 === 0;
      var r1 = R + 3, r2 = R + (long ? 11 : 6);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
      ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
      ctx.lineWidth = long ? 1.4 : 1;
      ctx.stroke();
    }

    // cross-hairs
    ctx.strokeStyle = rgba(accent, 0.07);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(-R, 0); ctx.lineTo(R, 0); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, -R); ctx.lineTo(0, R); ctx.stroke();
    ctx.restore();
  }

  function drawSweep(now) {
    var speed = 0.45 + T * 0.75;                    // rad/s, faster under threat
    var head = sweep;
    ctx.save();
    ctx.translate(CX, CY);

    if (ctx.createConicGradient) {
      try {
        var grad = ctx.createConicGradient(head, 0, 0);
        grad.addColorStop(0,    rgba(SEV_COLORS.low, 0.16 + 0.06 * T));
        grad.addColorStop(0.12, rgba(SEV_COLORS.low, 0.03));
        grad.addColorStop(0.25, rgba(SEV_COLORS.low, 0));
        grad.addColorStop(1,    rgba(SEV_COLORS.low, 0));
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.arc(0, 0, R, head - Math.PI * 0.5, head);
        ctx.closePath();
        ctx.fill();
      } catch (e) { /* older engines: fall through to line only */ }
    }

    // bright leading edge
    var hx = Math.cos(head), hy = Math.sin(head);
    var lg = ctx.createLinearGradient(hx * R * 0.2, hy * R * 0.2, hx * R, hy * R);
    lg.addColorStop(0, rgba(SEV_COLORS.low, 0));
    lg.addColorStop(1, rgba(SEV_COLORS.low, 0.5 + 0.2 * T));
    ctx.strokeStyle = lg;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(hx * R * 0.18, hy * R * 0.18);
    ctx.lineTo(hx * R, hy * R);
    ctx.stroke();

    // faint trailing fan
    for (var i = 1; i <= 5; i++) {
      var ta = head - i * 0.09;
      ctx.strokeStyle = rgba(SEV_COLORS.low, 0.14 - i * 0.026);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(Math.cos(ta) * R, Math.sin(ta) * R);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawDust() {
    ctx.save();
    for (var i = 0; i < dust.length; i++) {
      var p = dust[i];
      var x = CX + Math.cos(p.a) * p.r;
      var y = CY + Math.sin(p.a) * p.r * 0.86;
      ctx.fillStyle = rgba(CYAN, p.o);
      ctx.fillRect(x, y, p.s, p.s);
    }
    ctx.restore();
  }

  function drawEdge(n) {
    var pc = edgeCtrl(n);
    ctx.beginPath();
    ctx.moveTo(n.x, n.y);
    ctx.quadraticCurveTo(pc.x, pc.y, CX, CY);
    ctx.strokeStyle = rgba(SEV_COLORS.low, 0.10);
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function drawNode(n, now) {
    var blink = (Math.sin(now * 0.0024 + n.phase) + 1) / 2;   // 0..1
    var accent = SEV_COLORS.low;

    ctx.save();
    // halo
    ctx.shadowColor = rgba(accent, 0.85);
    ctx.shadowBlur = 10 + blink * 8;
    ctx.fillStyle = rgba(accent, 0.9);
    ctx.beginPath();
    ctx.arc(n.x, n.y, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // expanding ping ring
    var pingT = ((now * 0.00035 + n.phase * 0.16) % 1);
    ctx.beginPath();
    ctx.arc(n.x, n.y, 4 + pingT * 14, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(accent, 0.28 * (1 - pingT));
    ctx.lineWidth = 1;
    ctx.stroke();

    // label + status tick
    ctx.font = '600 10px ui-monospace, Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.fillStyle = rgba(accent, 0.62);
    ctx.fillText(n.src.label, n.x, n.y - 13);

    ctx.fillStyle = rgba(blink > 0.5 ? accent : SEV_COLORS.medium, 0.85);
    ctx.fillRect(n.x - 10, n.y + 6, 20, 1);
    ctx.restore();
  }

  function drawPacket(p, color, alpha) {
    ctx.save();
    ctx.shadowColor = rgba(color, 0.95);
    ctx.shadowBlur = 12;
    ctx.fillStyle = rgba(color, alpha);
    ctx.beginPath();
    ctx.arc(p.x, p.y, 2.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawCore(now) {
    var flash = coreFlash;
    var hot = T > 0.75 ? mix(SEV_COLORS.low, SEV_COLORS.critical, (T - 0.75) / 0.25)
                       : SEV_COLORS.low;

    var breathe = 1 + 0.06 * Math.sin(now * 0.0016) + flash * 0.35;

    ctx.save();
    ctx.translate(CX, CY);

    // layered glow
    var g1 = ctx.createRadialGradient(0, 0, 0, 0, 0, 26 * breathe);
    g1.addColorStop(0, rgba([220, 255, 240], 0.9));
    g1.addColorStop(0.25, rgba(hot, 0.65 + flash * 0.3));
    g1.addColorStop(1, rgba(hot, 0));
    ctx.fillStyle = g1;
    ctx.beginPath();
    ctx.arc(0, 0, 26 * breathe, 0, Math.PI * 2);
    ctx.fill();

    ctx.shadowColor = rgba(hot, 0.9);
    ctx.shadowBlur = 18 + flash * 22;
    ctx.fillStyle = '#eafff5';
    ctx.beginPath();
    ctx.arc(0, 0, 4.4 * breathe, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // rotating retainer bracket
    ctx.rotate(now * 0.0004);
    ctx.strokeStyle = rgba(hot, 0.5);
    ctx.lineWidth = 1.4;
    for (var i = 0; i < 3; i++) {
      ctx.rotate((Math.PI * 2) / 3);
      ctx.beginPath();
      ctx.arc(0, 0, 11, -0.5, 0.5);
      ctx.stroke();
    }
    ctx.restore();

    // label
    ctx.save();
    ctx.font = '700 10px ui-monospace, Consolas, monospace';
    ctx.textAlign = 'center';
    ctx.fillStyle = rgba(hot, 0.75);
    ctx.fillText('INGEST-CORE', CX, CY + R * 0.34);
    ctx.restore();
  }

  function drawPulses(dt, now) {
    // spawn per-source, rate modulated by activity wave + threat index
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      var activity = 0.5 + 0.5 * Math.sin(now * 0.00045 + n.phase * 2);
      n.spawnAcc += n.src.rate * (0.5 + activity) * (0.6 + T * 1.1) * dt;
      while (n.spawnAcc >= 1) {
        n.spawnAcc -= 1;
        pulses.push({
          n: n,
          t: 0,
          speed: 0.32 + Math.random() * 0.2,
          sev: sampleSeverity(),
          jitter: (Math.random() - 0.5) * 0.06
        });
      }
    }

    ctx.save();
    for (i = pulses.length - 1; i >= 0; i--) {
      var pl = pulses[i];
      pl.t += pl.speed * dt;
      if (pl.t >= 1) {
        coreFlash = Math.min(1, coreFlash + (pl.sev === 'critical' ? 0.55 : 0.18));
        pulses.splice(i, 1);
        continue;
      }
      var pc = edgeCtrl(pl.n);
      var tt = Math.min(0.999, pl.t + pl.jitter);
      // trail ghosts
      for (var g = 3; g >= 1; g--) {
        var gt = tt - g * 0.028;
        if (gt <= 0) { continue; }
        var gp = qPoint({ x: pl.n.x, y: pl.n.y }, pc, { x: CX, y: CY }, gt);
        drawPacket(gp, SEV_COLORS[pl.sev], 0.16 * (4 - g));
      }
      var hp = qPoint({ x: pl.n.x, y: pl.n.y }, pc, { x: CX, y: CY }, tt);
      var boost = pl.sev === 'critical' ? 1 : pl.sev === 'high' ? 0.9 : 0.75;
      drawPacket(hp, SEV_COLORS[pl.sev], boost);
    }
    ctx.restore();
  }

  function drawRipples(now) {
    if (sevCounts.critical > 0 || T > 0.85) {
      if (now - lastRipple > 3600) {
        lastRipple = now;
        ripples.push({ born: now });
      }
    }
    ctx.save();
    for (var i = ripples.length - 1; i >= 0; i--) {
      var age = (now - ripples[i].born) / 2600;
      if (age >= 1) { ripples.splice(i, 1); continue; }
      ctx.beginPath();
      ctx.arc(CX, CY, R * 0.15 + age * R * 1.15, 0, Math.PI * 2);
      ctx.strokeStyle = rgba(SEV_COLORS.critical, 0.30 * (1 - age));
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.restore();
  }

  /* ---- main loop ----------------------------------------------------------- */
  var lastTs = 0;
  var onScreen = true;

  function frame(ts) {
    var dt = Math.min(0.05, (ts - lastTs) / 1000) || 0.016;
    lastTs = ts;

    if (onScreen && !document.hidden) {
      sweep += (0.45 + T * 0.75) * dt;
      coreFlash *= Math.pow(0.02, dt);              // exponential decay
      for (var i = 0; i < dust.length; i++) {
        dust[i].r -= dust[i].v * R * dt * 60 * 0.05;
        if (dust[i].r < R * 0.12) { dust[i].r = R * (0.95 + Math.random() * 0.2); }
      }
      T += (computeThreatIndex() - T) * Math.min(1, dt * 0.8);   // smooth transitions

      ctx.clearRect(0, 0, W, H);
      drawAmbientGlow();
      drawRings();
      drawSweep(ts);
      drawDust();
      nodes.forEach(function (n) { drawEdge(n); });
      drawPulses(dt, ts);
      drawRipples(ts);
      drawCore(ts);
      nodes.forEach(function (n) { drawNode(n, ts); });
    }
    requestAnimationFrame(frame);
  }

  /* ---- public micro-API ------------------------------------------------------ */
  window.RadarViz = {
    setThreatLevel: function (level) {
      overrideSev = level === 'auto' ? null : level;
    },
    refresh: function () {
      readFeedSeverity();
    }
  };

  /* ---- boot -------------------------------------------------------------------- */
  function start() {
    if (!layout()) { return; }
    readFeedSeverity();

    if (REDUCED) {
      drawStatic(performance.now());
      return;
    }

    if ('ResizeObserver' in window) {
      new ResizeObserver(function () { layout(); }).observe(canvas.parentElement || canvas);
    } else {
      window.addEventListener('resize', layout);
    }

    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (entries) {
        onScreen = entries[0].isIntersecting;
      }).observe(canvas);
    }

    requestAnimationFrame(function (ts) { lastTs = ts; frame(ts); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
