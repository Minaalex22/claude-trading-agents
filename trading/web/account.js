// One account: every platform, desk and ATAS together, with the Demo / Live switch.
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const money = (v) => v == null ? '--' : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const sign = (v) => v > 0 ? 'up' : v < 0 ? 'dn' : '';
  let data = null;

  function table(head, rows) {
    const t = el('table'); const hr = el('tr');
    head.forEach((h) => hr.append(el('th', /^(Equity|P&L|Net|Gross|Trades|Win)/.test(h) ? 'r' : '', h)));
    t.append(hr);
    rows.forEach((cells) => {
      const tr = el('tr');
      cells.forEach((c) => {
        const o = c && typeof c === 'object' ? c : null;  // plain strings have a .sub() method, so check the type
        const td = el('td', o && o.cls ? o.cls : '');
        if (o && o.node) td.append(o.node); else td.textContent = o ? (o.text == null ? '' : o.text) : (c == null ? '' : c);
        if (o && o.sub) td.append(el('small', '', o.sub));
        tr.append(td);
      });
      t.append(tr);
    });
    return t;
  }
  const tag = (text, cls) => ({ node: el('span', 'oa-tag ' + (cls || ''), text) });

  function render(d) {
    data = d;
    const live = d.mode === 'live';
    $('m-demo').className = live ? '' : 'on';
    $('m-live').className = live ? 'on live' : '';
    $('badge').textContent = live ? 'LIVE · REAL MONEY' : 'DEMO · PRACTICE MONEY';
    $('word').textContent = d.confirm_word;
    $('switch-txt').textContent = live
      ? 'Live: orders on platforms with your keys go to your real accounts. Every order still needs a stop loss and passes the limits below. Tap DEMO to go back at once.'
      : d.armed ? 'Demo: orders go to demo and practice accounts. Live is armed on Render; tap LIVE and type the confirm word to switch.'
        : 'Demo: orders go to demo and practice accounts. To allow Live, add LIVE_MONEY=on in Render > Environment once.';

    const t = d.totals; $('kpis').replaceChildren();
    [['Combined equity', money(t.equity), `${d.accounts.filter((a) => a.state === 'connected').length} accounts connected`],
      ['Total P&L', money(t.pnl), `from ${money(t.start)} start`, sign(t.pnl)],
      ['Today', money(t.today_pnl), 'Melbourne day, closed + open', sign(t.today_pnl)],
      ['Open positions', String(t.open_positions), `limit ${d.risk.max_open}`],
      ['Gross exposure', money(t.gross_exposure), `limit ${money(d.risk.gross_limit)}`],
      ['Closed trades', String(t.closed_trades), t.closed_trades ? `${Math.round(t.won / t.closed_trades * 100)}% won` : 'none yet'],
      ['Leading desk', (d.desks.find((x) => x.leading) || {}).name || '--', 'follow the sun']]
      .forEach(([k, v, s, c]) => { const b = el('div', 'oa-kpi'); b.append(el('small', '', k), el('b', c || '', v), el('em', '', s)); $('kpis').append(b); });

    const r = d.risk; $('risk').replaceChildren();
    const used = r.daily_loss_limit ? Math.min(100, r.daily_loss_used / r.daily_loss_limit * 100) : 0;
    const lbl = el('div', 'small', `Daily loss used: ${money(r.daily_loss_used)} of ${money(r.daily_loss_limit)} (2% of the whole account)`);
    const bar = el('div', 'oa-bar'); const fill = el('i'); fill.style.width = used + '%'; bar.append(fill);
    $('risk').append(lbl, bar);
    r.breaches.forEach((b) => $('risk').append(el('div', 'oa-breach', b)));

    $('accounts').replaceChildren(table(['Account', 'State', 'Equity', 'P&L'], d.accounts.map((a) => [
      { text: a.name, sub: a.note }, tag(a.state.replace('_', ' '), a.state === 'connected' || a.state === 'live' ? 'demo' : ''),
      { text: a.equity == null ? '--' : money(a.equity), cls: 'r' }, { text: money(a.pnl), cls: 'r ' + sign(a.pnl) }])));

    $('desks').replaceChildren(table(['Desk', 'Status', 'Trades', 'Win', 'P&L'], d.desks.map((k) => [
      { text: k.name, sub: Object.entries(k.by_platform).map(([p, v]) => `${p} ${money(v)}`).join(' · ') || 'no closed trades yet' },
      k.leading ? tag('LEADING', 'lead') : tag(k.open ? 'OPEN' : 'CLOSED', k.open ? 'demo' : ''),
      { text: String(k.trades), cls: 'r' }, { text: k.win_rate == null ? '--' : Math.round(k.win_rate * 100) + '%', cls: 'r' },
      { text: money(k.pnl), cls: 'r ' + sign(k.pnl) }])));

    $('book').replaceChildren(d.by_symbol.length ? table(['Symbol', 'Platforms', 'Net', 'P&L'], d.by_symbol.map((s) => [
      s.symbol, s.platforms.join(', '), { text: money(s.net), cls: 'r' }, { text: money(s.pnl), cls: 'r ' + sign(s.pnl) }]))
      : el('div', 'small', 'No open positions on any platform.'));

    $('routes').replaceChildren(table(['Platform', 'Mode', 'Orders go to'], d.routes.map((x) => [
      x.name, tag(x.mode.toUpperCase(), x.mode === 'live' ? 'live' : x.mode === 'demo' ? 'demo' : ''), { text: x.goes_to, sub: x.why || '' }])));

    $('recent').replaceChildren(d.recent.length ? table(['When', 'Platform', 'Symbol', 'P&L'], d.recent.map((c) => [
      typeof c.ts === 'number' ? new Date(c.ts * 1000).toLocaleString() : '', c.platform, c.symbol || '',
      { text: money(c.pnl), cls: 'r ' + sign(c.pnl) }])) : el('div', 'small', 'Nothing closed yet.'));
  }

  function renderAtas(a) {
    const box = $('atas'); box.replaceChildren();
    const state = { off: 'OFF', waiting: 'WAITING FOR ATAS', live: 'LIVE', stale: 'STALE' }[a.state] || a.state;
    const head = el('div'); head.append(tag(state, a.state === 'live' ? 'demo' : a.state === 'stale' ? 'live' : '').node); box.append(head);
    if (a.help) box.append(el('div', 'small', a.help));
    if (a.state === 'waiting') {
      box.append(el('div', 'small', a.installed
        ? `Add-on installed on your ${a.installed.system === 'windows' ? 'Windows PC' : a.installed.system === 'mac' ? 'Mac' : 'computer'}. Now open ATAS, add JarvisFeed (ATS Matrix) to a chart and bars appear here within a minute.`
        : 'Not connected yet. Use Connect ATAS below: three steps, about two minutes.'));
    }
    $('atas-connect').hidden = a.state === 'live';
    if (a.books.length) {
      box.append(table(['Symbol', 'Close', 'Delta', 'Cum delta', 'POC'], a.books.map((b) => {
        const l = b.last || {};
        return [{ text: b.symbol, sub: `${b.timeframe || ''} · ${b.fresh ? 'fresh' : b.age_s + 's old'}` }, l.close, { text: l.delta, cls: sign(l.delta) },
          { text: l.cum_delta, cls: sign(l.cum_delta) }, l.poc];
      })));
    }
    if (a.account) box.append(el('div', 'small', `ATAS account ${a.account.name}: balance ${money(a.account.balance)}, open P&L ${money(a.account.open_pnl)}`));
  }

  // Connect ATAS: a fresh 30-minute install line for the Mac or Windows computer with ATAS on it.
  let lines = null; let os = /Win/.test(navigator.platform || navigator.userAgent) ? 'windows' : 'mac';
  function showLine() {
    $('os-mac').className = os === 'mac' ? 'on' : ''; $('os-win').className = os === 'windows' ? 'on' : '';
    $('atas-line').textContent = lines ? lines[os] : '';
    $('atas-note').textContent = lines ? `Works for ${lines.minutes} minutes. ${os === 'mac' ? 'Open Terminal (Cmd+Space, type Terminal), paste, press Enter.' : 'Open PowerShell (Start, type PowerShell), paste, press Enter.'} Safe to run again later to update.` : '';
  }
  $('atas-go').addEventListener('click', async () => {
    $('atas-go').disabled = true;
    try {
      const r = await fetch('/api/atas/install', { cache: 'no-store' });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || 'could not make the install line');
      lines = j; $('atas-lines').hidden = false; showLine();
    } catch (e) { $('atas-lines').hidden = false; $('atas-line').textContent = ''; $('atas-note').textContent = String(e.message || e); }
    $('atas-go').disabled = false; $('atas-go').textContent = 'New install line';
  });
  $('os-mac').addEventListener('click', () => { os = 'mac'; showLine(); });
  $('os-win').addEventListener('click', () => { os = 'windows'; showLine(); });
  $('atas-copy').addEventListener('click', async () => {
    if (!lines) return;
    try { await navigator.clipboard.writeText(lines[os]); $('atas-copy').textContent = 'Copied'; }
    catch (e) { const r = document.createRange(); r.selectNodeContents($('atas-line')); const s = getSelection(); s.removeAllRanges(); s.addRange(r); $('atas-copy').textContent = 'Selected'; }
    setTimeout(() => { $('atas-copy').textContent = 'Copy'; }, 2000);
  });

  async function load() {
    try {
      const [r, a] = await Promise.all([fetch('/api/account', { cache: 'no-store' }), fetch('/api/atas', { cache: 'no-store' })]);
      if (r.status === 401) { location.href = '/login'; return; }
      render(await r.json());
      if (a.ok) renderAtas(await a.json());
    } catch (e) { $('switch-msg').textContent = 'Could not load the account. Retrying.'; }
  }

  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || 'refused');
    return j;
  }

  $('m-demo').onclick = async () => {
    $('confirm').hidden = true;
    try { await post('/api/account/mode', { mode: 'demo' }); $('switch-msg').textContent = 'Back on demo.'; load(); }
    catch (e) { $('switch-msg').textContent = e.message; }
  };
  $('m-live').onclick = () => { if (data && data.mode !== 'live') { $('confirm').hidden = false; $('confirm-in').focus(); } };
  $('cancel').onclick = () => { $('confirm').hidden = true; $('confirm-in').value = ''; };
  $('go-live').onclick = async () => {
    try {
      await post('/api/account/mode', { mode: 'live', confirm: $('confirm-in').value });
      $('confirm').hidden = true; $('confirm-in').value = ''; $('switch-msg').textContent = 'LIVE: real money.'; load();
    } catch (e) { $('switch-msg').textContent = e.message; }
  };

  $('t-pf').onchange = () => {
    const et = $('t-pf').value === 'etoro';
    ['l-sym', 'l-vol'].forEach((id) => { $(id).hidden = et; });
    ['l-inst', 'l-amt'].forEach((id) => { $(id).hidden = !et; });
  };
  async function order(side) {
    const pf = $('t-pf').value;
    const body = { platform: pf, side, entry: $('t-entry').value, stop_loss: $('t-sl').value, take_profit: $('t-tp').value };
    if (pf === 'mt5') Object.assign(body, { symbol: $('t-sym').value.trim(), volume: $('t-vol').value });
    else Object.assign(body, { instrument_id: $('t-inst').value, amount: $('t-amt').value, leverage: 1 });
    if (data && data.mode === 'live' && !window.confirm(`REAL MONEY: ${side.toUpperCase()} on ${pf}. Send it?`)) return;
    try { const j = await post('/api/account/order', body); $('t-msg').textContent = `Sent (${j.mode}): ${JSON.stringify(j.order).slice(0, 160)}`; load(); }
    catch (e) { $('t-msg').textContent = e.message; }
  }
  $('t-buy').onclick = () => order('buy');
  $('t-sell').onclick = () => order('sell');

  load();
  setInterval(load, 15000);
})();
