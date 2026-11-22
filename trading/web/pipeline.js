// News pipeline page: live counters, an orange nebula core (three.js), the flow diagram with items moving
// through it, the bill, a what-if gate slider, the decision log and the human queue. Data: /api/pipeline.
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = v => "$" + Number(v || 0).toFixed(2);
const pct = v => v == null || isNaN(v) ? "–" : Math.round(v * 100) + "%";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let S = null, seen = 0, started = Date.now();

// ---------- eased counters ----------
const shown = {};
function count(id, v, fmt = x => Math.round(x).toLocaleString()) {
  const el = $(id), from = shown[id] == null ? 0 : shown[id], t0 = performance.now();
  shown[id] = v;
  if (reduced || from === v) { el.textContent = fmt(v); return; }
  const step = now => { const t = Math.min(1, (now - t0) / 800), e = 1 - Math.pow(2, -10 * t); el.textContent = fmt(from + (v - from) * e); if (t < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}

// ---------- the core: orange nebula sphere ----------
const core = {flash: 0, rings: [], sparks: []};
(function buildCore() {
  const host = $("#core");
  if (!window.THREE) return;
  const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  cam.position.set(0, 0, 9);
  let renderer;
  try { renderer = new THREE.WebGLRenderer({antialias: true, alpha: true}); } catch (e) { return; }
  renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
  host.appendChild(renderer.domElement);
  const glowTex = (() => {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d"), r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(.25, "rgba(255,170,90,.9)"); r.addColorStop(.6, "rgba(255,92,0,.35)"); r.addColorStop(1, "rgba(255,92,0,0)");
    g.fillStyle = r; g.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(c);
  })();
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({map: glowTex, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false}));
  glow.scale.set(4.2, 4.2, 1); scene.add(glow);
  // particles on a swirling shell: radius, angle and speed per particle (inner ones turn faster)
  const N = 6000, pos = new Float32Array(N * 3), col = new Float32Array(N * 3), P = [];
  for (let i = 0; i < N; i++) {
    const r = 1.2 + Math.pow(Math.random(), 0.7) * 1.9, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
    const arm = Math.sin(th * 3 + r * 2.2) * 0.25;
    P.push({r, th, ph: ph + arm, sp: 0.35 / r + Math.random() * 0.05});
    const hot = Math.random();
    col[i * 3] = 1; col[i * 3 + 1] = 0.35 + hot * 0.45; col[i * 3 + 2] = hot * 0.25;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({size: 0.045, vertexColors: true, transparent: true, opacity: .9, blending: THREE.AdditiveBlending, depthWrite: false}));
  scene.add(pts);
  const ringMat = () => new THREE.MeshBasicMaterial({color: 0xff5c00, transparent: true, opacity: .8, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false});
  core.ring = () => { const m = new THREE.Mesh(new THREE.RingGeometry(1, 1.06, 96), ringMat()); m.userData.t = 0; scene.add(m); core.rings.push(m); };
  core.spark = () => {
    const m = new THREE.Sprite(new THREE.SpriteMaterial({map: glowTex, color: 0xffd2a8, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false}));
    const a = Math.random() * Math.PI * 2; m.userData = {t: 0, dx: Math.cos(a), dy: Math.sin(a)}; m.scale.set(.35, .35, 1); scene.add(m); core.sparks.push(m);
  };
  function size() { const w = host.clientWidth, h = host.clientHeight; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix(); }
  addEventListener("resize", size); size();
  let last = performance.now();
  (function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const breathe = 1 + 0.04 * Math.sin(now / 4000 * Math.PI * 2) + 0.04;  // 1.0 -> 1.08 over ~4s
    for (let i = 0; i < N; i++) {
      const p = P[i]; if (!reduced) p.th += p.sp * dt;
      const r = p.r * breathe * (1 + core.flash * 0.12);
      pos[i * 3] = r * Math.sin(p.ph) * Math.cos(p.th); pos[i * 3 + 1] = r * Math.cos(p.ph) * 0.92; pos[i * 3 + 2] = r * Math.sin(p.ph) * Math.sin(p.th);
    }
    geo.attributes.position.needsUpdate = true;
    pts.rotation.y += reduced ? 0 : dt * 0.22; pts.rotation.x = 0.35;
    glow.scale.setScalar(4.2 * breathe + core.flash * 2.2); glow.material.opacity = 0.85 + core.flash * 0.15;
    core.flash = Math.max(0, core.flash - dt * 1.6);
    core.rings = core.rings.filter(m => { m.userData.t += dt; const s = 1.4 + m.userData.t * 5; m.scale.set(s, s, 1); m.material.opacity = Math.max(0, .8 - m.userData.t * .8); if (m.userData.t > 1) { scene.remove(m); return false; } return true; });
    core.sparks = core.sparks.filter(m => { m.userData.t += dt; const d = 1.4 + m.userData.t * 4; m.position.set(m.userData.dx * d, m.userData.dy * d, 0); m.material.opacity = Math.max(0, 1 - m.userData.t * 1.5); if (m.userData.t > .7) { scene.remove(m); return false; } return true; });
    renderer.render(scene, cam);
    requestAnimationFrame(frame);
  })(last);
})();

