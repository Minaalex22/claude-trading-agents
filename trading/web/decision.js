// Decision page: the fast gate (typed heads -> confidence -> ACT / ESCALATE / HOLD) in the "Decision layout":
// one big 3D globe on top, ten explaining panels underneath. Practice only.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v, d) => (v == null ? "--" : (v * 100).toFixed(d == null ? 0 : d) + "%");
  const usd = (v) => (v == null ? "--" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  const kv = (rows) => rows.map(([k, v, c]) => `<div class="kv"><span>${esc(k)}</span><b class="${c || ""}">${v}</b></div>`).join("");
  const panel = (t, sub, body) => `<div class="dz-p"><h3>${esc(t)}<span>${esc(sub || "")}</span></h3>${body}</div>`;
  const VC = { ACT: "ok", ESCALATE: "warn", HOLD: "mu" };
  const VOTE = { up: "ok", down: "bad", abstain: "mu" };
  let data = null, pick = null;

  // ---------- globe: our agents as clusters on a sphere, the gate's heads as plates around the live case ----------
  const cv = $("globe"), ctx = cv.getContext("2d");
  let W = 0, H = 0, pts = [];
  function resize() { const d = Math.min(2, window.devicePixelRatio || 1); W = cv.clientWidth; H = cv.clientHeight; cv.width = W * d; cv.height = H * d; ctx.setTransform(d, 0, 0, d, 0, 0); }
  window.addEventListener("resize", resize); resize();
  const DEPT_COL = { trend: [255, 92, 0], reversion: [255, 160, 64], research: [244, 244, 244], risk: [47, 211, 122] };
  function buildPoints() {
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    pts = [];
    const deps = (data && data.departments) || {};
    Object.keys(DEPT_COL).forEach((k, g) => {
      const n = Math.min(500, Math.max(60, (deps[k] && deps[k].agents) || 120));
      const lon0 = g * 1.57, lat0 = g % 2 ? 0.45 : -0.35;
      for (let i = 0; i < n; i++) {
        const lon = lon0 + (rnd() - 0.5) * 1.3, lat = Math.max(-1.4, Math.min(1.4, lat0 + (rnd() - 0.5) * 1.0));
        pts.push([Math.cos(lat) * Math.cos(lon), Math.sin(lat), Math.cos(lat) * Math.sin(lon), DEPT_COL[k], rnd()]);
      }
    });
  }
  let t = 0;
  function frame() {
    t += 0.016;
    ctx.clearRect(0, 0, W, H);
    const cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.34, rot = t * 0.12, tilt = 0.32;
    const ct = Math.cos(tilt), st = Math.sin(tilt);
    const proj = (x, y, z) => { const x1 = x * Math.cos(rot) - z * Math.sin(rot), z1 = x * Math.sin(rot) + z * Math.cos(rot);
      const y2 = y * ct - z1 * st, z2 = y * st + z1 * ct, s = 2.6 / (2.6 - z2 * 0.6); return [cx + x1 * R * s, cy + y2 * R * s, z2, s]; };
    // wire sphere
    ctx.strokeStyle = "rgba(255,92,0,0.10)"; ctx.lineWidth = 1;
    for (let k = 1; k < 6; k++) { const phi = -Math.PI / 2 + (k * Math.PI) / 6; ctx.beginPath();
      for (let i = 0; i <= 64; i++) { const th = (i / 64) * Math.PI * 2, p = proj(Math.cos(phi) * Math.cos(th), Math.sin(phi), Math.cos(phi) * Math.sin(th)); i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); } ctx.stroke(); }
    for (let k = 0; k < 8; k++) { const th = (k / 8) * Math.PI; ctx.beginPath();
      for (let i = 0; i <= 48; i++) { const phi = -Math.PI / 2 + (i / 48) * Math.PI, p = proj(Math.cos(phi) * Math.cos(th), Math.sin(phi), Math.cos(phi) * Math.sin(th)); i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); } ctx.stroke(); }
    // agents
    for (const q of pts) { const p = proj(q[0], q[1], q[2]); const a = p[2] > 0 ? 0.85 : 0.18;
      ctx.fillStyle = `rgba(${q[3][0]},${q[3][1]},${q[3][2]},${a * (0.6 + 0.4 * Math.sin(t * 2 + q[4] * 20))})`;
      ctx.fillRect(p[0], p[1], 1.6 * p[3], 1.6 * p[3]); }
    // the live case: heads as plates on a ring, joined to a core that shows the verdict
    const d = pick && data && data.decisions.find((x) => x.symbol === pick);
    if (d) {
      const col = d.verdict === "ACT" ? "47,211,122" : d.verdict === "ESCALATE" ? "255,160,64" : "138,138,138";
      const ring = R * 1.22;
      d.heads.forEach((h, i) => {
        const a = (i / d.heads.length) * Math.PI * 2 - Math.PI / 2 + t * 0.05, x = cx + Math.cos(a) * ring, y = cy + Math.sin(a) * ring * 0.62;
        const c = h.vote === "up" ? "47,211,122" : h.vote === "down" ? "255,77,77" : "85,85,85";
        ctx.strokeStyle = `rgba(${c},${h.vote === "abstain" ? 0.25 : 0.25 + h.score * 0.6})`; ctx.lineWidth = 1 + h.score * 2;
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = "rgba(0,0,0,0.85)"; ctx.strokeStyle = `rgba(${c},0.9)`; ctx.lineWidth = 1;
        ctx.fillRect(x - 46, y - 16, 92, 32); ctx.strokeRect(x - 46, y - 16, 92, 32);
        ctx.fillStyle = `rgba(${c},1)`; ctx.font = "600 10px ui-monospace,Menlo,monospace"; ctx.textAlign = "center";
        ctx.fillText(h.head.toUpperCase(), x, y - 3); ctx.fillStyle = "#bbb"; ctx.fillText(h.vote + (h.vote !== "abstain" ? " " + h.score.toFixed(2) : ""), x, y + 10);
      });
      const pulse = 1 + 0.06 * Math.sin(t * 3);
      ctx.fillStyle = `rgba(${col},0.18)`; ctx.beginPath(); ctx.arc(cx, cy, 46 * pulse, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = `rgba(${col},0.95)`; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(cx, cy, 40, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.arc(cx, cy, 48, -Math.PI / 2, -Math.PI / 2 + d.confidence * Math.PI * 2); ctx.strokeStyle = "rgba(255,92,0,1)"; ctx.lineWidth = 3; ctx.stroke();
      ctx.fillStyle = `rgba(${col},1)`; ctx.font = "700 14px ui-monospace,Menlo,monospace"; ctx.fillText(d.verdict, cx, cy + 2);
      ctx.fillStyle = "#ddd"; ctx.font = "600 10px ui-monospace,Menlo,monospace"; ctx.fillText(d.symbol + " " + pct(d.confidence), cx, cy + 16);
    }
    requestAnimationFrame(frame);
  }

  // ---------- panels ----------
  function render() {
    const d = data.decisions.find((x) => x.symbol === pick) || data.decisions[0];
    if (d) pick = d.symbol;
    $("casetag").textContent = d ? d.symbol : "NO CASE";
    $("herosub").textContent = d ? `${d.verdict} · ${d.direction} · confidence ${pct(d.confidence)} · ${d.agree}/${d.voting} heads agree` : "waiting for the desk";
    const c = data.counts, tot = data.totals || {};
    $("top").innerHTML = [["ACT", c.ACT, "practice orders the gate would place"], ["ESCALATE", c.ESCALATE, "shown to you, nothing placed"],
      ["HOLD", c.HOLD, "not enough agreement or a risk check failed"], ["DESK WIN RATE", pct(tot.win_rate, 1), (tot.trades || 0) + " closed practice trades"]]
      .map(([k, v, s]) => `<div><div class="k">${k}</div><div class="v">${v == null ? "--" : v}</div><div class="s">${s}</div></div>`).join("");
    if (!d) { $("grid").innerHTML = panel("Waiting", "", '<div class="small">No prices yet. Start the desk and this fills in.</div>'); return; }
    const p = [];
    p.push(panel("Verdict", d.symbol, `<div class="big ${VC[d.verdict]}">${d.verdict}</div>` + kv([["direction", d.direction], ["confidence", pct(d.confidence, 1)],
      ["heads agreeing", `${d.agree} of ${d.voting}`]]) + `<div class="small">${esc(d.reasons.join("; ") || "all checks passed")}</div>`));
    p.push(panel("The heads", "typed votes", kv(d.heads.map((h) => [h.head, h.vote + (h.vote !== "abstain" ? " " + h.score.toFixed(2) : ""), VOTE[h.vote]]))));
    p.push(panel("Confidence", "gate", `<div class="meter"><i data-w="${Math.round(d.confidence * 100)}"></i><em data-l="${d.gates.escalate * 100}"></em><em data-l="${d.gates.act * 100}"></em></div>`
      + kv([["ACT at", pct(d.gates.act)], ["ESCALATE at", pct(d.gates.escalate)], ["heads needed", d.gates.min_agree]])
      + '<div class="small">Confidence = votes with minus against, over heads that voted, cut when fewer than 4 speak.</div>'));
    p.push(panel("All symbols", "click one", data.decisions.slice(0, 12).map((x) => `<div class="sym${x.symbol === pick ? " on" : ""}" data-s="${esc(x.symbol)}"><span>${esc(x.symbol)}</span><span class="${VC[x.verdict]}">${x.verdict} ${pct(x.confidence)}</span></div>`).join("")));
    p.push(panel("Why each head", "", d.heads.map((h) => `<div class="small"><b class="${VOTE[h.vote]}">${esc(h.head)}</b> ${esc(h.why)}</div>`).join("")));
    p.push(panel("Departments", "1,400 agents", kv(Object.values(data.departments).map((x) => [x.name, `${x.active}/${x.agents} · ${x.avg_vote == null ? "--" : x.avg_vote.toFixed(2)}`]))
      + '<div class="small">active / agents · average vote</div>'));
    p.push(panel("Managers", "", (data.managers || []).map((m) => `<div class="small"><b>${esc(m.manager)}</b> ${esc(m.call)}</div>`).join("") || '<div class="small">warming up</div>'));
    const s = data.sizing || {}, dr = data.drift || {};
    p.push(panel("Size and drift", "half Kelly", kv([["size per trade", usd(s.size)], ["Kelly", s.kelly == null ? "--" : s.kelly], ["drift breaker", dr.tripped ? "TRIPPED" : "ok", dr.tripped ? "bad" : "ok"]])
      + `<div class="small">${esc(s.why || "")}. ${esc(dr.why || "")}</div>`));
    p.push(panel("Money rules", "always on", data.rules.map((r) => `<div class="small"><b>${esc(r.rule)}</b> ${esc(r.detail)}</div>`).join("")));
    const g = data.graduation;
    p.push(panel("Real money test", g.passed ? "passed" : "locked", g.checks.map((x) => `<div class="kv"><span>${esc(x.check)}</span><b class="${x.ok ? "ok" : "bad"}">${x.ok ? "✓" : "✗"} ${esc(x.detail)}</b></div>`).join("")
      + `<div class="small">Then: ${esc(g.then)}. Real money stays locked until you say yes.</div>`));
    $("grid").innerHTML = p.join("");
    // CSP forbids inline style attributes, so sizes are set through the DOM
    document.querySelectorAll(".meter [data-w]").forEach((el) => { el.style.width = el.dataset.w + "%"; });
    document.querySelectorAll(".meter [data-l]").forEach((el) => { el.style.left = el.dataset.l + "%"; });
  }
  $("grid").addEventListener("click", (e) => { const el = e.target.closest(".sym"); if (el) { pick = el.dataset.s; render(); } });

  async function load() {
    try {
      const r = await fetch("/api/decision", { credentials: "same-origin" });
      if (r.status === 401) { location.href = "/login"; return; }
      const first = !data; data = await r.json(); if (first) buildPoints(); render();
    } catch (e) { /* keep the last view */ }
  }
  load(); setInterval(load, 5000); frame();
})();
