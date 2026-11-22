// Wallet: balances across accounts, practice money in/out, crypto watch list. Data from /api/wallet.
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = (v, sign) => v == null ? "–" : (sign ? (v >= 0 ? "+" : "−") : v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cls = v => v == null ? "" : v >= 0 ? "up" : "dn";
const STATE = {connected: "CONNECTED", keys_set: "KEYS SET", not_connected: "NOT CONNECTED", not_installed: "COMING", idle: "IDLE", paper: "PAPER", error: "ERROR"};
const KIND = {start: "Opened", deposit: "Added", withdraw: "Taken out"};
let moveKind = "deposit";

function render(d) {
  $("#k-total").textContent = usd(d.total);
  $("#k-profit").textContent = usd(d.profit, true); $("#k-profit").className = cls(d.profit);
  const k = v => "$" + Math.round(v).toLocaleString();
  $("#k-inout").textContent = `${k(d.deposits)} / ${k(d.withdrawals)}`;
  $("#k-crypto").textContent = d.crypto.length ? usd(d.crypto_usd) : "–";
  $("#k-crypto-sub").textContent = d.crypto.length ? `${d.crypto.length} public address${d.crypto.length > 1 ? "es" : ""}, read only` : "add a public address below";

  $("#accounts").innerHTML = d.accounts.map(a => `<tr><td class="o">${esc(a.name)}</td>
    <td><span class="wl-state ${esc(a.state)}">${STATE[a.state] || esc(a.state)}</span></td>
    <td class="r ${a.key === "practice" ? "" : cls(a.balance)}">${a.balance == null ? "–" : (a.currency && a.currency !== "USD" ? esc(a.currency) + " " : "") + usd(a.balance, a.key === "desk" || a.key === "polymarket")}${a.equity != null ? `<br><small class="g">equity ${usd(a.equity)}</small>` : ""}</td>
    <td class="hide-s g">${esc(a.note)}</td></tr>`).join("");

  $("#crypto").innerHTML = d.crypto.map(c => `<tr><td class="o">${esc(c.label || "Unnamed")}</td><td>${esc(c.coin)}</td>
    <td class="hide-s"><a class="wl-addr" href="${esc(c.explorer)}" target="_blank" rel="noopener noreferrer">${esc(c.address.slice(0, 10))}…${esc(c.address.slice(-6))}</a></td>
    <td class="r">${c.balance == null ? `<span class="g">${esc(c.error || "–")}</span>` : c.balance.toFixed(c.balance >= 1 ? 4 : 8) + " " + esc(c.coin)}</td>
    <td class="r">${usd(c.usd)}</td><td class="r"><button class="wl-x" data-a="${esc(c.address)}" title="Stop watching">×</button></td></tr>`).join("")
    || '<tr><td colspan="6" class="g">No addresses yet. Add a public Bitcoin or Ethereum address to see its balance here.</td></tr>';

  $("#ledger").innerHTML = d.ledger.map(e => `<tr class="${e.kind === "withdraw" ? "loss" : "win"}">
    <td>${new Date(e.ts * 1000).toLocaleString([], {hour12: false})}</td><td>${KIND[e.kind] || esc(e.kind)}</td>
    <td class="r ${e.kind === "withdraw" ? "dn" : "up"}">${e.kind === "withdraw" ? "−" : "+"}${usd(e.amount)}</td>
    <td class="r">${usd(e.balance)}</td><td class="hide-s g">${esc(e.note)}</td></tr>`).join("");
  $("#rules").innerHTML = d.rules.map(r => `<li>${esc(r)}</li>`).join("");
}

async function load() {
  try {
    const r = await fetch("/api/wallet", {cache: "no-store"});
    if (r.status === 401) return location.assign("/login");
    render(await r.json());
  } catch (e) { /* keep the last view; try again on the next round */ }
}

async function post(path, body, msgEl, form) {
  const btn = form.querySelector(".wl-go"); btn.disabled = true; msgEl.className = "wl-msg"; msgEl.textContent = "…";
  try {
    const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "something went wrong");
    msgEl.className = "wl-msg ok"; msgEl.textContent = "Done."; form.reset(); await load();
  } catch (e) { msgEl.className = "wl-msg err"; msgEl.textContent = e.message; }
  btn.disabled = false;
}

$("#kind").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  moveKind = b.dataset.k; $("#kind").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
});
$("#move").addEventListener("submit", e => {
  e.preventDefault(); const f = e.target;
  post("/api/wallet/move", {kind: moveKind, amount: Number(f.amount.value), note: f.note.value}, $("#move-msg"), f);
});
$("#watch").addEventListener("submit", e => {
  e.preventDefault(); const f = e.target;
  post("/api/wallet/watch", {address: f.address.value.trim(), label: f.label.value}, $("#watch-msg"), f);
});
$("#crypto").addEventListener("click", async e => {
  const b = e.target.closest(".wl-x"); if (!b) return;
  await fetch("/api/wallet/unwatch", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({address: b.dataset.a})});
  load();
});
load(); setInterval(load, 15000);
