// Brief and close page: /api/brief (trading/session_brief.py and trading/daily_close.py).
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = v => v == null ? "–" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});

async function load() {
  try {
    const b = await (await fetch("/api/brief", {credentials: "same-origin"})).json();
    const n = b.now;
    $("#acct").textContent = "ACCOUNT · " + n.account.label;
    $("#brief-sub").textContent = `${n.session_name} · ${n.written}`;
    $("#ideas").innerHTML = n.top_ideas.length ? n.top_ideas.map(i => `<tr><td>${i.rank}</td><td>${esc(i.symbol)}</td><td class="${i.side === "BUY" ? "buy" : "sell"}">${i.side}</td><td>${Math.round(i.confidence * 100)}%${i.event !== "clear" ? " · " + esc(i.event) : ""}</td><td>${esc(i.why)}</td></tr>`).join("")
      : `<tr><td colspan="5" class="g">No strong setups right now: the desk waits.</td></tr>`;
    $("#blocked").textContent = n.blocked.length ? "Blacked out: " + n.blocked.map(x => `${x.symbol} (${x.why})`).join("; ") : "";
    $("#next").textContent = "next open " + b.next_open;
    $("#sessions").innerHTML = b.sessions.map(s => `<tr><td>${esc(s.name)}</td><td>${s.opens}</td></tr>`).join("");
    $("#managers").innerHTML = n.managers.map(m => `<tr><td>${esc(m.name)}</td><td>${esc(m.call)} · ${esc(m.active)}</td></tr>`).join("") || `<tr><td class="g">warming up</td></tr>`;
    $("#events").innerHTML = n.events.length ? n.events.map(e => `<tr><td>${esc(e.at)}</td><td>${esc(e.name)}</td><td class="${e.impact === "HIGH" ? "no" : ""}">${esc(e.impact)}</td></tr>`).join("")
      : `<tr><td class="g">No events loaded yet.</td></tr>`;
    const c = b.close;
    $("#close-day").textContent = `${c.day} · ${c.written}`;
    $("#c-n").textContent = c.trades;
    $("#c-w").textContent = c.win_rate == null ? "–" : c.win_rate + "%";
    $("#c-p").textContent = usd(c.pnl);
    $("#c-p").className = c.pnl >= 0 ? "up" : "dn";
    $("#c-d").textContent = usd(c.worst_drop);
    const L = c.ledger;
    $("#close-checks").innerHTML = `<li><b class="${L.ok ? "ok" : "no"}">${L.ok ? "OK" : "CHECK"}</b><span><strong>ledger</strong> ${L.checked} trades checked against the day's prices${L.problems.length ? ": " + esc(L.problems[0]) : ""}</span></li>`
      + `<li><b class="ok">${c.checker.passed}</b><span><strong>Sentinel passed</strong></span></li><li><b class="no">${c.checker.failed}</b><span><strong>Sentinel blocked</strong></span></li>`;
    const w = b.weekly;
    $("#weekly").innerHTML = `<b>${w.real_win_rate == null ? "–" : w.real_win_rate + "%"}</b>real trades this week (${w.trades}) vs ${w.practice_win_rate}% in practice. ${esc(w.verdict)}`;
  } catch (err) { $("#ideas").innerHTML = `<tr><td colspan="5" class="g">${esc(err.message)}</td></tr>`; }
}
load();
setInterval(load, 60000);
