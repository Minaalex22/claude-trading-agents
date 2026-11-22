// Trades: every closed practice trade, won or lost, from /api/trades. Agent totals from /api/report.
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = v => (v >= 0 ? "+" : "−") + "$" + Math.abs(v || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const pct = v => v == null || isNaN(v) ? "–" : (v * 100).toFixed(1) + "%";
const px = v => v == null ? "–" : Number(v) >= 1000 ? Number(v).toFixed(0) : Number(v).toFixed(2);
const C = {o: "#ff5c00", up: "#2fd37a", dn: "#ff4d4d", g: "#8a8a8a", line: "#1f1f1f"};
let DATA = {trades: [], totals: {}, by_symbol: {}}, filter = "all";

function canvas(id) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px ui-monospace, Menlo, monospace"; return [g, w, h];
}
function empty(g, msg) { g.fillStyle = C.g; g.fillText(msg, 10, 22); }

function drawCum() {
  const [g, w, h] = canvas("#cum");
  const ts = DATA.trades.slice().reverse();
  if (ts.length < 2) return empty(g, "Waiting for the first finished trades…");
  let run = 0; const pts = ts.map(t => (run += t.pnl || 0));
  const lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), sp = hi - lo || 1;
  const L = 6, R = w - 70, x = i => L + (R - L) * i / (pts.length - 1), y = v => h - 10 - (v - lo) / sp * (h - 20);
  g.strokeStyle = C.line; g.beginPath(); g.moveTo(L, y(0)); g.lineTo(R, y(0)); g.stroke();
  g.fillStyle = C.g; g.fillText("$0", R + 6, y(0) + 4); g.fillText(money(hi), R + 6, 12); if (lo < 0) g.fillText(money(lo), R + 6, h - 4);
  const up = pts[pts.length - 1] >= 0, grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, up ? "rgba(47,211,122,.3)" : "rgba(255,77,77,.3)"); grad.addColorStop(1, "rgba(0,0,0,0)");
  g.beginPath(); pts.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)));
  g.lineTo(x(pts.length - 1), y(0)); g.lineTo(x(0), y(0)); g.closePath(); g.fillStyle = grad; g.fill();
  g.beginPath(); pts.forEach((v, i) => i ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)));
  g.strokeStyle = up ? C.up : C.dn; g.lineWidth = 2; g.stroke();
  ts.forEach((t, i) => { g.fillStyle = t.won ? C.up : C.dn; g.fillRect(x(i) - 1, h - 4, 2, 4); });
}

function drawDonut() {
  const [g, w, h] = canvas("#donut"), t = DATA.totals || {};
  if (!t.trades) return empty(g, "No finished trades yet");
  const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 14, a = (t.won / t.trades) * Math.PI * 2;
  g.lineWidth = 18; g.lineCap = "butt";
  g.beginPath(); g.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + a); g.strokeStyle = C.up; g.stroke();
  g.beginPath(); g.arc(cx, cy, r, -Math.PI / 2 + a, Math.PI * 1.5); g.strokeStyle = C.dn; g.stroke();
  g.fillStyle = "#f4f4f4"; g.font = "700 26px ui-monospace, Menlo, monospace"; g.textAlign = "center";
  g.fillText(pct(t.win_rate), cx, cy + 6);
  g.font = "11px ui-monospace, Menlo, monospace"; g.fillStyle = C.g; g.fillText(`${t.won} won · ${t.lost} lost`, cx, cy + 26);
  g.textAlign = "left";
}

function drawBySymbol() {
  const [g, w, h] = canvas("#bysym"), rows = Object.entries(DATA.by_symbol || {});
  if (!rows.length) return empty(g, "No finished trades yet");
  const mx = Math.max(1, ...rows.map(([, v]) => Math.abs(v.pnl))), bw = w / rows.length, mid = h / 2 - 4;
  g.strokeStyle = C.line; g.beginPath(); g.moveTo(0, mid); g.lineTo(w, mid); g.stroke();
  rows.forEach(([s, v], i) => {
    const bh = v.pnl / mx * (mid - 16), x = i * bw + bw * .2;
    g.fillStyle = v.pnl >= 0 ? C.up : C.dn; g.fillRect(x, v.pnl >= 0 ? mid - bh : mid, bw * .6, Math.abs(bh));
    g.fillStyle = C.o; g.fillText(s, x, h - 14);
    g.fillStyle = C.g; g.fillText(`${Math.round(v.win_rate * 100)}% won`, x, h - 2);
  });
}

function renderRows() {
  const all = DATA.trades;
  const ts = all.filter(t => filter === "all" || (filter === "win" ? t.won : !t.won)).slice(0, 300);
  $("#rows").innerHTML = ts.map(t => {
    const time = t.ts ? new Date(t.ts * 1000).toLocaleTimeString([], {hour12: false}) : "–";
    return `<tr class="${t.won ? "win" : "loss"}"><td>${time}</td><td class="o">${esc(t.symbol)}</td><td>${t.side === "long" ? "Bought" : "Sold short"}</td>
      <td class="r hide-s">${px(t.qty)}</td><td class="r hide-s">${px(t.entry)}</td><td class="r hide-s">${px(t.exit)}</td>
      <td class="r ${t.move >= 0 ? "up" : "dn"}">${(t.move * 100).toFixed(2)}%</td><td class="r ${t.pnl >= 0 ? "up" : "dn"}">${money(t.pnl)}</td>
      <td><span class="pg-pill ${t.won ? "win" : "loss"}">${t.won ? "WON" : "LOST"}</span></td></tr>`;
  }).join("") || '<tr><td colspan="9" class="g">No trades here yet. The desk closes its first trades a few minutes after it starts.</td></tr>';
  $("#t-sub").textContent = `showing ${ts.length} of ${all.length} kept · newest first`;
  $("#t-note").textContent = "Profit is the paper money each trade made or lost between getting in and getting out. Practice money, simulated fills.";
}
$("#filter").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  filter = b.dataset.f; $("#filter").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); renderRows();
});

async function load() {
  try {
    const [r, rep] = await Promise.all([fetch("/api/trades", {cache: "no-store"}), fetch("/api/report", {cache: "no-store"})]);
    if (r.status === 401) { location.href = "/login"; return; }
    DATA = await r.json();
    const t = DATA.totals || {}, at = (await rep.json()).agent_trades || {};
    $("#k-n").textContent = (t.trades || 0).toLocaleString();
    $("#k-win").textContent = pct(t.win_rate);
    $("#k-wl").textContent = `${(t.won || 0).toLocaleString()} won · ${(t.lost || 0).toLocaleString()} lost`;
    const k = $("#k-pnl"); k.textContent = money(t.pnl || 0); k.className = (t.pnl || 0) >= 0 ? "up" : "dn";
    $("#k-agents").textContent = pct(at.win_rate);
    $("#k-agents-sub").textContent = `${(at.won || 0).toLocaleString()} won · ${(at.lost || 0).toLocaleString()} lost, agents' own accounts`;
  } catch (e) { return; }
  drawCum(); drawDonut(); drawBySymbol(); renderRows();
}
addEventListener("resize", () => { drawCum(); drawDonut(); drawBySymbol(); });
load(); setInterval(load, 5000);
