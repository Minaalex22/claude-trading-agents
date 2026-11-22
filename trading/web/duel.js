// Night desk: Hunter (traders) vs Judge (risk desk), on real paper-desk data only.
// Every number comes from /api/report, /api/org or the /events stream. Nothing is invented.
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = v => v == null || isNaN(v) ? "–" : "$" + Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const signed = v => v == null || isNaN(v) ? "–" : (v >= 0 ? "+" : "−") + money(Math.abs(v));
const pct = v => v == null || isNaN(v) ? "–" : (v * 100).toFixed(1) + "%";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

function pop(el) { el.classList.remove("dd-pop"); void el.offsetWidth; el.classList.add("dd-pop"); }

// ---- live balance counter (eased, never jumps) ----
let disp = null, target = null, animFrom = 0, animStart = 0;
const easeOutExpo = t => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t);
function tickBalance(now) {
  const t = reduced ? 1 : Math.min(1, (now - animStart) / 900);
  disp = animFrom + (target - animFrom) * easeOutExpo(t);
  $("#bal").textContent = money(disp);
  if (t < 1) return requestAnimationFrame(tickBalance);
  setTimeout(() => $("#bal").classList.remove("up", "down"), 400);
}
function setBalance(v) {
  if (v == null || isNaN(v) || v === target) return;
  if (target != null) $("#bal").classList.add(v > target ? "up" : "down");
  animFrom = disp == null ? v : disp;
  target = v;
  animStart = performance.now();
  requestAnimationFrame(tickBalance);
}

// ---- equity curve with drawdown shading ----
const points = []; // {t, v}
let peak = -Infinity, maxDD = 0;
function addPoint(v) {
  if (v == null || isNaN(v)) return;
  points.push({t: Date.now(), v});
  while (points.length > 600) points.shift();
  peak = Math.max(peak, v);
  maxDD = Math.max(maxDD, peak > 0 ? (peak - v) / peak : 0);
  drawCurve();
}
function drawCurve() {
  const c = $("#curve"), dpr = devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (points.length < 2) return;
  const vals = points.map(p => p.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * .12; lo -= pad; hi += pad;
  const L = 8, R = w - 64, T = 8, B = h - 18;
  const x = i => L + (R - L) * i / (points.length - 1);
  const y = v => B - (B - T) * (v - lo) / (hi - lo);
  // grid + labels
  g.strokeStyle = "#1f1f1f"; g.fillStyle = "#9ca3af"; g.font = "11px ui-monospace, Menlo, monospace"; g.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const v = lo + (hi - lo) * i / 3, yy = y(v);
    g.beginPath(); g.moveTo(L, yy); g.lineTo(R, yy); g.stroke();
    g.fillText("$" + Math.round(v).toLocaleString(), R + 6, yy + 4);
  }
  // drawdown shading: area between running peak and equity
  let run = -Infinity;
  const peaks = vals.map(v => (run = Math.max(run, v)));
  g.beginPath();
  peaks.forEach((p, i) => i ? g.lineTo(x(i), y(p)) : g.moveTo(x(i), y(p)));
  for (let i = vals.length - 1; i >= 0; i--) g.lineTo(x(i), y(vals[i]));
  g.closePath(); g.fillStyle = "rgba(239,68,68,.22)"; g.fill();
  // area under the curve
  const up = vals[vals.length - 1] >= vals[0];
  const grad = g.createLinearGradient(0, T, 0, B);
  grad.addColorStop(0, up ? "rgba(34,197,94,.28)" : "rgba(239,68,68,.28)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  g.beginPath();
  vals.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)));
  g.lineTo(x(vals.length - 1), B); g.lineTo(x(0), B); g.closePath(); g.fillStyle = grad; g.fill();
  // line
  g.beginPath();
  vals.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)));
  g.strokeStyle = up ? "#22c55e" : "#ef4444"; g.lineWidth = 2; g.stroke();
  // last point
  const lx = x(vals.length - 1), ly = y(vals[vals.length - 1]);
  g.beginPath(); g.arc(lx, ly, 4, 0, 7); g.fillStyle = "#ff5c00"; g.fill();
  const mins = Math.max(1, Math.round((points[points.length - 1].t - points[0].t) / 60000));
  $("#curve-note").textContent = `${points.length} points over ${mins} min · red shading = drawdown from the peak · max drawdown ${pct(maxDD)}`;
  $("#s-dd").textContent = pct(maxDD);
}
addEventListener("resize", drawCurve);

// ---- win-rate donut ----
function drawDonut(rate) {
  const c = $("#donut"), g = c.getContext("2d"), r = 48, cx = 60, cy = 60;
  g.clearRect(0, 0, 120, 120);
  g.lineWidth = 14; g.lineCap = "round";
  g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.strokeStyle = "#ef4444"; g.globalAlpha = .55; g.stroke(); g.globalAlpha = 1;
  if (rate > 0) { g.beginPath(); g.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * rate); g.strokeStyle = "#22c55e"; g.stroke(); }
}