// ---------- flow diagram: items travel the pipes ----------
const svgNS = "http://www.w3.org/2000/svg";
const queue = [];
function light(stage, on) { const li = $(`#stages [data-s="${stage}"]`); if (li) li.classList.toggle("on", on); const n = $(`#n-${stage}`); if (n) n.classList.toggle("hot", on); }
function travel(pathId, deep, ms) {
  return new Promise(res => {
    const path = $("#" + pathId), len = path.getTotalLength(), dot = document.createElementNS(svgNS, "circle");
    dot.setAttribute("r", deep ? 6 : 4.5); dot.setAttribute("class", "pl-dot" + (deep ? " deep" : ""));
    $("#dots").appendChild(dot);
    const t0 = performance.now();
    (function step(now) {
      const t = Math.min(1, (now - t0) / ms), p = path.getPointAtLength(len * t);
      dot.setAttribute("cx", p.x); dot.setAttribute("cy", p.y);
      if (t < 1) requestAnimationFrame(step); else { dot.remove(); res(); }
    })(t0);
  });
}
async function animate(rec) {
  const deep = rec.route === "deep", fast = reduced ? 0 : 1;
  const leg = async (stage, path, ms) => { light(stage, true); if (path && fast) await travel(path, deep, ms); light(stage, false); };
  await leg("dec", "p1", 260);
  await leg("tri", "p2", 260);
  term(rec);
  await leg("gate", deep ? "p3" : "p4", 380);
  if (deep) { core.flash = 1; core.ring && core.ring(); } else if (core.spark) core.spark();
  await leg(deep ? "deep" : "cheap", deep ? "p5" : "p6", 380);
  await leg("tags", "p7", 160);
  light("bill", true); setTimeout(() => light("bill", false), 300);
}
async function pump() {
  while (true) {
    const rec = queue.shift();
    if (rec) await animate(rec); else await new Promise(r => setTimeout(r, 300));
  }
}

// ---------- terminal ----------
const lines = [];
function term(r) {
  const t = new Date().toLocaleTimeString([], {hour12: false});
  const route = r.route === "deep" ? `<b>&lt; ${r.gate.toFixed(2)} -&gt; deep read</b>` : `&gt;= ${r.gate.toFixed(2)} kept`;
  lines.push(`[${t}] TRIAGE · ${esc(r.symbol)} · "${esc(String(r.text || "").replace(/^[A-Z]+:\s*/, "").slice(0, 60))}" · job: ${esc(r.triage.job || "")} · sure ${r.triage.confidence.toFixed(2)}${r.triage.why ? " · " + esc(r.triage.why) : ""}`);
  lines.push(`[${t}] ROUTE · ${route}${r.overrule ? ` · <b>overrule: ${esc(r.overrule)}</b>` : ""}${r.audit ? " · audit sample" : ""}${r.human ? " · <b>human queue</b>" : ""} · ${r.call}`);
  while (lines.length > 9) lines.shift();
  $("#term").innerHTML = lines.join("\n");
}

