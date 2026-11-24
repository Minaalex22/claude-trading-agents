// Audit log page: Sentinel's PASS/FAIL verdicts (trading/trade_checker.py) and the event calendar (trading/event_guard.py).
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const num = (v, d = 2) => v == null ? "–" : Number(v).toLocaleString(undefined, {maximumFractionDigits: d});

async function load() {
  try {
    const b = await (await fetch("/api/audit", {credentials: "same-origin"})).json();
    $("#n-pass").textContent = b.passed.toLocaleString();
    $("#n-fail").textContent = b.failed.toLocaleString();
    const reasons = Object.entries(b.reasons || {}).sort((a, c) => c[1] - a[1]);
    $("#reasons").innerHTML = reasons.length ? reasons.map(([k, n]) => `<li><b class="no">${n}</b><span><strong>${esc(k)}</strong></span></li>`).join("")
      : `<li><b class="ok">0</b><span>No failed checks yet.</span></li>`;
    $("#switches").textContent = `Trade checker ${b.checker_on ? "on" : "off"} · event guard ${b.guard_on ? "on" : "off"}`;
    const st = b.calendar_status || {};
    $("#cal-status").textContent = `earnings ${st.earnings || "?"} · news ${st.macro || "?"}${st.updated ? " · " + st.updated : ""}`;
    $("#cal").innerHTML = (b.calendar || []).length ? b.calendar.map(e => `<tr><td>${esc(e.at)}</td><td>${esc(e.name)}</td><td>${esc(e.scope)}</td><td class="${e.impact === "HIGH" ? "no" : ""}">${esc(e.impact)}</td></tr>`).join("")
      : `<tr><td colspan="4" class="g">No events loaded yet.</td></tr>`;
    $("#rows").innerHTML = b.rows.length ? b.rows.map(r => `<tr><td>${esc(r.at)}</td><td>${esc(r.symbol)}</td><td class="${r.side === "BUY" ? "buy" : "sell"}">${esc(r.side)}</td><td>${num(r.price, 4)}</td><td>${num(Math.abs(r.desk_score))} / ${num(r.threshold)}</td><td>${esc(r.event)}</td><td class="${r.passed ? "ok" : "no"}">${esc(r.verdict)}</td></tr>`).join("")
      : `<tr><td colspan="7" class="g">No decisions logged yet. The desk writes one for every order that adds risk.</td></tr>`;
  } catch (err) { $("#rows").innerHTML = `<tr><td colspan="7" class="g">${esc(err.message)}</td></tr>`; }
}
load();
setInterval(load, 30000);
