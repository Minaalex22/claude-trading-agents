// Command center: the whole system on one screen.
//
// - A 3D scene of every agent (one glowing dot each, coloured by its latest vote), the three managers above
//   their departments, and the pipeline (market feed -> departments -> desk -> risk gate -> Jev -> broker)
//   with data streaming along it all the time and a bright burst for every order.
// - Key numbers, risk gauges (daily loss, drawdown, exposure against their stops), the real-money approval
//   desk, TradingView price and P&L charts, the news the reading room scored, managers, the ring heatmap and
//   a stream of orders and checks.
// DOM is built with textContent / createElement only (no event data is ever parsed as HTML), so the strict
// Content-Security-Policy holds. Libraries are vendored: three.js r128 (MIT), lightweight-charts (Apache-2.0).
(function () {
'use strict';
const $ = (id) => document.getElementById(id);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
function api(url) { return fetch(url, { credentials: 'same-origin' }).then((r) => { if (r.status === 401) { location.href = '/login'; throw new Error('signed out'); } return r.json(); }); }
const money = (v) => (v < 0 ? '−$' : '$') + Math.abs(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
const signed = (v) => (v >= 0 ? '+' : '−') + '$' + Math.abs(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
const pct = (v, d = 1) => (v === null || v === undefined ? '–' : (100 * v).toFixed(d) + '%');

// ---- animated numbers -------------------------------------------------------------------------------
const shown = {};
function tween(id, value, fmt, flash) {
  const node = $(id); const from = shown[id] ?? value; shown[id] = value;
  if (from !== value && !reduced) { node.classList.remove('glitch'); void node.offsetWidth; node.classList.add('glitch'); }
  if (flash && from !== value) { const k = node.closest('.kpi'); if (k) { k.classList.remove('flash'); void k.offsetWidth; k.classList.add('flash'); } }
  if (reduced || from === value) { node.textContent = fmt(value); return; }
  const t0 = performance.now();
  (function step(now) { const f = Math.min(1, (now - t0) / 700), e = 1 - Math.pow(1 - f, 3);
    node.textContent = fmt(from + (value - from) * e); if (f < 1) requestAnimationFrame(step); })(t0);
}

// ---- 3D -----------------------------------------------------------------------------------------------
const DEPTS = { trend: { pos: [-38, 0, -30], name: 'Trend' }, reversion: { pos: [38, 0, -30], name: 'Reversion' },
  research: { pos: [-38, 0, 30], name: 'Research' }, risk: { pos: [38, 0, 30], name: 'Risk' } };
const MANAGERS = { Atlas: { pos: [0, 34, -30], depts: ['trend', 'reversion'] }, Sage: { pos: [-38, 34, 30], depts: ['research'] },
  Sentinel: { pos: [38, 34, 30], depts: ['risk'] } };
const STAGES = { feed: [-100, 0, 0, 'Market feed'], desk: [82, 0, 0, 'Desk'], riskgate: [112, 0, 0, 'Risk gate'],
  jev: [142, 0, 0, 'Jev gate'], broker: [172, 0, 0, 'Broker'] };
const host = $('scene');
let THREE_OK = !!window.THREE, scene, camera, renderer, glow, clusters = {}, labels = [], streams = [], bursts = [], stageMesh = {};
let yaw = 0.55, pitch = 0.38, dist = 190, auto = !reduced, lastInput = 0, orderHeat = 0;
const target = THREE_OK ? new THREE.Vector3(40, 2, 0) : null;

function glowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, 'rgba(255,255,255,1)'); grd.addColorStop(0.25, 'rgba(255,255,255,.75)'); grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
}
function voteColor(v) {
  // Hologram tints: cyan-white light for a buy lean, hot pink for a sell lean, dim teal while warming up.
  if (v === null || v === undefined) return [0.12, 0.32, 0.38];
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? [0.25 + 0.3 * a, 0.75 + 0.25 * a, 0.85 + 0.15 * a] : [0.7 + 0.3 * a, 0.2, 0.45 + 0.1 * a];
}
function label(text, sub, pos) {
  const n = el('div', 'label3d'); const b = el('b', '', text); const s = el('div', 'mut', sub || '');
  n.append(b, s); host.appendChild(n); const l = { n, sub: s, pos: new THREE.Vector3(...pos) }; labels.push(l); return l;
}
function sphere(pos, color, r) {
  const m = new THREE.Mesh(new THREE.SphereGeometry(r, 24, 16), new THREE.MeshBasicMaterial({ color }));
  m.position.set(...pos); scene.add(m);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color, transparent: true, opacity: 0.55,
    blending: THREE.AdditiveBlending, depthWrite: false }));
  halo.scale.setScalar(r * 7); m.add(halo); return m;
}
function curve(a, b, lift) {
  const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b);
  const mid = A.clone().lerp(B, 0.5); mid.y += lift;
  return new THREE.QuadraticBezierCurve3(A, mid, B);
}
function addStream(a, b, color, lift, n) {
  const c = curve(a, b, lift);
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(c.getPoints(40)),
    new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.18 }));
  scene.add(line);
  const pos = new Float32Array(n * 3), t = new Float32Array(n);
  for (let i = 0; i < n; i++) t[i] = Math.random();
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(g, new THREE.PointsMaterial({ size: 2.6, map: glow, color, transparent: true, opacity: 0.9,
    blending: THREE.AdditiveBlending, depthWrite: false }));
  scene.add(pts); streams.push({ c, g, t, speed: 0.08 + Math.random() * 0.05 });
}
function buildCluster(key, n) {
  const old = clusters[key]; if (old && old.n === n) return old; if (old) scene.remove(old.points);
  const base = DEPTS[key].pos, R = 5 + Math.cbrt(n) * 1.7, pos = new Float32Array(n * 3), col = new Float32Array(n * 3), home = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const y = 1 - (i + 0.5) / n * 2, rad = Math.sqrt(1 - y * y), th = i * 2.399963, r = R * Math.cbrt(((i * 7919) % n + 0.5) / n);
    home[i * 3] = Math.cos(th) * rad * r; home[i * 3 + 1] = y * r; home[i * 3 + 2] = Math.sin(th) * rad * r;
    col.set(voteColor(null), i * 3);
  }
  pos.set(home);
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const points = new THREE.Points(g, new THREE.PointsMaterial({ size: 3.0, map: glow, vertexColors: true, transparent: true,
    opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }));
  points.position.set(...base); scene.add(points);
  return (clusters[key] = { points, n, home, label: old ? old.label : null, spin: (Math.random() - 0.5) * 0.004 });
}
const holo = { rings: [], scan: null, beam: null, emitter: null };
function ring(radius, pos, tilt, color, opacity, dashed) {
  const pts = []; for (let i = 0; i <= 128; i++) { const a = i / 128 * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(a) * radius, 0, Math.sin(a) * radius)); }
  const g = new THREE.BufferGeometry().setFromPoints(pts);
  const m = dashed ? new THREE.LineDashedMaterial({ color, dashSize: 2.5, gapSize: 2, transparent: true, opacity, blending: THREE.AdditiveBlending })
    : new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending });
  const l = new THREE.Line(g, m); if (dashed) l.computeLineDistances(); l.position.set(...pos); l.rotation.x = tilt || 0; scene.add(l); return l;
}
// ---- floating holo screens: one per symbol, orbiting behind the brain, each a live price chart ----------------
const screens = {};
function holoScreen(symbol, i, n) {
  const c = document.createElement('canvas'); c.width = 320; c.height = 170;
  const tex = new THREE.CanvasTexture(c);
  const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.92, side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending, depthWrite: false });
  const m = new THREE.Mesh(new THREE.PlaneGeometry(62, 33), mat);
  const a = Math.PI * (0.08 + 0.84 * (i / Math.max(1, n - 1))), R = 165;
  m.userData = { base: new THREE.Vector3(36 - Math.cos(a) * R, 40 + (i % 2) * 30, -Math.sin(a) * R * 0.6 - 20), phase: i * 0.7 };
  m.position.copy(m.userData.base); scene.add(m);
  return (screens[symbol] = { m, c, tex, last: null });
}
function drawScreen(sc, symbol, pts) {
  const g = sc.c.getContext('2d'), w = sc.c.width, h = sc.c.height; g.clearRect(0, 0, w, h);
  const up = pts.length > 1 && pts[pts.length - 1] >= pts[0], col = up ? '#2fd37a' : '#ff4d4d';
  g.strokeStyle = 'rgba(255,92,0,.55)'; g.lineWidth = 2; g.strokeRect(3, 3, w - 6, h - 6);
  g.fillStyle = 'rgba(255,92,0,.07)'; g.fillRect(3, 3, w - 6, h - 6);
  for (let y = 8; y < h; y += 4) { g.fillStyle = 'rgba(255,92,0,.04)'; g.fillRect(4, y, w - 8, 1); }
  g.font = '600 22px ui-monospace, Menlo, monospace'; g.fillStyle = '#ffa040'; g.shadowColor = '#ff5c00'; g.shadowBlur = 12; g.fillText(symbol, 14, 34);
  if (pts.length) {
    const last = pts[pts.length - 1], chg = pts.length > 1 ? last / pts[0] - 1 : 0;
    g.textAlign = 'right'; g.fillStyle = col; g.shadowColor = col; g.fillText(last.toFixed(2), w - 14, 34);
    g.font = '16px ui-monospace, Menlo, monospace'; g.fillText((chg >= 0 ? '+' : '') + (100 * chg).toFixed(2) + '%', w - 14, 56); g.textAlign = 'left';
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1, x0 = 14, x1 = w - 14, y0 = h - 16, y1 = 66;
    const X = (i) => x0 + (x1 - x0) * i / Math.max(1, pts.length - 1), Y = (v) => y0 - (y0 - y1) * (v - lo) / span;
    const grd = g.createLinearGradient(0, y1, 0, y0); grd.addColorStop(0, up ? 'rgba(93,255,196,.35)' : 'rgba(255,92,138,.35)'); grd.addColorStop(1, 'rgba(0,0,0,0)');
    g.beginPath(); pts.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v)))); g.lineTo(x1, y0); g.lineTo(x0, y0); g.closePath(); g.fillStyle = grd; g.shadowBlur = 0; g.fill();
    g.beginPath(); pts.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v)))); g.strokeStyle = col; g.lineWidth = 2.5; g.shadowColor = col; g.shadowBlur = 14; g.stroke();
    g.beginPath(); g.arc(x1, Y(last), 5, 0, Math.PI * 2); g.fillStyle = '#fff'; g.fill();
  }
  sc.tex.needsUpdate = true;
}
function updateScreens() {
  if (!scene || !history.length) return;
  const syms = Object.keys(history[history.length - 1].prices);
  syms.forEach((s, i) => { const sc = screens[s] || holoScreen(s, i, syms.length);
    drawScreen(sc, s, history.slice(-90).map((h) => h.prices[s]).filter((v) => v !== undefined)); });
}
function floatScreens(t) {
  for (const sc of Object.values(screens)) {
    const u = sc.m.userData; sc.m.position.set(u.base.x, u.base.y + 2.5 * Math.sin(t * 0.9 + u.phase), u.base.z);
    sc.m.lookAt(camera.position); sc.m.material.opacity = Math.random() < 0.01 ? 0.4 : 0.92;
  }
}
function holoProjector() {
  // A projector on the floor under the brain, a cone of light rising from it, and a scan plane sweeping the projection.
  const c = [36, -30, 0];
  holo.emitter = new THREE.Mesh(new THREE.RingGeometry(18, 26, 64), new THREE.MeshBasicMaterial({ color: 0xff5c00, transparent: true, opacity: 0.35,
    side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
  holo.emitter.rotation.x = -Math.PI / 2; holo.emitter.position.set(...c); scene.add(holo.emitter);
  for (const r of [34, 48]) holo.rings.push(ring(r, c, 0, 0xff5c00, 0.35, r === 48));
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(110, 24, 90, 64, 1, true), new THREE.MeshBasicMaterial({ color: 0xff5c00, transparent: true,
    opacity: 0.045, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
  beam.position.set(c[0], c[1] + 45, c[2]); scene.add(beam); holo.beam = beam;
  holo.scan = new THREE.Mesh(new THREE.PlaneGeometry(300, 160), new THREE.MeshBasicMaterial({ color: 0xff5c00, transparent: true, opacity: 0.05,
    side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
  holo.scan.rotation.x = -Math.PI / 2; holo.scan.position.set(36, 0, 0); scene.add(holo.scan);
  // orbit rings around every department and the managers' level
  for (const d of Object.values(DEPTS)) { holo.rings.push(ring(22, d.pos, 0.35, 0xff5c00, 0.4, true)); holo.rings.push(ring(28, d.pos, -0.5, 0xff5c00, 0.18, false)); }
  holo.rings.push(ring(60, [0, 34, 0], 0, 0xffa040, 0.22, true));
}
// ---- electricity and heartbeat ---------------------------------------------------------------------------
// Arcs: jagged bolts that jump between two points for a moment, re-jittered every frame like real sparks.
// Shockwaves: a ring that bursts out of every cluster on each heartbeat (each engine tick), bigger on orders.
const arcs = [], waves = [];
function worldPoint(key) {
  const c = clusters[key]; if (!c) return null; const i = Math.floor(Math.random() * c.n) * 3;
  const v = new THREE.Vector3(c.home[i], c.home[i + 1], c.home[i + 2]); v.applyEuler(c.points.rotation).multiplyScalar(c.points.scale.x);
  return v.add(c.points.position);
}
function zap(a, b, color, life, width) {
  if (!scene || arcs.length > 40) return;
  const n = 14, g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  const core = new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 1, blending: THREE.AdditiveBlending, depthWrite: false }));
  const glowLine = new THREE.Line(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false }));
  glowLine.scale.setScalar(1.002); scene.add(core, glowLine);
  const flash = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
  flash.scale.setScalar(width || 10); flash.position.copy(b); scene.add(flash);
  arcs.push({ a: a.clone(), b: b.clone(), g, core, glowLine, flash, n, life: life || 0.22, age: 0 });
}
function jitter(arc) {
  const pos = arc.g.attributes.position, len = arc.a.distanceTo(arc.b), amp = Math.min(9, len * 0.07);
  for (let i = 0; i < arc.n; i++) {
    const f = i / (arc.n - 1), p = arc.a.clone().lerp(arc.b, f), k = Math.sin(Math.PI * f);
    p.x += (Math.random() - 0.5) * amp * k; p.y += (Math.random() - 0.5) * amp * k; p.z += (Math.random() - 0.5) * amp * k;
    pos.setXYZ(i, p.x, p.y, p.z);
  }
  pos.needsUpdate = true;
}
function shockwave(pos, color, size) {
  if (!scene || waves.length > 30) return;
  const m = new THREE.Mesh(new THREE.RingGeometry(0.92, 1, 64), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.8,
    side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }));
  m.position.copy(pos); m.rotation.x = -Math.PI / 2; scene.add(m); waves.push({ m, age: 0, size: size || 30 });
}
const V = (p) => new THREE.Vector3(...p);
function heartbeat(strong) {
  if (!scene || reduced) return;
  for (const d of Object.values(DEPTS)) shockwave(V(d.pos), strong ? 0xffc65c : 0xff5c00, strong ? 46 : 30);
  if (holo.emitter) shockwave(holo.emitter.position.clone(), 0xff5c00, 70);
  queueBeat(strong);
}
function sparks(dt) {
  if (!scene || reduced) return;
  // ambient electricity: inside clusters (agents talking), feed to departments, managers to their teams
  if (Math.random() < 0.35) { const keys = Object.keys(clusters), k = keys[Math.floor(Math.random() * keys.length)], a = worldPoint(k), b = worldPoint(k); if (a && b) zap(a, b, 0xffa040, 0.12, 5); }
  if (Math.random() < 0.06) { const d = Object.values(DEPTS)[Math.floor(Math.random() * 4)]; zap(V(STAGES.feed.slice(0, 3)), V(d.pos), 0xff5c00, 0.2, 12); }
  if (Math.random() < 0.04) { const [name, m] = Object.entries(MANAGERS)[Math.floor(Math.random() * 3)]; zap(V(m.pos), V(DEPTS[m.depts[0]].pos), 0xffa040, 0.2, 10); }
  for (let i = arcs.length - 1; i >= 0; i--) {
    const r = arcs[i]; r.age += dt; const f = r.age / r.life;
    if (f >= 1) { scene.remove(r.core, r.glowLine, r.flash); r.g.dispose(); r.core.material.dispose(); r.glowLine.material.dispose(); r.flash.material.dispose(); arcs.splice(i, 1); continue; }
    jitter(r); r.core.material.opacity = 1 - f; r.glowLine.material.opacity = 0.8 * (1 - f); r.flash.material.opacity = 1 - f;
  }
  for (let i = waves.length - 1; i >= 0; i--) {
    const w = waves[i]; w.age += dt; const f = w.age / 1.1;
    if (f >= 1) { scene.remove(w.m); w.m.geometry.dispose(); w.m.material.dispose(); waves.splice(i, 1); continue; }
    w.m.scale.setScalar(4 + w.size * f); w.m.material.opacity = 0.8 * (1 - f);
  }
}

// ECG strip in the hero: a heartbeat line that spikes on every tick and jumps higher on orders
const beat = { queue: [], pts: [], canvas: null, frame: -1 };
// One heartbeat: the P wave, the sharp QRS spike, the T wave. Orders beat harder.
const PQRST = [0, .05, .1, .05, 0, 0, -.12, 1, -.35, 0, 0, .06, .14, .18, .12, .05, 0];
function queueBeat(strong) { if (beat.queue.length < 40) beat.queue.push(...PQRST.map((v) => v * (strong ? 1 : 0.62))); }
function drawBeat() {
  const c = beat.canvas; if (!c) return; const g = c.getContext('2d'), w = c.width, h = c.height;
  beat.pts.push(beat.queue.length ? beat.queue.shift() : (Math.random() - 0.5) * 0.015);
  if (beat.pts.length > w / 2) beat.pts.shift();
  g.clearRect(0, 0, w, h); g.lineWidth = 1.6; g.strokeStyle = '#ff5c00'; g.shadowColor = '#ff5c00'; g.shadowBlur = 8; g.beginPath();
  beat.pts.forEach((p, i) => { const x = w - (beat.pts.length - i) * 2, y = h * 0.62 - p * h * 0.55; i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke();
  const last = beat.pts[beat.pts.length - 1] || 0; g.shadowBlur = 14; g.fillStyle = '#fff'; g.beginPath(); g.arc(w - 2, h * 0.62 - last * h * 0.55, 2.6, 0, Math.PI * 2); g.fill();
}
function animateHolo(t) {
  holo.rings.forEach((r, i) => { r.rotation.y += (i % 2 ? -1 : 1) * 0.004 * (1 + (i % 3)); });
  if (holo.scan) { holo.scan.position.y = -28 + ((t * 18) % 70); holo.scan.material.opacity = 0.03 + 0.03 * Math.sin(t * 3); }
  if (holo.beam) holo.beam.material.opacity = 0.035 + 0.015 * Math.sin(t * 1.7) + (Math.random() < 0.02 ? 0.03 : 0);
  if (holo.emitter) holo.emitter.material.opacity = 0.28 + 0.12 * Math.sin(t * 2.4);
}
function init3d() {
  scene = new THREE.Scene(); scene.fog = new THREE.FogExp2(0x000000, 0.0019);
  camera = new THREE.PerspectiveCamera(48, 1, 0.1, 3000);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  host.appendChild(renderer.domElement); glow = glowTexture();
  // star dust
  const N = 1400, sp = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) { sp[i * 3] = (Math.random() - 0.5) * 900; sp[i * 3 + 1] = (Math.random() - 0.5) * 500; sp[i * 3 + 2] = (Math.random() - 0.5) * 900; }
  const sg = new THREE.BufferGeometry(); sg.setAttribute('position', new THREE.BufferAttribute(sp, 3));
  scene.add(new THREE.Points(sg, new THREE.PointsMaterial({ size: 1.2, map: glow, color: 0x555555, transparent: true, opacity: 0.5, depthWrite: false })));
  // floor grid
  const grid = new THREE.GridHelper(560, 56, 0xff5c00, 0x2a1204); grid.position.set(36, -30, 0); grid.material.transparent = true; grid.material.opacity = 0.28;
  grid.material.blending = THREE.AdditiveBlending; scene.add(grid);
  holoProjector();
  for (const [k, d] of Object.entries(DEPTS)) { buildCluster(k, 350); clusters[k].label = label(d.name, '', [d.pos[0], d.pos[1] - 19, d.pos[2]]); }
  for (const [k, [x, y, z, name]] of Object.entries(STAGES)) { stageMesh[k] = sphere([x, y, z], k === 'riskgate' ? 0xffc65c : 0xff5c00, 2.6); label(name, '', [x, y - 8, z]); }
  for (const d of Object.values(DEPTS)) { addStream(STAGES.feed.slice(0, 3), d.pos, 0xff5c00, 18, 26); addStream(d.pos, STAGES.desk.slice(0, 3), 0xff5c00, 18, 26); }
  addStream(STAGES.desk.slice(0, 3), STAGES.riskgate.slice(0, 3), 0xff5c00, 4, 10);
  addStream(STAGES.riskgate.slice(0, 3), STAGES.jev.slice(0, 3), 0xff5c00, 4, 10);
  addStream(STAGES.jev.slice(0, 3), STAGES.broker.slice(0, 3), 0xff5c00, 4, 10);
  for (const [name, m] of Object.entries(MANAGERS)) {
    m.mesh = sphere(m.pos, 0xffa040, 3.2); m.label = label(name, '', [m.pos[0], m.pos[1] + 9, m.pos[2]]);
    for (const k of m.depts) addStream(m.pos, DEPTS[k].pos, 0xffa040, 0, 8);
  }
  beat.canvas = $('beat');
  resize(); addEventListener('resize', resize); controls(); requestAnimationFrame(frame);
}
function resize() { const w = host.clientWidth, h = host.clientHeight; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
  if (!lastInput) dist = camera.aspect < 1 ? 470 : 265; }
function controls() {
  let drag = null;
  host.addEventListener('pointerdown', (e) => { drag = [e.clientX, e.clientY]; host.setPointerCapture(e.pointerId); host.style.cursor = 'grabbing'; lastInput = performance.now(); });
  host.addEventListener('pointermove', (e) => { if (!drag) return; yaw -= (e.clientX - drag[0]) * 0.006; pitch = Math.max(-0.2, Math.min(1.35, pitch + (e.clientY - drag[1]) * 0.006)); drag = [e.clientX, e.clientY]; lastInput = performance.now(); });
  host.addEventListener('pointerup', () => { drag = null; host.style.cursor = 'grab'; });
  host.addEventListener('wheel', (e) => { e.preventDefault(); dist = Math.max(70, Math.min(600, dist * (1 + Math.sign(e.deltaY) * 0.08))); lastInput = performance.now(); }, { passive: false });
}
function burst(path, color, size) {
  if (bursts.length > 60) return;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, color, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
  s.scale.setScalar(size || 9); scene.add(s); bursts.push({ s, path: path.map((p) => new THREE.Vector3(...p)), t: 0 });
}
function frame(now) {
  requestAnimationFrame(frame);
  const t = now / 1000;
  if (auto && now - lastInput > 4000) yaw += 0.0012;
  camera.position.set(target.x + dist * Math.cos(pitch) * Math.sin(yaw), target.y + dist * Math.sin(pitch), target.z + dist * Math.cos(pitch) * Math.cos(yaw));
  camera.lookAt(target);
  orderHeat *= 0.985;
  const flow = reduced ? 0 : 1 + 3 * orderHeat;
  for (const st of streams) {
    const a = st.g.attributes.position;
    for (let i = 0; i < st.t.length; i++) { st.t[i] = (st.t[i] + st.speed * flow / 60) % 1; const p = st.c.getPoint(st.t[i]); a.setXYZ(i, p.x, p.y, p.z); }
    a.needsUpdate = true;
  }
  for (const [k, c] of Object.entries(clusters)) {
    if (!reduced) { c.points.rotation.y += c.spin + 0.0015; const s = 1 + 0.035 * Math.sin(t * 1.3 + k.length); c.points.scale.setScalar(s);
      // flicker like a projection: brief dips in brightness
      c.points.material.opacity = Math.random() < 0.012 ? 0.45 : 0.95; }
  }
  if (!reduced) { animateHolo(t); floatScreens(t); } else if (Object.keys(screens).length) floatScreens(0);
  sparks(1 / 60);
  if (!reduced && Math.floor(now / 33) !== beat.frame) { beat.frame = Math.floor(now / 33); drawBeat(); }
  for (const m of Object.values(stageMesh)) m.scale.setScalar(1 + (reduced ? 0 : 0.12 * Math.sin(t * 2.2 + m.position.x)));
  for (let i = bursts.length - 1; i >= 0; i--) {
    const b = bursts[i]; b.t += 0.035; const seg = Math.floor(b.t);
    if (seg >= b.path.length - 1) { scene.remove(b.s); b.s.material.dispose(); bursts.splice(i, 1); continue; }
    b.s.position.copy(b.path[seg]).lerp(b.path[seg + 1], b.t - seg);
  }
  renderer.render(scene, camera);
  const w = host.clientWidth, h = host.clientHeight;
  for (const l of labels) {
    const v = l.pos.clone().project(camera); const vis = v.z < 1 && Math.abs(v.x) < 1.05 && Math.abs(v.y) < 1.05;
    l.n.hidden = !vis; if (vis) { l.n.style.left = ((v.x + 1) / 2 * w) + 'px'; l.n.style.top = ((1 - v.y) / 2 * h) + 'px'; }
  }
}
function paintClusters(org) {
  for (const [k, d] of Object.entries(org.departments)) {
    if (!scene || !d.agents || !DEPTS[k]) continue;
    const c = buildCluster(k, d.agents), col = c.points.geometry.attributes.color;
    d.votes.forEach((v, i) => { if (i < c.n) col.setXYZ(i, ...voteColor(v)); }); col.needsUpdate = true;
    c.label.sub.textContent = `${d.agents.toLocaleString()} agents · ${d.sitting_out} out · ${d.avg_vote === null ? '–' : d.avg_vote.toFixed(2)}`;
  }
  for (const m of org.managers) { const mm = MANAGERS[m.manager]; if (mm && mm.label) mm.label.sub.textContent = m.call; }
}

// ---- charts -------------------------------------------------------------------------------------------
let priceSeries, pnlSeries, history = [], sym = null;
function initCharts() {
  if (!window.LightweightCharts) return;
  const opts = { layout: { background: { color: 'transparent' }, textColor: '#8a8a8a', fontFamily: 'ui-monospace, Menlo, monospace' }, autoSize: true,
    grid: { vertLines: { color: 'rgba(255,92,0,.06)' }, horzLines: { color: 'rgba(255,92,0,.06)' } },
    rightPriceScale: { borderColor: 'rgba(255,92,0,.18)' }, timeScale: { borderColor: 'rgba(255,92,0,.18)', timeVisible: true },
    crosshair: { mode: 1, vertLine: { color: 'rgba(255,92,0,.5)' }, horzLine: { color: 'rgba(255,92,0,.5)' } } };
  priceSeries = LightweightCharts.createChart($('priceChart'), opts).addAreaSeries({ lineColor: '#ff5c00', lineWidth: 2,
    topColor: 'rgba(255,92,0,.32)', bottomColor: 'rgba(255,92,0,0)' });
  pnlSeries = LightweightCharts.createChart($('pnlChart'), opts).addBaselineSeries({ baseValue: { type: 'price', price: 0 },
    topLineColor: '#2fd37a', topFillColor1: 'rgba(93,255,196,.28)', topFillColor2: 'rgba(93,255,196,0)',
    bottomLineColor: '#ff4d4d', bottomFillColor1: 'rgba(255,92,138,0)', bottomFillColor2: 'rgba(255,92,138,.28)' });
}
function drawPrice() {
  if (!priceSeries || !sym) return; let last = 0;
  priceSeries.setData(history.filter((h) => h.prices[sym] !== undefined).map((h) => { const t = Math.max(last + 1, Math.floor(h.ts)); last = t; return { time: t, value: h.prices[sym] }; }));
}
let views = {};
function showView() {
  const v = views[sym]; const node = $('symView'); node.textContent = ''; if (!v) return;
  const dir = v.direction; node.className = 'view ' + (dir > 0.05 ? 'up' : dir < -0.05 ? 'dn' : 'mut');
  node.textContent = `reading room: ${dir > 0.05 ? 'leaning up' : dir < -0.05 ? 'leaning down' : 'no lean'} (${dir.toFixed(2)})`;
}
function loadPrices() {
  api('/api/prices').then((d) => {
    history = d.history; const syms = history.length ? Object.keys(history[history.length - 1].prices) : [];
    if (syms.length && !sym) {
      sym = syms[0]; const tabs = $('symTabs');
      tabs.replaceChildren(...syms.map((s) => { const b = el('button', s === sym ? 'on' : '', s); b.type = 'button';
        b.onclick = () => { sym = s; for (const x of tabs.children) x.classList.toggle('on', x === b); drawPrice(); showView(); }; return b; }));
    }
    drawPrice(); showView(); updateScreens();
    if (pnlSeries && d.pnl.length) { const t0 = Math.floor(Date.now() / 1000) - d.pnl.length; pnlSeries.setData(d.pnl.map((p, i) => ({ time: t0 + i, value: p.pnl }))); }
  }).catch(() => {});
}

// ---- risk, KPIs ---------------------------------------------------------------------------------------
function gauge(id, used, text) {
  const g = $(id); const f = Math.max(0, Math.min(1, used));
  g.querySelector('.fill').style.strokeDashoffset = String(157 * (1 - f));
  g.classList.toggle('warn', f >= 0.5 && f < 0.85); g.classList.toggle('hot', f >= 0.85);
  g.querySelector('b').textContent = text;
}
function facts(rows) { const dl = el('dl', 'facts'); for (const [k, v, cls] of rows) dl.append(el('dt', '', k), el('dd', cls || '', v)); $('riskFacts').replaceChildren(...dl.childNodes); }
function renderState(st) {
  const ticks = Object.entries(st.shards).filter(([k, v]) => /^\d+$/.test(k) && v.tick).map(([, v]) => v.tick);
  const starts = Object.entries(st.shards).filter(([k, v]) => /^\d+$/.test(k) && v.start).map(([, v]) => v.start);
  if (!ticks.length) return;
  const equity = ticks.reduce((a, t) => a + (t.equity || 0), 0), pnl = ticks.reduce((a, t) => a + (t.pnl || 0), 0);
  const risks = ticks.map((t) => t.risk).filter(Boolean);
  const day = risks.reduce((a, r) => a + r.day_pnl, 0), dayStart = risks.reduce((a, r) => a + r.day_start, 0) || 1;
  const limit = risks.length ? risks[0].daily_limit : 0.02, maxdd = risks.length ? risks[0].max_drawdown : 0.05;
  const dd = Math.max(0, ...risks.map((r) => r.drawdown)), gross = risks.reduce((a, r) => a + r.gross, 0), grossLim = risks.reduce((a, r) => a + r.gross_limit, 0) || 1;
  tween('kEquity', equity, money); tween('kPnl', pnl, signed, true);
  $('kPnl').className = pnl >= 0 ? 'up' : 'dn';
  tween('kDay', day, signed); $('kDay').className = day >= 0 ? 'up' : 'dn';
  $('kDaySub').textContent = `${pct(day / dayStart, 2)} · stops at −${pct(limit, 0)}`;
  const jev = st.jev_desk; $('kJev').textContent = jev.approval_rate === null ? '–' : pct(jev.approval_rate);
  $('kJevSub').textContent = `${(jev.approved + jev.rejected).toLocaleString()} checked`;
  gauge('gDay', day < 0 ? -day / dayStart / limit : 0, (day >= 0 ? '+' : '') + pct(day / dayStart, 2));
  gauge('gDd', dd / maxdd, pct(dd, 2));
  gauge('gGross', gross / grossLim, pct(gross / grossLim, 0));
  const stopped = ticks.map((t) => t.stopped).find(Boolean), killed = ticks.some((t) => t.killed);
  const stale = [...new Set(ticks.flatMap((t) => t.stale || []))];
  const k = $('kState'); k.classList.toggle('stopped', !!stopped && !killed); k.classList.toggle('killed', killed);
  $('kStateText').textContent = killed ? 'KILLED' : stopped ? 'STOPPED' : 'TRADING';
  $('kStateSub').textContent = killed ? 'KILL file: closing everything' : stopped || (stale.length ? `${stale.length} stale feed(s): exits only` : 'all stops clear');
  const market = starts.length ? starts[0].market : 'simulated';
  const tag = $('marketTag'); tag.textContent = market === 'live' ? 'LIVE PRICES · PAPER' : 'SIMULATED'; tag.classList.toggle('live', market === 'live');
  const mk = Object.values(st.shards).map((v) => v.market).find(Boolean);
  facts([
    ['Daily loss stop', `−${pct(limit, 0)} of the day's start`],
    ['Drawdown halt', `−${pct(maxdd, 0)} from the peak`],
    ['Exposure', `${money(gross)} of ${money(grossLim)}`],
    ['Open positions', String(risks.reduce((a, r) => a + r.positions, 0))],
    ['Stale prices', stale.length ? stale.join(', ') : 'none', stale.length ? 'dn' : ''],
    ['Price feed', mk ? `${mk.priced}/${mk.symbols} priced · ${mk.errors} errors · ${mk.spikes_held || 0} spikes held` : (market === 'live' ? 'starting…' : 'simulator')],
    ['News feeds', mk && mk.sources ? `${mk.feeds_ok}/${mk.sources} ok · ${mk.headlines_seen} headlines` : '–'],
  ]);
  const hs = $('heroStats'); const tickNo = Math.max(...ticks.map((t) => t.tick));
  const loop = ticks.map((t) => t.loop_ms).filter((x) => x !== undefined);
  hs.replaceChildren(el('div', '', `tick ${tickNo.toLocaleString()}`), el('div', '', `loop ${loop.length ? Math.max(...loop).toFixed(1) : '–'} ms`),
    el('div', '', `${starts.reduce((a, s) => a + (s.agents || 0), 0).toLocaleString()} agents`));
  $('liveDot').classList.add('on');
  const heat = st.heatmap || {}; renderHeat(heat);
  renderLeader(Object.entries(st.shards).filter(([k]) => /^\d+$/.test(k)).flatMap(([, v]) => (v.leaderboard && v.leaderboard.top) || []),
    ticks.reduce((a, t) => a + (t.proven || 0), 0));
}
function renderLeader(rows, provenTotal) {
  const top = rows.filter((r) => r.account).sort((a, b) => (b.account.proven - a.account.proven) || (b.account.equity - a.account.equity)).slice(0, 8);
  $('provenSub').textContent = `${provenTotal.toLocaleString()} proven · each trades its own $10,000 practice account · only proven agents can back real money`;
  if (!top.length) return;
  const head = el('tr'); for (const h of ['agent', 'symbol', 'account', 'trades', 'won', 'profit factor', 'worst drop', '']) head.append(el('th', '', h));
  const body = top.map((r) => {
    const a = r.account, tr = el('tr', a.proven ? 'proven' : '');
    tr.append(el('td', '', r.agent.replace(/^trader-0*/, '#')), el('td', 'sym', r.symbol), el('td', a.pnl >= 0 ? 'up' : 'dn', signed(a.pnl)),
      el('td', '', String(a.trades)), el('td', '', pct(a.win_rate, 0)), el('td', '', a.profit_factor === null ? '–' : a.profit_factor.toFixed(2)),
      el('td', '', pct(a.max_drawdown, 1)));
    const badge = el('td'); badge.append(el('span', 'pill ' + (a.proven ? 'filled' : ''), a.proven ? 'proven' : 'learning')); tr.append(badge); return tr;
  });
  $('leader').replaceChildren(head, ...body);
}
function renderHeat(heat) {
  $('heat').replaceChildren(...Object.entries(heat).map(([bot, v]) => {
    const c = el('div'); const a = Math.min(1, Math.abs(v));
    c.style.background = v >= 0 ? `rgba(255,92,0,${0.06 + a * 0.3})` : `rgba(255,92,138,${0.08 + a * 0.35})`;
    c.style.boxShadow = `inset 0 0 ${6 + 14 * a}px ${v >= 0 ? 'rgba(255,92,0,.5)' : 'rgba(255,92,138,.5)'}`;
    c.append(el('span', '', bot.replace('_', ' ')), el('b', '', v.toFixed(2))); return c;
  }));
}

// ---- org: departments, managers, news -----------------------------------------------------------------
function renderOrg(org) {
  paintClusters(org);
  let agents = 0, active = 0;
  const rows = [];
  for (const m of org.managers) {
    const r = el('div', 'row'); const l = el('div'); l.append(el('b', '', m.manager + ' '), el('span', 'mut', m.title), el('div', '', m.call));
    r.append(l, el('div', 'r ' + (m.pnl >= 0 ? 'up' : 'dn'), signed(m.pnl))); rows.push(r);
  }
  for (const d of Object.values(org.departments)) {
    agents += d.agents; active += d.active;
    const r = el('div', 'row'); const l = el('div');
    const win = d.trade_win_rate === null || d.trade_win_rate === undefined ? '' : ` · trades won ${pct(d.trade_win_rate)}`;
    l.append(el('b', '', d.name), el('div', 'mut', `${d.agents.toLocaleString()} agents · trust ${d.avg_weight === null ? '–' : d.avg_weight.toFixed(2)}${win}`));
    const bar = el('div', 'bar-mini'); const fill = el('i'); fill.style.width = (d.agents ? 100 * d.active / d.agents : 0) + '%'; bar.append(fill); l.append(bar);
    r.append(l, el('div', 'r ' + (d.pnl >= 0 ? 'up' : 'dn'), signed(d.pnl))); rows.push(r);
  }
  $('depts').replaceChildren(...rows); $('depts').classList.remove('mut');
  tween('kAgents', active, (v) => Math.round(v).toLocaleString()); $('kAgentsSub').textContent = `voting of ${agents.toLocaleString()}`;
  views = (org.reading_room && org.reading_room.views) || {}; showView();
  const news = org.news || [];
  if (news.length) {
    $('news').classList.remove('mut');
    $('news').replaceChildren(...news.map((n) => {
      const v = views[n.symbol]; const lean = v ? v.news : 0;
      const h = el('div', 'h' + (lean > 0.05 ? ' bull' : lean < -0.05 ? ' bear' : ''));
      h.append(el('span', 'sym', n.symbol || ''), el('span', '', String(n.text || '').replace(/^[A-Z]+:\s*/, '')));
      return h;
    }));
    $('newsSub').textContent = news[0].kind === 'live' ? 'real headlines, scored by the reading room' : 'practice wire (simulated)';
  }
}

// ---- real-money approvals -----------------------------------------------------------------------------
let seenOrders = new Set(), liveState = null;
function renderLive(d) {
  liveState = d; const lim = d.limits;
  $('liveSub').textContent = !lim.on ? 'off · set the limits to turn it on' : !d.bridge ? 'bridge token not set' : !d.two_step ? 'needs two-step login'
    : `${money(d.spent_today)} of ${money(lim.max_day_usd)} today · ${money(lim.max_order_usd)} per order`;
  const open = d.orders.filter((o) => o.status === 'proposed');
  $('codeRow').hidden = !open.length || !d.two_step || !d.bridge;
  if (!d.orders.length) { $('liveList').replaceChildren(el('div', 'mut', lim.on ? 'No proposals yet. They appear here when the desk trades a listed symbol.'
    : 'Real-money trading is off. See "Real money" in the README.')); return; }
  $('liveList').classList.remove('mut');
  $('liveList').replaceChildren(...d.orders.slice(0, 8).map((o) => {
    const row = el('div', 'order' + (!seenOrders.has(o.id) && o.status === 'proposed' ? ' new' : '')); seenOrders.add(o.id);
    const who = el('div'); who.append(el('span', 'who', `${o.side.toUpperCase()} ${o.qty} ${o.symbol}`), el('span', 'mut', ` @ $${o.price} · ${money(o.qty * o.price)}`));
    const right = el('div', 'act');
    if (o.status === 'proposed') {
      const yes = el('button', 'yes', 'Approve'), no = el('button', 'no', 'Reject'); yes.type = no.type = 'button';
      yes.onclick = () => decide(o.id, true); no.onclick = () => decide(o.id, false); right.append(yes, no);
    } else right.append(el('span', 'pill ' + o.status, o.status));
    row.append(who, right, el('div', 'why', o.detail || o.why || '')); return row;
  }));
}
function decide(id, approve) {
  const code = $('code').value.replace(/\s/g, '');
  if (approve && !/^\d{6}$/.test(code)) { $('liveMsg').textContent = 'Type the 6-digit code from your authenticator app first.'; $('code').focus(); return; }
  $('liveMsg').textContent = approve ? 'Approving…' : 'Rejecting…';
  fetch('/api/live/decide', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, approve, code }) }).then((r) => r.json().then((b) => [r.status, b])).then(([s, b]) => {
    $('liveMsg').textContent = s === 200 ? (approve ? 'Approved. The bridge places it within a few seconds.' : 'Rejected.') : b.error || 'That didn\'t work.';
    $('code').value = ''; loadLive();
  }).catch(() => { $('liveMsg').textContent = 'Could not reach the app.'; });
}
function loadLive() { api('/api/live').then(renderLive).catch(() => {}); }

