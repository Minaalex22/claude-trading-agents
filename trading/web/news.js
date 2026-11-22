// News desk: headlines, analyst houses, managers, order flow footprint and charts, all from the app's own data.
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const num = (v, d = 2) => v == null || isNaN(v) ? "–" : Number(v).toFixed(d);
const sgn = v => (v >= 0 ? "+" : "") + num(v);
const lean = v => v > 0.1 ? "bull" : v < -0.1 ? "bear" : "flat";
const leanWord = v => v > 0.1 ? "BUY" : v < -0.1 ? "SELL" : "WAIT";
const C = {o: "#ff5c00", up: "#2fd37a", dn: "#ff4d4d", g: "#8a8a8a", line: "#1f1f1f", w: "#f4f4f4"};

let ORG = {}, FLOW = {books: {}, houses: []}, PRICES = {history: []}, picked = null;
const fpRedraw = window.Footprint ? Footprint.attach($("#fp"), () => FLOW.books[picked]) : () => {};

function canvas(id) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  if (c.width !== w * dpr || c.height !== h * dpr) { c.width = w * dpr; c.height = h * dpr; }
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  g.font = "11px ui-monospace, Menlo, monospace"; return [g, w, h];
}
function spark(c, vals) {
  const dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (vals.length < 2) return;
  const lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1;
  const up = vals[vals.length - 1] >= vals[0];
  g.beginPath(); vals.forEach((v, i) => { const x = i * w / (vals.length - 1), y = h - 3 - (v - lo) / sp * (h - 6); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.strokeStyle = up ? C.up : C.dn; g.lineWidth = 1.5; g.stroke();
  g.lineTo(w, h); g.lineTo(0, h); g.closePath(); g.fillStyle = up ? "rgba(47,211,122,.12)" : "rgba(255,77,77,.12)"; g.fill();
}
const meter = v => { const x = Math.max(-1, Math.min(1, v || 0)); return `<div class="pg-meter"><i class="${x >= 0 ? "up" : "dn"}" data-l="${x >= 0 ? 50 : 50 + x * 50}" data-w="${Math.abs(x) * 50}"></i></div>`; };
function sizeMeters(root) { root.querySelectorAll(".pg-meter i").forEach(i => { i.style.left = i.dataset.l + "%"; i.style.width = i.dataset.w + "%"; }); }

function views() { return (ORG.reading_room && ORG.reading_room.views) || {}; }
function flowDir(sym) { const b = FLOW.books[sym]; return b && b.view && b.view.ready ? b.view.direction : 0; }
function score(sym) { const v = views()[sym] || {}; return ((v.direction || 0) * 2 + flowDir(sym)) / 3; }
function symbols() { return [...new Set([...Object.keys(views()), ...Object.keys(FLOW.books)])]; }

function renderTop() {
  const syms = symbols();
  if (!syms.length) return;
  const s = syms.map(x => [x, score(x)]).sort((a, b) => b[1] - a[1]);
  const avg = s.reduce((a, [, v]) => a + v, 0) / s.length;
  const mood = $("#k-mood");
  mood.textContent = avg > 0.1 ? "Bullish" : avg < -0.1 ? "Bearish" : "Mixed";
  mood.className = avg > 0.1 ? "up" : avg < -0.1 ? "dn" : "o";
  $("#k-mood-sub").textContent = `average lean ${sgn(avg)} across ${s.length} markets`;
  $("#k-bull").textContent = s[0][0]; $("#k-bull-sub").textContent = `lean ${sgn(s[0][1])}`;
  const w = s[s.length - 1]; $("#k-bear").textContent = w[0]; $("#k-bear-sub").textContent = `lean ${sgn(w[1])}`;
  const rr = ORG.reading_room || {};
  $("#k-read").textContent = ((rr.read && rr.read.headlines) || 0).toLocaleString();
  $("#k-read-sub").textContent = rr.reader && rr.reader.claude ? "scored by Claude and the reading room" : "scored by the reading room";
}

function renderNews() {
  const news = ORG.news || [], v = views();
  $("#news-src").textContent = !news.length ? "no headlines yet" : news[0].kind === "live" ? "real headlines, scored by the reading room" : "practice wire (simulated headlines)";
  $("#news").innerHTML = news.slice(0, 12).map(n => {
    const l = (v[n.symbol] || {}).news || 0;
    return `<div class="pg-item"><span class="sym">${esc(n.symbol || "")}</span><div><span class="t">${esc(String(n.text || "").replace(/^[A-Z]+:\s*/, ""))}</span><small>${esc((n.kind || "").replace("_", " "))}${n.event ? " · " + esc(n.event.replace(/_/g, " ")) : ""}</small></div><span class="pg-pill ${lean(l)}">${leanWord(l)}</span></div>`;
  }).join("") || '<div class="pg-empty">Waiting for the first headlines…</div>';
}

function renderManagers() {
  $("#mgrs").innerHTML = (ORG.managers || []).map(m => `<div class="pg-item"><span class="sym">${esc(m.manager)}</span><div><span class="t">${esc(m.call || "")}</span><small>${esc(m.title || "")}</small></div><span class="${m.pnl >= 0 ? "up" : "dn"}">${m.pnl >= 0 ? "+" : "−"}$${Math.abs(m.pnl || 0).toLocaleString(undefined, {maximumFractionDigits: 0})}</span></div>`).join("") || '<div class="pg-empty">Waiting for the first report…</div>';
  const rows = Object.entries(FLOW.books).map(([s, b]) => [s, b.view || {}]).sort((a, b) => Math.abs(b[1].direction || 0) - Math.abs(a[1].direction || 0)).slice(0, 6);
  $("#flow").innerHTML = rows.map(([s, v]) => `<div class="pg-item"><span class="sym">${esc(s)}</span><div>${meter(v.direction)}<small>delta ${num(v.cum_delta, 0)} · ${(v.reasons || [])[0] ? esc(v.reasons[0]) : "no strong print"}</small></div><span class="pg-pill ${lean(v.direction)}">${leanWord(v.direction)}</span></div>`).join("") || '<div class="pg-empty">Waiting for prices…</div>';
  sizeMeters($("#flow"));
}

function renderBoard() {
  const syms = symbols(), v = views(), hist = PRICES.history || [];
  if (!syms.length) return;
  if (!picked || !syms.includes(picked)) picked = Object.keys(FLOW.books)[0] || syms[0];
  const box = $("#board");
  box.innerHTML = syms.map(s => {
    const px = hist.length ? hist[hist.length - 1].prices[s] : null, first = hist.length ? hist[0].prices[s] : null;
    const ch = px && first ? (px / first - 1) * 100 : 0, r = v[s] || {};
    return `<div class="pg-tile${s === picked ? " on" : ""}" data-s="${esc(s)}"><div class="top"><span class="o">${esc(s)}</span><span>${px ? num(px) : "–"} <span class="${ch >= 0 ? "up" : "dn"}">${ch >= 0 ? "+" : ""}${num(ch)}%</span></span></div><canvas></canvas>
      <div class="row"><span>news</span>${meter(r.news)}<span>${num(r.news)}</span></div>
      <div class="row"><span>chart</span>${meter(r.chart)}<span>${num(r.chart)}</span></div>
      <div class="row"><span>flow</span>${meter(flowDir(s))}<span>${num(flowDir(s))}</span></div></div>`;
  }).join("");
  box.querySelectorAll(".pg-tile").forEach(t => spark(t.querySelector("canvas"), hist.map(h => h.prices[t.dataset.s]).filter(x => x != null)));
  sizeMeters(box);
}
$("#board").addEventListener("click", e => { const t = e.target.closest(".pg-tile"); if (t) { picked = t.dataset.s; renderBoard(); renderFootprint(); } });

function renderFootprint() {
  const b = FLOW.books[picked];
  $("#fp-sym").textContent = picked ? `${picked}${b && b.feed ? " · " + b.feed : ""} · last bars` : "–";
  fpRedraw();
  if (!b || !b.bars.length) { canvas("#vp"); canvas("#cvd"); return; }
  // volume profile
  let [g, w, h] = canvas("#vp");
  const sess = Object.entries(b.session_levels).map(([k, v]) => [+k, v]).sort((a, c) => c[0] - a[0]);
  const mx = Math.max(1, ...sess.map(s => s[1])), sh = (h - 10) / Math.max(1, sess.length), pr = b.profile;
  sess.forEach(([p, v], i) => { g.fillStyle = p === pr.poc ? C.o : (p >= pr.val && p <= pr.vah) ? "rgba(255,92,0,.45)" : "#2a2a2a"; g.fillRect(60, i * sh + 5, (w - 64) * v / mx, Math.max(1, sh - 1)); });
  g.fillStyle = C.g; if (sess.length) { g.fillText(num(sess[0][0]), 2, 12); g.fillText(num(sess[sess.length - 1][0]), 2, h - 4); }
  $("#vp-sub").textContent = pr.poc == null ? "no volume yet" : `busiest ${num(pr.poc)} · value area ${num(pr.val)} – ${num(pr.vah)}`;
  // cumulative delta
  [g, w, h] = canvas("#cvd");
  const c = b.cvd; if (c.length < 2) return;
  const lo = Math.min(0, ...c), hi = Math.max(0, ...c), sp = hi - lo || 1, y = v => h - 8 - (v - lo) / sp * (h - 16);
  g.strokeStyle = C.line; g.beginPath(); g.moveTo(0, y(0)); g.lineTo(w, y(0)); g.stroke();
  g.beginPath(); c.forEach((v, i) => { const x = i * w / (c.length - 1); i ? g.lineTo(x, y(v)) : g.moveTo(x, y(v)); });
  g.strokeStyle = c[c.length - 1] >= 0 ? C.up : C.dn; g.lineWidth = 2; g.stroke();
  g.fillStyle = C.g; g.fillText(`now ${Math.round(c[c.length - 1])}`, w - 70, 12);
}

function renderLean() {
  let [g, w, h] = canvas("#lean");
  const v = Object.entries(views()); if (!v.length) return;
  const bw = w / v.length, mid = h / 2 - 6;
  g.strokeStyle = C.line; g.beginPath(); g.moveTo(0, mid); g.lineTo(w, mid); g.stroke();
  v.forEach(([s, r], i) => {
    const x = i * bw;
    [[r.news, "rgba(255,92,0,.9)", 0], [r.chart, "rgba(244,244,244,.55)", 1]].forEach(([val, col, k]) => {
      const bh = (val || 0) * (mid - 8); g.fillStyle = col;
      g.fillRect(x + bw * .2 + k * bw * .3, bh >= 0 ? mid - bh : mid, bw * .28, Math.abs(bh));
    });
    g.fillStyle = C.g; g.fillText(s, x + bw * .2, h - 2);
  });
  g.fillStyle = C.o; g.fillText("■ news", 4, 12); g.fillStyle = "rgba(244,244,244,.7)"; g.fillText("■ chart", 64, 12);
}

function renderHouses() {
  $("#houses").innerHTML = (FLOW.houses || []).map(hs => {
    const l = hs.latest;
    const call = l ? `<span class="pg-pill ${l.direction > 0 ? "bull" : l.direction < 0 ? "bear" : "flat"}">${esc(l.market)} ${l.direction > 0 ? "BULLISH" : l.direction < 0 ? "BEARISH" : "NEUTRAL"}</span> <span class="g">${esc(String(l.summary || "").slice(0, 110))}</span>` : '<span class="g">no public note yet</span>';
    return `<tr><td>${esc(hs.city)}</td><td>${esc(hs.name)}</td><td class="r">${hs.scored_calls ? Math.round(hs.hit_rate * 100) + "%" : '<span class="g">new</span>'}</td><td>${call}</td></tr>`;
  }).join("") || '<tr><td colspan="4" class="g">No analyst houses loaded yet.</td></tr>';
}

async function get(url) { const r = await fetch(url, {cache: "no-store"}); if (r.status === 401) { location.href = "/login"; throw 0; } return r.json(); }
async function load() {
  try { [ORG, FLOW, PRICES] = await Promise.all([get("/api/org"), get("/api/orderflow"), get("/api/prices")]); } catch (e) { return; }
  renderTop(); renderNews(); renderManagers(); renderBoard(); renderFootprint(); renderLean(); renderHouses();
}
addEventListener("resize", () => { renderBoard(); renderFootprint(); renderLean(); });
load(); setInterval(load, 8000);
