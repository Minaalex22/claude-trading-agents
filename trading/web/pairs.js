// Pairs trading toolkit: Engle-Granger, Ornstein-Uhlenbeck half-life, Kalman hedge ratio, z-score rule, backtest.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const pct = v => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(1) + "%";
let D = null, key = "sim";

function canvas(id) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px ui-monospace, Menlo, monospace"; g.textBaseline = "middle"; return [g, w, h];
}

// One plotting helper: series = [{v: [...], color, width, dash}], with a train/test split line.
function plot(id, series, opt = {}) {
  const [g, w, h] = canvas(id), L = 8, T = 22, B = opt.bottom ?? 40, Rm = opt.right ?? 8;
  const all = series.flatMap(s => s.v.filter(v => v != null)).concat(opt.extra || []);
  const lo = opt.lo ?? Math.min(...all), hi = opt.hi ?? Math.max(...all), n = Math.max(...series.map(s => s.v.length));
  const x = i => L + (w - L - Rm) * i / Math.max(1, n - 1), y = v => T + (h - T - B) * (1 - (v - lo) / (hi - lo || 1));
  if (opt.split != null) {
    g.fillStyle = "rgba(255,255,255,.025)"; g.fillRect(x(opt.split), T - 6, w - Rm - x(opt.split), h - T - B + 12);
    g.strokeStyle = "#333"; g.setLineDash([3, 4]); g.beginPath(); g.moveTo(x(opt.split), T - 6); g.lineTo(x(opt.split), h - B + 6); g.stroke(); g.setLineDash([]);
    g.fillStyle = "#6b6b6b"; g.fillText("train", L + 2, h - B + 16); g.fillText("test (unseen)", x(opt.split) + 4, h - B + 16);
  }
  (opt.hlines || []).forEach(([v, c, label]) => { g.strokeStyle = c; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(L, y(v)); g.lineTo(w - Rm, y(v)); g.stroke(); g.setLineDash([]); if (label) { g.fillStyle = c; g.fillText(label, w - Rm - g.measureText(label).width, y(v) - 7); } });
  series.forEach(s => {
    g.beginPath(); let on = false;
    s.v.forEach((v, i) => { if (v == null) { on = false; return; } on ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)); on = true; });
    g.strokeStyle = s.color; g.lineWidth = s.width || 1.2; g.setLineDash(s.dash || []); g.stroke(); g.setLineDash([]);
  });
  let lx = L + 2; (opt.legend || []).forEach(([t, c]) => { g.fillStyle = c; g.fillText(t, lx, 9); lx += g.measureText(t).width + 14; });
  return {g, x, y, w, h};
}

function eg() {
  const e = D.eg, split = Math.floor(D.split / D.step);
  plot("#eg", [{v: D.ly, color: "#ffb020", width: 1.3}, {v: D.fit, color: "#5b8cff", width: 1.3}],
    {split, legend: [[`ln ${D.y_name}`, "#ffb020"], [`a + β·ln ${D.x_name}`, "#5b8cff"]]});
  $("#eg-chip").textContent = `β̂ ${e.beta.toFixed(2)} · ADF ${e.adf.toFixed(2)}`;
  $("#eg-read").textContent = e.cointegrated
    ? `Yes, tied together: ADF ${e.adf} is below the ${e.level} line (${e.crit[e.level]}). Hedge ratio ${e.beta.toFixed(3)}: for every 1 of ${D.y_name}, hold ${e.beta.toFixed(2)} of ${D.x_name} the other way.`
    : `Not tied together in training: ADF ${e.adf} is above the 5% line (${e.crit["5%"]}). The gap may never close, so the screen says skip it.`;
}

