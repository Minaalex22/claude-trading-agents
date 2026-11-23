// Polymarket platform: real markets (read only), paper bets, the agents' odds, and the bot lab
// (profit by strategy, neg-risk sums, cross-book costs, maker band, bot paper trades). Nothing is sent to Polymarket.
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

// ---------------------------------------------------------------- the platform: markets, ticket, paper bets
const CATS = {all: "All", crypto: "Crypto", politics: "Politics", sports: "Sports", economy: "Economy", tech: "Tech", world: "World", other: "Other"};
let M = null, B = null, sel = null, side = "YES", cat = "all", btab = "open", qTimer = 0;
const cents = p => (p == null ? "–" : (p * 100).toFixed(p < 0.1 || p > 0.9 ? 1 : 0) + "c");
const big = v => v >= 1e6 ? "$" + (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? "$" + (v / 1e3).toFixed(0) + "k" : "$" + Math.round(v || 0);
const money = v => "$" + Number(v || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const safeUrl = u => (/^https:\/\/polymarket\.com\//.test(u || "") ? u : "https://polymarket.com");
const ends = e => { const d = (new Date(e) - Date.now()) / 864e5; return isNaN(d) ? "–" : d < 1 ? Math.max(0, d * 24).toFixed(0) + "h" : d < 60 ? d.toFixed(0) + "d" : (d / 30).toFixed(0) + "mo"; };

async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", credentials: "same-origin", cache: "no-store", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {credentials: "same-origin", cache: "no-store"});
  if (r.status === 401) { location.href = "/login"; throw new Error("sign in"); }
  const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j;
}

function leanTag(l, m) {
  if (!l || !l.side) return `<span class="pg-pill flat">SKIP</span>`;
  const name = m.outcomes[l.side === "YES" ? 0 : 1] || l.side;
  return `<span class="pg-pill ${l.side === "YES" ? "win" : "loss"}">${esc(name).slice(0, 12).toUpperCase()} ${l.confidence}</span>`;
}

function drawMarkets() {
  $("#cats").innerHTML = Object.keys(CATS).filter(k => k === "all" || (M.categories[k] || 0) > 0)
    .map(k => `<button type="button" data-c="${k}" class="${k === cat ? "on" : ""}">${CATS[k]}<span>${M.categories[k] || 0}</span></button>`).join("");
  $("#mk-sub").textContent = M.mode === "live" ? `top ${M.categories.all} Polymarket markets by 24h volume · updated ${new Date(M.fetched_at * 1000).toLocaleTimeString([], {hour12: false})}` : "sample markets (Polymarket not reachable)";
  const msg = $("#feed-msg"); msg.hidden = !M.error; msg.textContent = M.error || "";
  $("#mk").innerHTML = M.markets.map(m => `<tr data-id="${esc(m.id)}" class="${sel && sel.id === m.id ? "on" : ""}">
    <td class="q">${esc(m.group ? m.event : m.question)}<small>${esc(m.group ? m.group + " · " : "")}${esc(CATS[m.category])}${m.change_1d ? ` · <span class="${m.change_1d > 0 ? "up" : "dn"}">${m.change_1d > 0 ? "+" : ""}${(m.change_1d * 100).toFixed(0)}c today</span>` : ""}</small></td>
    <td><span class="pm-bar"><i class="w${Math.round(m.yes * 20) * 5}"></i></span>${cents(m.yes)}${m.outcomes[0] !== "Yes" ? `<small class="g"> ${esc(m.outcomes[0]).slice(0, 14)}</small>` : ""}</td>
    <td class="r">${big(m.volume_24h)}</td><td>${ends(m.end)}</td><td>${leanTag(m.lean, m)}</td></tr>`).join("") || '<tr><td colspan="5" class="g">No markets match.</td></tr>';
  $("#picks").innerHTML = M.picks.map(m => `<button type="button" class="pm-pick" data-id="${esc(m.id)}">${esc(m.question)}
    <small>price ${cents(m.yes)} · agents ${cents(m.lean.fair)} · <b class="${m.lean.side === "YES" ? "up" : "dn"}">${esc(m.outcomes[m.lean.side === "YES" ? 0 : 1])}</b> · edge ${(m.lean.net * 100).toFixed(1)}c</small></button>`).join("") || '<div class="pg-empty">No market has a clear edge right now. That is normal: most prices are fair.</div>';
}

function find(id) { return (M && (M.markets.find(m => m.id === id) || M.picks.find(m => m.id === id))) || null; }

function ticket() {
  $("#tk-empty").hidden = !!sel; $("#tk").hidden = !sel; if (!sel) return;
  const q = $("#tk-q"); q.textContent = sel.question; q.href = safeUrl(sel.url);
  const yesP = sel.ask, noP = 1 - sel.bid;
  const [o1, o2] = sel.outcomes || ["Yes", "No"];
  $("#tk-yes").innerHTML = `${esc(o1).slice(0, 22)} <b id="tk-yes-p">${cents(yesP)}</b>`; $("#tk-no").innerHTML = `${esc(o2).slice(0, 22)} <b id="tk-no-p">${cents(noP)}</b>`;
  $("#tk-yes").classList.toggle("on", side === "YES"); $("#tk-no").classList.toggle("on", side === "NO");
  const usdv = Number($("#tk-usd").value) || 0, p = side === "YES" ? yesP : noP, fee = (sel.fee_rate || 0) * p * (1 - p);
  const sh = p > 0 ? usdv / (p + fee) : 0;
  $("#tk-est").innerHTML = `Shares <b>${sh.toFixed(1)}</b> at <b>${cents(p)}</b>${fee ? ` + fee ${(fee * 100).toFixed(2)}c` : ""}<br>
    If ${esc(side === "YES" ? o1 : o2)} wins you get <b>${money(sh)}</b> (profit <b class="up">${money(sh - usdv)}</b>)<br>If not, you lose <b class="dn">${money(usdv)}</b>${B ? ` · max bet ${money(B.max_bet)}` : ""}`;
  const l = sel.lean || {};
  $("#tk-lean").innerHTML = l.side ? `Agents say <b>${esc(l.side === "YES" ? o1 : o2)}</b>: fair odds ${cents(l.fair)} vs price ${cents(sel.yes)}. ${esc(l.reasons.join("; "))}.`
    : `Agents skip this one: ${esc(l.skip || "no edge")}.`;
}

function pick(id) { const m = find(id); if (!m) return; sel = m; side = (m.lean && m.lean.side) || "YES"; $("#tk-msg").textContent = ""; drawMarkets(); ticket(); }

function drawBook() {
  if (!B) return;
  $("#b-equity").textContent = money(B.equity); $("#b-cash").textContent = `${money(B.cash)} cash · started ${money(B.start)}`;
  const p = $("#b-pnl"); p.textContent = usd(B.pnl); p.className = B.pnl >= 0 ? "up" : "dn";
  $("#b-pnl-sub").textContent = `${usd(B.realised)} banked · ${usd(B.unrealised)} open`;
  $("#b-win").textContent = B.win_rate == null ? "–" : B.win_rate + "%"; $("#b-win-sub").textContent = `${B.wins} of ${B.bets} closed bets · ${B.open.length} open`;
  const a = B.agents;
  $("#b-brier").textContent = a.brier == null ? "–" : a.brier.toFixed(3);
  $("#b-brier-sub").textContent = a.brier == null ? `watching ${a.tracked} picks · scored when markets close` : `market ${a.brier_market.toFixed(3)} · lower wins · ${a.right}/${a.scored} right`;
  $("#open-sub").textContent = `${B.open.length} of ${B.max_open} open · paid $1 a share when right · real money locked`;
  if (btab === "open") {
    $("#bt-head").innerHTML = "<tr><th>#</th><th>Market</th><th>Side</th><th class='r'>Paid</th><th class='r'>Now</th><th class='r'>Cost</th><th class='r'>Value</th><th class='r'>P/L</th><th class='r'>Pays if right</th><th></th></tr>";
    $("#bt").innerHTML = B.open.map(o => `<tr><td>${o.id}</td><td><a href="${safeUrl(o.url)}" target="_blank" rel="noopener noreferrer">${esc(o.question)}</a></td>
      <td><span class="pg-pill ${o.side === "YES" ? "win" : "loss"}">${esc(o.outcome || o.side)}</span></td><td class="r">${cents(o.price)}</td><td class="r">${cents(o.mark)}</td>
      <td class="r">${money(o.cost)}</td><td class="r">${money(o.value)}</td><td class="r ${o.pnl >= 0 ? "up" : "dn"}">${usd(o.pnl)}</td><td class="r">${money(o.payout)}</td>
      <td><button type="button" class="pm-sell" data-sell="${o.id}">SELL</button></td></tr>`).join("") || '<tr><td colspan="10" class="g">No open bets. Pick a market above.</td></tr>';
  } else {
    $("#bt-head").innerHTML = "<tr><th>#</th><th>Market</th><th>Side</th><th class='r'>Paid</th><th class='r'>Out</th><th class='r'>Cost</th><th class='r'>Got back</th><th class='r'>P/L</th><th>How</th></tr>";
    $("#bt").innerHTML = B.closed.map(o => `<tr class="${o.pnl > 0 ? "win" : "loss"}"><td>${o.id}</td><td>${esc(o.question)}</td>
      <td><span class="pg-pill ${o.side === "YES" ? "win" : "loss"}">${esc(o.outcome || o.side)}</span></td><td class="r">${cents(o.price)}</td><td class="r">${cents(o.exit)}</td>
      <td class="r">${money(o.cost)}</td><td class="r">${money(o.proceeds)}</td><td class="r ${o.pnl >= 0 ? "up" : "dn"}">${usd(o.pnl)}</td><td class="g">${esc(o.reason)}</td></tr>`).join("") || '<tr><td colspan="9" class="g">Nothing settled yet.</td></tr>';
  }
  ticket();
}

async function loadMarkets() {
  try { M = await api(`/api/polymarket/markets?cat=${encodeURIComponent(cat)}&q=${encodeURIComponent($("#q").value)}`); } catch (e) { return; }
  if (sel) sel = find(sel.id) || sel;
  drawMarkets(); ticket();
}
async function loadBook() { try { B = await api("/api/polymarket/book"); drawBook(); } catch (e) { /* keep last */ } }

$("#cats").addEventListener("click", e => { const b = e.target.closest("button[data-c]"); if (b) { cat = b.dataset.c; loadMarkets(); } });
$("#q").addEventListener("input", () => { clearTimeout(qTimer); qTimer = setTimeout(loadMarkets, 300); });
$("#mk").addEventListener("click", e => { const r = e.target.closest("tr[data-id]"); if (r) pick(r.dataset.id); });
$("#picks").addEventListener("click", e => { const b = e.target.closest("[data-id]"); if (b) pick(b.dataset.id); });
$("#tk-yes").addEventListener("click", () => { side = "YES"; ticket(); });
$("#tk-no").addEventListener("click", () => { side = "NO"; ticket(); });
$("#tk-usd").addEventListener("input", ticket);
$("#tk-go").addEventListener("click", async () => {
  if (!sel) return; const go = $("#tk-go"), msg = $("#tk-msg"); go.disabled = true;
  try { const r = await api("/api/polymarket/bet", {market_id: sel.id, side, usd: Number($("#tk-usd").value)}); B = r.book; drawBook(); msg.className = "pm-msg ok"; msg.textContent = `Paper bet #${r.bet.id} placed: ${r.bet.shares.toFixed(1)} "${r.bet.outcome}" shares at ${cents(r.bet.price)}.`; }
  catch (e) { msg.className = "pm-msg bad"; msg.textContent = e.message; }
  go.disabled = false;
});
$("#btabs").addEventListener("click", e => { const b = e.target.closest("button[data-t]"); if (!b) return; btab = b.dataset.t; [...$("#btabs").children].forEach(x => x.classList.toggle("on", x === b)); drawBook(); });
$("#bt").addEventListener("click", async e => {
  const b = e.target.closest("button[data-sell]"); if (!b) return; b.disabled = true;
  try { const r = await api("/api/polymarket/sell", {id: Number(b.dataset.sell)}); B = r.book; drawBook(); } catch (err) { b.disabled = false; b.textContent = err.message.slice(0, 40); }
});
loadMarkets(); loadBook(); setInterval(loadMarkets, 30000); setInterval(loadBook, 15000);
