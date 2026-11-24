// News Lab: the controls go to /api/newslab, which runs the lab (trading/news_lab.py) and returns every number shown.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const usd = v => v == null ? "–" : (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString(undefined, {maximumFractionDigits: 0});
const pct = v => v == null ? "–" : (v * 100).toFixed(1) + "%";
const sgn = v => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2);
const form = $("#ctl");
const SLIDERS = ["gate", "half_life", "news_share", "confident", "cost_bps", "honest"];
const SHOW = {gate: v => (+v).toFixed(2), half_life: v => Math.round(v) + " ticks", news_share: v => Math.round(v * 100) + "%",
  confident: v => (+v).toFixed(2), cost_bps: v => (+v) + " bps", honest: v => Math.round(v * 100) + "%"};
let state = {}, defaults = null, timer = null, seq = 0, last = null;

function setControls(p) {
  state = {...p};
  for (const k of SLIDERS) { form[k].value = p[k]; $("#v-" + k).textContent = SHOW[k](p[k]); }
  for (const b of document.querySelectorAll("#sources button")) b.classList.toggle("on", +b.dataset.v === +p.sources);
  for (const b of document.querySelectorAll("#pipe button")) b.classList.toggle("on", +b.dataset.v === (p.use_pipeline ? 1 : 0));
  $("#seed").textContent = `Practice market #${p.seed}. Unseen-market check uses #${p.seed + 1000}.`;
}

function run() {
  clearTimeout(timer);
  $("#busy").textContent = "working…";
  timer = setTimeout(async () => {
    const mine = ++seq, q = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) q.set(k, k === "use_pipeline" ? (v ? 1 : 0) : v);
    try {
      const r = await fetch("/api/newslab?" + q, {cache: "no-store"});
      if (r.status === 401) return location.assign("/login");
      const d = await r.json();
      if (mine !== seq) return;
      $("#busy").textContent = "";
      if (!defaults) { defaults = d.defaults; setControls(d.params); }
      last = d;
      render(d);
    } catch (e) {
      if (mine === seq) $("#busy").textContent = "can't reach the app";
    }
  }, 200);
}

function line(ctx, pts, w, h, lo, hi, color, width) {
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath();
  pts.forEach((v, i) => { const x = i / Math.max(1, pts.length - 1) * w, y = h - (v - lo) / (hi - lo || 1) * h; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.stroke();
}

function canvas(el) {
  const r = el.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  el.width = r.width * dpr; el.height = r.height * dpr;
  const ctx = el.getContext("2d"); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, r.width, r.height);
  return [ctx, r.width, r.height];
}

function drawCurve(d) {
  const [ctx, w, h] = canvas($("#curve"));
  const sets = [[d.compare.chart_only_curve, "#555", 1.5], [d.compare.today_curve, "#bbb", 1.5], [d.curve, "#ff5c00", 2.5]];
  const all = sets.flatMap(s => s[0]);
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  const pad = 6, H = h - pad * 2;
  ctx.save(); ctx.translate(0, pad);
  const zy = H - (0 - lo) / (hi - lo || 1) * H;
  ctx.strokeStyle = "#222"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, zy); ctx.lineTo(w, zy); ctx.stroke();
  for (const [pts, c, lw] of sets) line(ctx, pts, w, H, lo, hi, c, lw);
  ctx.restore();
}