function ou() {
  const o = D.ou, [g, w, h] = canvas("#ou"), L = 30, T = 22, B = 40, days = Math.max(30, Math.ceil((o.half_life || 20) * 4));
  const x = d => L + (w - L - 10) * d / days, y = v => T + (h - T - B) * (1 - v);
  g.strokeStyle = "#1f1f1f"; g.beginPath(); g.moveTo(L, y(0)); g.lineTo(w - 10, y(0)); g.moveTo(L, y(0)); g.lineTo(L, y(1)); g.stroke();
  if (o.theta) {
    g.beginPath(); for (let d = 0; d <= days; d += days / 120) { const v = Math.exp(-o.theta * d); d ? g.lineTo(x(d), y(v)) : g.moveTo(x(d), y(v)); }
    g.strokeStyle = "#2fd37a"; g.lineWidth = 2; g.stroke(); g.lineWidth = 1;
    const hl = o.half_life; g.strokeStyle = "#666"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(L, y(.5)); g.lineTo(x(hl), y(.5)); g.lineTo(x(hl), y(0)); g.stroke(); g.setLineDash([]);
    g.fillStyle = "#ffb020"; g.beginPath(); g.arc(x(hl), y(.5), 4, 0, 7); g.fill(); g.fillStyle = "#ddd"; g.fillText(`half-life = ${hl}d`, x(hl) + 8, y(.5) - 10);
    if (o.true) { g.strokeStyle = "rgba(255,255,255,.35)"; g.setLineDash([2, 3]); g.beginPath(); g.moveTo(x(o.true), y(1)); g.lineTo(x(o.true), y(0)); g.stroke(); g.setLineDash([]); g.fillStyle = "#888"; g.fillText(`true ${o.true}d`, x(o.true) + 4, y(.95)); }
  } else { g.fillStyle = "#ff4d4d"; g.fillText("No mean reversion found (φ ≥ 1)", L + 10, h / 2); }
  g.fillStyle = "#6b6b6b"; g.fillText("E[gap] after a shock: e^(−θt)", L + 2, 9); g.fillText("days since shock →", L + 2, h - B + 16);
  $("#ou-chip").textContent = o.half_life ? `half-life ${o.half_life}d${o.true ? ` (true ${o.true}d)` : ""}` : "no mean reversion";
  $("#ou-read").textContent = o.half_life
    ? `A gap halves in about ${o.half_life} days (φ ${o.phi}). On the unseen half the static spread's half-life was ${o.test_half_life == null ? "infinite" : o.test_half_life + " days"}${o.test_half_life == null || o.test_half_life > 3 * o.half_life ? ", so the old hedge ratio stopped working: that is what the Kalman filter is for." : ", so it held up."}`
    : "The spread does not pull back, so there is nothing to trade.";
}

function kf() {
  const k = D.kalman, split = Math.floor(D.split / D.step), S = [];
  if (k.true_beta) S.push({v: k.true_beta, color: "#f4f4f4", width: 1.4});
  S.push({v: k.beta, color: "#a78bfa", width: 1.8}, {v: k.beta.map(() => D.eg.beta), color: "#888", dash: [5, 4]});
  plot("#kf", S, {split, legend: [...(k.true_beta ? [["true β", "#f4f4f4"]] : []), ["Kalman β", "#a78bfa"], ["static OLS β", "#888"]]});
  $("#kf-chip").textContent = k.rmse ? `β RMSE ${k.rmse.kalman} vs OLS ${k.rmse.ols}` : `β now ${k.beta[k.beta.length - 1].toFixed(3)}`;
  $("#kf-read").textContent = (k.rmse ? `On the unseen half the Kalman β was ${k.rmse.kalman} off the truth on average; the frozen OLS β was ${k.rmse.ols} off. ` : `The hedge ratio moved from ${k.beta[split].toFixed(3)} to ${k.beta[k.beta.length - 1].toFixed(3)} on the unseen half. `) +
    `Q ${k.q.toExponential(1)} and R ${k.r.toExponential(1)} came from the training half only.`;
}

