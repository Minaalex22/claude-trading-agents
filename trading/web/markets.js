// Markets board: favourites, all 25 products by kind, top risers and fallers.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = x => x == null ? "–" : Math.abs(x) >= 1000 ? x.toLocaleString(undefined, {maximumFractionDigits: 2}) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(4);
const pct = x => x == null ? '<span class="mut">–</span>' : `<span class="${x >= 0 ? "up" : "dn"}">${x >= 0 ? "+" : ""}${x.toFixed(2)}%</span>`;
const TABS = [["favourites", "★ Favourites"], ["all", "All 25"], ["crypto", "Crypto"], ["forex", "Forex"], ["commodity", "Commodities"], ["index", "Indices"], ["share", "Shares"]];
let data = null, tab = "favourites";

function spark(v, w = 90, h = 24) {
  if (!v || v.length < 2) return "";
  const mn = Math.min(...v), mx = Math.max(...v), r = mx - mn || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - (x - mn) / r * h).toFixed(1)}`).join(" ");
  return `<svg width="${w}" height="${h}" aria-hidden="true"><polyline fill="none" stroke="${v[v.length - 1] >= v[0] ? "#2fd37a" : "#ff4d4d"}" stroke-width="1.5" points="${pts}"/></svg>`;
}
const mini = rows => rows.map(r => `<tr><td><a href="/markets/${esc(r.symbol)}">${esc(r.name)}</a></td><td>${fmt(r.price)}</td><td>${pct(r.change_1d)}</td></tr>`).join("") || '<tr><td class="mut">No data yet</td></tr>';

function render() {
  $("#tabs").innerHTML = TABS.map(([k, l]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("");
  let rows = data.products;
  if (tab === "favourites") rows = rows.filter(r => data.favourites.includes(r.symbol));
  else if (tab !== "all") rows = rows.filter(r => r.kind === tab);
  $("#board").innerHTML = "<tr><th></th><th class='l'>Product</th><th>Price</th><th>5 min</th><th>1 day</th><th>40 days</th><th>48 h</th></tr>" +
    (rows.map(r => `<tr><td><span class="mk-star ${data.favourites.includes(r.symbol) ? "on" : ""}" data-fav="${esc(r.symbol)}" role="button" tabindex="0">★</span></td>
     <td class="l"><a href="/markets/${esc(r.symbol)}">${esc(r.name)}</a> <span class="mut">${esc(r.symbol)}</span></td>
     <td>${fmt(r.price)}</td><td>${pct(r.change_5m)}</td><td>${pct(r.change_1d)}</td><td>${pct(r.change_40d)}</td><td>${spark(r.spark)}</td></tr>`).join("")
     || '<tr><td></td><td class="l mut">No favourites yet. Tap ★ on any product.</td></tr>');
  $("#risers").innerHTML = mini(data.movers_1d.risers);
  $("#fallers").innerHTML = mini(data.movers_1d.fallers);
  const sim = data.products.filter(r => r.source === "simulated").length;
  $("#src").textContent = sim ? `${sim} of ${data.products.length} products show simulated prices because the live source can't be reached.`
    : "Live prices: Coinbase for crypto, Yahoo Finance for the rest.";
}

async function fav(sym) {
  const r = await fetch(`/api/markets/${encodeURIComponent(sym)}/favourite`, {method: "POST"});
  if (r.ok) { data.favourites = (await r.json()).favourites; render(); }
}
document.addEventListener("click", e => {
  const t = e.target.closest("[data-tab],[data-fav]");
  if (!t) return;
  if (t.dataset.tab) { tab = t.dataset.tab; render(); } else fav(t.dataset.fav);
});
async function load() {
  try {
    const r = await fetch("/api/markets");
    if (r.status === 401) return location.assign("/login");
    data = await r.json(); render();
  } catch (e) { $("#src").textContent = "Couldn't load prices: " + e.message; }
}
load(); setInterval(load, 15000);
