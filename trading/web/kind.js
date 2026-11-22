// One page per market type (/crypto, /forex, /commodities, /indices, /shares). Shared parts: the board,
// candle chart, outlook, volume profile, moves and our desk's trades. Each type also gets its own two panels.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = x => x == null || isNaN(x) ? "–" : Math.abs(x) >= 1000 ? x.toLocaleString(undefined, {maximumFractionDigits: 2}) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(4);
const pc = x => x == null || isNaN(x) ? "–" : (x >= 0 ? "+" : "") + x.toFixed(2) + "%";
const money = v => (v >= 0 ? "+" : "−") + "$" + Math.abs(v || 0).toLocaleString(undefined, {maximumFractionDigits: 2});
const LINE = ["#ff5c00", "#ffd2a8", "#f4f4f4", "#2fd37a", "#8a8a8a"];

const KINDS = {
  crypto: {kind: "crypto", title: "Crypto", sub: "24/7 markets", desc: "Bitcoin and the big coins. Crypto never closes, moves fast and moves together, so momentum and volatility matter most.",
    a: "Momentum heat", b: "How wild each coin is"},
  forex: {kind: "forex", title: "Forex", sub: "currencies", desc: "The big currency pairs. A pair is one currency against another, so the page shows which currencies are strong and which trading sessions are open.",
    a: "Pairs rebased to 100", b: "Currency strength and sessions"},
  commodities: {kind: "commodity", title: "Gold and commodities", sub: "metals and energy", desc: "Gold, silver, oil, gas and copper. Gold is where money hides when people are scared; copper and oil rise when the economy grows.",
    a: "Rebased to 100 over 40 days", b: "Ratios traders watch"},
  indices: {kind: "index", title: "Indices", sub: "whole stock markets", desc: "The S&P 500, Nasdaq, Dow, ASX 200 and DAX: each one is a whole market in one number, so they show the world's mood.",
    a: "Rebased to 100 over 40 days", b: "By region"},
  shares: {kind: "share", title: "Shares", sub: "single companies", desc: "Apple, Nvidia, Tesla, Microsoft and Amazon: the companies our desk trades most. See if they move together and how the desk is doing in each.",
    a: "Rebased to 100 over 40 days", b: "Do they move together?"},
};
const K = KINDS[location.pathname.replace(/^\//, "").split("/")[0]] || KINDS.crypto;
document.title = `ATS Matrix · ${K.title}`;
$("#k-title").innerHTML = `<b>${esc(K.title)}</b>`; $("#k-sub").textContent = K.sub; $("#k-desc").textContent = K.desc;
$("#sa-title").innerHTML = `${esc(K.a)}`; $("#sb-title").innerHTML = `${esc(K.b)}`;
document.querySelectorAll(`.ats-nav a[href="${location.pathname}"]`).forEach(a => a.classList.add("on"));

let ALL = [], P = [], picked = null, tf = "1h", TR = {by_symbol: {}};

function canvas(id) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px ui-monospace, Menlo, monospace"; g.textBaseline = "middle"; return [g, w, h];
}
function spark(c, vals) {
  const dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight; c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); if (vals.length < 2) return;
  const lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1, up = vals[vals.length - 1] >= vals[0];
  g.beginPath(); vals.forEach((v, i) => { const x = i * w / (vals.length - 1), y = h - 3 - (v - lo) / sp * (h - 6); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.strokeStyle = up ? "#2fd37a" : "#ff4d4d"; g.lineWidth = 1.5; g.stroke();
}
const returns = s => s.slice(1).map((v, i) => v / s[i] - 1);
const stdev = a => { const m = a.reduce((x, y) => x + y, 0) / (a.length || 1); return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length || 1)); };
function corr(a, b) {
  const n = Math.min(a.length, b.length); if (n < 3) return 0;
  a = a.slice(-n); b = b.slice(-n);
  const ma = a.reduce((x, y) => x + y) / n, mb = b.reduce((x, y) => x + y) / n;
  let s = 0, sa = 0, sb = 0; for (let i = 0; i < n; i++) { s += (a[i] - ma) * (b[i] - mb); sa += (a[i] - ma) ** 2; sb += (b[i] - mb) ** 2; }
  return sa && sb ? s / Math.sqrt(sa * sb) : 0;
}

