// Smart money: insiders' real open-market trades (SEC Form 4) and the analyst houses' calls. Read only.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const usd = (v) => (v == null ? "--" : "$" + Math.round(v).toLocaleString());
  const kv = (rows) => rows.map(([k, v, c]) => `<div class="kv"><span>${esc(k)}</span><b class="${c || ""}">${v}</b></div>`).join("");
  const panel = (t, sub, body) => `<div class="dz-p"><h3>${esc(t)}<span>${esc(sub || "")}</span></h3>${body}</div>`;
  const SIG = { buying: "ok", selling: "bad", mixed: "warn", "no data": "mu" };
  async function load() {
    let d;
    try { const r = await fetch("/api/smart", { credentials: "same-origin" }); if (r.status === 401) { location.href = "/login"; return; } d = await r.json(); }
    catch (e) { return; }
    const st = d.stocks || [];
    const buying = st.filter((s) => s.signal === "buying").length, selling = st.filter((s) => s.signal === "selling").length;
    $("top").innerHTML = [["INSIDERS BUYING", buying, "stocks where insiders bought 70%+ of the dollars"], ["INSIDERS SELLING", selling, "often routine planned sales"],
      ["SOURCE", d.ready ? "SEC" : "OFF", d.source || "needs setup"], ["ANALYST HOUSES", (d.street || []).length, "public calls, scored on our record"]]
      .map(([k, v, s]) => `<div><div class="k">${k}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`).join("");
    let html = "";
    if (d.setup) html = panel("Switch on insider data", "one step", `<div class="small">${esc(d.setup)}</div>`);
    else if (!st.length) html = panel("Loading", "", `<div class="small">${d.error ? "SEC error: " + esc(d.error) : "Fetching Form 4 filings from SEC EDGAR, about a minute…"}</div>`);
    html += st.map((s) => panel(s.ticker, "last " + (d.days || 90) + " days",
      `<div class="big ${SIG[s.signal]}">${esc(s.signal.toUpperCase())}</div>` +
      kv([["bought", `${usd(s.buy_dollars)} (${s.buys})`, "ok"], ["sold", `${usd(s.sell_dollars)} (${s.sells})`, "bad"], ["planned sales", s.planned_sales]]) +
      (s.people || []).slice(0, 3).map((p) => `<div class="small"><b>${esc(p.name)}</b> ${esc(p.title || "")}: ${p.bought ? "bought " + usd(p.bought) : ""}${p.bought && p.sold ? ", " : ""}${p.sold ? "sold " + usd(p.sold) : ""}</div>`).join(""))).join("");
    $("grid").innerHTML = html;
    $("street").innerHTML = panel("Analyst houses", "New York, London, China, Singapore",
      (d.street || []).map((h) => `<div class="kv"><span>${esc(h.name || h.id)} · ${esc(h.city || "")}</span><b>${h.latest ? esc(h.latest.market + " " + (h.latest.direction > 0 ? "bullish" : h.latest.direction < 0 ? "bearish" : "neutral")) : "<span class=mu>no call yet</span>"} · hit ${Math.round((h.hit_rate || 0) * 100)}%</b></div>`).join("") || '<div class="small">No calls yet.</div>');
  }
  load(); setInterval(load, 15000);
})();
