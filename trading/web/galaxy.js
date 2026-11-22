// Agent galaxy: every agent is a dot, clustered around its department; the top 20 agents by profit
// are the big orange nodes, wired to the markets they trade. A live terminal shows the desk's orders.
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = x => x == null ? "–" : (x < 0 ? "-$" : "$") + Math.abs(x).toLocaleString(undefined, {maximumFractionDigits: 0});
const signed = x => x == null ? "–" : (x >= 0 ? "+" : "-") + "$" + Math.abs(x).toLocaleString(undefined, {maximumFractionDigits: 0});
const pct = x => x == null ? "–" : (x * 100).toFixed(1) + "%";
const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const ORANGE = "255,92,0", AMBER = "255,160,64";
const BEAT_MS = 1400;

// Seeded random so the galaxy has the same shape on every load.
let seed = 7;
const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
const gauss = () => { let u = 0, v = 0; while (!u) u = rnd(); while (!v) v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

const world = {hubs: [], symbols: {}, dots: [], links: [], heroes: [], particles: [], built: false};
let topAgents = [];
let active = 0;
let beatAt = performance.now();
const cam = {x: 0, y: 0, z: 1, tx: 0, ty: 0, tz: 1};

function deptOf(agent) { return String(agent).split("-")[0]; }

function build(departments, symbols) {
  seed = 7;
  const keys = Object.keys(departments);
  world.hubs = keys.map((k, i) => {
    const a = (i / keys.length) * Math.PI * 2 - Math.PI / 2;
    return {key: k, name: departments[k].name || k, agents: departments[k].agents || 0, x: Math.cos(a) * 0.62, y: Math.sin(a) * 0.52};
  });
  world.symbols = {};
  symbols.forEach((s, i) => {
    const a = (i / symbols.length) * Math.PI * 2;
    world.symbols[s] = {name: s, x: Math.cos(a) * 0.2, y: Math.sin(a) * 0.17, flash: 0};
  });
  // One dot per few agents (capped) so big desks stay smooth on phones.
  const total = world.hubs.reduce((t, h) => t + h.agents, 0) || 400;
  const per = Math.max(1, Math.ceil(total / 520));
  world.dots = [];
  world.hubs.forEach(h => {
    const n = Math.max(30, Math.round(h.agents / per));
    for (let i = 0; i < n; i++) {
      const r = 0.16 * Math.abs(gauss());
      const a = rnd() * Math.PI * 2;
      world.dots.push({hub: h.key, x: h.x + Math.cos(a) * r, y: h.y + Math.sin(a) * r * 0.85, s: 0.6 + rnd() * 1.2, tw: rnd() * 6.28, d: 0.4 + rnd() * 0.6});
    }
  });
  // Faint constellation lines: each dot to its nearest neighbour in the same department.
  world.links = [];
  for (let i = 0; i < world.dots.length; i += 2) {
    const a = world.dots[i];
    let best = -1, bd = 0.004;
    for (let j = 0; j < world.dots.length; j++) {
      if (j === i || world.dots[j].hub !== a.hub) continue;
      const dx = world.dots[j].x - a.x, dy = world.dots[j].y - a.y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = j; }
    }
    if (best >= 0) world.links.push([i, best]);
  }
  world.particles = Array.from({length: 140}, () => ({x: rnd() * 2.6 - 1.3, y: rnd() * 2.2 - 1.1, vx: (rnd() - .5) * 0.0004, vy: (rnd() - .5) * 0.0003, s: rnd() * 1.4 + 0.2, o: rnd() * 0.5 + 0.1, warm: rnd() < 0.3}));
  world.built = true;
  placeHeroes();
}

function placeHeroes() {
  seed = 99;
  world.heroes = topAgents.map((t, i) => {
    const h = world.hubs.find(x => x.key === deptOf(t.agent)) || world.hubs[i % Math.max(1, world.hubs.length)] || {x: 0, y: 0};
    const a = rnd() * Math.PI * 2, r = 0.05 + rnd() * 0.1;
    return {i, agent: t.agent, symbol: t.symbol, x: h.x + Math.cos(a) * r, y: h.y + Math.sin(a) * r * 0.85, hub: h};
  });
}

// ---- drawing ----
const canvas = $("#sky");
const ctx = canvas.getContext("2d");
let W = 0, H = 0, DPR = 1;
function resize() {
  const r = canvas.getBoundingClientRect();
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = r.width; H = r.height;
  canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}
