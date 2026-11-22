// Agents trading floor: 6 charts with entry/stop/target lines, agent terminal, conductor orb, account bar, toasts.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = (v, d = 2) => (v >= 0 ? "+" : "−") + "$" + Math.abs(v || 0).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const money = v => "$" + (v || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const px = v => v >= 1000 ? v.toFixed(1) : v >= 10 ? v.toFixed(2) : v >= 1 ? v.toFixed(4) : v.toFixed(5);
const hhmm = t => new Date(t * 1000).toISOString().slice(11, 19);
const COL = {conductor: "#f4f4f4", entry: "#ff5c00", scalper: "#facc15", hedge: "#a78bfa", risk: "#ff4d4d", exit: "#2fd37a", news: "#8a8a8a"};
const NAME = {conductor: "CONDUCTOR", entry: "ENTRY", scalper: "SCALPER", hedge: "HEDGE", risk: "RISK", exit: "EXIT", news: "NEWS"};
const LC = window.LightweightCharts;
const charts = {};
let D = null, seenToast = new Set(), lastPulse = null, pulseAt = 0, shownEq = null;

function legend() {
  $("#legend").innerHTML = Object.keys(COL).map(k => `<span data-a="${k}">${NAME[k]}</span>`).join("");
  document.querySelectorAll("#legend span").forEach(s => s.style.setProperty("--c", COL[s.dataset.a]));
}