// ---- scoreboard ----
let rejected = 0;
function score(id, n) { const el = $(id); const s = n.toLocaleString(); if (el.textContent !== s) { el.textContent = s; pop(el); } }

// ---- report ----
async function loadReport() {
  try {
    const r = await fetch("/api/report", {cache: "no-store"});
    if (r.status === 401) { location.href = "/login"; return; }
    const d = await r.json();
    setBalance(d.equity);
    addPoint(d.equity);
    const sp = Number(d.session_pnl || 0);
    const pnl = $("#pnl"); pnl.textContent = signed(sp); pnl.className = sp >= 0 ? "up" : "down";
    $("#start").textContent = money(Number(d.equity || 0) - sp);
    $("#mkt").textContent = d.market || "simulated";
    const at = d.agent_trades || {};
    $("#s-win").textContent = pct(at.win_rate);
    drawDonut(Number(at.win_rate) || 0);
    $("#s-trades").textContent = `${(at.won || 0).toLocaleString()} / ${(at.lost || 0).toLocaleString()}`;
    $("#s-proven").textContent = (d.proven_agents || 0).toLocaleString();
    rejected = Number(d.jev_rejected || 0);
    score("#h-n", Number(d.orders || 0));
    score("#j-n", rejected + liveCuts);
  } catch (e) { /* keep last numbers */ }
}

// ---- who made the money ----
async function loadOrg() {
  try {
    const org = await fetch("/api/org", {cache: "no-store"}).then(r => r.json());
    const deps = Object.entries(org.departments || {});
    if (!deps.length) return;
    const max = Math.max(1, ...deps.map(([, d]) => Math.abs(Number(d.pnl) || 0)));
    const box = $("#bars");
    if (!box.children.length) {
      box.innerHTML = deps.map(([k, d]) => `<div class="dd-bar" data-k="${esc(k)}"><span>${esc(d.name || k)}</span><div class="track"><i class="fill"></i></div><span class="v">–</span></div>`).join("");
    }
    deps.forEach(([k, d]) => {
      const row = box.querySelector(`[data-k="${CSS.escape(k)}"]`);
      if (!row) return;
      const v = Number(d.pnl) || 0;
      const fill = row.querySelector(".fill");
      fill.classList.toggle("neg", v < 0);
      requestAnimationFrame(() => { fill.style.width = (Math.abs(v) / max * 100).toFixed(1) + "%"; });
      row.querySelector(".v").textContent = signed(v);
    });
  } catch (e) { /* try again next round */ }
}

// ---- live log from the event stream ----
let liveCuts = 0;
const lastRow = {};
function row(html, cls) {
  const tb = $("#log");
  const tr = document.createElement("tr");
  tr.className = "new " + (cls || "");
  tr.innerHTML = html;
  tb.prepend(tr);
  while (tb.children.length > 14) tb.lastChild.remove();
}
const hhmmss = () => new Date().toLocaleTimeString([], {hour12: false});
function onEvent(e) {
  const now = performance.now();
  if (e.type === "order") {
    const side = e.qty >= 0 ? "BUY" : "SELL";
    $("#h-last").textContent = `${side} ${e.symbol} · score ${Number(e.score || 0).toFixed(2)}`;
    if (now - (lastRow.order || 0) < 400) return;
    lastRow.order = now;
    row(`<td>${hhmmss()}</td><td>${esc(e.symbol)}</td><td class="side ${side === "BUY" ? "buy" : "sell"}">${side}</td><td class="r">${Math.abs(Number(e.qty) || 0).toFixed(2)}</td><td class="r">${Number(e.price || 0).toFixed(2)}</td><td class="r">${Number(e.score || 0).toFixed(2)}</td>`);
  } else if (e.type === "risk") {
    liveCuts += 1; score("#j-n", rejected + liveCuts);
    $("#j-last").textContent = `${e.symbol}: ${e.reason || "cut down"}`;
    if (now - (lastRow.risk || 0) < 1500) return;
    lastRow.risk = now;
    const want = Math.abs(Number(e.wanted) || 0), ok = Math.abs(Number(e.allowed) || 0);
    row(`<td>${hhmmss()}</td><td>${esc(e.symbol)}</td><td class="side">CUT</td><td class="r">${want.toFixed(2)} → ${ok.toFixed(2)}</td><td class="r">–</td><td class="r">${esc((e.reason || "").slice(0, 18))}</td>`, "cut");
  }
}
function connect() {
  const es = new EventSource("/events");
  es.onmessage = m => { let e; try { e = JSON.parse(m.data); } catch (_) { return; } onEvent(e); };
  es.onerror = () => { es.close(); setTimeout(connect, 4000); };
}

loadReport(); loadOrg(); connect();
setInterval(loadReport, 3000);
setInterval(loadOrg, 8000);
