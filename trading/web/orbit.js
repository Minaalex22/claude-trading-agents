// Orbit portfolio page: five arms on one account, replayed from our real desk trades (/api/orbit).
(() => {
  const $ = (id) => document.getElementById(id);
  const COLORS = { usdjpy: "#ff5c00", fx: "#ffa040", gold: "#ffd166", us: "#f4f4f4", crypto: "#c084fc" };
  const UP = "#2fd37a", DN = "#ff4d4d", START = 100000, REPLAY_MS = 24000;
  let data = null, arms = [], pts = [], mode = "on", cursor = 0, playing = false, t0 = 0, angle = 0, uploaded = false;

  const money = (v, sign) => (sign && v > 0 ? "+" : v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0 });
  const pct = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + v.toFixed(2) + "%");
  const num = (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d));
  const day = (t) => new Date(t * 1000).toISOString().slice(0, 10);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // The page's security policy blocks inline style attributes; colours are set through the DOM instead.
  const paint = (root) => root.querySelectorAll("[data-bg]").forEach((el) => { el.style.background = el.dataset.bg; });
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "g");

  function stats(rows) {
    const w = rows.filter((r) => r[2] > 0), l = rows.filter((r) => r[2] < 0);
    const gain = w.reduce((a, r) => a + r[2], 0), loss = -l.reduce((a, r) => a + r[2], 0);
    let peak = START, dd = 0;
    for (const r of rows) { peak = Math.max(peak, r[3]); dd = Math.max(dd, (peak - r[3]) / peak); }
    return { n: rows.length, w: w.length, wr: rows.length ? w.length / rows.length : null, net: gain - loss,
      pf: loss ? gain / loss : gain ? 99 : null, rr: w.length && l.length ? (gain / w.length) / (loss / l.length) : null,
      dd: 100 * dd, eq: rows.length ? rows[rows.length - 1][3] : START };
  }

  function sizeCanvas(c) {
    const r = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * r) || c.height !== Math.round(h * r)) { c.width = Math.round(w * r); c.height = Math.round(h * r); }
    const g = c.getContext("2d"); g.setTransform(r, 0, 0, r, 0, 0); return [g, w, h];
  }

  // ---- the orbit: one arm per strategy, a dot per trade (green won, red lost), newest at the tip ----
  function drawOrbit() {
    const [g, w, h] = sizeCanvas($("orbit"));
    g.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 18;
    g.strokeStyle = "#1f1f1f";
    for (let k = 1; k <= 4; k++) { g.beginPath(); g.arc(cx, cy, (R * k) / 4, 0, Math.PI * 2); g.stroke(); }
    const shown = pts.slice(0, cursor), last = shown[shown.length - 1];
    arms.forEach((a, i) => {
      const th = angle + (i / arms.length) * Math.PI * 2, col = COLORS[a.key] || "#ff5c00";
      const mine = shown.filter((p) => p[1] === a.key), keep = mine.slice(-60);
      g.strokeStyle = col + "55"; g.lineWidth = 2; g.beginPath(); g.moveTo(cx, cy);
      g.lineTo(cx + Math.cos(th) * R, cy + Math.sin(th) * R); g.stroke();
      keep.forEach((p, j) => {
        const rr = 26 + ((R - 26) * (j + 1)) / Math.max(keep.length, 1);
        const sway = Math.sin(j * 1.7) * 0.06;
        g.fillStyle = p[2] > 0 ? UP : DN; g.globalAlpha = 0.35 + (0.65 * (j + 1)) / keep.length;
        g.beginPath(); g.arc(cx + Math.cos(th + sway) * rr, cy + Math.sin(th + sway) * rr, last === p ? 6 : 3.2, 0, Math.PI * 2); g.fill();
      });
      g.globalAlpha = 1;
      const s = stats(mine);
      g.fillStyle = col; g.font = "600 11px JetBrains Mono, monospace"; g.textAlign = "center";
      const lx = cx + Math.cos(th) * (R + 2), ly = cy + Math.sin(th) * (R + 2);
      g.fillText(a.label.split(" ")[0] + (s.n ? " " + money(s.net, true) : ""), Math.max(40, Math.min(w - 40, lx)), Math.max(12, Math.min(h - 4, ly)));
    });
    const eq = last ? last[3] : START;
    g.fillStyle = "#000"; g.beginPath(); g.arc(cx, cy, 24, 0, Math.PI * 2); g.fill();
    g.strokeStyle = "#ff5c00"; g.lineWidth = 2; g.stroke();
    g.fillStyle = eq >= START ? UP : DN; g.font = "700 10px JetBrains Mono, monospace"; g.textAlign = "center";
    g.fillText(((eq / START - 1) * 100).toFixed(1) + "%", cx, cy + 4);
    $("active").textContent = last ? `${(arms.find((a) => a.key === last[1]) || {}).label || last[1]} · ${last[4]} ${money(last[2], true)}` : "idle";
  }

  // ---- balance over time ----
  function drawBalance() {
    const [g, w, h] = sizeCanvas($("bal"));
    g.clearRect(0, 0, w, h);
    if (!pts.length) { g.fillStyle = "#8a8a8a"; g.fillText("No trades yet", 12, 20); return; }
    const all = [[pts[0][0], 0, 0, START], ...pts], t0_ = all[0][0], t1 = all[all.length - 1][0];
    const lo = Math.min(...all.map((p) => p[3])), hi = Math.max(...all.map((p) => p[3]));
    const pad = Math.max((hi - lo) * 0.1, 50), L = 64, B = h - 22;
    const X = (t) => L + ((t - t0_) / Math.max(t1 - t0_, 1)) * (w - L - 10), Y = (v) => 10 + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (B - 10);
    g.font = "10px JetBrains Mono, monospace"; g.fillStyle = "#8a8a8a"; g.strokeStyle = "#1a1a1a";
    for (let k = 0; k <= 4; k++) { const v = lo - pad + ((hi - lo + 2 * pad) * k) / 4; g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(w - 10, Y(v)); g.stroke(); g.textAlign = "right"; g.fillText("$" + Math.round(v).toLocaleString(), L - 6, Y(v) + 3); }
    const y0 = new Date(t0_ * 1000).getUTCFullYear(), y1 = new Date(t1 * 1000).getUTCFullYear();
    g.textAlign = "center";
    for (let y = y0 + 1; y <= y1; y++) { const t = Date.UTC(y, 0, 1) / 1000; g.fillText(y, X(t), h - 6); }
    g.strokeStyle = "#ff5c0055"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(L, Y(START)); g.lineTo(w - 10, Y(START)); g.stroke(); g.setLineDash([]);
    const upto = all.slice(0, cursor + 1);
    g.beginPath(); upto.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[3])) : g.moveTo(X(p[0]), Y(p[3]))));
    g.strokeStyle = "#ff5c00"; g.lineWidth = 2; g.stroke();
    const lp = upto[upto.length - 1];
    g.lineTo(X(lp[0]), B); g.lineTo(L, B); g.closePath(); g.fillStyle = "rgba(255,92,0,.08)"; g.fill();
    g.fillStyle = "#ff5c00"; g.beginPath(); g.arc(X(lp[0]), Y(lp[3]), 4, 0, Math.PI * 2); g.fill();
    $("bal-sub").textContent = `${day(all[0][0])} → ${day(lp[0])}`;
  }

  function kpis() {
    const s = stats(pts.slice(0, cursor));
    const fl = !uploaded && data.floating ? data.floating.max_drawdown_pct : null;
    $("k-eq").textContent = money(s.eq); $("k-eq").className = cls(s.eq - START);
    $("k-eq-sub").textContent = `${money(s.eq - START, true)} (${pct((s.eq / START - 1) * 100)}) from $100,000`;
    $("k-wr").textContent = s.n ? `${s.n} · ${(100 * s.wr).toFixed(1)}%` : "0";
    $("k-wr-sub").textContent = `avg win ÷ avg loss ${num(s.rr)}`;
    $("k-pf").textContent = num(s.pf); $("k-pf").className = s.pf == null ? "" : cls(s.pf - 1);
    const unw = uploaded ? null : data[mode === "on" ? "with_heat" : "without_heat"].combined.unweighted_pf;
    $("k-pf-sub").textContent = `won $ ÷ lost $` + (unw != null ? ` · count-only ${num(unw)}` : "");
    $("k-dd").textContent = s.dd.toFixed(2) + "%"; $("k-dd").className = "dn";
    $("k-dd-sub").textContent = "closed trades" + (fl != null ? ` · floating ${fl.toFixed(2)}% (open trades marked)` : "");
    $("date").textContent = cursor ? day(pts[cursor - 1][0]) : pts.length ? day(pts[0][0]) : "–";
    $("scrub").value = pts.length ? Math.round((1000 * cursor) / pts.length) : 1000;
  }

  function armsTable() {
    const S = uploaded ? null : data[mode === "on" ? "with_heat" : "without_heat"];
    const rows = arms.map((a) => ({ a, s: stats(pts.filter((p) => p[1] === a.key)), srv: S && S.arms[a.key], rec: S && S.recent && S.recent.arms && S.recent.arms[a.key] }))
      .sort((x, y) => y.s.net - x.s.net);
    setTimeout(() => paint($("arms")));
    const heatCell = (v) => (v == null ? "–" : `<span class="${v > 1 ? "heat-hot" : v < 1 ? "heat-cold" : ""}">${v.toFixed(2)}x</span>`);
    $("arms").innerHTML = `<tr><th>#</th><th>Arm</th><th>Markets</th><th class="r">Trades</th><th class="r">Win</th><th class="r">Net</th><th class="r">PF $</th><th class="r">Avg win÷loss</th><th class="r">Heat now</th><th class="r">Last 12 mo</th></tr>` +
      rows.map((r, i) => `<tr class="${r.s.net > 0 ? "win" : r.s.net < 0 ? "loss" : ""}"><td>${i + 1}</td><td><span class="arm-dot" data-bg="${COLORS[r.a.key]}"></span>${esc(r.a.label)}<br><small class="g">${esc(r.a.core)} · ${esc(r.a.timeframe)}</small></td>` +
        `<td class="g">${esc((r.a.replay_symbols && r.a.replay_symbols.length ? r.a.replay_symbols : r.a.symbols).join(" "))}</td><td class="r">${r.s.n}</td><td class="r">${r.s.wr == null ? "–" : (100 * r.s.wr).toFixed(0) + "%"}</td>` +
        `<td class="r ${cls(r.s.net)}">${money(r.s.net, true)}</td><td class="r">${num(r.s.pf)}</td><td class="r">${num(r.s.rr)}</td><td class="r">${mode === "on" && r.srv ? heatCell(r.srv.heat_now) : "1.00x"}</td>` +
        `<td class="r ${r.rec ? cls(r.rec.net) : ""}">${r.rec && r.rec.trades ? `${r.rec.trades} · ${money(r.rec.net, true)}` : "–"}</td></tr>`).join("");
  }

  function heatmap() {
    const byM = {}; let prev = START;
    for (const p of pts) { const m = day(p[0]).slice(0, 7); if (!byM[m]) byM[m] = { start: prev, pnl: 0 }; byM[m].pnl += p[2]; prev = p[3]; }
    const years = [...new Set(Object.keys(byM).map((m) => m.slice(0, 4)))].sort();
    const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const cell = (v) => { if (v == null) return `<td class="g">·</td>`; const a = Math.min(Math.abs(v) / 0.3, 1) * 0.75 + 0.1;
      return `<td data-bg="${v >= 0 ? `rgba(47,211,122,${a})` : `rgba(255,77,77,${a})`}">${v.toFixed(2)}</td>`; };
    $("heat").innerHTML = `<tr><th></th>${M.map((m) => `<th>${m}</th>`).join("")}<th>Year</th></tr>` + years.map((y) => {
      let yr = 0, any = false;
      const tds = M.map((_, i) => { const k = `${y}-${String(i + 1).padStart(2, "0")}`, b = byM[k]; if (!b) return cell(null); any = true; const v = (100 * b.pnl) / b.start; yr += b.pnl; return cell(v); }).join("");
      return `<tr><th>${y}</th>${tds}${any ? cell((100 * yr) / START) : cell(null)}</tr>`; }).join("");
    paint($("heat"));
  }

  function recentTable() {
    const shown = pts.slice(0, cursor).slice(-40).reverse();
    $("recent").innerHTML = `<tr><th>Date</th><th>Arm</th><th>Market</th><th>Kind</th><th class="r">Heat</th><th class="r">P&amp;L</th><th class="r">Balance</th></tr>` +
      (shown.map((p) => `<tr class="${p[2] > 0 ? "win" : "loss"}"><td>${day(p[0])}</td><td><span class="arm-dot" data-bg="${COLORS[p[1]] || "#888"}"></span>${esc((arms.find((a) => a.key === p[1]) || {}).label || p[1])}</td>` +
        `<td>${esc(p[4])}</td><td class="g">${esc(p[5])}</td><td class="r">${num(p[6])}x</td><td class="r ${cls(p[2])}">${money(p[2], true)}</td><td class="r">${money(p[3])}</td></tr>`).join("") || `<tr><td colspan="7" class="g">No trades yet</td></tr>`);
    paint($("recent"));
  }

  function serverPanels() {
    const on = data.with_heat.combined, off = data.without_heat.combined;
    const C = data.with_heat.correlation, ks = arms.map((a) => a.key);
    $("corr").innerHTML = `<tr><th></th>${arms.map((a) => `<th title="${esc(a.label)}">${esc(a.label.split(" ")[0])}</th>`).join("")}</tr>` +
      ks.map((k, i) => `<tr><th>${esc(arms[i].label.split(" ")[0])}</th>${ks.map((j) => { const v = C[k][j]; if (v == null) return `<td class="g">–</td>`;
        const a = Math.min(Math.abs(v), 1) * 0.6 + 0.08; return `<td data-bg="${k === j ? "#151515" : v > 0 ? `rgba(255,77,77,${a})` : `rgba(47,211,122,${a})`}">${v.toFixed(2)}</td>`; }).join("")}</tr>`).join("");
    paint($("corr"));
    const f = data.floating || {};
    $("crowd").innerHTML = `Average correlation between arms: <b>${num(on.avg_correlation)}</b> (near 0 = they don't lose together; red = they move together). ` +
      (f.same_direction_share != null ? `3 or more arms leaned the same way <b>${(100 * f.same_direction_share).toFixed(0)}%</b> of the time` + (f.same_direction_now ? `, and right now 3+ arms lean <b>${f.same_direction_now}</b>: a crowded bet.` : ".") : "") +
      ` A dash means the arm made too few trades to measure.`;
    const row = (name, a, b, fmt, better) => { const good = better === "low" ? a < b : a > b;
      return `<tr><td>${name}</td><td class="r">${fmt(b)}</td><td class="r ${a === b ? "" : good ? "up" : "dn"}">${fmt(a)}</td></tr>`; };
    $("cmp").innerHTML = `<tr><th></th><th class="r">Without heat</th><th class="r">With heat</th></tr>` +
      row("Net profit", on.net, off.net, (v) => money(v, true)) + row("Profit factor ($)", on.profit_factor, off.profit_factor, num) +
      row("Max drawdown (closed)", on.max_drawdown_pct, off.max_drawdown_pct, (v) => v.toFixed(2) + "%", "low") +
      row("Avg win ÷ avg loss", on.rr, off.rr, num) + row("Win rate", on.win_rate, off.win_rate, (v) => (100 * v).toFixed(1) + "%");
    $("cmp-note").innerHTML = `Heat doesn't change which trades happen, only their size: 0.35x while an arm is cold, 1.45x while hot, 0.85x for its first 5 trades. It is switched on for the paper desk too (ORBIT_HEAT=off turns it off).`;
    const best = data.top[0], worst = data.top[data.top.length - 1], lbl = (k) => (arms.find((a) => a.key === k) || {}).label;
    const src = data.source;
    $("lesson").innerHTML = `<p>Win rate <b>${(100 * on.win_rate).toFixed(0)}%</b> with average win ÷ loss <b>${num(on.rr)}</b>: as one strategy that loses. The question is whether the mix and the sizing fix it.</p>` +
      `<p>Best arm: <b>${esc(lbl(best))}</b>. Worst: <b>${esc(lbl(worst))}</b>. Heat ${on.net > off.net ? "cut the loss / raised the profit" : "did not help"}: ${money(off.net, true)} → ${money(on.net, true)}, drawdown ${off.max_drawdown_pct.toFixed(2)}% → ${on.max_drawdown_pct.toFixed(2)}%.</p>` +
      `<p>Our desk trades rarely on daily bars, so ${src.backtest_trades} replay trades in ${new Date(src.first * 1000).getUTCFullYear()}–${new Date(src.last * 1000).getUTCFullYear()} is a small sample. Treat the arms' numbers as a direction, not a promise.</p>`;
    $("src").textContent = `Replay: ${src.backtest_trades} desk trades on real ${src.bar} prices (${src.made || "?"}), costs ${src.cost_bps_per_fill} bp per fill, each arm its own desk. Paper desk: ${src.paper_trades} closed trades added live. Practice-quota trades: never included.`;
  }

  function legend() {
    $("legend").innerHTML = arms.map((a) => `<span><i data-bg="${COLORS[a.key]}"></i>${esc(a.label)}</span>`).join("") +
      `<span><i data-bg="${UP}"></i>won</span><span><i data-bg="${DN}"></i>lost</span>`;
    paint($("legend"));
  }

  function render() { drawOrbit(); drawBalance(); kpis(); recentTable(); }
  function renderAll() { render(); armsTable(); heatmap(); }

  function frame(now) {
    angle += 0.0025;
    if (playing) {
      const f = Math.min((now - t0) / REPLAY_MS, 1);
      cursor = Math.round(f * pts.length);
      if (f >= 1) { playing = false; $("play").innerHTML = "&#9654; Replay in 24s"; }
      render();
    } else drawOrbit();
    requestAnimationFrame(frame);
  }

  function setMode(m) {
    mode = m; uploaded = false;
    pts = m === "on" ? data.points : data.points_off;
    cursor = pts.length;
    document.querySelectorAll("#mode button").forEach((b) => b.classList.toggle("on", b.dataset.m === m));
    $("badge").textContent = "BACKTEST + PAPER · NOT A LIVE ACCOUNT";
    renderAll();
  }

  // mina's CSV format: date (or timestamp),arm,symbol,pnl_usd,is_practice. Practice rows are dropped.
  function loadCsv(text) {
    const lines = text.trim().split(/\r?\n/), head = lines.shift().toLowerCase().split(",").map((s) => s.trim());
    const col = (n) => head.indexOf(n), iT = col("date") >= 0 ? col("date") : col("timestamp"), iA = col("arm"), iS = col("symbol"), iP = col("pnl_usd"), iX = col("is_practice");
    if (iT < 0 || iP < 0) { alert("CSV needs date (or timestamp) and pnl_usd columns"); return; }
    const findArm = (name, sym) => { const n = String(name || "").toLowerCase();
      const a = arms.find((x) => x.key === n || x.name.toLowerCase() === n || x.label.toLowerCase() === n || (n && x.name.toLowerCase().startsWith(n.split("_")[0])));
      return a || arms.find((x) => x.symbols.includes(String(sym || "").toUpperCase())); };
    const rows = [];
    let skipped = 0;
    for (const ln of lines) {
      const c = ln.split(","); if (c.length < 2) continue;
      if (iX >= 0 && /^(1|true|yes)$/i.test((c[iX] || "").trim())) { skipped++; continue; }
      const raw = (c[iT] || "").trim(), t = /^\d+(\.\d+)?$/.test(raw) ? (Number(raw) > 1e11 ? Number(raw) / 1000 : Number(raw)) : Date.parse(raw) / 1000;
      const a = findArm(iA >= 0 ? c[iA] : "", iS >= 0 ? c[iS] : "");
      if (!a || !isFinite(t)) { skipped++; continue; }
      rows.push([t, a.key, Number(c[iP]) || 0, 0, iS >= 0 ? c[iS] : "", "csv", 1]);
    }
    rows.sort((a, b) => a[0] - b[0]);
    let eq = START; rows.forEach((r) => { eq += r[2]; r[3] = Math.round(eq * 100) / 100; });
    uploaded = true; pts = rows; cursor = rows.length;
    $("badge").textContent = `YOUR CSV · ${rows.length} TRADES · ${skipped} PRACTICE/UNKNOWN ROWS DROPPED`;
    renderAll();
  }

  $("play").onclick = () => { if (!pts.length) return; playing = !playing; if (playing) { if (cursor >= pts.length) cursor = 0; t0 = performance.now() - (cursor / pts.length) * REPLAY_MS; $("play").textContent = "❚❚ Pause"; } else $("play").innerHTML = "&#9654; Replay in 24s"; };
  $("reset").onclick = () => { playing = false; cursor = 0; $("play").innerHTML = "&#9654; Replay in 24s"; render(); };
  $("scrub").oninput = (e) => { playing = false; cursor = Math.round((e.target.value / 1000) * pts.length); render(); };
  document.querySelectorAll("#mode button").forEach((b) => (b.onclick = () => setMode(b.dataset.m)));
  $("csv-in").onchange = (e) => { const f = e.target.files[0]; if (f) f.text().then(loadCsv); };
  window.addEventListener("resize", render);

  fetch("/api/orbit", { cache: "no-store" }).then((r) => r.json()).then((d) => {
    data = d; arms = d.arms; legend(); serverPanels(); setMode("on"); requestAnimationFrame(frame);
  }).catch((e) => { $("lesson").textContent = "Could not load the portfolio: " + e; });
})();
