// Mitrade page: Mitrade's own live bid/ask (public site feed, no login) and practice trades at those prices.
// Nothing here can send an order to Mitrade.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 2) => (v == null ? "--" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const sgn = (v) => (v > 0 ? "ok" : v < 0 ? "bad" : "");
  const safeUrl = (u) => (/^https:\/\//.test(u || "") ? u : "#");
  let data = null, sym = "XAUUSD", tab = "prices";

  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; throw new Error("sign in"); }
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  const q = (s) => (data && data.quotes && data.quotes[s]) || null;
  const dg = (s) => (q(s) ? q(s).digits : 2);

  function spark(arr) {
    if (!arr || arr.length < 2) return "";
    const lo = Math.min(...arr), hi = Math.max(...arr), w = 90, h = 22;
    const pts = arr.map((v, i) => `${(i / (arr.length - 1) * w).toFixed(1)},${(h - (hi === lo ? h / 2 : (v - lo) / (hi - lo) * h)).toFixed(1)}`).join(" ");
    const up = arr[arr.length - 1] >= arr[0];
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="${up ? "var(--ats-up)" : "var(--ats-dn)"}" stroke-width="1.5" points="${pts}"/></svg>`;
  }

  function ticket() {
    const sel = $("sym"), syms = Object.keys(data.practice.caps);
    if (!sel.options.length) sel.innerHTML = syms.map((s) => `<option value="${s}">${s}</option>`).join("");
    sel.value = sym;
    const x = q(sym), cap = data.practice.caps[sym] || 1;
    $("lev").max = cap; if (+$("lev").value > cap) $("lev").value = cap; $("levv").textContent = $("lev").value + "x (max " + cap + "x)";
    $("price").innerHTML = x ? `<span class="bad">${num(x.bid, x.digits)}</span> / <span class="ok">${num(x.ask, x.digits)}</span>` : "--";
    $("spread").textContent = x ? `sell at bid · buy at ask · Mitrade spread ${num(x.spread, x.digits)}` : "no Mitrade price yet";
  }

  function render() {
    if (!data) return;
    const f = data.feed, p = data.practice;
    $("api").innerHTML = f.connected
      ? `<b class="ok">MITRADE PRICES LIVE</b> ${f.count} markets from mitrade.com, no login · <a href="${safeUrl(data.links.site)}" target="_blank" rel="noopener noreferrer">Open Mitrade ↗</a>`
      : `<b class="warn">MITRADE FEED NOT ANSWERING</b> ${esc(f.error || "waiting for prices")} · <a href="${safeUrl(data.links.site)}" target="_blank" rel="noopener noreferrer">Open Mitrade ↗</a>`;
    $("top").innerHTML = [["PRACTICE EQUITY", "$" + num(p.equity), "started with $" + num(p.start, 0)],
      ["UNREALIZED P&L", `<span class="${sgn(p.unrealized)}">${num(p.unrealized)}</span>`, "margin in use " + num(p.margin)],
      ["REALIZED P&L", `<span class="${sgn(p.realized)}">${num(p.realized)}</span>`, (p.closed || []).length + " closed"],
      ["WIN RATE", p.win_rate == null ? "--" : p.win_rate + "%", "real money locked"]]
      .map(([k, v, s]) => `<div><div class="k">${k}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`).join("");
    ticket();
    table();
  }

  function table() {
    const p = data.practice;
    let html;
    if (tab === "prices") {
      const rows = Object.values(data.quotes || {});
      html = rows.length ? `<table class="pg-tbl"><tr><th>Market</th><th>Group</th><th>Sell (bid)</th><th>Buy (ask)</th><th>Spread</th><th>Day</th><th>High / Low</th><th>Trend</th><th></th></tr>` +
        rows.map((x) => `<tr><td><b>${esc(x.symbol)}</b></td><td>${esc(x.group)}</td><td class="bad">${num(x.bid, x.digits)}</td><td class="ok">${num(x.ask, x.digits)}</td><td>${num(x.spread, x.digits)}</td><td class="${sgn(x.change_pct)}">${x.change_pct == null ? "--" : x.change_pct + "%"}</td><td>${num(x.high, x.digits)} / ${num(x.low, x.digits)}</td><td>${spark((data.trend || {})[x.symbol])}</td><td><button type="button" data-s="${esc(x.symbol)}">Trade</button></td></tr>`).join("") + "</table>"
        : `<div class="small">No Mitrade prices yet. ${esc(data.feed.error || "")}</div>`;
    } else if (tab === "open") {
      html = p.open.length ? `<table class="pg-tbl"><tr><th>Market</th><th>Side</th><th>Margin</th><th>Lev</th><th>Entry</th><th>Now</th><th>SL / TP</th><th>P&L (ROE)</th><th></th></tr>` +
        p.open.map((o) => `<tr><td>${esc(o.symbol)}</td><td class="${o.side === "buy" ? "ok" : "bad"}">${o.side.toUpperCase()}</td><td>${num(o.margin)}</td><td>${o.leverage}x</td><td>${num(o.entry, dg(o.symbol))}</td><td>${num(o.mark, dg(o.symbol))}</td><td>${num(o.stop_loss, dg(o.symbol))} / ${o.take_profit ? num(o.take_profit, dg(o.symbol)) : "--"}</td><td class="${sgn(o.pnl)}">${num(o.pnl)} (${o.roe}%)</td><td><button type="button" data-close="${o.id}">Close</button></td></tr>`).join("") + "</table>"
        : `<div class="small">No practice positions. Pick a market, set a stop loss, and press Buy or Sell.</div>`;
    } else if (tab === "closed") {
      html = p.closed.length ? `<table class="pg-tbl"><tr><th>Market</th><th>Side</th><th>Entry</th><th>Exit</th><th>Lev</th><th>P&L</th><th>Why</th></tr>` +
        p.closed.map((c) => `<tr><td>${esc(c.symbol)}</td><td class="${c.side === "buy" ? "ok" : "bad"}">${c.side.toUpperCase()}</td><td>${num(c.entry, dg(c.symbol))}</td><td>${num(c.exit, dg(c.symbol))}</td><td>${c.leverage}x</td><td class="${sgn(c.pnl)}">${num(c.pnl)}</td><td>${esc(c.reason)}</td></tr>`).join("") + "</table>"
        : `<div class="small">No closed practice trades yet.</div>`;
    } else {
      html = `<div class="small">${esc(data.account.note)} Never paste your Mitrade password in chat. <a href="${safeUrl(data.links.web_trader)}" target="_blank" rel="noopener noreferrer">Open the Mitrade web trader ↗</a></div>`;
    }
    $("table").innerHTML = html;
  }

  async function load() {
    try { data = await api("/api/mitrade"); } catch (e) { return; }
    render();
  }

  async function order(side) {
    $("msg").textContent = "";
    try {
      const body = { symbol: sym, side, margin: +$("margin").value, leverage: +$("lev").value, stop_loss: +$("sl").value, take_profit: $("tp").value || null };
      if (!body.stop_loss) throw new Error("set a stop loss first");
      const r = await api("/api/mitrade/open", body);
      $("msg").innerHTML = `<span class="ok">Practice ${side} ${esc(r.position.symbol)} opened at ${num(r.position.entry, dg(sym))}.</span>`;
      tab = "open"; document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.t === "open"));
      await load();
    } catch (e) { $("msg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  }

  document.addEventListener("click", async (ev) => {
    const t = ev.target.closest("button");
    if (!t) return;
    if (t.dataset.s) { sym = t.dataset.s; $("sl").value = ""; $("tp").value = ""; ticket(); window.scrollTo({ top: 0, behavior: "smooth" }); }
    else if (t.dataset.t) { tab = t.dataset.t; document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === t)); table(); }
    else if (t.dataset.close) { try { await api("/api/mitrade/close", { id: +t.dataset.close }); await load(); } catch (e) { $("msg").textContent = e.message; } }
    else if (t.id === "buy") order("buy");
    else if (t.id === "sell") order("sell");
  });
  $("sym").addEventListener("change", () => { sym = $("sym").value; $("sl").value = ""; $("tp").value = ""; ticket(); });
  $("lev").addEventListener("input", () => { $("levv").textContent = $("lev").value + "x"; });
  load();
  setInterval(load, 10000);
})();
