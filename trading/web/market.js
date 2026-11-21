// One product: candles + indicators, outlook, why it's moving, volume profile, crypto order flow.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (x, d) => x == null ? "–" : Math.abs(x) >= 1000 ? x.toLocaleString(undefined, {maximumFractionDigits: d ?? 2}) : Math.abs(x) >= 1 ? x.toFixed(d ?? 2) : x.toPrecision(4);
const pct = x => x == null ? '<span class="mut">–</span>' : `<span class="${x >= 0 ? "up" : "dn"}">${x >= 0 ? "+" : ""}${x.toFixed(2)}%</span>`;
const SYM = location.pathname.split("/").pop().toUpperCase();
let tf = new URLSearchParams(location.search).get("tf") || "1h";
const LC = window.LightweightCharts;
const opts = h => ({height: h, layout: {background: {color: "transparent"}, textColor: "#6fa9b6"},
  grid: {vertLines: {color: "rgba(79,243,255,.06)"}, horzLines: {color: "rgba(79,243,255,.06)"}},
  timeScale: {timeVisible: true, borderColor: "rgba(79,243,255,.14)"}, rightPriceScale: {borderColor: "rgba(79,243,255,.14)"}});
let ch = null, cdChart = null, cdSeries = null;

function charts() {
  if (!LC || ch) return;
  const main = LC.createChart($("#chart"), opts($("#chart").clientHeight));
  ch = {main,
    candles: main.addCandlestickSeries({upColor: "#5dffc4", downColor: "#ff5c8a", wickUpColor: "#5dffc4", wickDownColor: "#ff5c8a", borderVisible: false}),
    vol: main.addHistogramSeries({priceScaleId: "v", priceFormat: {type: "volume"}}),
    ma20: main.addLineSeries({color: "#4ff3ff", lineWidth: 1}), ma50: main.addLineSeries({color: "#ffc65c", lineWidth: 1}),
    bu: main.addLineSeries({color: "#55707a", lineWidth: 1}), bl: main.addLineSeries({color: "#55707a", lineWidth: 1})};
  main.priceScale("v").applyOptions({scaleMargins: {top: .8, bottom: 0}});
  ch.rsiChart = LC.createChart($("#rsi"), opts(120)); ch.rsi = ch.rsiChart.addLineSeries({color: "#c69bff", lineWidth: 1});
  ch.macdChart = LC.createChart($("#macd"), opts(120));
  ch.hist = ch.macdChart.addHistogramSeries({}); ch.macd = ch.macdChart.addLineSeries({color: "#4ff3ff", lineWidth: 1});
  ch.signal = ch.macdChart.addLineSeries({color: "#ffc65c", lineWidth: 1});
  new ResizeObserver(() => { const w = $("#chart").clientWidth; [main, ch.rsiChart, ch.macdChart].forEach(c => c.applyOptions({width: w})); }).observe($("#chart"));
}
const line = (c, xs) => xs.map((v, i) => v == null ? null : {time: c[i].t, value: v}).filter(Boolean);

function outlookCard(h, o) {
  const d = o.direction, cls = d === "up" ? "up" : d === "down" ? "dn" : "mut";
  return `<section class="mk-card mk-out"><h3>Next ${esc(h)}</h3>
   <div class="dir ${cls}">${d === "up" ? "▲ Up" : d === "down" ? "▼ Down" : "● Flat"} <span class="mut mk-small">confidence ${(o.confidence * 100).toFixed(0)}%</span></div>
   ${d !== "flat" ? `<div>Stop loss <b>${fmt(o.stop_loss)}</b> · Take profit <b>${fmt(o.take_profit)}</b> · reward/risk ${o.reward_risk}x</div>` : ""}
   ${o.warning ? `<div class="mk-warn">⚠ ${esc(o.warning)}</div>` : ""}
   <ul>${o.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
   <div class="mut mk-small">Judged on ${esc(o.timeframe)} candles</div></section>`;
}

function volumeProfile(vp) {
  $("#vpinfo").textContent = `Busiest price (POC) ${fmt(vp.poc)} · value area ${fmt(vp.val)} – ${fmt(vp.vah)}`;
  const box = $("#vp"); box.textContent = "";
  const mx = Math.max(...vp.levels.map(l => l.volume)) || 1;
  const pocIdx = vp.levels.reduce((b, l, i, a) => l.volume > a[b].volume ? i : b, 0);
  vp.levels.map((l, i) => [l, i]).reverse().forEach(([l, i]) => {
    const row = document.createElement("div"); row.className = "mk-vp";
    const lab = document.createElement("span"); lab.textContent = fmt(l.price);
    const bar = document.createElement("i");
    bar.className = i === pocIdx ? "poc" : (l.price >= vp.val && l.price <= vp.vah ? "va" : "");
    bar.style.width = (l.volume / mx * 100).toFixed(1) + "%";
    row.append(lab, bar); box.append(row);
  });
}