// ---- activity: what the system did each second over the last two minutes --------------------------------
const act = { secs: [], kinds: ['order', 'risk', 'jev', 'check'], totals: { order: 0, risk: 0, jev: 0, check: 0 } };
function countEvent(type) {
  if (!act.kinds.includes(type)) return; act.totals[type]++;
  const sec = Math.floor(Date.now() / 1000); let last = act.secs[act.secs.length - 1];
  if (!last || last.t !== sec) { last = { t: sec, order: 0, risk: 0, jev: 0, check: 0 }; act.secs.push(last); if (act.secs.length > 120) act.secs.shift(); }
  last[type]++;
}
const ACT_COLORS = { order: '#ffc65c', risk: '#ff4d4d', jev: '#ffa040', check: '#ff5c00' };
function drawActivity() {
  const c = $('activity'); if (!c) return; const r = c.getBoundingClientRect(); const dpr = Math.min(2, devicePixelRatio);
  if (c.width !== Math.round(r.width * dpr)) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
  const g = c.getContext('2d'), w = c.width, h = c.height, now = Math.floor(Date.now() / 1000), n = 60, bw = w / n;
  const by = Object.fromEntries(act.secs.map((x) => [x.t, x]));
  const max = Math.max(4, ...act.secs.map((x) => x.order + x.risk + x.jev + x.check));
  g.clearRect(0, 0, w, h);
  for (let i = 0; i < n; i++) {
    const s = by[now - (n - 1 - i)]; if (!s) continue; let y = h;
    for (const k of act.kinds) { const bh = (s[k] / max) * (h - 4); if (!bh) continue; g.fillStyle = ACT_COLORS[k]; g.shadowColor = ACT_COLORS[k]; g.shadowBlur = 8 * dpr;
      g.fillRect(i * bw + 1, y - bh, bw - 2, bh); y -= bh; }
  }
  $('actTotals').replaceChildren(...act.kinds.map((k) => { const d = el('div'); const i = el('i'); i.style.background = ACT_COLORS[k]; i.style.boxShadow = `0 0 6px ${ACT_COLORS[k]}`;
    d.append(i, el('b', '', act.totals[k].toLocaleString()), el('span', 'mut', { order: ' orders', risk: ' risk cuts', jev: ' Jev rejects', check: ' order checks' }[k])); return d; }));
}
setInterval(drawActivity, 500);