// ---------- board ----------
function board() {
  $("#tiles").innerHTML = P.map(p => `<div class="pg-tile${p.symbol === picked ? " on" : ""}" data-s="${esc(p.symbol)}">
    <div class="top"><span class="o">${esc(p.symbol)}</span><span class="${p.change_1d >= 0 ? "up" : "dn"}">${pc(p.change_1d)}</span></div>
    <div class="nm">${esc(p.name)}</div><div class="px">${fmt(p.price)}</div><canvas></canvas></div>`).join("");
  $("#tiles").querySelectorAll(".pg-tile").forEach(t => spark(t.querySelector("canvas"), (P.find(p => p.symbol === t.dataset.s) || {}).spark || []));
}
$("#tiles").addEventListener("click", e => { const t = e.target.closest(".pg-tile"); if (t) { picked = t.dataset.s; board(); detail(); } });

// ---------- candle chart ----------
const LC = window.LightweightCharts; let ch = null;
function makeChart() {
  if (!LC || ch) return;
  const c = LC.createChart($("#chart"), {height: $("#chart").clientHeight, layout: {background: {color: "transparent"}, textColor: "#8a8a8a"},
    grid: {vertLines: {color: "rgba(255,255,255,.04)"}, horzLines: {color: "rgba(255,255,255,.04)"}}, crosshair: {mode: 0},
    timeScale: {timeVisible: true, borderColor: "#1f1f1f"}, rightPriceScale: {borderColor: "#1f1f1f"}});
  ch = {c, candles: c.addCandlestickSeries({upColor: "#2fd37a", downColor: "#ff4d4d", wickUpColor: "#2fd37a", wickDownColor: "#ff4d4d", borderVisible: false}),
    vol: c.addHistogramSeries({priceScaleId: "v", priceFormat: {type: "volume"}}),
    ma20: c.addLineSeries({color: "#ff5c00", lineWidth: 1, priceLineVisible: false}), ma50: c.addLineSeries({color: "#ffd2a8", lineWidth: 1, priceLineVisible: false})};
  c.priceScale("v").applyOptions({scaleMargins: {top: 0.82, bottom: 0}});
  new ResizeObserver(() => c.applyOptions({width: $("#chart").clientWidth})).observe($("#chart"));
}
$("#tf").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { tf = b.dataset.tf; detail(); } });

async function detail() {
  if (!picked) return;
  let d; try { d = await (await fetch(`/api/markets/${encodeURIComponent(picked)}?tf=${tf}`, {cache: "no-store"})).json(); } catch (e) { return; }
  if (!d.candles) return;
  $("#ch-title").innerHTML = `${esc(d.product.name)} <span>${esc(picked)} · candles, 20 and 50 averages, volume · ${esc(d.source)}</span>`;
  $("#tf").innerHTML = (d.timeframes || []).map(t => `<button class="${t === d.timeframe ? "on" : ""}" data-tf="${t}">${t}</button>`).join("");
  makeChart();
  if (ch) {
    const c = d.candles, I = d.indicators || {};
    ch.candles.setData(c.map(x => ({time: x.t, open: x.o, high: x.h, low: x.l, close: x.c})));
    ch.vol.setData(c.map(x => ({time: x.t, value: x.v, color: x.c >= x.o ? "rgba(47,211,122,.35)" : "rgba(255,77,77,.35)"})));
    const line = k => c.map((x, i) => (I[k] || [])[i] == null ? null : {time: x.t, value: I[k][i]}).filter(Boolean);
    ch.ma20.setData(line("ma20")); ch.ma50.setData(line("ma50")); ch.c.timeScale().fitContent();
  }
  outlook(d); profile(d);
}

function outlook(d) {
  const o = (d.outlook || {})[d.timeframe] || Object.values(d.outlook || {})[0];
  $("#ol-tf").textContent = `${d.timeframe} · what the desk's rules say`;
  if (!o) { $("#outlook").innerHTML = '<div class="pg-empty">No outlook yet.</div>'; return; }
  const dir = o.direction === "up" ? ["Leaning up", "up"] : o.direction === "down" ? ["Leaning down", "dn"] : ["No clear way", "o"];
  $("#outlook").innerHTML = `<div class="kd-dir ${dir[1]}">${dir[0]}</div>
    <div class="kd-ol"><div><small>Confidence</small><b>${Math.round((o.confidence || 0) * 100)}%</b></div><div><small>Reward / risk</small><b>${o.reward_risk ?? "–"}</b></div>
    <div><small>Stop loss</small><b class="dn">${fmt(o.stop_loss)}</b></div><div><small>Take profit</small><b class="up">${fmt(o.take_profit)}</b></div></div>
    ${o.not_worth_the_risk ? '<p class="pg-note dn">Not worth the risk right now.</p>' : ""}${o.warning ? `<p class="pg-note o">${esc(o.warning)}</p>` : ""}
    <ul class="kd-why">${(o.reasons || []).concat(d.why || []).slice(0, 5).map(r => `<li>${esc(r)}</li>`).join("")}</ul>
    <p class="pg-note">Practice signal only. Real money stays locked.</p>`;
}