function makeChart(c) {
  const box = document.createElement("div");
  box.className = "cd-chart";
  box.innerHTML = `<header><b>${esc(c.symbol)} <small>${esc(c.name)}</small></b><span class="px"></span></header>
    <div class="lc"></div><div class="foot"><span class="st">${esc(c.strategy)}</span><span class="score"></span></div><span class="badge">ENTRY</span>`;
  $("#wall").appendChild(box);
  const el = box.querySelector(".lc");
  const ch = LC.createChart(el, {height: el.clientHeight, layout: {background: {color: "transparent"}, textColor: "#6b6b6b", fontSize: 10},
    grid: {vertLines: {color: "rgba(255,255,255,.03)"}, horzLines: {color: "rgba(255,255,255,.03)"}}, crosshair: {mode: 0},
    rightPriceScale: {borderVisible: false, scaleMargins: {top: .08, bottom: .2}}, timeScale: {borderVisible: false, timeVisible: true, secondsVisible: false},
    handleScroll: false, handleScale: false});
  const o = {box, el, ch, lines: [], open: new Set(),
    candles: ch.addCandlestickSeries({upColor: "#2fd37a", downColor: "#ff4d4d", wickUpColor: "#2fd37a", wickDownColor: "#ff4d4d", borderVisible: false, priceFormat: {type: "price", precision: c.price < 10 ? 5 : 2, minMove: c.price < 10 ? 0.00001 : 0.01}}),
    vol: ch.addHistogramSeries({priceScaleId: "v", priceFormat: {type: "volume"}, lastValueVisible: false, priceLineVisible: false}),
    e9: ch.addLineSeries({color: "#ffd2a8", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false}),
    e21: ch.addLineSeries({color: "#5b8cff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false})};
  ch.priceScale("v").applyOptions({scaleMargins: {top: .82, bottom: 0}});
  new ResizeObserver(() => ch.applyOptions({width: el.clientWidth, height: el.clientHeight})).observe(el);
  return o;
}

function drawChart(c) {
  const o = charts[c.symbol] || (charts[c.symbol] = makeChart(c));
  o.candles.setData(c.bars.map(b => ({time: b.t, open: b.o, high: b.h, low: b.l, close: b.c})));
  o.vol.setData(c.bars.map(b => ({time: b.t, value: b.v, color: b.c >= b.o ? "rgba(47,211,122,.35)" : "rgba(255,77,77,.35)"})));
  o.e9.setData(c.bars.map((b, i) => c.ema9[i] == null ? null : {time: b.t, value: c.ema9[i]}).filter(Boolean));
  o.e21.setData(c.bars.map((b, i) => c.ema21[i] == null ? null : {time: b.t, value: c.ema21[i]}).filter(Boolean));
  o.lines.forEach(l => o.candles.removePriceLine(l)); o.lines = [];
  const add = (price, color, title, style = 0) => price != null && o.lines.push(o.candles.createPriceLine({price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: style === 0, title}));
  add(c.levels.hi20, "rgba(255,255,255,.25)", "", 2); add(c.levels.lo20, "rgba(255,255,255,.25)", "", 2);
  const now = new Set();
  c.trades.forEach(t => {
    now.add(t.id);
    if (t.kind === "hedge") { add(t.entry, "#a78bfa", "HEDGE " + t.side); return; }
    add(t.entry, "#ff5c00", "ENTRY " + t.side); add(t.sl, "#ff4d4d", t.be ? "SL (BE)" : "SL"); add(t.tp, "#2fd37a", "TP");
    if (!o.open.has(t.id) && D.prev) { o.box.classList.add("flash"); const b = o.box.querySelector(".badge"); b.textContent = "ENTRY " + t.side; b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop"); setTimeout(() => o.box.classList.remove("flash"), 1200); }
  });
  o.open = now;
  const first = c.bars[0].t;
  o.candles.setMarkers(c.marks.filter(m => m.t >= first).sort((a, b) => a.t - b.t).map(m => m.kind === "in"
    ? {time: m.t, position: m.side === "LONG" ? "belowBar" : "aboveBar", color: "#ff5c00", shape: m.side === "LONG" ? "arrowUp" : "arrowDown", text: m.side === "LONG" ? "BUY" : "SELL"}
    : {time: m.t, position: "inBar", color: m.won ? "#2fd37a" : "#ff4d4d", shape: "circle", text: ""}));
  const up = c.trades.reduce((a, t) => a + t.upnl, 0);
  o.box.querySelector(".px").innerHTML = `${px(c.price)} <span class="${c.change >= 0 ? "up" : "dn"}">${c.change >= 0 ? "+" : ""}${c.change.toFixed(2)}%</span>` +
    (c.trades.length ? ` <span class="${up >= 0 ? "up" : "dn"}">${usd(up)}</span>` : "");
  o.box.querySelector(".score").innerHTML = `score ${c.score}/10 ` + Array.from({length: 10}, (_, i) => `<i class="${i < c.score ? "on" : ""}"></i>`).join("");
  o.box.classList.toggle("win", c.trades.length > 0 && up > 0);
  o.box.classList.toggle("loss", c.trades.length > 0 && up < 0);
}

function terminal() {
  const box = $("#term");
  D.log.slice(-60).forEach(l => {
    const key = l.t + "|" + l.text;
    if (box.querySelector(`[data-k="${CSS.escape(key)}"]`)) return;
    const d = document.createElement("div");
    d.className = "cd-line"; d.dataset.k = key; d.style.setProperty("--c", COL[l.agent] || "#888");
    d.innerHTML = `<time>${hhmm(l.t)}</time><em>${NAME[l.agent] || l.agent} ${esc(l.type)}</em>${esc(l.text)}`;
    box.appendChild(d);
  });
  while (box.children.length > 60) box.removeChild(box.firstChild);
  $("#clock").textContent = hhmm(D.time) + " UTC (simulated)";
}

function toasts() {
  D.toasts.forEach(t => {
    const k = t.id + "|" + t.t;
    if (seenToast.has(k)) return; seenToast.add(k);
    if (!D.prev) return;
    const d = document.createElement("div");
    d.className = "cd-toast"; d.style.setProperty("--c", t.pnl >= 0 ? "#2fd37a" : "#ff4d4d");
    d.innerHTML = `<b>${usd(t.pnl)}</b>${esc(t.asset)} · ${t.r >= 0 ? "+" : ""}${t.r.toFixed(1)}R · ${t.mins} min · ${esc(t.strategy)}`;
    $("#toasts").appendChild(d); setTimeout(() => d.remove(), 3100);
    const o = charts[t.asset]; if (o) { o.box.classList.add(t.pnl >= 0 ? "win" : "loss"); }
  });
}

// Eased account numbers so they count instead of jumping.
function tween(to) {
  const from = shownEq == null ? to : shownEq, t0 = performance.now();
  const go = now => { const k = Math.min(1, (now - t0) / 700), e = 1 - Math.pow(1 - k, 3); shownEq = from + (to - from) * e; $("#k-eq").textContent = money(shownEq); $("#bar-eq") && ($("#bar-eq").textContent = money(shownEq)); if (k < 1) requestAnimationFrame(go); };
  requestAnimationFrame(go);
}

function kpis() {
  const a = D.account, s = D.stats, r = D.risk;
  tween(a.equity);
  const tot = a.equity - a.start;
  $("#k-eq-sub").textContent = `${usd(tot)} since start · ${a.open} open`;
  const kd = $("#k-day"); kd.textContent = usd(a.today); kd.className = a.today >= 0 ? "up" : "dn";
  $("#k-day-sub").textContent = `${r.daily_used_pct}% of the ${r.daily_loss_pct}% daily loss limit used`;
  $("#k-wr").textContent = s.win_rate == null ? "–" : s.win_rate + "%";
  $("#k-wr-sub").textContent = `${s.wins} of ${s.trades} · avg ${s.avg_r == null ? "–" : (s.avg_r >= 0 ? "+" : "") + s.avg_r + "R"}`;
  const kr = $("#k-risk");
  kr.textContent = r.killed ? "KILLED" : r.paused ? "PAUSED" : "ON";
  kr.className = r.killed ? "dn" : r.paused ? "o" : "up";
  $("#k-risk-sub").textContent = r.killed ? "daily loss hit, all closed for today" : r.paused ? `news: ${r.pause_reason}` : `next news: ${r.next_news.name} in ${r.next_news.in_min} min`;
  $("#bar").innerHTML = `<div class="live"><small>Practice account</small><b>LIVE SIM</b></div>
    <div><small>Balance</small><b>${money(a.balance)}</b></div><div><small>Equity</small><b id="bar-eq">${money(shownEq ?? a.equity)}</b></div>
    <div><small>Realised</small><b class="${a.realized >= 0 ? "up" : "dn"}">${usd(a.realized)}</b></div>
    <div><small>Open profit</small><b class="${a.unrealized >= 0 ? "up" : "dn"}">${usd(a.unrealized)}</b></div>
    <div><small>Margin used</small><b>${money(a.margin_used)}</b></div><div><small>Win rate</small><b>${s.win_rate == null ? "–" : s.win_rate + "%"}</b></div>
    <div><small>Open</small><b>${a.open} / ${r.max_open}</b></div><div><small>Fees paid</small><b>${money(s.fees)}</b></div>`;
}

function states() {
  const order = ["PROPOSED", "RISK_CHECK", "APPROVED", "REJECTED", "EXECUTING", "OPEN", "MANAGING", "HEDGED", "CLOSING", "CLOSED", "LOGGED", "REVIEWED"];
  $("#states").innerHTML = order.map((s, i) => `<div class="${s === "REJECTED" ? "rej" : ""}"><b>${(D.states[s] || 0).toLocaleString()}</b>${s.replace("_", " ")}</div>${i < order.length - 1 ? "<span>›</span>" : ""}`).join("");
  const R = Object.entries(D.rejects), max = Math.max(1, ...R.map(r => r[1]));
  $("#rejects").innerHTML = R.length ? R.map(([k, v]) => `<div class="pg-item"><span class="sym">${v}</span><span class="t">${esc(k)}<div class="pg-meter"><i class="dn"></i></div></span><span class="g">${Math.round(v / max * 100)}%</span></div>`).join("") : '<div class="pg-empty">Nothing vetoed yet.</div>';
  document.querySelectorAll("#rejects .pg-meter i").forEach((el, i) => { el.style.left = "0"; el.style.width = (R[i][1] / max * 100) + "%"; });
}

function rules() {
  const r = D.risk, a = D.account;
  const rows = [
    ["Risk per trade", `${r.risk_pct}% = ${money(a.equity * r.risk_pct / 100)}`, "up"],
    ["Size", "risk $ ÷ |entry − stop|", "g"],
    ["Daily loss limit", `${r.daily_loss_pct}% (${r.daily_used_pct}% used)`, r.daily_used_pct > r.daily_loss_pct * 0.7 ? "dn" : "up"],
    ["Max open trades", `${a.open} / ${r.max_open}`, a.open >= r.max_open ? "o" : "up"],
    ["Max correlation", `${r.max_corr} between open trades`, "up"],
    ["Propose only if", `score ≥ ${r.score_gate}/10`, "up"],
    ["News pause", r.paused ? `ON: ${r.pause_reason}` : `15 min before ${r.next_news.name}`, r.paused ? "o" : "up"],
    ["Kill switch", r.killed ? "HIT: all closed" : "armed at the daily limit", r.killed ? "dn" : "up"],
    ["Costs charged", `${r.fee_pct}% fee + ${r.slip_pct}% slip per side`, "g"],
  ];
  $("#rules").innerHTML = rows.map(([k, v, c]) => `<li><span>${k}</span><b class="${c}">${esc(v)}</b></li>`).join("");
}

function strategies() {
  const B = Object.values(D.stats.by_strategy);
  $("#strats").innerHTML = B.map(s => `<tr><td>${esc(s.name)}${D.benched && D.benched[s.name] ? ' <span class="pg-pill loss">BENCHED</span>' : ""}</td><td class="r">${s.n}</td>
    <td class="r">${s.n ? Math.round(s.wins / s.n * 100) : 0}%</td><td class="r ${s.r >= 0 ? "up" : "dn"}">${s.r >= 0 ? "+" : ""}${s.r.toFixed(1)}</td><td class="r ${s.pnl >= 0 ? "up" : "dn"}">${usd(s.pnl, 0)}</td></tr>`).join("") || '<tr><td colspan="5" class="g">No closed trades yet.</td></tr>';
  const s = D.stats;
  $("#stat-note").textContent = `Profit factor ${s.profit_factor ?? "–"} · expectancy ${s.expectancy == null ? "–" : usd(s.expectancy)} per trade · max drawdown ${s.max_drawdown_pct}% · worst day ${usd(s.worst_day, 0)}`;
  $("#agents").innerHTML = Object.entries(D.agents).map(([k, v]) => `<div class="pg-item"><span class="sym" data-a="${k}">${NAME[k]}</span><span class="t">${esc(v.split(": ")[1] || v)}</span><span class="g">${(D.agent_counts[k] || 0).toLocaleString()} msgs</span></div>`).join("");
  document.querySelectorAll("#agents .sym").forEach(el => el.style.color = COL[el.dataset.a]);
}

function closed() {
  $("#closed").innerHTML = D.closed.map(t => `<tr class="${t.won ? "win" : "loss"}"><td><b>${esc(t.asset)}</b>${t.kind === "hedge" ? ' <span class="pg-pill flat">HEDGE</span>' : ""}</td>
    <td>${t.side}</td><td class="hide-s g">${esc(t.strategy)}</td><td class="r ${t.pnl >= 0 ? "up" : "dn"}">${usd(t.pnl)}</td>
    <td class="r ${t.r >= 0 ? "up" : "dn"}">${t.r >= 0 ? "+" : ""}${t.r.toFixed(2)}</td><td class="r hide-s">${t.bars}</td><td class="g">${esc(t.why)}</td></tr>`).join("") ||
    '<tr><td colspan="7" class="g">No closed trades yet.</td></tr>';
}

function canvas(id) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px ui-monospace, Menlo, monospace"; g.textBaseline = "middle"; return [g, w, h];
}

function curve() {
  const [g, w, h] = canvas("#curve"), C = D.equity_curve; if (C.length < 2) return;
  const v = C.map(p => p.equity), lo = Math.min(...v, D.account.start), hi = Math.max(...v, D.account.start);
  const x = i => 8 + (w - 90) * i / (C.length - 1), y = q => h - 14 - (q - lo) / (hi - lo || 1) * (h - 28);
  g.strokeStyle = "#2a2a2a"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(8, y(D.account.start)); g.lineTo(w - 82, y(D.account.start)); g.stroke(); g.setLineDash([]);
  const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, "rgba(255,92,0,.35)"); grad.addColorStop(1, "rgba(255,92,0,0)");
  g.beginPath(); C.forEach((p, i) => i ? g.lineTo(x(i), y(p.equity)) : g.moveTo(x(i), y(p.equity))); g.lineTo(x(C.length - 1), h - 14); g.lineTo(x(0), h - 14); g.fillStyle = grad; g.fill();
  g.beginPath(); C.forEach((p, i) => i ? g.lineTo(x(i), y(p.equity)) : g.moveTo(x(i), y(p.equity))); g.strokeStyle = "#ff5c00"; g.lineWidth = 2; g.stroke();
  g.fillStyle = "#f4f4f4"; g.fillText(money(v[v.length - 1]), w - 78, y(v[v.length - 1]));
  g.fillStyle = "#6b6b6b"; g.fillText("start", w - 78, y(D.account.start) + 12);
}

// Conductor orb: a glowing node wired to the six charts. It pulses when it approves a trade.
function orb(now) {
  if (!D) return requestAnimationFrame(orb);
  const [g, w, h] = canvas("#orb"), cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 34;
  const k = Math.max(0, 1 - (now - pulseAt) / 1400), breathe = 1 + 0.04 * Math.sin(now / 600);
  D.charts.forEach((c, i) => {
    const a = -Math.PI / 2 + i * Math.PI / 3, nx = cx + Math.cos(a) * R, ny = cy + Math.sin(a) * R;
    const open = c.trades.length > 0, up = c.trades.reduce((s, t) => s + t.upnl, 0);
    g.strokeStyle = open ? "rgba(255,92,0,.6)" : "rgba(255,255,255,.08)"; g.lineWidth = open ? 1.6 : 1;
    g.beginPath(); g.moveTo(cx, cy); g.lineTo(nx, ny); g.stroke();
    if (open) { const f = (now / 900 + i / 6) % 1; g.fillStyle = "#ff5c00"; g.beginPath(); g.arc(cx + (nx - cx) * f, cy + (ny - cy) * f, 2.2, 0, 7); g.fill(); }
    g.fillStyle = open ? (up >= 0 ? "#2fd37a" : "#ff4d4d") : "#1c1c1c"; g.beginPath(); g.arc(nx, ny, 15, 0, 7); g.fill();
    g.strokeStyle = COL[c.agent]; g.lineWidth = 1.5; g.stroke();
    g.fillStyle = open ? "#000" : "#bbb"; g.textAlign = "center"; g.font = "600 9px ui-monospace, Menlo, monospace"; g.fillText(c.symbol.slice(0, 4), nx, ny);
  });
  const r0 = 26 * breathe + k * 10;
  const glow = g.createRadialGradient(cx, cy, 2, cx, cy, r0 * 2.6); glow.addColorStop(0, `rgba(255,140,60,${0.9})`); glow.addColorStop(.35, `rgba(255,92,0,${0.35 + k * 0.4})`); glow.addColorStop(1, "rgba(255,92,0,0)");
  g.fillStyle = glow; g.beginPath(); g.arc(cx, cy, r0 * 2.6, 0, 7); g.fill();
  if (k > 0) { g.strokeStyle = `rgba(255,92,0,${k})`; g.lineWidth = 2; g.beginPath(); g.arc(cx, cy, r0 + (1 - k) * 60, 0, 7); g.stroke(); }
  g.fillStyle = "#000"; g.font = "700 9px ui-monospace, Menlo, monospace"; g.fillText("CONDUCTOR", cx, cy); g.textAlign = "left";
  requestAnimationFrame(orb);
}

async function load() {
  let next;
  try { const r = await fetch("/api/conductor", {cache: "no-store"}); if (r.status === 401) { location.href = "/login"; return; } next = await r.json(); } catch (e) { return; }
  next.prev = !!D; D = next;
  if (lastPulse != null && D.pulse > lastPulse) pulseAt = performance.now();
  lastPulse = D.pulse;
  D.charts.forEach(drawChart);
  terminal(); toasts(); kpis(); states(); rules(); strategies(); closed(); curve();
}
legend(); load(); setInterval(load, 1600); requestAnimationFrame(orb);
addEventListener("resize", () => D && curve());
