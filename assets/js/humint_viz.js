/* ============================================================
   CyberPent HUMINT — Intelligence Visualization Engine
   Knowledge Graph + Timeline + Confidence Rendering
   ============================================================ */

(function () {
  'use strict';

  // Knowledge Graph Force-Directed Renderer
  window.CyberPentGraph = function (canvasId, data) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var nodes = data.nodes || [];
    var edges = data.edges || [];
    var width, height, dpr;
    var simulation = [];
    var hovered = null;
    var selected = null;
    var offset = { x: 0, y: 0 };
    var scale = 1;
    var dragging = null;
    var dragStart = null;

    var COLORS = {
      Person: '#00e5ff',
      Organization: '#a855f7',
      Alias: '#ff2d78',
      Account: '#00ff88',
      Domain: '#ffb800',
      IP: '#ff6b35',
      Infrastructure: '#64748b',
      ThreatActor: '#ff3333',
      Campaign: '#e040fb',
      Wallet: '#00e5ff',
      default: '#64748b'
    };

    function init() {
      resize();
      window.addEventListener('resize', resize);
      canvas.addEventListener('mousemove', onMouseMove);
      canvas.addEventListener('mousedown', onMouseDown);
      canvas.addEventListener('mouseup', onMouseUp);
      canvas.addEventListener('wheel', onWheel, { passive: false });

      nodes.forEach(function (n, i) {
        n.x = width / 2 + (Math.random() - 0.5) * 300;
        n.y = height / 2 + (Math.random() - 0.5) * 300;
        n.vx = 0;
        n.vy = 0;
        n.radius = n.size || 8;
        n.color = COLORS[n.type] || COLORS.default;
      });

      simulate();
      render();
    }

    function resize() {
      dpr = window.devicePixelRatio || 1;
      var rect = canvas.parentElement.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      canvas.style.width = width + 'px';
      canvas.style.height = height + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function simulate() {
      var iterations = 300;
      for (var i = 0; i < iterations; i++) {
        applyForces();
      }
    }

    function applyForces() {
      var k = 0.01;
      var repulsion = 5000;
      var damping = 0.9;

      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var dx = nodes[j].x - nodes[i].x;
          var dy = nodes[j].y - nodes[i].y;
          var dist = Math.sqrt(dx * dx + dy * dy) || 1;
          var force = repulsion / (dist * dist);
          var fx = (dx / dist) * force;
          var fy = (dy / dist) * force;
          nodes[i].vx -= fx;
          nodes[i].vy -= fy;
          nodes[j].vx += fx;
          nodes[j].vy += fy;
        }
      }

      edges.forEach(function (e) {
        var src = nodes.find(function (n) { return n.id === e.source; });
        var tgt = nodes.find(function (n) { return n.id === e.target; });
        if (!src || !tgt) return;
        var dx = tgt.x - src.x;
        var dy = tgt.y - src.y;
        var dist = Math.sqrt(dx * dx + dy * dy) || 1;
        var force = (dist - 100) * k;
        var fx = (dx / dist) * force;
        var fy = (dy / dist) * force;
        src.vx += fx;
        src.vy += fy;
        tgt.vx -= fx;
        tgt.vy -= fy;
      });

      nodes.forEach(function (n) {
        if (n === dragging) return;
        n.vx *= damping;
        n.vy *= damping;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(n.radius, Math.min(width - n.radius, n.x));
        n.y = Math.max(n.radius, Math.min(height - n.radius, n.y));
      });
    }

    function render() {
      ctx.clearRect(0, 0, width, height);
      ctx.save();
      ctx.translate(offset.x, offset.y);
      ctx.scale(scale, scale);

      edges.forEach(function (e) {
        var src = nodes.find(function (n) { return n.id === e.source; });
        var tgt = nodes.find(function (n) { return n.id === e.target; });
        if (!src || !tgt) return;
        ctx.beginPath();
        ctx.moveTo(src.x, src.y);
        ctx.lineTo(tgt.x, tgt.y);
        ctx.strokeStyle = (e === selected) ? '#00e5ff' : 'rgba(100,116,139,0.3)';
        ctx.lineWidth = (e === selected) ? 2 : 1;
        ctx.stroke();

        var mx = (src.x + tgt.x) / 2;
        var my = (src.y + tgt.y) / 2;
        ctx.fillStyle = 'rgba(100,116,139,0.5)';
        ctx.font = '9px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(e.type || '', mx, my - 4);
      });

      nodes.forEach(function (n) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
        ctx.fillStyle = (n === hovered || n === selected) ? n.color : n.color + '99';
        ctx.fill();
        ctx.strokeStyle = n.color;
        ctx.lineWidth = (n === hovered || n === selected) ? 2 : 1;
        ctx.stroke();

        ctx.fillStyle = '#e2e8f0';
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText(n.label || n.id, n.x, n.y + n.radius + 14);
      });

      ctx.restore();
      requestAnimationFrame(render);
    }

    function onMouseMove(e) {
      var rect = canvas.getBoundingClientRect();
      var mx = (e.clientX - rect.left - offset.x) / scale;
      var my = (e.clientY - rect.top - offset.y) / scale;

      if (dragging) {
        dragging.x = mx;
        dragging.y = my;
        return;
      }

      hovered = null;
      for (var i = nodes.length - 1; i >= 0; i--) {
        var dx = mx - nodes[i].x;
        var dy = my - nodes[i].y;
        if (Math.sqrt(dx * dx + dy * dy) < nodes[i].radius + 4) {
          hovered = nodes[i];
          canvas.style.cursor = 'pointer';
          break;
        }
      }
      if (!hovered) canvas.style.cursor = 'default';
    }

    function onMouseDown(e) {
      if (hovered) {
        dragging = hovered;
        dragStart = { x: e.clientX, y: e.clientY };
      }
    }

    function onMouseUp() {
      dragging = null;
    }

    function onWheel(e) {
      e.preventDefault();
      var delta = e.deltaY > 0 ? 0.9 : 1.1;
      scale = Math.max(0.3, Math.min(3, scale * delta));
    }

    init();
    return { nodes: nodes, edges: edges };
  };

  // Timeline Renderer
  window.CyberPentTimeline = function (containerId, events) {
    var container = document.getElementById(containerId);
    if (!container) return;

    events.sort(function (a, b) { return new Date(a.date) - new Date(b.date); });

    var html = '<div class="timeline-line"></div>';
    events.forEach(function (ev, i) {
      var side = i % 2 === 0 ? 'odd' : 'even';
      html += '<div class="timeline-event ' + side + '">' +
        '<div class="timeline-date">' + ev.date + '</div>' +
        '<div class="timeline-title">' + ev.title + '</div>' +
        '<div class="timeline-detail">' + (ev.detail || '') + '</div>' +
        '</div>';
    });
    container.innerHTML = html;
  };

  // Confidence Dimension Renderer
  window.CyberPentConfidence = function (containerId, dims) {
    var container = document.getElementById(containerId);
    if (!container) return;

    var colors = {
      source_reliability: '#00e5ff',
      evidence_quality: '#00ff88',
      independent_corroboration: '#a855f7',
      temporal_consistency: '#ffb800',
      relationship_strength: '#ff2d78',
      contradictory_evidence_penalty: '#ff3333',
      duplicate_evidence_penalty: '#ff6b35'
    };

    var labels = {
      source_reliability: 'Source Reliability',
      evidence_quality: 'Evidence Quality',
      independent_corroboration: 'Corroboration',
      temporal_consistency: 'Temporal',
      relationship_strength: 'Relationship',
      contradictory_evidence_penalty: 'Contradictions',
      duplicate_evidence_penalty: 'Duplicates'
    };

    var html = '<div class="conf-dimensions">';
    Object.keys(dims).forEach(function (key) {
      var val = Math.round(dims[key] * 100);
      html += '<div class="conf-dim">' +
        '<span class="dim-label">' + (labels[key] || key) + '</span>' +
        '<div class="dim-bar"><div class="dim-fill" style="width:' + val + '%;background:' + (colors[key] || '#64748b') + '"></div></div>' +
        '<span class="conf-value">' + val + '%</span>' +
        '</div>';
    });
    html += '</div>';
    container.innerHTML = html;
  };
})();