function profile(d) {
  const [g, w, h] = canvas("#vp"), L = ((d.volume_profile || {}).levels || []).slice().sort((a, b) => b.price - a.price);
  if (!L.length) return;
  const mx = Math.max(...L.map(l => l.volume)), rh = (h - 4) / L.length, poc = L.reduce((a, b) => b.volume > a.volume ? b : a);
  L.forEach((l, i) => { g.fillStyle = l === poc ? "#ff5c00" : "rgba(255,92,0,.35)"; g.fillRect(64, 2 + i * rh, (w - 70) * l.volume / mx, Math.max(1, rh - 1)); });
  g.fillStyle = "#8a8a8a"; g.fillText(fmt(L[0].price), 2, 8); g.fillText(fmt(L[L.length - 1].price), 2, h - 6);
  g.fillStyle = "#ff5c00"; g.fillText(fmt(poc.price), 2, 2 + L.indexOf(poc) * rh + rh / 2);
  $("#vp-sub").textContent = `busiest price ${fmt(poc.price)}`;
}

// ---------- shared: moves ----------
function moves() {
  const [g, w, h] = canvas("#moves"), keys = [["change_5m", "5m"], ["change_1d", "1d"], ["change_40d", "40d"]];
  const mx = Math.max(0.5, ...P.flatMap(p => keys.map(([k]) => Math.abs(p[k] || 0)))), rowH = (h - 18) / P.length, mid = 60 + (w - 60) / 2;
  P.forEach((p, i) => {
    g.fillStyle = "#ff5c00"; g.fillText(p.symbol, 2, 14 + i * rowH + rowH / 2);
    keys.forEach(([k], j) => { const v = p[k] || 0, bw = (w - 70) / 2 * v / mx, y = 14 + i * rowH + j * (rowH / 3.4) + 3;
      g.fillStyle = v >= 0 ? ["rgba(47,211,122,.45)", "rgba(47,211,122,.7)", "#2fd37a"][j] : ["rgba(255,77,77,.45)", "rgba(255,77,77,.7)", "#ff4d4d"][j];
      g.fillRect(bw >= 0 ? mid : mid + bw, y, Math.abs(bw), rowH / 3.4 - 2); });
  });
  g.fillStyle = "#8a8a8a"; g.fillText("light 5m · mid 1d · solid 40d", 60, 6);
  g.strokeStyle = "#2a2a2a"; g.beginPath(); g.moveTo(mid, 12); g.lineTo(mid, h); g.stroke();
}

// ---------- shared: our desk ----------
function desk() {
  const rows = P.map(p => [p.symbol, TR.by_symbol[p.symbol]]).filter(([, v]) => v);
  $("#desk").innerHTML = rows.length ? `<table class="pg-tbl"><thead><tr><th>Market</th><th class="r">Trades</th><th class="r">Won</th><th class="r">Profit</th></tr></thead><tbody>${
    rows.map(([s, v]) => `<tr><td class="o">${esc(s)}</td><td class="r">${v.trades}</td><td class="r">${Math.round(v.win_rate * 100)}%</td><td class="r ${v.pnl >= 0 ? "up" : "dn"}">${money(v.pnl)}</td></tr>`).join("")}</tbody></table>
    <p class="pg-note"><a href="/trades">See every trade</a></p>` : '<div class="pg-empty">Our practice desk doesn\'t trade these markets yet. It trades the symbols on the News page.</div>';
}

// ---------- rebased lines (used by several types) ----------
function rebased(title, note) {
  const [g, w, h] = canvas("#sa"), S = P.map(p => (p.spark || []).map(v => v / (p.spark[0] || 1) * 100));
  const all = S.flat(); if (!all.length) return;
  const lo = Math.min(...all), hi = Math.max(...all), sp = hi - lo || 1, n = Math.max(...S.map(s => s.length));
  const x = i => 6 + (w - 96) * i / (n - 1), y = v => h - 10 - (v - lo) / sp * (h - 20);
  g.strokeStyle = "#1f1f1f"; g.beginPath(); g.moveTo(6, y(100)); g.lineTo(w - 90, y(100)); g.stroke();
  S.forEach((s, k) => { g.beginPath(); s.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v))); g.strokeStyle = LINE[k % 5]; g.lineWidth = P[k].symbol === picked ? 2.5 : 1.3; g.stroke();
    g.fillStyle = LINE[k % 5]; g.fillText(`${P[k].symbol} ${(s[s.length - 1] - 100).toFixed(1)}%`, w - 86, y(s[s.length - 1])); });
  $("#sa-note").textContent = note || "Every line starts at 100, so you can compare markets with very different prices.";
}

