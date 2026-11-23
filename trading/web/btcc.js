// BTCC page: practice USDT perpetuals on live prices, the real BTCC account (read only, when BTCC's API is on),
// and BTCC news. Nothing here can send an order to BTCC.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 2) => (v == null ? "--" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const sgn = (v) => (v > 0 ? "ok" : v < 0 ? "bad" : "");
  const safeUrl = (u) => (/^https:\/\//.test(u || "") ? u : "#");
  let data = null, sym = "BTC", tab = "open", marks = {};

  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; throw new Error("sign in"); }
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }

  function prices() {
    Object.entries((data && data.marks) || {}).forEach(([s, v]) => { if (v) marks[s] = v; });
  }

  function strip() {
    $("strip").innerHTML = ["BTC", "ETH", "SOL"].map((s) => `<button type="button" class="${s === sym ? "on" : ""}" data-s="${s}"><b>${s}USDT</b> <span>${marks[s] ? num(marks[s], s === "BTC" ? 1 : 2) : "--"}</span><em>PERP</em></button>`).join("") +
      `<a href="${safeUrl(data && data.links.futures)}" target="_blank" rel="noopener noreferrer">Open BTCC ↗</a>`;
    $("syms").innerHTML = ["BTC", "ETH", "SOL"].map((s) => `<button type="button" class="${s === sym ? "on" : ""}" data-s="${s}">${s}</button>`).join("");
    $("price").textContent = marks[sym] ? num(marks[sym], sym === "BTC" ? 1 : 2) + " USDT" : "--";
  }

  function render() {
    if (!data) return;
    const a = data.account, p = data.practice;
    $("api").innerHTML = a.connected
      ? `<b class="ok">BTCC CONNECTED</b> read only · ${a.positions.length} open position(s) on your real account`
      : `<b class="warn">BTCC API ${a.api === "error" ? "ERROR" : "PAUSED BY BTCC"}</b> ${esc(a.status)} ${a.error ? "(" + esc(a.error) + ")" : ""} <a href="${safeUrl(a.notice)}" target="_blank" rel="noopener noreferrer">BTCC notice ↗</a>`;
    $("top").innerHTML = [["PRACTICE EQUITY", num(p.equity) + " USDT", "started with " + num(p.start, 0)],
      ["UNREALIZED P&L", `<span class="${sgn(p.unrealized)}">${num(p.unrealized)}</span>`, "margin in use " + num(p.margin)],
      ["REALIZED P&L", `<span class="${sgn(p.realized)}">${num(p.realized)}</span>`, (p.closed || []).length + " closed"],
      ["WIN RATE", p.win_rate == null ? "--" : p.win_rate + "%", "max leverage " + p.max_leverage + "x · real money locked"]]
      .map(([k, v, s]) => `<div><div class="k">${k}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`).join("");
    table();
    const n = data.news || [];
    $("newsn").textContent = n.length ? n.length + " stories" : "";
    $("news").innerHTML = (n.length ? n.slice(0, 15).map((it) => `<a class="bt-item" href="${safeUrl(it.link)}" target="_blank" rel="noopener noreferrer"><b>${esc(it.title)}</b><span>${esc(it.source)} · ${esc((it.published || "").slice(5, 22))}</span></a>`).join("")
      : `<div class="small">No BTCC headlines yet.</div>`) + `<a class="bt-item" href="${safeUrl(data.links.announcements)}" target="_blank" rel="noopener noreferrer"><b>All BTCC announcements ↗</b><span>btcc.com</span></a>`;
    strip();
  }

  function table() {
    const p = data.practice;
    let html;
    if (tab === "open") {
      html = p.open.length ? `<table class="pg-tbl"><tr><th>Contract</th><th>Side</th><th>Margin</th><th>Lev</th><th>Entry</th><th>Mark</th><th>SL / TP</th><th>P&L (ROE)</th><th></th></tr>` +
        p.open.map((o) => `<tr><td>${o.symbol}USDT</td><td class="${o.side === "long" ? "ok" : "bad"}">${o.side.toUpperCase()}</td><td>${num(o.margin)}</td><td>${o.leverage}x</td><td>${num(o.entry)}</td><td>${num(o.mark)}</td><td>${num(o.stop_loss)} / ${o.take_profit ? num(o.take_profit) : "--"}</td><td class="${sgn(o.pnl)}">${num(o.pnl)} (${o.roe}%)</td><td><button type="button" data-close="${o.id}">Close</button></td></tr>`).join("") + "</table>"
        : `<div class="small">No practice positions. Pick a contract, set a stop loss, and press Long or Short.</div>`;
    } else if (tab === "closed") {
      html = p.closed.length ? `<table class="pg-tbl"><tr><th>Contract</th><th>Side</th><th>Entry</th><th>Exit</th><th>Lev</th><th>P&L</th><th>Why</th></tr>` +
        p.closed.map((c) => `<tr><td>${c.symbol}USDT</td><td class="${c.side === "long" ? "ok" : "bad"}">${c.side.toUpperCase()}</td><td>${num(c.entry)}</td><td>${num(c.exit)}</td><td>${c.leverage}x</td><td class="${sgn(c.pnl)}">${num(c.pnl)}</td><td>${esc(c.reason)}</td></tr>`).join("") + "</table>"
        : `<div class="small">No closed practice trades yet.</div>`;
    } else {
      const a = data.account;
      html = a.connected
        ? (a.positions.length ? `<table class="pg-tbl"><tr><th>Contract</th><th>Side</th><th>Volume</th><th>Entry</th><th>Lev</th><th>P&L</th></tr>` + a.positions.map((o) => `<tr><td>${esc(o.symbol)}</td><td>${esc(o.side)}</td><td>${num(o.volume, 4)}</td><td>${num(o.entry)}</td><td>${o.leverage || "--"}x</td><td class="${sgn(o.pnl)}">${num(o.pnl)}</td></tr>`).join("") + "</table>" : `<div class="small">No open positions on your BTCC account.</div>`)
        : `<div class="small">Your real BTCC account can't be read right now because BTCC has paused its API for everyone. When BTCC turns it back on, make a read-only API key on BTCC and add these on Render: ${(a.needs || []).map(esc).join(", ") || "the BTCC keys"}. Never paste keys or your password in chat.</div>`;
    }
    $("table").innerHTML = html;
  }

  async function load() {
    try { data = await api("/api/btcc"); } catch (e) { return; }
    prices();
    render();
  }

  async function order(side) {
    $("msg").textContent = "";
    try {
      const body = { symbol: sym, side, margin: +$("margin").value, leverage: +$("lev").value, stop_loss: +$("sl").value, take_profit: $("tp").value || null };
      if (!body.stop_loss) throw new Error("set a stop loss first");
      const r = await api("/api/btcc/open", body);
      $("msg").innerHTML = `<span class="ok">Practice ${side} ${r.position.symbol} opened at ${num(r.position.entry)}.</span>`;
      tab = "open"; await load();
    } catch (e) { $("msg").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
  }

  document.addEventListener("click", async (ev) => {
    const t = ev.target.closest("button");
    if (!t) return;
    if (t.dataset.s) { sym = t.dataset.s; $("sl").value = ""; $("tp").value = ""; strip(); }
    else if (t.dataset.t) { tab = t.dataset.t; document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === t)); table(); }
    else if (t.dataset.close) { try { await api("/api/btcc/close", { id: +t.dataset.close }); await load(); } catch (e) { $("msg").textContent = e.message; } }
    else if (t.id === "long") order("long");
    else if (t.id === "short") order("short");
  });
  $("lev").addEventListener("input", () => { $("levv").textContent = $("lev").value + "x"; });
  load();
  setInterval(load, 15000);
})();