// ---------- render ----------
function render() {
  const c = S.counts;
  count("#k-read", c.read); count("#k-kept", c.kept); count("#k-esc", c.escalated); count("#k-over", c.overruled); count("#k-human", c.human);
  $("#k-audit").textContent = S.audit_agree == null ? "–" : pct(S.audit_agree) + ` of ${c.audited}`;
  const save = "−" + Math.round(S.savings * 100) + "%";
  $("#k-save").textContent = save; $("#badge-save").textContent = save;
  $("#gate-t").textContent = `GATE ${S.gate.toFixed(2)}`; $("#c-gate").textContent = `thresh ${S.gate.toFixed(2)}`;
  $("#pl-path").textContent = `pipeline / news triage · gate ${S.gate.toFixed(2)}`;
  $("#s-gate").textContent = `${S.gate.toFixed(2)} now (started ${S.start_gate.toFixed(2)}) · ${S.gate_moves.length} moves from audits`;
  $("#c-dec").textContent = `read ${c.read}`; $("#c-tri").textContent = S.models.cheap.replace("claude-", "");
  $("#c-deep").textContent = `${c.escalated} · ${S.models.deep.replace("claude-", "")}`; $("#c-cheap").textContent = `kept ${c.kept}`;
  $("#c-tags").textContent = `index ${c.read}`; $("#c-bill").textContent = usd(S.cost.hybrid);
  $("#pl-mode").textContent = S.mode === "demo" ? "DEMO MODE · ESTIMATED COSTS" : "LIVE MODELS";
  const mx = Math.max(S.cost.deep_only, 0.0001), u = S.unknowns;
  const rows = [["Cheap only", S.cost.cheap_only, u.cheap_only, ""], ["Deep only", S.cost.deep_only, u.deep_only, "deep"], ["Hybrid", S.cost.hybrid, u.hybrid, "hybrid"]];
  $("#bill").innerHTML = rows.map(([n, v, k, cls]) => `<div class="pl-billrow ${cls}"><span>${n}</span><div class="track"><i data-w="${(v / mx * 100).toFixed(1)}"></i></div><span class="r">${usd(v)}</span><span class="u">${k} unknown</span></div>`).join("");
  requestAnimationFrame(() => $("#bill").querySelectorAll("i").forEach(i => { i.style.width = i.dataset.w + "%"; }));
  $("#bill-note").textContent = `${c.read} headlines. Cheap ${usd(S.prices.cheap * 1000)} and deep ${usd(S.prices.deep * 1000)} per 1,000 headlines (estimates). Unknown = still unsure at the end.`;
  $("#log").innerHTML = S.log.slice(0, 40).map(r => {
    const tags = [r.topic, ...((r.deep && r.deep.tags) || []).map(t => t.tag)];
    return `<tr><td class="o">${esc(r.symbol)}</td><td>${esc(String(r.text || "").replace(/^[A-Z]+:\s*/, ""))}</td><td class="r">${r.triage.confidence.toFixed(2)}</td>
      <td><span class="pl-route ${r.route}">${r.route === "deep" ? "DEEP" : "KEPT"}</span>${r.audit ? ' <span class="pl-tag">audit</span>' : ""}</td>
      <td><span class="pg-pill ${r.call === "BUY" ? "bull" : r.call === "SELL" ? "bear" : "flat"}">${r.call}</span></td>
      <td class="hide-s">${tags.map(t => `<span class="pl-tag ${t === "unknown" ? "unknown" : ""}">${esc(t)}</span>`).join("")}</td></tr>`;
  }).join("") || '<tr><td colspan="6" class="g">Waiting for the first headlines…</td></tr>';
  $("#human").innerHTML = S.human.map(r => `<div class="pg-item"><span class="sym">${esc(r.symbol)}</span><div><span class="t">${esc(String(r.text || "").replace(/^[A-Z]+:\s*/, ""))}</span><small>deep read ${r.deep ? r.deep.confidence.toFixed(2) : "–"} sure · ${esc(r.overrule ? "high impact: " + r.overrule : r.triage.why || "unsure")}</small></div><span class="pl-tag unknown">unknown</span></div>`).join("") || '<div class="pg-empty">Nothing waiting. Every headline was called.</div>';
  whatIf();
}