function orderFlow(f) {
  $("#flow").classList.remove("mk-hidden");
  const d = f.dom;
  $("#dominfo").textContent = `Book imbalance ${(d.imbalance * 100).toFixed(0)}% ${d.imbalance > 0 ? "(more bids)" : "(more asks)"} · spread ${fmt(d.spread)} · amber rows are walls`;
  $("#dom").innerHTML = d.asks.slice().reverse().map(r => `<tr class="${r.wall ? "mk-wall" : ""}"><td></td><td class="dn">${fmt(r.price)}</td><td>${fmt(r.size, 4)}</td></tr>`).join("") +
    d.bids.map(r => `<tr class="${r.wall ? "mk-wall" : ""}"><td>${fmt(r.size, 4)}</td><td class="up">${fmt(r.price)}</td><td></td></tr>`).join("");
  const t = f.tape;
  $("#tapeinfo").textContent = `Buying ${(t.buy_share * 100).toFixed(0)}% of volume · big print ≥ ${fmt(t.big_threshold, 4)}`;
  $("#tape").innerHTML = t.prints.map(p => `<tr class="${p.big ? "mk-big" : ""}"><td>${new Date(p.t * 1000).toLocaleTimeString()}</td><td class="${p.side === "buy" ? "up" : "dn"}">${fmt(p.price)}</td><td>${fmt(p.size, 4)}</td></tr>`).join("");
  if (LC) {
    if (!cdChart) { cdChart = LC.createChart($("#cd"), opts(170)); cdSeries = cdChart.addBaselineSeries({baseValue: {type: "price", price: 0}});
      new ResizeObserver(() => cdChart.applyOptions({width: $("#cd").clientWidth})).observe($("#cd")); }
    const seen = new Set();
    cdSeries.setData(f.cumulative_delta.map(p => ({time: Math.floor(p.t), value: p.cd})).filter(p => !seen.has(p.time) && seen.add(p.time)));
  }
  $("#fp").innerHTML = f.footprint.map(b => `<div class="mk-fpbar"><div class="mut"><span>${new Date(b.t * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</span><span class="${b.delta >= 0 ? "up" : "dn"}">Δ ${fmt(b.delta, 3)}</span></div>` +
    b.rows.slice(0, 18).map(r => `<div class="${r.imbalance === "buy" ? "mk-ib" : r.imbalance === "sell" ? "mk-is" : ""}"><span>${fmt(r.sell, 3)}</span><span class="mut">${fmt(r.price)}</span><span>${fmt(r.buy, 3)}</span></div>`).join("") + "</div>").join("");
}

async function load() {
  const r = await fetch(`/api/markets/${encodeURIComponent(SYM)}?tf=${encodeURIComponent(tf)}`);
  if (r.status === 401) return location.assign("/login");
  const d = await r.json();
  if (!r.ok) { $("#title").textContent = d.error || "Not found"; return; }
  document.title = `${d.product.name} · ATS Matrix`;
  $("#title").textContent = d.product.name;
  $("#price").textContent = fmt(d.summary.price);
  $("#chg").innerHTML = `5m ${pct(d.summary.change_5m)} · 1d ${pct(d.summary.change_1d)} · 40d ${pct(d.summary.change_40d)}`;
  $("#src").textContent = d.source === "simulated" ? "simulated data" : "live · " + d.source;
  $("#star").classList.toggle("on", d.favourite);
  $("#tfs").innerHTML = d.timeframes.map(t => `<button data-tf="${t}" class="${t === tf ? "on" : ""}">${t}</button>`).join("");
  charts();
  if (ch) {
    const c = d.candles, I = d.indicators;
    ch.candles.setData(c.map(k => ({time: k.t, open: k.o, high: k.h, low: k.l, close: k.c})));
    ch.vol.setData(c.map(k => ({time: k.t, value: k.v, color: k.c >= k.o ? "rgba(93,255,196,.3)" : "rgba(255,92,138,.3)"})));
    ch.ma20.setData(line(c, I.ma20)); ch.ma50.setData(line(c, I.ma50));
    ch.bu.setData(line(c, I.bollinger.upper)); ch.bl.setData(line(c, I.bollinger.lower));
    ch.rsi.setData(line(c, I.rsi)); ch.macd.setData(line(c, I.macd.macd)); ch.signal.setData(line(c, I.macd.signal));
    ch.hist.setData(line(c, I.macd.hist).map(p => ({...p, color: p.value >= 0 ? "#5dffc4" : "#ff5c8a"})));
  }
  $("#outlooks").innerHTML = Object.entries(d.outlook).map(([h, o]) => outlookCard(h, o)).join("");
  $("#why").innerHTML = d.why.map(w => `<li>${esc(w)}</li>`).join("");
  volumeProfile(d.volume_profile);
  if (d.orderflow) orderFlow(d.orderflow);
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-tf]");
  if (b) { tf = b.dataset.tf; history.replaceState(null, "", `?tf=${tf}`); load(); return; }
  if (e.target.closest("#star")) {
    const r = await fetch(`/api/markets/${encodeURIComponent(SYM)}/favourite`, {method: "POST"});
    if (r.ok) $("#star").classList.toggle("on", (await r.json()).favourites.includes(SYM));
  }
});
load(); setInterval(load, 10000);
