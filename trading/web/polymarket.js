// Polymarket practice desk: profit by strategy, neg-risk sums, cross-book costs, maker band, paper trades.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = v => (v >= 0 ? "+" : "−") + "$" + Math.abs(v || 0).toLocaleString(undefined, {maximumFractionDigits: 2});
const NAMES = {neg_risk_squeeze: "Neg-risk squeeze", cross_book_arb: "Cross-book arb", maker_rebate: "Maker rebate"};
const COL = {neg_risk_squeeze: "#ff5c00", cross_book_arb: "#ffd2a8", maker_rebate: "#2fd37a"};
let D = null;

function canvas(id) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px ui-monospace, Menlo, monospace"; g.textBaseline = "middle"; return [g, w, h];
}

function curve() {
  const [g, w, h] = canvas("#curve"), C = D.curve; if (C.length < 2) return;
  const keys = Object.keys(NAMES), vals = C.flatMap(p => keys.map(k => p[k]).concat(p.pnl));
  const lo = Math.min(0, ...vals), hi = Math.max(1, ...vals), x = i => 6 + (w - 80) * i / (C.length - 1), y = v => h - 12 - (v - lo) / (hi - lo) * (h - 24);
  g.strokeStyle = "#1f1f1f"; g.beginPath(); g.moveTo(6, y(0)); g.lineTo(w - 74, y(0)); g.stroke();
  const draw = (k, col, lw) => { g.beginPath(); C.forEach((p, i) => i ? g.lineTo(x(i), y(p[k])) : g.moveTo(x(i), y(p[k]))); g.strokeStyle = col; g.lineWidth = lw; g.stroke(); g.fillStyle = col; g.fillText(usd(C[C.length - 1][k]), w - 70, y(C[C.length - 1][k])); };
  keys.forEach(k => draw(k, COL[k], 1.5)); draw("pnl", "#f4f4f4", 2.5);
  let lx = 8; [["pnl", "Total", "#f4f4f4"], ...keys.map(k => [k, NAMES[k], COL[k]])].forEach(([, n, c]) => { g.fillStyle = c; g.fillText("■ " + n, lx, 10); lx += g.measureText("■ " + n).width + 14; });
}

function split() {
  const [g, w, h] = canvas("#split"), keys = Object.keys(NAMES), tot = keys.reduce((a, k) => a + Math.max(0, D.pnl[k]), 0) || 1;
  const cx = w / 2, cy = h / 2 - 10, r = Math.min(w, h) / 2 - 30; let a = -Math.PI / 2;
  g.lineWidth = 20;
  keys.forEach(k => { const s = Math.max(0, D.pnl[k]) / tot * Math.PI * 2; g.beginPath(); g.arc(cx, cy, r, a, a + s); g.strokeStyle = COL[k]; g.stroke(); a += s; });
  g.textAlign = "center"; g.fillStyle = "#f4f4f4"; g.font = "700 18px ui-monospace, Menlo, monospace"; g.fillText(usd(D.total), cx, cy);
  g.font = "11px ui-monospace, Menlo, monospace";
  keys.forEach((k, i) => { g.fillStyle = COL[k]; g.fillText(`${NAMES[k]} ${D.count[k]}x`, cx, h - 40 + i * 14); });
  g.textAlign = "left";
}

function events() {
  const [g, w, h] = canvas("#events"), E = D.events, rh = (h - 20) / E.length, sx = v => 70 + (w - 130) * v / 1.15;
  E.forEach((e, i) => {
    let x0 = 0; const y0 = 12 + i * rh;
    e.outcomes.forEach((o, j) => { g.fillStyle = `rgba(255,${120 + j * 25},${40 + j * 30},${0.35 + 0.1 * (j % 3)})`; g.fillRect(sx(x0), y0, sx(x0 + o.ask) - sx(x0) - 1, rh - 8); x0 += o.ask; });
    const arb = 1 - e.sum >= D.rules.neg_risk_epsilon;
    g.fillStyle = "#8a8a8a"; g.fillText(e.id.replace("event-", "event "), 2, y0 + rh / 2 - 4);
    g.fillStyle = arb ? "#2fd37a" : e.sum < 1 ? "#ffd2a8" : "#8a8a8a"; g.fillText(`$${e.sum.toFixed(3)}${arb ? " ✓" : ""}`, sx(Math.max(e.sum, 1)) + 6, y0 + rh / 2 - 4);
  });
  g.strokeStyle = "#ff5c00"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(sx(1), 6); g.lineTo(sx(1), h - 6); g.stroke(); g.setLineDash([]);
  g.fillStyle = "#ff5c00"; g.fillText("$1.00", sx(1) - 18, h - 4);
}