window.addEventListener("resize", resize);

function toScreen(x, y, depth = 1) {
  const k = Math.min(W, H) * 0.5 * cam.z;
  return [W / 2 + (x - cam.x * depth) * k, H / 2 + (y - cam.y * depth) * k];
}

function frame(now) {
  requestAnimationFrame(frame);
  if (!W) resize();
  const t = now / 1000;
  // Camera: slow drift, gentle push-in, eased towards the active node.
  const hero = world.heroes[active];
  cam.tx = hero ? hero.x * 0.35 : 0;
  cam.ty = hero ? hero.y * 0.35 : 0;
  cam.tz = 1.06 + (reduced ? 0 : 0.04 * Math.sin(t * 0.08));
  const ease = reduced ? 1 : 0.035;
  cam.x += (cam.tx + (reduced ? 0 : Math.sin(t * 0.21) * 0.025) - cam.x) * ease;
  cam.y += (cam.ty + (reduced ? 0 : Math.cos(t * 0.17) * 0.02) - cam.y) * ease;
  cam.z += (cam.tz - cam.z) * ease;

  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  if (!world.built) return;

  // 1. far particles (slow parallax)
  for (const p of world.particles) {
    if (!reduced) { p.x += p.vx; p.y += p.vy; if (p.x > 1.4) p.x = -1.4; if (p.x < -1.4) p.x = 1.4; if (p.y > 1.2) p.y = -1.2; if (p.y < -1.2) p.y = 1.2; }
    const [sx, sy] = toScreen(p.x, p.y, 0.4);
    ctx.fillStyle = p.warm ? `rgba(${AMBER},${p.o * 0.6})` : `rgba(200,200,200,${p.o * 0.5})`;
    ctx.fillRect(sx, sy, p.s, p.s);
  }

  // 2. constellation lines and agent dots
  ctx.lineWidth = 0.5;
  ctx.strokeStyle = "rgba(140,140,140,0.12)";
  ctx.beginPath();
  for (const [a, b] of world.links) {
    const [x1, y1] = toScreen(world.dots[a].x, world.dots[a].y, 0.8);
    const [x2, y2] = toScreen(world.dots[b].x, world.dots[b].y, 0.8);
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
  }
  ctx.stroke();
  const activeHub = hero ? hero.hub.key : null;
  for (const d of world.dots) {
    const [sx, sy] = toScreen(d.x, d.y, 0.8);
    const tw = 0.55 + 0.45 * Math.sin(t * 1.3 + d.tw);
    const hot = d.hub === activeHub;
    ctx.fillStyle = hot ? `rgba(${ORANGE},${0.35 + 0.4 * tw * d.d})` : `rgba(180,180,180,${0.18 + 0.3 * tw * d.d})`;
    ctx.beginPath(); ctx.arc(sx, sy, d.s, 0, Math.PI * 2); ctx.fill();
  }

  // 3. department hubs
  ctx.font = "10px ui-monospace, Menlo, monospace";
  ctx.textAlign = "center";
  for (const h of world.hubs) {
    const [sx, sy] = toScreen(h.x, h.y);
    const on = h.key === activeHub;
    ctx.fillStyle = on ? `rgba(${ORANGE},0.9)` : "rgba(160,160,160,0.6)";
    ctx.fillText(h.name.toUpperCase() + " · " + h.agents, sx, sy - Math.min(W, H) * 0.5 * cam.z * 0.19);
  }

  // 4. market nodes in the core
  for (const s of Object.values(world.symbols)) {
    const [sx, sy] = toScreen(s.x, s.y);
    s.flash *= 0.94;
    const f = s.flash;
    const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, 14 + f * 18);
    g.addColorStop(0, `rgba(${ORANGE},${0.25 + f * 0.6})`); g.addColorStop(1, "rgba(255,92,0,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(sx, sy, 14 + f * 18, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = f > 0.2 ? "#fff" : "rgba(255,255,255,0.75)";
    ctx.beginPath(); ctx.arc(sx, sy, 2.4 + f * 2, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = f > 0.2 ? `rgba(${ORANGE},1)` : "rgba(200,200,200,0.7)";
    ctx.fillText(s.name, sx, sy + 16);
  }

  // 5. hero agents: edges to their market and hub, glow on the active one
  world.heroes.forEach((h, i) => {
    const on = i === active;
    const [sx, sy] = toScreen(h.x, h.y);
    const m = world.symbols[h.symbol];
    if (m) {
      const [mx, my] = toScreen(m.x, m.y);
      ctx.strokeStyle = on ? `rgba(${ORANGE},0.85)` : `rgba(${ORANGE},0.13)`;
      ctx.lineWidth = on ? 1.4 : 0.6;
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(mx, my); ctx.stroke();
    }
    if (on) {
      // radiate to near neighbours in the same department
      ctx.strokeStyle = `rgba(${ORANGE},0.35)`; ctx.lineWidth = 0.6; ctx.beginPath();
      let n = 0;
      for (const d of world.dots) {
        if (d.hub !== h.hub.key) continue;
        const dx = d.x - h.x, dy = d.y - h.y;
        if (dx * dx + dy * dy < 0.006) { const [x2, y2] = toScreen(d.x, d.y, 0.8); ctx.moveTo(sx, sy); ctx.lineTo(x2, y2); if (++n > 14) break; }
      }
      ctx.stroke();
    }
    const since = (performance.now() - beatAt) / 600;
    const pulse = on ? 1 + 0.25 * Math.max(0, 1 - since) + 0.08 * Math.sin(t * 4) : 1;
    const r = (on ? 5 : 3.2) * pulse;
    const glow = ctx.createRadialGradient(sx, sy, 0, sx, sy, r * (on ? 7 : 4));
    glow.addColorStop(0, `rgba(${ORANGE},${on ? 0.65 : 0.35})`); glow.addColorStop(1, "rgba(255,92,0,0)");
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(sx, sy, r * (on ? 7 : 4), 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = on ? "#fff" : `rgba(${ORANGE},1)`;
    ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = on ? "#fff" : "rgba(255,140,60,0.75)";
    ctx.textAlign = "left";
    ctx.font = on ? "600 12px ui-monospace, Menlo, monospace" : "9px ui-monospace, Menlo, monospace";
    ctx.fillText((on ? "#" + String(i + 1).padStart(2, "0") + " " : "") + shortName(h.agent), sx + r + 6, sy + 3);
    ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.textAlign = "center";
  });
}

function shortName(agent) {
  const p = String(agent).split("-");
  return p.length >= 3 ? `${p[0]}-${p[1]} ${p.slice(2).join("_")}` : agent;
}

// ---- UI ----
function renderList() {
  $("#rank").innerHTML = topAgents.map((t, i) =>
    `<li data-i="${i}"><span class="n">#${String(i + 1).padStart(2, "0")}</span>` +
    `<span class="nm">${esc(shortName(t.agent))}<em>${esc(t.symbol || "")}</em></span>` +
    `<span class="pl">${pct(t.account && t.account.win_rate)}</span></li>`).join("") ||
    `<li><span class="n">–</span><span class="nm">No agents have traded yet</span><span></span></li>`;
  setActive(Math.min(active, Math.max(0, topAgents.length - 1)), false);
}

function setActive(i, fromBeat = true) {
  if (!topAgents.length) return;
  active = i;
  beatAt = performance.now();
  document.querySelectorAll("#rank li").forEach(li => li.classList.toggle("on", Number(li.dataset.i) === i));
  const li = document.querySelector(`#rank li[data-i="${i}"]`);
  if (li && fromBeat) li.scrollIntoView({block: "nearest", behavior: reduced ? "auto" : "smooth"});
  const t = topAgents[i];
  const dept = deptOf(t.agent);
  document.querySelectorAll("#cats span").forEach(s => s.classList.toggle("on", s.dataset.k === dept));
  $("#p-rank").textContent = `#${String(i + 1).padStart(2, "0")} / ${topAgents.length}`;
  $("#p-name").textContent = `${shortName(t.agent)} · ${t.symbol || ""} · profit ${signed(t.account && t.account.pnl)} · ${t.account ? t.account.trades : 0} trades`;
  $("#p-state").textContent = t.account && t.account.proven ? "proven" : "scanning";
  $("#p-bar").style.width = `${((i + 1) / topAgents.length) * 100}%`;
}

$("#rank").addEventListener("click", e => {
  const li = e.target.closest("li[data-i]");
  if (li) { setActive(Number(li.dataset.i), false); nextBeat = performance.now() + BEAT_MS * 3; }
});

let nextBeat = performance.now() + BEAT_MS;
setInterval(() => {
  if (reduced || !topAgents.length || performance.now() < nextBeat) return;
  nextBeat = performance.now() + BEAT_MS;
  setActive((active + 1) % topAgents.length);
}, 200);

function metric(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = cls || "";
}

async function loadReport() {
  try {
    const r = await fetch("/api/report", {cache: "no-store"});
    if (r.status === 401) { location.href = "/login"; return; }
    const d = await r.json();
    metric("#m-agents", (d.agents || 0).toLocaleString());
    metric("#m-equity", money(d.equity));
    metric("#m-pnl", signed(d.session_pnl), d.session_pnl >= 0 ? "gx-up" : "gx-down");
    const at = d.agent_trades || {};
    metric("#m-win", pct(at.win_rate));
    metric("#m-orders", (d.orders || 0).toLocaleString());
    metric("#m-proven", (d.proven_agents || 0).toLocaleString());
    $("#p-src").textContent = `${d.market || "simulated"} market · practice money only · real money locked`;
    const fresh = (d.top_agents || []).slice(0, 20);
    const changed = fresh.map(t => t.agent).join() !== topAgents.map(t => t.agent).join();
    topAgents = fresh;
    if (changed) { placeHeroes(); renderList(); } else if (topAgents.length) setActive(active, false);
  } catch (e) { /* keep last numbers */ }
}

async function loadWorld() {
  try {
    const [org, state] = await Promise.all([fetch("/api/org").then(r => r.json()), fetch("/api/state").then(r => r.json())]);
    const symbols = new Set();
    Object.values(state.shards || {}).forEach(s => ((s.start && s.start.symbols) || []).forEach(x => symbols.add(x)));
    topAgents.forEach(t => t.symbol && symbols.add(t.symbol));
    const departments = org.departments || {};
    if (!Object.keys(departments).length) return setTimeout(loadWorld, 3000);
    build(departments, [...symbols].slice(0, 16));
    $("#cats").innerHTML = Object.entries(departments).map(([k, d]) => `<span data-k="${esc(k)}">${esc(d.name || k)}</span>`).join("");
    if (topAgents.length) setActive(active, false);
  } catch (e) { setTimeout(loadWorld, 3000); }
}

// ---- live terminal ----
const lines = [];
function log(html) {
  lines.push(html);
  while (lines.length > 9) lines.shift();
  $("#term").innerHTML = lines.join("\n") + "\n<b>&gt;</b> <span class=\"gx-cursor\">_</span>";
}
function onEvent(e) {
  const s = e.symbol ? world.symbols[e.symbol] : null;
  if (e.type === "order") {
    if (s) s.flash = 1;
    const side = e.qty >= 0 ? "buy" : "sell";
    log(`<b>&gt;</b> <strong>${esc(e.symbol)}</strong> ${side} ${Math.abs(e.qty).toFixed(2)} @ ${Number(e.price).toFixed(2)} <b>score ${Number(e.score).toFixed(2)}</b>`);
  } else if (e.type === "risk") {
    log(`  risk  ${esc(e.symbol)} ${esc(e.reason || "")}`);
  } else if (e.type === "fill") {
    if (s) s.flash = 1;
    log(`<b>✓</b> filled ${esc(e.symbol || "")} ${e.price != null ? "@ " + Number(e.price).toFixed(2) : ""}`);
  } else if (e.type === "halt" || e.type === "stop") {
    log(`<b>!</b> ${esc(e.type)} ${esc(e.reason || "")}`);
  }
}
const lastLog = {};
function connect() {
  const es = new EventSource("/events");
  es.onmessage = m => {
    let e; try { e = JSON.parse(m.data); } catch (_) { return; }
    const now = performance.now();
    if (e.type === "order" && e.symbol && world.symbols[e.symbol]) world.symbols[e.symbol].flash = 1;
    // Keep it readable: orders at most ~3 a second, risk notes at most one a second.
    const gap = e.type === "risk" ? 1000 : 330;
    if (now - (lastLog[e.type] || 0) < gap) return;
    lastLog[e.type] = now;
    onEvent(e);
  };
  es.onerror = () => { es.close(); log("  connection lost, retrying…"); setTimeout(connect, 4000); };
}

resize();
requestAnimationFrame(frame);
loadReport().then(loadWorld);
setInterval(loadReport, 5000);
connect();