// ---------- the type-specific panels ----------
const SPECIAL = {
  crypto() {
    const [g, w, h] = canvas("#sa"), cols = [["change_5m", "5 min"], ["change_1d", "1 day"], ["change_40d", "40 days"]];
    const cw = (w - 60) / 3, rh = (h - 24) / P.length;
    cols.forEach(([, l], j) => { g.fillStyle = "#8a8a8a"; g.fillText(l, 60 + j * cw + 6, 10); });
    P.forEach((p, i) => { g.fillStyle = "#ff5c00"; g.fillText(p.symbol, 4, 24 + i * rh + rh / 2);
      cols.forEach(([k], j) => { const v = p[k] || 0, a = Math.min(1, Math.abs(v) / (j === 0 ? 1 : j === 1 ? 6 : 25));
        g.fillStyle = `rgba(${v >= 0 ? "47,211,122" : "255,77,77"},${0.12 + a * 0.75})`; g.fillRect(60 + j * cw + 2, 22 + i * rh, cw - 4, rh - 4);
        g.fillStyle = "#f4f4f4"; g.fillText(pc(v), 60 + j * cw + 10, 22 + i * rh + rh / 2 - 2); }); });
    $("#sa-note").textContent = "Brighter green or red means a bigger move. When every coin is the same colour, crypto is moving as one.";
    const vol = P.map(p => [p.symbol, stdev(returns(p.spark || [])) * 100]).sort((a, b) => b[1] - a[1]), mx = Math.max(...vol.map(v => v[1]), 0.01);
    $("#sb").innerHTML = vol.map(([s, v]) => `<div class="kd-strength"><span class="o">${esc(s)}</span><div class="pg-meter"><i class="dn" data-l="0" data-w="${(v / mx * 100).toFixed(0)}"></i></div><span class="r">${v.toFixed(2)}%</span></div>`).join("")
      + '<div class="kd-sessions"><div class="open"><b>Open</b>24 hours</div><div class="open"><b>Open</b>7 days</div><div><b>Weekend</b>thin, jumpy</div><div><b>News</b>moves fast</div></div>';
    $("#sb-note").textContent = "Typical move per step. Wilder coins need smaller positions.";
  },
  forex() {
    rebased();
    const ccy = {}; const add = (c, v) => { (ccy[c] = ccy[c] || []).push(v); };
    P.forEach(p => { const b = p.symbol.slice(0, 3), q = p.symbol.slice(3), v = p.change_1d || 0; add(b, v); add(q, -v); });
    const st = Object.entries(ccy).map(([c, v]) => [c, v.reduce((a, b) => a + b, 0) / v.length]).sort((a, b) => b[1] - a[1]);
    const mx = Math.max(0.1, ...st.map(s => Math.abs(s[1])));
    const now = new Date().getUTCHours(), open = (a, b) => a < b ? now >= a && now < b : now >= a || now < b;
    const sess = [["Sydney", 22, 7], ["Tokyo", 0, 9], ["London", 8, 17], ["New York", 13, 22]];
    $("#sb").innerHTML = st.map(([c, v]) => `<div class="kd-strength"><span class="o">${c}</span><div class="pg-meter"><i class="${v >= 0 ? "up" : "dn"}" data-l="${v >= 0 ? 50 : 50 + v / mx * 50}" data-w="${Math.abs(v) / mx * 50}"></i></div><span class="r ${v >= 0 ? "up" : "dn"}">${pc(v)}</span></div>`).join("")
      + `<div class="kd-sessions">${sess.map(([n, a, b]) => `<div class="${open(a, b) ? "open" : ""}"><b>${n}</b>${open(a, b) ? "open now" : "closed"}</div>`).join("")}</div>`;
    $("#sb-note").textContent = "Strength: each currency's average move over a day across these pairs. Most action happens when London and New York overlap.";
  },
  commodity() {
    rebased();
    const px = s => (P.find(p => p.symbol === s) || {}).price;
    const gs = px("GOLD") / px("SILVER"), og = px("OIL") / px("NATGAS"), cg = px("COPPER") / px("GOLD") * 1000;
    $("#sb").innerHTML = `<div class="kd-ol"><div><small>Gold / silver</small><b class="kd-big">${fmt(gs)}</b></div><div><small>Oil / gas</small><b class="kd-big">${fmt(og)}</b></div></div>
      <div class="kd-ol"><div><small>Copper / gold x1000</small><b>${fmt(cg)}</b></div><div><small>Mood</small><b class="${(P.find(p => p.symbol === "GOLD") || {}).change_1d > (P.find(p => p.symbol === "COPPER") || {}).change_1d ? "dn" : "up"}">${(P.find(p => p.symbol === "GOLD") || {}).change_1d > (P.find(p => p.symbol === "COPPER") || {}).change_1d ? "Fear: gold leads" : "Growth: copper leads"}</b></div></div>`;
    $("#sb-note").textContent = "Gold/silver above 80 means silver is cheap next to gold. Copper beating gold usually means traders expect growth.";
  },
  index() {
    rebased();
    const reg = {"United States": ["SPX", "NDX", "DJI"], "Australia": ["ASX200"], "Europe": ["DAX"]};
    const rows = Object.entries(reg).map(([r, ss]) => { const v = ss.map(s => (P.find(p => p.symbol === s) || {}).change_1d || 0); return [r, v.reduce((a, b) => a + b, 0) / v.length]; });
    const mx = Math.max(0.1, ...rows.map(r => Math.abs(r[1]))), avg = rows.reduce((a, r) => a + r[1], 0) / rows.length;
    $("#sb").innerHTML = rows.map(([r, v]) => `<div class="kd-strength"><span>${r.split(" ").map(w => w[0]).join("")}</span><div class="pg-meter"><i class="${v >= 0 ? "up" : "dn"}" data-l="${v >= 0 ? 50 : 50 + v / mx * 50}" data-w="${Math.abs(v) / mx * 50}"></i></div><span class="r ${v >= 0 ? "up" : "dn"}">${pc(v)}</span></div>`).join("")
      + `<div class="kd-ol"><div><small>World mood today</small><b class="${avg >= 0 ? "up" : "dn"}">${avg >= 0.3 ? "Risk on" : avg <= -0.3 ? "Risk off" : "Calm"}</b></div><div><small>Average move</small><b>${pc(avg)}</b></div></div>`;
    $("#sb-note").textContent = "US = S&P 500, Nasdaq and Dow; A = ASX 200; E = DAX. Risk on means investors are buying shares.";
  },
  share() {
    rebased();
    const R = P.map(p => returns(p.spark || []));
    $("#sb").innerHTML = `<table class="kd-matrix"><tr><th></th>${P.map(p => `<th>${esc(p.symbol)}</th>`).join("")}</tr>${P.map((p, i) => `<tr><th>${esc(p.symbol)}</th>${P.map((q, j) => {
      const c = i === j ? 1 : corr(R[i], R[j]), a = Math.abs(c);
      return `<td class="kd-c" data-c="${c.toFixed(2)}">${c.toFixed(2)}</td>`; }).join("")}</tr>`).join("")}</table>`;
    $("#sb").querySelectorAll(".kd-c").forEach(td => { const c = +td.dataset.c; td.style.background = `rgba(${c >= 0 ? "255,92,0" : "138,138,138"},${Math.abs(c) * 0.55})`; });
    $("#sb-note").textContent = "1.00 = always move together, 0 = no link, below 0 = opposite. Holding shares that all move together is one big bet, not five.";
  },
};
function sizeMeters() { document.querySelectorAll(".pg-meter i").forEach(i => { i.style.left = i.dataset.l + "%"; i.style.width = i.dataset.w + "%"; }); }

async function load() {
  try {
    const [m, t] = await Promise.all([fetch("/api/markets", {cache: "no-store"}), fetch("/api/trades", {cache: "no-store"})]);
    if (m.status === 401) { location.href = "/login"; return; }
    const d = await m.json(); TR = await t.json();
    ALL = d.products || []; P = ALL.filter(p => p.kind === K.kind);
    const src = P[0] && P[0].source; $("#k-src").textContent = `PRACTICE MONEY · ${src === "live" ? "LIVE PRICES" : "SIMULATED PRICES"}`;
  } catch (e) { return; }
  if (!P.length) return;
  if (!picked) { picked = P[0].symbol; detail(); }
  board(); moves(); desk(); SPECIAL[K.kind](); sizeMeters();
}
addEventListener("resize", () => { board(); moves(); SPECIAL[K.kind] && P.length && (SPECIAL[K.kind](), sizeMeters()); });
load(); setInterval(load, 10000); setInterval(detail, 30000);
