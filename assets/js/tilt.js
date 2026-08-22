/* =========================================================================
   tilt.js - mouse-tracking 3D tilt engine
   - Applies to every element carrying a [data-tilt] attribute
   - data-tilt="<max-degrees>" controls intensity (default 6)
   - rAF + lerp smoothing for natural motion, auto-sleeps when idle
   - Auto-disables: touch devices and prefers-reduced-motion users
   ========================================================================= */
(function () {
  'use strict';

  var hoverCapable = window.matchMedia('(hover: hover) and (pointer: fine)');
  var motionOK = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!hoverCapable.matches || !motionOK) { return; }

  var EASE = 0.08;          // lerp factor per frame (lower = heavier feel)
  var SETTLE_EPS = 0.02;    // degrees; below this we snap and stop looping

  var items = [];
  var running = false;

  function createItem(el) {
    if (el.__tilt) { return; }
    var max = parseFloat(el.getAttribute('data-tilt'));
    if (!max || max <= 0) { max = 6; }

    var st = {
      el: el,
      max: max,
      tx: 0, ty: 0, ts: 1,   // target rotX / rotY / scale
      cx: 0, cy: 0, cs: 1,   // current values
      hovered: false
    };
    el.__tilt = st;
    items.push(st);

    el.addEventListener('mouseenter', function () {
      st.hovered = true;
      el.classList.add('is-tilting');
      startLoop();
    });

    el.addEventListener('mousemove', function (e) {
      var rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) { return; }
      // Normalised cursor offset from element centre (-0.5 .. 0.5)
      var px = (e.clientX - rect.left) / rect.width - 0.5;
      var py = (e.clientY - rect.top) / rect.height - 0.5;
      st.ty = px * 2 * st.max;    // rotateY tracks horizontal position
      st.tx = py * -2 * st.max;   // rotateX inverts vertical for natural tilt
      st.ts = 1 + Math.min(0.02, st.max * 0.0028); // gentle lift, capped
    });

    el.addEventListener('mouseleave', function () {
      st.hovered = false;
      st.tx = 0; st.ty = 0; st.ts = 1;
      el.classList.remove('is-tilting');
      startLoop(); // ease back to rest
    });
  }

  function tick() {
    var alive = false;
    for (var i = 0; i < items.length; i++) {
      var s = items[i];

      s.cx += (s.tx - s.cx) * EASE;
      s.cy += (s.ty - s.cy) * EASE;
      s.cs += (s.ts - s.cs) * EASE;

      var settled =
        Math.abs(s.tx - s.cx) < SETTLE_EPS &&
        Math.abs(s.ty - s.cy) < SETTLE_EPS &&
        Math.abs(s.ts - s.cs) < 0.0004;

      if (settled && !s.hovered) {
        // Snap to rest exactly once, then leave the element alone.
        s.cx = 0; s.cy = 0; s.cs = 1;
        s.el.style.transform = '';
      } else {
        alive = true;
        s.el.style.transform =
          'perspective(1100px)' +
          ' rotateX(' + s.cx.toFixed(3) + 'deg)' +
          ' rotateY(' + s.cy.toFixed(3) + 'deg)' +
          ' scale(' + s.cs.toFixed(4) + ')';
      }
    }
    if (alive) {
      requestAnimationFrame(tick);
    } else {
      running = false;
    }
  }

  function startLoop() {
    if (!running) {
      running = true;
      requestAnimationFrame(tick);
    }
  }

  function init() {
    var nodes = document.querySelectorAll('[data-tilt]');
    for (var i = 0; i < nodes.length; i++) { createItem(nodes[i]); }
    startLoop();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
