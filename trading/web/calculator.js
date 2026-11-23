// Trading calculator: the form sends the trade to /api/calculator, which does all the maths (trading/calculator.py).
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = v => v == null ? "–" : (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const num = (v, d = 2) => v == null ? "–" : Number(v).toLocaleString(undefined, {maximumFractionDigits: d});
const KINDS = [["forex", "Forex"], ["commodity", "Gold & commodities"], ["index", "Indices"], ["crypto", "Crypto"], ["share", "Shares"]];
const form = $("#calc");
let products = [], live = {}, kind = "forex", timer = null, seq = 0, touched = false;

const spec = () => products.find(p => p.symbol === form.symbol.value);
const decimals = p => Math.max(0, Math.min(6, Math.ceil(-Math.log10(p.pip)) + 1));

function fillSymbols() {
  const list = products.filter(p => p.kind === kind);
  form.symbol.innerHTML = list.map(p => `<option value="${esc(p.symbol)}">${esc(p.symbol)} · ${esc(p.name)}</option>`).join("");
  pickSymbol();
}

// New market: set leverage to its default and put in a sensible example entry and stop.
function pickSymbol() {
  const p = spec();
  if (!p) return;
  form.leverage.value = p.leverage;
  const price = live[p.symbol] || p.price;
  const d = decimals(p);
  form.entry.value = price.toFixed(d);
  form.stop.value = (price - p.pip * (p.kind === "forex" ? 20 : p.kind === "share" ? 100 : 50) * (p.kind === "index" ? 0.4 : 1)).toFixed(d);
  form.target.value = "";
  run();
}

function query() {
  const f = new FormData(form), q = new URLSearchParams();
  for (const k of ["symbol", "balance", "risk", "entry", "stop", "target", "leverage", "win"]) if (f.get(k)) q.set(k, f.get(k));
  for (const k of ["USDJPY", "USDCAD", "AUDUSD", "EURUSD"]) if (live[k]) q.set(k.toLowerCase(), live[k]);
  return q;
}

function run() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const mine = ++seq;
    try {
      const r = await fetch("/api/calculator?" + query(), {cache: "no-store"});
      if (r.status === 401) return location.assign("/login");
      const d = await r.json();
      if (mine !== seq) return;
      if (!r.ok) { $("#msg").textContent = d.error || "Check the numbers"; $("#msg").className = "wl-msg err"; return; }
      $("#msg").textContent = ""; $("#msg").className = "wl-msg";
      render(d);
    } catch (e) {
      if (mine === seq) { $("#msg").textContent = "Can't reach the app right now"; $("#msg").className = "wl-msg err"; }
    }
  }, 180);
}

function render(d) {
  const s = d.size, lotWord = s.contract === 1 ? s.unit : "lots";
  $("#what").textContent = `${d.symbol} · ${d.name}`;
  $("#o-lots").textContent = s.too_small ? "too small" : num(s.contract === 1 ? s.units : s.lots, 4) + (s.contract === 1 ? "" : " lots");
  $("#o-units").textContent = s.too_small ? `risk is under one ${s.contract === 1 ? "step" : "lot step"} (${s.lot_step}); widen risk or tighten stop`
    : s.contract === 1 ? s.unit : `${num(s.units, 2)} ${s.unit}`;
  $("#o-side").textContent = d.side.toUpperCase(); $("#o-side").className = d.side;
  $("#o-side-sub").textContent = d.side === "buy" ? "stop is below entry" : "stop is above entry";
  $("#o-loss").textContent = usd(-d.loss_usd);
  $("#o-stop-pips").textContent = `${num(d.stop_pips, 1)} pips to stop`;
  $("#o-win").textContent = usd(d.win_usd);
  $("#o-target-pips").textContent = `${num(d.target_pips, 1)} pips${d.inputs.target_given ? "" : " (2.4x stop)"}`;
  const rows = [
    ["Money at risk", `${usd(d.risk_usd)} (${num(d.inputs.risk_pct)}% of ${usd(d.inputs.balance)})`],
    ["Risk : reward", `1 : ${num(d.rr)} · you need to win ${num(d.breakeven_win_rate, 1)}% to break even`],
    ["Target price", num(d.inputs.target, 6)],
    ["Pip size", `${d.pip} (${d.quote} price)`],
    ["Pip value", `${usd(d.pip_value_lot_usd)} per lot · ${usd(d.pip_value_usd)} for this size`],
    ["1 lot is", `${num(s.contract, 0)} ${s.unit}, smallest step ${s.lot_step} ${lotWord}`],
    ["Trade value", usd(d.notional_usd)],
    ["Margin needed", `${usd(d.margin_usd)} at ${num(d.leverage, 0)}:1 · effective leverage ${num(d.effective_leverage)}x`],
    ["Size the rules allow", `${num(d.rule_size_lots, 4)} ${lotWord} (risk cap ${usd(d.rule_cap_usd)})`],
  ];
  $("#detail").innerHTML = rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="r">${esc(v)}</td></tr>`).join("");
  $("#checks").innerHTML = d.checks.map(c => `<li><b class="${c.ok ? "ok" : "no"}">${c.ok ? "OK" : "OVER"}</b><div><strong>${esc(c.rule)}</strong> <span>${esc(c.detail)}</span></div></li>`).join("");
  const k = d.kelly;
  $("#kelly").innerHTML = !k ? "Add your win rate (the desk's practice win rate is a good start) to see the most Kelly would ever allow."
    : `<b>${k.edge ? num(k.half_pct) + "%" : "no edge"}</b>half-Kelly at ${num(k.win_rate, 0)}% wins and 1:${num(d.rr)} reward (full Kelly ${num(k.full_pct)}%). ${esc(k.note)}.`;
}

async function loadLive() {
  try {
    const r = await fetch("/api/markets", {cache: "no-store"});
    if (!r.ok) return;
    for (const p of (await r.json()).products || []) if (p.price) live[p.symbol] = p.price;
  } catch (e) { /* example prices still work */ }
}

$("#live").addEventListener("click", async () => {
  await loadLive();
  const p = spec();
  if (p && live[p.symbol]) { form.entry.value = live[p.symbol].toFixed(decimals(p)); run(); }
  else { $("#msg").textContent = "No live price right now"; $("#msg").className = "wl-msg err"; }
});
form.addEventListener("input", e => { if (e.target !== form.symbol) { touched = true; run(); } });
form.symbol.addEventListener("change", pickSymbol);
form.addEventListener("submit", e => { e.preventDefault(); run(); });

(async () => {
  const r = await fetch("/api/calculator", {cache: "no-store"});
  if (r.status === 401) return location.assign("/login");
  products = (await r.json()).products;
  $("#kinds").innerHTML = KINDS.map(([k, t]) => `<button type="button" data-k="${k}" class="${k === kind ? "on" : ""}">${t}</button>`).join("");
  $("#kinds").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    kind = b.dataset.k;
    document.querySelectorAll("#kinds button").forEach(x => x.classList.toggle("on", x === b));
    fillSymbols();
  });
  fillSymbols();
  await loadLive();
  if (!touched) pickSymbol();
})();