function drawSweep(key, rows, mine) {
  const el = $("#sw-" + key);
  const [ctx, w, h] = canvas(el);
  const ys = rows.map(r => r.pnl), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys);
  const xs = rows.map(r => r.x), x0 = Math.min(...xs), x1 = Math.max(...xs);
  const pad = 14, W = w - pad * 2, H = h - pad * 2;
  const X = x => pad + (x - x0) / (x1 - x0 || 1) * W, Y = y => pad + H - (y - lo) / (hi - lo || 1) * H;
  ctx.strokeStyle = "#222"; ctx.beginPath(); ctx.moveTo(pad, Y(0)); ctx.lineTo(w - pad, Y(0)); ctx.stroke();
  ctx.strokeStyle = "#ff5c00"; ctx.lineWidth = 2; ctx.beginPath();
  rows.forEach((r, i) => i ? ctx.lineTo(X(r.x), Y(r.pnl)) : ctx.moveTo(X(r.x), Y(r.pnl))); ctx.stroke();
  ctx.fillStyle = "#8a8a8a"; ctx.font = "10px ui-monospace, monospace";
  let lastX = -99;
  rows.forEach(r => { if (X(r.x) - lastX < 26) return; lastX = X(r.x); ctx.fillText(key === "news_share" ? Math.round(r.x * 100) + "%" : r.x, X(r.x) - 8, h - 1); });
  ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(X(Math.min(x1, Math.max(x0, mine.x))), Y(mine.pnl), 5, 0, 7); ctx.fill();
  ctx.fillStyle = "#8a8a8a"; ctx.fillText(usd(hi), pad, 10);
}