// ---- event stream -------------------------------------------------------------------------------------
function connect() {
  const es = new EventSource('/events');
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    countEvent(e.type);
    if (scene) {
      const S = (k) => STAGES[k].slice(0, 3);
      if (e.type === 'order') { orderHeat = Math.min(1, orderHeat + 0.25); burst([S('desk'), S('riskgate'), S('jev'), S('broker')], 0xffc65c, 11);
        zap(V(S('desk')), V(S('riskgate')), 0xffc65c, 0.25, 16); zap(V(S('riskgate')), V(S('jev')), 0xffc65c, 0.3, 16); zap(V(S('jev')), V(S('broker')), 0xffc65c, 0.35, 20); heartbeat(true); }
      if (e.type === 'risk') { burst([S('desk'), S('riskgate')], 0xff4d4d, 8); zap(V(S('desk')), V(S('riskgate')), 0xff4d4d, 0.3, 14); }
      if (e.type === 'live_proposal') { burst([S('broker'), [S('broker')[0], 40, 0]], 0x2fd37a, 16); loadLive(); }
      if (e.type === 'tick' && e.shard === 0 && e.tick % 2 === 0) heartbeat(false);
      if (e.type === 'tick' && e.shard === 0 && e.tick % 3 === 0) { const keys = Object.keys(DEPTS); burst([S('feed'), DEPTS[keys[e.tick % 4]].pos, S('desk')], 0xff5c00, 6); }
    }
    if (!['order', 'risk', 'check', 'jev', 'live_proposal', 'coach', 'brain'].includes(e.type)) return;
    let text = '';
    if (e.type === 'order') text = `ORDER ${e.qty > 0 ? 'BUY' : 'SELL'} ${Math.abs(e.qty).toFixed(2)} ${e.symbol} @ ${e.price}`;
    else if (e.type === 'risk') text = `RISK ${e.symbol}: ${e.reason}`;
    else if (e.type === 'check') text = `CHECK ${e.symbol}: ${(e.notes || []).join('; ')}`;
    else if (e.type === 'jev') text = `JEV ${e.symbol || e.bot || e.strategy || ''} ${e.decision || ''} ${(e.codes || []).join(',')}`;
    else if (e.type === 'live_proposal') text = `REAL MONEY proposal: ${e.side} ${e.qty} ${e.symbol} @ ${e.price} (approve on the right)`;
    else if (e.type === 'coach') text = `COACH ${e.summary || ''}`;
    else text = `BRAIN weights ${JSON.stringify(e.weights)}`;
    const row = el('div', e.type === 'risk' || e.type === 'jev' ? 'dn' : e.type === 'live_proposal' ? 'up' : '', `${new Date(e.ts * 1000).toLocaleTimeString()}  [${e.shard}] ${text}`);
    const s = $('stream'); if (s.classList.contains('mut')) { s.classList.remove('mut'); s.textContent = ''; }
    s.prepend(row); while (s.children.length > 120) s.lastChild.remove();
  };
  es.onerror = () => { $('liveDot').classList.remove('on'); fetch('/api/state', { credentials: 'same-origin' }).then((r) => { if (r.status === 401) location.href = '/login'; }).catch(() => {}); };
}

