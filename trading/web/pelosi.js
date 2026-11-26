// Pelosi trader page: /api/pelosi (trading/pelosi.py). Paper only.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = v => v == null ? "–" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString(undefined, {maximumFractionDigits: 0});
const pct = v => v == null ? "–" : (v > 0 ? "+" : "") + v.toFixed(1) + "%";
const side = s => `<td class="${s === "buy" ? "buy" : "sell"}">${s === "buy" ? "BUY" : "SELL"}</td>`;
const days = t => `<td class="${t.late ? "no" : ""}">${t.lag_days}${t.late ? " late" : ""}${t.announce_estimated ? " (est.)" : ""}</td>`;
const safeUrl = u => /^https:\/\//.test(u || "") ? u : "";
const link = t => safeUrl(t.url) ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.ticker || "–")}</a>` : esc(t.ticker || "–");
const ARMS = {pelosi: "Pelosi", congress: "Congress $50k+"};

async function load() {
  try {
    const d = await (await fetch("/api/pelosi", {credentials: "same-origin"})).json();
    const b = d.book;
    $("#b-eq").textContent = usd(b.equity);
    $("#b-open").textContent = b.open.length;
    $("#b-win").textContent = b.closed_win_rate == null ? "–" : b.closed_win_rate + "%";
    $("#book-sub").textContent = `$${(b.book / 1000).toFixed(0)}k paper · ${b.closed.length} closed`;
    const f = d.feed;
    $("#feed").textContent = `${f.trades} trades loaded` + (f.updated ? ` · updated ${f.updated.replace("T", " ")} UTC` : " · first load about 20 s after start")
      + " · " + Object.entries(f.sources || {}).map(([k, v]) => `${k} ${v.ok ? "ok" : "off"}`).join(", ");
    const bt = d.backtest || {};
    $("#bt-sub").textContent = bt.period ? `${bt.period} · hold ${bt.hold_days} trading days` : "";
    $("#bt").innerHTML = (bt.arms || []).map(a => `<tr><td>${esc(a.label)}</td><td>${a.trades}</td><td>${a.win_rate}%</td><td>${a.profit_factor}</td><td>${pct(a.avg_return_pct)}</td><td>${pct(a.vs_spy_pct)}</td><td>${a.beat_spy_pct}%</td><td>${usd(-a.max_drawdown)} on $1k a trade</td></tr>`).join("")
      || `<tr><td colspan="8" class="g">No replay saved.</td></tr>`;
    $("#bt-note").textContent = bt.note || "";
    $("#pel").innerHTML = d.pelosi.map(t => `<tr><td>${link(t)} <span class="g">${esc(String(t.asset).slice(0, 40))}</span></td>${side(t.side)}<td>${t.kind === "stock" ? "shares" : esc(t.kind) + " options"}</td><td>${esc(t.amount)}</td><td>${t.trade_date}</td><td>${t.announce_date}</td>${days(t)}<td>${esc(t.owner)}</td></tr>`).join("")
      || `<tr><td colspan="8" class="g">Loading the disclosures…</td></tr>`;
    $("#open").innerHTML = b.open.map(p => `<tr><td>${esc(p.ticker)}</td><td>${esc(p.member)} · ${ARMS[p.arm] || esc(p.arm)}</td><td>${p.trade_date} / ${p.announce_date}</td><td>${p.move_trade_to_announce == null ? "–" : pct(100 * p.move_trade_to_announce)}</td><td class="${p.pnl >= 0 ? "up" : "dn"}">${usd(p.pnl)}</td></tr>`).join("")
      || `<tr><td colspan="5" class="g">No open positions yet: waits for the next announcement.</td></tr>`;
    $("#agents").innerHTML = (d.agents_see || []).map(a => `<tr><td>${esc(a.ticker)}</td><td class="${a.bias > 0 ? "up" : "dn"}">${a.bias > 0 ? "+" : ""}${a.bias.toFixed(2)}</td><td class="g">${esc(a.why)}</td></tr>`).join("")
      || `<tr><td colspan="3" class="g">Nothing announced in the last 60 days.</td></tr>`;
    $("#sig").innerHTML = d.signals.map(t => `<tr><td>${esc(t.member)}</td><td>${link(t)}</td>${side(t.side)}<td>${esc(t.amount)}</td><td>${t.trade_date}</td><td>${t.announce_date}</td>${days(t)}</tr>`).join("")
      || `<tr><td colspan="7" class="g">Loading…</td></tr>`;
    $("#note").textContent = d.note;
  } catch (err) { $("#pel").innerHTML = `<tr><td colspan="8" class="g">${esc(err.message)}</td></tr>`; }
}
load();
setInterval(load, 60000);
