// Pro footprint (cluster) chart, ATAS-style, drawn on a canvas from one /api/orderflow book.
// Per bar: a candle, then sellers x buyers at each price, coloured by who won the price,
// imbalances in bold, the bar's busiest price (POC) outlined. Right: session volume profile,
// value area and POC line. Bottom: cluster statistics (volume, delta, trades). Hover: crosshair.
(function () {
  const C = {bg: "#050505", grid: "#141414", axis: "#8a8a8a", w: "#f4f4f4", up: "47,211,122", dn: "255,77,77", o: "#ff5c00", va: "rgba(255,92,0,.35)"};
  const IMB = 3; // buyers >= 3x sellers at a price (or the other way) = imbalance
  const fmt = (v, step) => step >= 1 ? v.toFixed(0) : step >= 0.1 ? v.toFixed(1) : v.toFixed(2);
  const short = v => v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v));

  function niceStep(range, rows) {
    const raw = range / rows, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  }

  function build(book, maxBars, rows) {
    const bars = book.bars.slice(-maxBars);
    let lo = Infinity, hi = -Infinity;
    bars.forEach(b => { lo = Math.min(lo, b.low); hi = Math.max(hi, b.high); });
    const step = niceStep(Math.max(hi - lo, 1e-9), rows);
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    const n = Math.max(1, Math.round((hi - lo) / step) + 1);
    const row = p => Math.min(n - 1, Math.max(0, Math.round((hi - p) / step)));
    const cols = bars.map(b => {
      const cells = Array.from({length: n}, () => ({bid: 0, ask: 0}));
      Object.entries(b.levels).forEach(([k, l]) => { const c = cells[row(+k)]; c.bid += l.bid; c.ask += l.ask; });
      let poc = 0; cells.forEach((c, i) => { if (c.bid + c.ask > cells[poc].bid + cells[poc].ask) poc = i; });
      return {b, cells, poc};
    });
    const sess = Array(n).fill(0);
    Object.entries(book.session_levels || {}).forEach(([k, v]) => { if (+k >= lo - step && +k <= hi + step) sess[row(+k)] += v; });
    return {bars, cols, lo, hi, step, n, row, sess};
  }

  function draw(canvas, book, opts = {}) {
    const dpr = devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
    const g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = C.bg; g.fillRect(0, 0, W, H);
    g.font = "10px ui-monospace, Menlo, monospace"; g.textBaseline = "middle";
    if (!book || !book.bars || !book.bars.length) { g.fillStyle = C.axis; g.fillText("No order flow for this market yet", 12, 20); return null; }
    const statsH = 54, axisW = 62, profW = Math.min(110, W * 0.14), top = 22;
    const plotW = W - axisW - profW, plotH = H - statsH - top - 4;
    const maxBars = opts.bars || Math.max(3, Math.min(12, Math.floor(plotW / 92)));
    const rows = opts.rows || Math.max(10, Math.min(36, Math.floor(plotH / 15)));
    const M = build(book, maxBars, rows);
    const rh = plotH / M.n, cw = plotW / M.cols.length, candleW = Math.max(4, Math.min(10, cw * 0.1));
    const y = i => top + i * rh;
    const maxCell = Math.max(1, ...M.cols.flatMap(c => c.cells.map(x => Math.max(x.bid, x.ask))));

    // grid
    g.strokeStyle = C.grid; g.lineWidth = 1;
    for (let i = 0; i <= M.n; i++) { g.beginPath(); g.moveTo(0, y(i) + .5); g.lineTo(plotW, y(i) + .5); g.stroke(); }
    // value area band + session POC line across the chart
    const sMax = Math.max(1, ...M.sess), pr = book.profile || {};
    if (pr.val != null) {
      const a = M.row(pr.vah), b = M.row(pr.val);
      g.fillStyle = "rgba(255,92,0,.04)"; g.fillRect(0, y(a), plotW, y(b + 1) - y(a));
    }
    // columns
    M.cols.forEach((c, j) => {
      const x0 = j * cw, b = c.b;
      // candle
      const cx = x0 + 4 + candleW / 2, up = b.close >= b.open, col = up ? C.up : C.dn;
      const py = p => top + (M.hi - p) / M.step * rh + rh / 2;
      g.strokeStyle = `rgb(${col})`; g.beginPath(); g.moveTo(cx, py(b.high)); g.lineTo(cx, py(b.low)); g.stroke();
      g.fillStyle = `rgb(${col})`; const yo = py(b.open), yc = py(b.close);
      g.fillRect(cx - candleW / 2, Math.min(yo, yc), candleW, Math.max(1.5, Math.abs(yc - yo)));
      // cells
      const cx0 = x0 + candleW + 8, cww = cw - candleW - 12;
      c.cells.forEach((cell, i) => {
        const v = cell.bid + cell.ask; if (!v) return;
        const d = cell.ask - cell.bid, a = 0.1 + 0.75 * Math.min(1, Math.max(cell.bid, cell.ask) / maxCell);
        g.fillStyle = `rgba(${d >= 0 ? C.up : C.dn},${a})`; g.fillRect(cx0, y(i) + 1, cww, rh - 2);
        if (i === c.poc) { g.strokeStyle = C.o; g.lineWidth = 1.5; g.strokeRect(cx0 + .75, y(i) + 1.75, cww - 1.5, rh - 3.5); g.lineWidth = 1; }
        if (rh >= 11 && cww >= 46) {
          const buyImb = cell.ask >= IMB * Math.max(1, cell.bid), sellImb = cell.bid >= IMB * Math.max(1, cell.ask);
          g.font = `${sellImb ? "700 " : ""}${Math.min(11, rh - 2)}px ui-monospace, Menlo, monospace`;
          g.fillStyle = sellImb ? "#ffb3b3" : C.w; g.textAlign = "right"; g.fillText(short(cell.bid), cx0 + cww / 2 - 5, y(i) + rh / 2);
          g.fillStyle = C.axis; g.textAlign = "center"; g.fillText("x", cx0 + cww / 2, y(i) + rh / 2);
          g.font = `${buyImb ? "700 " : ""}${Math.min(11, rh - 2)}px ui-monospace, Menlo, monospace`;
          g.fillStyle = buyImb ? "#b6ffd6" : C.w; g.textAlign = "left"; g.fillText(short(cell.ask), cx0 + cww / 2 + 5, y(i) + rh / 2);
          g.textAlign = "left"; g.font = "10px ui-monospace, Menlo, monospace";
        }
      });
      // big trades
      (b.big_trades || []).forEach(t => { const i = M.row(t.price != null ? t.price : t[0]); g.strokeStyle = C.o; g.beginPath(); g.arc(cx0 + cww - 6, y(i) + rh / 2, Math.min(rh / 2 - 1, 6), 0, 7); g.stroke(); });
      g.strokeStyle = "#1c1c1c"; g.beginPath(); g.moveTo(x0 + cw - .5, top); g.lineTo(x0 + cw - .5, top + plotH); g.stroke();
      // time label
      g.fillStyle = C.axis; g.textAlign = "center";
      g.fillText(new Date(b.start * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", hour12: false}), x0 + cw / 2, top - 10);
      g.textAlign = "left";
    });
    // session POC line
    if (pr.poc != null) { const i = M.row(pr.poc); g.strokeStyle = C.o; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(0, y(i) + rh / 2); g.lineTo(plotW + profW, y(i) + rh / 2); g.stroke(); g.setLineDash([]); }
    // profile
    const px = plotW + 4;
    M.sess.forEach((v, i) => {
      if (!v) return;
      const inVA = pr.val != null && M.lo + (M.n - 1 - i) * M.step >= pr.val - M.step / 2 && M.lo + (M.n - 1 - i) * M.step <= pr.vah + M.step / 2;
      g.fillStyle = i === M.row(pr.poc) ? C.o : inVA ? C.va : "#2a2a2a";
      g.fillRect(px, y(i) + 1, (profW - 10) * v / sMax, rh - 2);
    });
    // price axis
    g.fillStyle = "#0a0a0a"; g.fillRect(W - axisW, 0, axisW, H);
    g.fillStyle = C.axis; const every = Math.max(1, Math.ceil(14 / rh));
    for (let i = 0; i < M.n; i += every) g.fillText(fmt(M.hi - i * M.step, M.step), W - axisW + 6, y(i) + rh / 2);
    const last = M.bars[M.bars.length - 1].close, ly = top + (M.hi - last) / M.step * rh + rh / 2;
    g.fillStyle = C.o; g.fillRect(W - axisW, ly - 8, axisW, 16); g.fillStyle = "#000"; g.font = "700 10px ui-monospace, Menlo, monospace";
    g.fillText(fmt(last, M.step / 10), W - axisW + 6, ly); g.font = "10px ui-monospace, Menlo, monospace";
    // cluster statistics
    const sy = top + plotH + 6, lh = (statsH - 8) / 3;
    const mxV = Math.max(1, ...M.bars.map(b => b.volume)), mxD = Math.max(1, ...M.bars.map(b => Math.abs(b.delta)));
    [["Volume", b => b.volume, b => `rgba(255,255,255,${0.08 + 0.3 * b.volume / mxV})`], ["Delta", b => b.delta, b => `rgba(${b.delta >= 0 ? C.up : C.dn},${0.15 + 0.6 * Math.abs(b.delta) / mxD})`], ["Trades", b => b.trades, () => "rgba(255,255,255,.05)"]]
      .forEach(([name, val, fill], r) => {
        M.bars.forEach((b, j) => {
          g.fillStyle = fill(b); g.fillRect(j * cw + 1, sy + r * lh, cw - 2, lh - 1);
          g.fillStyle = C.w; g.textAlign = "center"; const v = val(b); g.fillText((name === "Delta" && v > 0 ? "+" : "") + short(v), j * cw + cw / 2, sy + r * lh + lh / 2);
        });
        g.textAlign = "left"; g.fillStyle = C.axis; g.fillText(name, W - axisW + 6, sy + r * lh + lh / 2);
      });
    return {M, top, rh, cw, plotW, plotH, axisW};
  }

  // crosshair: redraw on hover, then overlay a price line and the bar's numbers
  function attach(canvas, getBook) {
    let geo = null, mouse = null;
    const redraw = () => {
      geo = draw(canvas, getBook());
      if (!geo || !mouse) return;
      const g = canvas.getContext("2d"), {M, top, rh, cw, plotW, plotH} = geo;
      if (mouse.x > plotW || mouse.y < top || mouse.y > top + plotH) return;
      const i = Math.floor((mouse.y - top) / rh), j = Math.min(M.cols.length - 1, Math.floor(mouse.x / cw));
      const price = M.hi - i * M.step, cell = M.cols[j].cells[i], b = M.cols[j].b;
      g.strokeStyle = "rgba(255,255,255,.35)"; g.setLineDash([3, 3]);
      g.beginPath(); g.moveTo(0, top + i * rh + rh / 2); g.lineTo(canvas.clientWidth, top + i * rh + rh / 2); g.stroke();
      g.beginPath(); g.moveTo(j * cw + cw / 2, top); g.lineTo(j * cw + cw / 2, top + plotH); g.stroke(); g.setLineDash([]);
      const txt = `${fmt(price, M.step)}  sellers ${short(cell.bid)} · buyers ${short(cell.ask)}  |  bar O ${b.open.toFixed(2)} H ${b.high.toFixed(2)} L ${b.low.toFixed(2)} C ${b.close.toFixed(2)} Δ ${Math.round(b.delta)}`;
      g.font = "11px ui-monospace, Menlo, monospace"; const tw = g.measureText(txt).width + 14;
      g.fillStyle = "rgba(10,10,10,.92)"; g.fillRect(6, top + 4, tw, 20); g.strokeStyle = "#ff5c00"; g.strokeRect(6.5, top + 4.5, tw - 1, 19);
      g.fillStyle = "#f4f4f4"; g.textBaseline = "middle"; g.fillText(txt, 13, top + 14);
    };
    canvas.addEventListener("mousemove", e => { const r = canvas.getBoundingClientRect(); mouse = {x: e.clientX - r.left, y: e.clientY - r.top}; redraw(); });
    canvas.addEventListener("mouseleave", () => { mouse = null; redraw(); });
    return redraw;
  }

  window.Footprint = {draw, attach};
})();
