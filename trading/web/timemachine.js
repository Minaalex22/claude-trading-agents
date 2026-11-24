// Time Machine page: /api/timemachine replays the trade (trading/timemachine.py), sizes it and checks the money rules.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const num = (v, d = 2) => v == null ? "–" : Number(v).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const usd = v => v == null ? "–" : "$" + num(v);
const form = $("#tm");
let last = null, orMin = 15;

function params() {
  const f = new FormData(form), q = {};
  for (const k of ["symbol", "time", "entry", "stop", "target", "sessions", "runs", "seed", "balance", "risk"]) if (f.get(k)) q[k] = String(f.get(k)).trim();
  return q;
}

async function run(e) {
  if (e) e.preventDefault();
  $("#msg").textContent = "Replaying…";
  $("#ticket").disabled = true;
  try {
    const r = await fetch("/api/timemachine?" + new URLSearchParams(params()), {credentials: "same-origin"});
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || "something went wrong");
    last = body;
    show(body);
    $("#msg").textContent = "";
  } catch (err) { $("#msg").textContent = err.message; }
}

function show(b) {
  const m = b.machine, c = b.check;
  if (!form.entry.value) form.entry.value = m.entry;
  if (!form.stop.value) form.stop.value = Number(m.stop.toFixed(2));
  if (!form.target.value) form.target.value = Number(m.target.toFixed(2));
  $("#what").textContent = `${b.symbol} ${m.side} ${num(m.entry)} · stop ${num(m.stop)} · target ${num(m.target)} · ${m.runs.toLocaleString()} replays`;
  $("#o-t").textContent = m.target_first + "%";
  $("#o-s").textContent = m.stop_first + "%";
  $("#o-f").textContent = m.flat + "%";
  $("#o-v").textContent = m.verdict;
  $("#o-t-sub").textContent = `needs ${m.breakeven_target_share}% of decided`;
  $("#o-s-sub").textContent = `1 : ${m.rr} reward to risk`;
  $("#o-f-sub").textContent = `green ${m.green}%`;
  $("#o-v-sub").textContent = `avg ${m.avg_r > 0 ? "+" : ""}${m.avg_r} R`;
  const h = m.histogram, top = Math.max(...h.counts) || 1, w = (h.high - h.low) / h.counts.length;
  const box = $("#hist");
  box.replaceChildren(...h.counts.map((n, i) => {
    const el = document.createElement("i"), mid = h.low + w * (i + 0.5);
    el.style.height = Math.max(1, 100 * n / top) + "%";
    const sgn = m.side === "buy" ? 1 : -1;
    el.className = sgn * (mid - m.target) >= 0 ? "up" : sgn * (mid - m.stop) <= 0 ? "dn" : "mid";
    el.title = `${num(mid)}: ${n} replays`;
    return el;
  }));
  $("#axis").innerHTML = `<span>${num(h.low)}</span><span>price at the close</span><span>${num(h.high)}</span>`;
  const hs = m.history;
  $("#detail").innerHTML = [
    ["Data", esc(b.source)],
    ["Sessions", `${m.sessions_used} full days, ${esc(m.first_day)} to ${esc(m.last_day)}`],
    ["90% of closes", `${num(m.close_spread.p5)} to ${num(m.close_spread.p95)} (middle ${num(m.close_spread.p50)})`],
    ["Same plan on real past days", `target ${hs.target} · stop ${hs.stop} · neither ${hs.flat}`],
    ["Seed", `${m.seed} (same plan, same data, same seed = same numbers)`],
  ].map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
  $("#warn").textContent = m.warning;
  $("#size").innerHTML = `<b>${c.shares} shares</b>decided by ${esc(c.decided_by)} · lose ${usd(c.loss_at_stop)} at the stop · win ${usd(c.win_at_target)} at the target`;
  $("#checks").innerHTML = c.checks.map(x => `<li><b class="${x.ok ? "ok" : "no"}">${x.ok ? "PASS" : "BLOCK"}</b><span><strong>${esc(x.rule)}</strong> ${esc(x.detail)}</span></li>`).join("")
    + `<li><b class="${c.verdict === "PASS" ? "ok" : "no"}">${c.verdict}</b><span><strong>overall</strong> broker ${esc(c.broker)}, real money ${esc(c.real_money)}</span></li>`;
  $("#ticket").disabled = c.verdict !== "PASS";
  tickets(b.tickets || []);
}