if (THREE_OK) { try { init3d(); } catch (err) { THREE_OK = false; $('err3d').hidden = false; } } else $('err3d').hidden = false;
initCharts();
const loadState = () => api('/api/state').then(renderState).catch(() => {});
const loadOrg = () => api('/api/org').then(renderOrg).catch(() => {});
loadState(); loadOrg(); loadPrices(); loadLive(); connect();
setInterval(loadState, 2000); setInterval(loadOrg, 2500); setInterval(loadPrices, 4000); setInterval(loadLive, 5000);
if (!reduced && matchMedia('(hover: hover)').matches) {
  document.addEventListener('pointermove', (e) => {
    const card = e.target.closest && e.target.closest('.card:not(.hero), .kpi'); if (!card) return;
    const r = card.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    card.style.transform = `perspective(900px) rotateX(${(-y * 6).toFixed(2)}deg) rotateY(${(x * 8).toFixed(2)}deg) translateZ(6px)`;
    card.style.setProperty('--mx', ((x + 0.5) * 100).toFixed(1) + '%'); card.style.setProperty('--my', ((y + 0.5) * 100).toFixed(1) + '%');
  });
  document.addEventListener('pointerout', (e) => { const card = e.target.closest && e.target.closest('.card, .kpi');
    if (card && !card.contains(e.relatedTarget)) card.style.transform = ''; });
}
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
})();