function render(d) {
  const s = d.summary, p = d.pipeline, a = d.analysts;
  $("#k-pnl").textContent = usd(s.pnl); $("#k-pnl").className = s.pnl >= 0 ? "up" : "dn";
  $("#k-pnl-sub").textContent = `after ${usd(s.fees)} costs`;
  $("#k-win").textContent = pct(s.win_rate); $("#k-win-sub").textContent = `${s.wins} of ${s.trades} trades`;
  $("#k-dd").textContent = usd(s.max_drawdown);
  $("#k-bill").textContent = "$" + p.bill.hybrid.toFixed(2);
  $("#k-bill-sub").textContent = `${Math.round(p.savings * 100)}% less than deep-reading all ${p.read}`;
  drawCurve(d);
  const c = d.compare, rows = [["Your settings", c.yours, "me"], ["Today's settings", c.today], ["Chart only, no news", c.chart_only], ["Yours on an unseen market", c.unseen]];
  $("#cmp").innerHTML = rows.map(([n, r, cls]) => `<tr class="${cls || ""}"><td>${n}</td><td class="r ${r.pnl >= 0 ? "up" : "dn"}">${usd(r.pnl)}</td><td class="r">${pct(r.win_rate)}</td><td class="r">${r.trades}</td><td class="r hide-s">${usd(r.max_drawdown)}</td></tr>`).join("");

  const st = (label, val, sub, note) => `<div class="st"><span>${label}</span><b>${val}</b>${sub ? `<small>${sub}</small>` : ""}</div>`;
  $("#analysts").innerHTML = [
    st("Headlines read", a.headlines, `${a.confirmed} confirmed by a second site, ${a.headlines - a.confirmed} rumours`),
    st("Reading room got the way right", pct(a.room_right), `${a.room_read} it could read, ${a.room_unread} it couldn't (no known words)`),
    st("Cheap read alone right", pct(a.cheap_right), `${a.cheap_calls} BUY/SELL calls from the word list`),
    st("Pipeline after the gate right", pct(a.pipeline_right), `${a.pipeline_calls} calls · ${p.kept} kept cheap, ${p.deep} deep read (${p.overruled} big news), ${p.human} sent to you`),
  ].join("");
  $("#formulas").innerHTML = d.formulas.map(f => `<li><b>${esc(f.name)}</b><span>${esc(f.text)}</span></li>`).join("");
  $("#checks").innerHTML = d.checks.map(k => `<li><b class="${k.level}">${k.level === "ok" ? "PASS" : k.level === "warn" ? "WATCH" : "FAIL"}</b><strong>${esc(k.check)}</strong><span>${esc(k.detail)}</span></li>`).join("");

  for (const key of ["news_share", "half_life", "gate"]) {
    const rows = d.sweeps[key];
    drawSweep(key, rows, {x: d.params[key], pnl: s.pnl});
  }
  const gp = d.sweeps.gate.map(r => r.pnl);
  $("#gate-note").textContent = Math.max(...gp) - Math.min(...gp) < 1
    ? (d.params.use_pipeline ? "The gate barely changes trades here." : "Gate only changes the bill while the pipeline is advice only. Switch it to filter the news to see trades move.")
    : "";

  $("#trace").innerHTML = d.trace.map(r => {
    const truth = r.truth > 0 ? "up" : "down";
    const call = r.call === "BUY" ? "bull" : r.call === "SELL" ? "bear" : "flat";
    const right = r.call !== "WAIT" && ((r.call === "BUY") === (r.truth > 0));
    const route = r.human ? `<span class="pg-pill human">YOU</span>` : `<span class="pg-pill ${r.route}">${r.route === "deep" ? "DEEP" : "KEPT"}</span>`;
    const sites = r.confirmed ? `2${r.second_tick ? " @" + r.second_tick : ""}` : "1";
    return `<tr class="${r.used ? "" : "off"}"><td>${r.tick}</td><td class="o">${esc(r.symbol)}</td><td>${esc(r.text)}${r.overrule ? ` <span class="g">· big news: ${esc(r.overrule)}</span>` : ""}</td>`
      + `<td class="hide-s">${sites}</td><td class="r">${r.push == null ? "<span class='g'>can't tell</span>" : sgn(r.push)}</td><td class="r hide-s">${r.conf.toFixed(2)}</td>`
      + `<td>${route}</td><td><span class="pg-pill ${call}">${r.call}</span>${r.call !== "WAIT" ? (right ? " <span class='up'>✓</span>" : " <span class='dn'>✗</span>") : ""}</td>`
      + `<td class="${truth === "up" ? "up" : "dn"}">${truth}</td><td class="r">${sgn(r.news_before)} → ${sgn(r.news_after)}</td></tr>`;
  }).join("");

  $("#live").innerHTML = d.live.length ? d.live.map(r => `<div class="pg-item"><span class="sym">${esc(r.symbol || "?")}</span><span class="t">${esc(r.text)}<small>${r.confidence.toFixed(2)} sure · app: ${esc(r.app_route)} at ${r.app_gate}</small></span><span class="pg-pill ${r.your_route === "deep" ? "deep" : "kept"}">${r.your_route === "deep" ? "DEEP" : "KEPT"}${r.changed ? " · changed" : ""}</span></div>`).join("")
    : `<div class="pg-empty">No live headlines read yet. They show up once the app has read some news.</div>`;
  $("#trades").innerHTML = d.recent_trades.map(t => `<tr class="${t.pnl > 0 ? "win" : "loss"}"><td class="o">${esc(t.symbol)}</td><td>${t.side > 0 ? "BUY" : "SELL"}</td><td class="r">${t.close_tick - t.open_tick}</td><td class="r">${usd(t.fee)}</td><td class="r ${t.pnl > 0 ? "up" : "dn"}">${usd(t.pnl)}</td></tr>`).join("");
}

for (const k of SLIDERS) form[k].addEventListener("input", () => { state[k] = +form[k].value; $("#v-" + k).textContent = SHOW[k](state[k]); run(); });
document.querySelectorAll("#sources button").forEach(b => b.addEventListener("click", () => { state.sources = +b.dataset.v; setControls(state); run(); }));
document.querySelectorAll("#pipe button").forEach(b => b.addEventListener("click", () => { state.use_pipeline = b.dataset.v === "1"; setControls(state); run(); }));
$("#newmkt").addEventListener("click", () => { state.seed = (state.seed % 999) + 1; setControls(state); run(); });
$("#reset").addEventListener("click", () => { if (defaults) { setControls({...defaults, seed: state.seed}); run(); } });
window.addEventListener("resize", () => { if (last) render(last); });
run();