// ---------- what-if gate ----------
function replay(g) {
  const L = S ? S.log : [];
  const n = L.length, esc2 = L.filter(r => r.overrule || r.triage.confidence < g).length;
  const cost = n * S.prices.cheap + esc2 * S.prices.deep, deep = n * S.prices.deep;
  return {n, esc: esc2, cost, save: deep ? 1 - cost / deep : 0};
}
function whatIf() {
  if (!S) return;
  const g = +$("#whatif").value, r = replay(g);
  $("#w-gate").textContent = g.toFixed(2); $("#w-esc").textContent = r.n ? pct(r.esc / r.n) : "–";
  $("#w-cost").textContent = usd(r.cost); $("#w-save").textContent = r.n ? "−" + Math.round(r.save * 100) + "%" : "–";
  const c = $("#curve"), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr; const x = c.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!r.n) return;
  const X = v => 10 + (w - 20) * (v - 0.5) / 0.45, Y = v => h - 16 - v * (h - 26);
  x.strokeStyle = "#1f1f1f"; x.beginPath(); x.moveTo(10, Y(0)); x.lineTo(w - 10, Y(0)); x.stroke();
  x.beginPath(); for (let v = 0.5; v <= 0.951; v += 0.01) { const q = replay(v); v === 0.5 ? x.moveTo(X(v), Y(q.esc / q.n)) : x.lineTo(X(v), Y(q.esc / q.n)); }
  x.strokeStyle = "#ffd2a8"; x.lineWidth = 2; x.stroke();
  x.fillStyle = "#ff5c00"; x.beginPath(); x.arc(X(S.gate), Y(replay(S.gate).esc / r.n), 5, 0, 7); x.fill();
  x.fillStyle = "#fff"; x.beginPath(); x.arc(X(g), Y(r.esc / r.n), 3.5, 0, 7); x.fill();
  x.fillStyle = "#8a8a8a"; x.font = "10px ui-monospace, Menlo, monospace"; x.fillText("0.50", 10, h - 2); x.fillText("0.95", w - 36, h - 2); x.fillText("sent deep", 12, 12);
}
$("#whatif").addEventListener("input", whatIf);

// ---------- status bar ----------
setInterval(() => {
  const s = Math.floor((Date.now() - started) / 1000), hms = [s / 3600, (s / 60) % 60, s % 60].map(v => String(Math.floor(v)).padStart(2, "0")).join(":");
  const flow = S && S.counts.read ? Math.round(S.counts.kept / S.counts.read * 100) + "% kept" : "–";
  $("#status").textContent = `SYSTEM: ONLINE · FLOW: ${flow} · MODE: ${S && S.mode === "demo" ? "DEMO" : "LIVE"} · UPTIME: ${hms} · QUEUE: ${queue.length} · STREAM: STABLE`;
}, 1000);

async function load() {
  try {
    const r = await fetch("/api/pipeline", {cache: "no-store"});
    if (r.status === 401) { location.href = "/login"; return; }
    S = await r.json();
  } catch (e) { return; }
  const total = S.counts.read;
  const fresh = seen ? Math.min(total - seen, S.log.length) : Math.min(6, S.log.length);
  if (fresh > 0) S.log.slice(0, fresh).reverse().forEach(rec => queue.push(rec));
  while (queue.length > 12) queue.shift();
  seen = total;
  render();
}
load(); setInterval(load, 3000); pump();