function pairs() {
  const [g, w, h] = canvas("#pairs"), P = D.pairs, rh = (h - 20) / P.length, sx = v => 70 + (w - 130) * (v - 0.85) / 0.3;
  P.forEach((p, i) => {
    const y0 = 12 + i * rh, arb = 1 - p.cost >= D.rules.cross_book_min_spread;
    g.fillStyle = "rgba(255,92,0,.55)"; g.fillRect(sx(0.85), y0, Math.max(0, sx(Math.min(1.15, p.cost)) - sx(0.85)), rh - 8);
    g.fillStyle = "#8a8a8a"; g.fillText(p.id.replace("pair-", "pair "), 2, y0 + rh / 2 - 4);
    g.fillStyle = arb ? "#2fd37a" : "#8a8a8a"; g.fillText(`${p.yes_a.toFixed(2)} + ${p.no_b.toFixed(2)} = ${p.cost.toFixed(3)}${arb ? " ✓" : ""}`, sx(Math.min(1.15, p.cost)) + 6, y0 + rh / 2 - 4);
  });
  g.strokeStyle = "#ff5c00"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(sx(1), 6); g.lineTo(sx(1), h - 6); g.stroke(); g.setLineDash([]);
  g.fillStyle = "#ff5c00"; g.fillText("$1.00", sx(1) - 18, h - 4);
}

function band() {
  const [g, w, h] = canvas("#band"), [lo, hi] = D.rules.maker_band, sx = v => 20 + (w - 40) * v, mid = h / 2;
  g.fillStyle = "rgba(255,92,0,.12)"; g.fillRect(sx(lo), 10, sx(hi) - sx(lo), h - 30);
  g.strokeStyle = "#2a2a2a"; g.beginPath(); g.moveTo(sx(0), mid); g.lineTo(sx(1), mid); g.stroke();
  // fee curve p(1-p): the bigger, the more rebate
  g.beginPath(); for (let p = 0; p <= 1.001; p += 0.02) { const yy = h - 22 - p * (1 - p) * 4 * (h - 40); p ? g.lineTo(sx(p), yy) : g.moveTo(sx(p), yy); }
  g.strokeStyle = "rgba(255,92,0,.5)"; g.stroke();
  D.binaries.forEach((b, i) => { g.beginPath(); g.arc(sx(b.mid), mid + ((i % 3) - 1) * 10, b.in_band ? 6 : 4, 0, 7); g.fillStyle = b.in_band ? "#ff5c00" : "#555"; g.fill(); });
  g.fillStyle = "#8a8a8a"; [0, .25, .5, .75, 1].forEach(v => g.fillText(Math.round(v * 100) + "c", sx(v) - 8, h - 6));
}

function table() {
  $("#trades").innerHTML = D.trades.map(t => `<tr><td>${new Date(t.ts * 1000).toLocaleTimeString([], {hour12: false})}</td>
    <td><span class="o">${NAMES[t.strategy]}</span></td><td class="g">${t.legs.map(l => `${l.side} ${l.qty.toFixed(0)} ${esc(l.instrument)} @ ${l.price}`).join("<br>")}</td>
    <td class="r ${t.pnl >= 0 ? "up" : "dn"}">${usd(t.pnl)}</td></tr>`).join("") || '<tr><td colspan="4" class="g">No edge found yet. Most of the time the books are fair, which is normal.</td></tr>';
}

async function load() {
  try { const r = await fetch("/api/polymarket", {cache: "no-store"}); if (r.status === 401) { location.href = "/login"; return; } D = await r.json(); } catch (e) { return; }
  const k = $("#k-pnl"); k.textContent = usd(D.total); k.className = D.total >= 0 ? "up" : "dn";
  const n = Object.values(D.count).reduce((a, b) => a + b, 0);
  $("#k-n").textContent = n.toLocaleString(); $("#k-n-sub").textContent = Object.keys(NAMES).map(s => `${D.count[s]} ${NAMES[s].toLowerCase()}`).join(" · ");
  $("#k-scan").textContent = D.scanned.toLocaleString(); $("#k-tick").textContent = `book update ${D.tick} · every 2 seconds`;
  $("#k-depth").textContent = "$" + (D.rules.min_depth_usdc / 1000).toFixed(0) + "k";
  $("#k-rules").textContent = `min book depth · max $${(D.rules.max_trade_usdc / 1000).toFixed(0)}k per trade`;
  curve(); split(); events(); pairs(); band(); table();
}
addEventListener("resize", () => D && (curve(), split(), events(), pairs(), band()));
load(); setInterval(load, 2500);