function tickets(list) {
  $("#tickets").innerHTML = list.slice(0, 8).map(t => `<tr><td>${esc(t.at)}</td><td class="${t.side}">${esc(t.side)} ${t.shares} ${esc(t.symbol)}</td><td>@ ${num(t.entry)} · SL ${num(t.stop_loss)} · TP ${num(t.take_profit)}</td></tr>`).join("");
}

async function sendTicket() {
  $("#ticket").disabled = true;
  try {
    const r = await fetch("/api/timemachine/ticket", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify(params())});
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || "blocked");
    tickets(body.tickets);
    $("#msg").textContent = "Practice ticket saved (Demo).";
  } catch (err) { $("#msg").textContent = err.message; }
}

async function scan() {
  $("#hits").innerHTML = `<tr><td colspan="7" class="g">Scanning…</td></tr>`;
  try {
    const b = await (await fetch("/api/timemachine/scan?or=" + orMin, {credentials: "same-origin"})).json();
    $("#scan-sub").textContent = `first ${b.or_minutes} minutes · ${b.found} of ${b.checked} broke out · ${(b.sources || []).join(", ")}`;
    $("#hits").innerHTML = b.hits.length ? b.hits.map((h, i) => `<tr><td>${esc(h.symbol)}</td><td class="${h.side}">${h.side} ${h.entry_time}</td><td>${num(h.range_low)}–${num(h.range_high)}</td><td>${num(h.entry)}</td><td>${num(h.stop)}</td><td>${num(h.target)}</td><td><button type="button" class="tm-btn" data-i="${i}">Replay</button></td></tr>`).join("")
      : `<tr><td colspan="7" class="g">Nothing broke out of its opening range.</td></tr>`;
    $("#hits").onclick = e => {
      const btn = e.target.closest("button[data-i]");
      if (!btn) return;
      const h = b.hits[+btn.dataset.i];
      form.symbol.value = h.symbol; form.time.value = h.entry_time; form.entry.value = h.entry; form.stop.value = h.stop; form.target.value = h.target;
      window.scrollTo({top: 0, behavior: "smooth"});
      run();
    };
  } catch (err) { $("#hits").innerHTML = `<tr><td colspan="7" class="g">${esc(err.message)}</td></tr>`; }
}

async function congress() {
  $("#congress").innerHTML = `<tr><td colspan="4" class="g">Loading…</td></tr>`;
  try {
    const b = await (await fetch("/api/timemachine/congress?member=" + encodeURIComponent($("#member").value.trim()), {credentials: "same-origin"})).json();
    if (!b.ok) { $("#congress").innerHTML = `<tr><td colspan="4" class="g">${esc(b.error)}</td></tr>`; return; }
    $("#congress").innerHTML = b.rows.length ? b.rows.map(r => `<tr><td>${esc(r.filed)}</td><td>${esc(r.member)}</td><td>${esc(r.district)}</td><td><a href="${esc(r.link)}" target="_blank" rel="noopener">report ${esc(r.doc_id)}</a></td></tr>`).join("")
      : `<tr><td colspan="4" class="g">No filings match.</td></tr>`;
    $("#cong-note").textContent = `${b.total} trade reports in ${b.year}. ${b.note}`;
  } catch (err) { $("#congress").innerHTML = `<tr><td colspan="4" class="g">${esc(err.message)}</td></tr>`; }
}

form.addEventListener("submit", run);
$("#ticket").addEventListener("click", sendTicket);
$("#scan").addEventListener("click", scan);
$("#cong").addEventListener("click", congress);
$("#or").addEventListener("click", e => {
  const b = e.target.closest("button[data-or]");
  if (!b) return;
  orMin = +b.dataset.or;
  document.querySelectorAll("#or button").forEach(x => x.classList.toggle("on", x === b));
  scan();
});
run();