function zs() {
  const z = D.z, split = Math.floor(D.split / D.step), clip = v => v == null ? null : Math.max(-5, Math.min(5, v));
  const p = plot("#zs", [{v: z.kalman.map(clip), color: "#ff8a3d", width: 1.1}], {split, lo: -5, hi: 5,
    hlines: [[z.in, "rgba(255,255,255,.4)", "+2σ"], [-z.in, "rgba(255,255,255,.4)", "−2σ"], [z.stop, "rgba(255,77,77,.5)", "stop"], [-z.stop, "rgba(255,77,77,.5)", ""], [0, "#2a2a2a", ""]],
    legend: [["rolling z-score (Kalman spread)", "#ff8a3d"], ["○ entry", "#f4f4f4"]]});
  D.backtest.kalman.marks.forEach(m => {
    const i = Math.floor((m.i + D.split) / D.step); if (m.z == null) return;
    p.g.strokeStyle = m.pos !== 0 ? "#f4f4f4" : m.why === "stop" ? "#ff4d4d" : "#2fd37a"; p.g.lineWidth = 1.5;
    p.g.beginPath(); p.g.arc(p.x(i), p.y(clip(m.z)), 3.5, 0, 7); p.g.stroke(); p.g.lineWidth = 1;
  });
  const b = D.backtest;
  $("#zs-chip").textContent = `Sharpe ${b.ols.sharpe} OLS · ${b.kalman.sharpe} KF`;
  $("#zs-read").textContent = `Test half: ${b.kalman.trades} Kalman trades (${b.kalman.win_rate ?? "–"}% won, ${pct(b.kalman.total)}) vs ${b.ols.trades} static trades (${b.ols.win_rate ?? "–"}% won, ${pct(b.ols.total)}). White circles are entries, green take-profits, red stops.`;
}

function backtest() {
  const b = D.backtest;
  plot("#bt", [{v: b.ols.curve, color: "#888", width: 1.6}, {v: b.kalman.curve, color: "#ff5c00", width: 2}],
    {hlines: [[1, "#2a2a2a", "$1"]], extra: [1], bottom: 18, legend: [["static OLS", "#888"], ["Kalman", "#ff5c00"]]});
  $("#cost").textContent = `${b.cost_per_leg_pct}% cost per leg per side`;
  const row = (n, f) => `<tr><td class="g">${n}</td><td class="r">${f(b.ols)}</td><td class="r">${f(b.kalman)}</td></tr>`;
  const cls = v => v >= 0 ? "up" : "dn";
  $("#res").innerHTML = row("Return", r => `<span class="${cls(r.total)}">${pct(r.total)}</span>`) + row("Sharpe", r => `<span class="${cls(r.sharpe)}">${r.sharpe}</span>`) +
    row("Max drawdown", r => `<span class="dn">${(r.max_dd * 100).toFixed(1)}%</span>`) + row("Trades", r => r.trades) + row("Won", r => r.win_rate == null ? "–" : r.win_rate + "%");
}

function kpis() {
  const e = D.eg, o = D.ou, b = D.backtest;
  const k1 = $("#k-eg"); k1.textContent = e.cointegrated ? "YES" : "NO"; k1.className = e.cointegrated ? "up" : "dn";
  $("#k-eg-sub").textContent = `ADF ${e.adf} vs ${e.crit["5%"]} at 5%`;
  $("#k-hl").textContent = o.half_life ? o.half_life + " days" : "none";
  $("#k-hl-sub").textContent = o.true ? `true ${o.true} days` : `φ ${o.phi}`;
  [["ols", "#k-ols"], ["kalman", "#k-kf"]].forEach(([n, id]) => { const el = $(id); el.textContent = pct(b[n].total); el.className = b[n].total >= 0 ? "up" : "dn"; $(id + "-sub").textContent = `Sharpe ${b[n].sharpe} · ${b[n].trades} trades`; });
  const bad = D.verdict.some(v => /Not |lost|too long/.test(v));
  const v = $("#verdict"); v.className = "pg-card c12 pt-verdict " + (bad ? "bad" : "good");
  v.innerHTML = D.verdict.map(t => `<p>${esc(t)}</p>`).join("");
  $("#src").textContent = `${D.label} · ${D.n} days · fit on first ${D.split}, trade last ${D.n - D.split} · data: ${D.source}`;
  $("#pairs").innerHTML = D.pairs.map(p => `<button type="button" data-k="${esc(p.key)}" class="${p.key === D.key ? "on" : ""}">${esc(p.label)}</button>`).join("");
}

function draw() { kpis(); eg(); ou(); kf(); zs(); backtest(); }

async function load() {
  try { const r = await fetch("/api/pairs?pair=" + encodeURIComponent(key), {cache: "no-store"}); if (r.status === 401) { location.href = "/login"; return; } if (!r.ok) return; D = await r.json(); } catch (e) { return; }
  draw();
}
$("#pairs").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { key = b.dataset.k; load(); } });
addEventListener("resize", () => D && draw());
load();
