// 24/7 session desks: /desks shows all four, /desks/<key> shows one desk in full.
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const ORDER = ['sydney', 'asia', 'europe', 'london', 'newyork'];
  const NAMES = { sydney: 'Sydney', asia: 'Asia', europe: 'Europe', london: 'London', newyork: 'New York' };
  const one = (location.pathname.match(/^\/desks\/(sydney|asia|europe|london|newyork)$/) || [])[1];
  const money = (v) => (v == null ? '–' : (v >= 0 ? '+' : '−') + '$' + Math.abs(v).toFixed(2));
  const pct = (v) => (v == null ? '–' : Math.round(v * 100) + '%');
  const mins = (m) => (m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + (m % 60) + ' min');
  if (one) document.querySelector('.dk').classList.add('one');

  function tabs() {
    const box = $('tabs');
    const all = el('a', '', null); all.href = '/desks';
    const b = el('button', one ? '' : 'on', 'All desks'); all.append(b); box.append(all);
    ORDER.forEach((k) => { const a = el('a'); a.href = '/desks/' + k; a.append(el('button', one === k ? 'on' : '', NAMES[k])); box.append(a); });
  }

  function pill(d) {
    if (d.status === 'leading') return el('span', 'pg-pill lead', d.weekend ? 'CRYPTO ONLY' : 'LEADING');
    if (d.status === 'open') return el('span', 'pg-pill open', 'OPEN');
    return el('span', 'pg-pill flat', 'CLOSED');
  }

  function deskCard(d) {
    const card = el(one ? 'div' : 'a', 'pg-card dk-desk ' + d.status);
    if (!one) card.href = '/desks/' + d.key;
    const head = el('header'); head.append(el('h2', '', d.name), pill(d)); card.append(head);
    card.append(el('div', 't', d.city + ' ' + d.city_time));
    card.append(el('p', '', d.note));
    const dl = el('dl');
    const row = (k, v, cls) => { dl.append(el('dt', '', k)); dl.append(el('dd', cls || '', v)); };
    row(d.open ? 'Closes' : 'Opens', d.open ? d.hours.close_melbourne : d.hours.open_melbourne);
    row('Hours', d.hours.melbourne.replace(' Melbourne', ''));
    row('City hours', d.hours.local);
    row('Agents on shift', d.on_shift.toLocaleString() + ' / ' + d.crew_total.toLocaleString());
    row('Trade size', d.size ? Math.round(d.size * 100) + '%' : 'no new trades');
    row('Trades', d.book.trades + ' · ' + pct(d.book.win_rate) + ' won');
    row('P&L', money(d.book.pnl), d.book.pnl > 0 ? 'up' : d.book.pnl < 0 ? 'dn' : '');
    card.append(dl);
    const chips = el('div', 'dk-chips');
    d.markets.forEach((m) => { const c = el(one ? 'a' : 'span', 'dk-chip', m.symbol); if (one) { c.href = '/markets/' + m.symbol; c.title = m.name; } chips.append(c); });
    card.append(chips);
    if (one) {
      const crew = el('div', 'dk-crew'); crew.style.marginTop = '14px';
      Object.entries(d.crew).forEach(([k, n]) => { const c = el('div'); c.append(el('small', '', k), el('b', '', n.toLocaleString())); crew.append(c); });
      card.append(crew);
    }
    return card;
  }

  function clock(data) {
    const box = $('clock'); box.replaceChildren();
    data.timeline.forEach((t) => {
      if (one && t.desk !== one) return;
      const r = el('div', 'dk-row'); r.append(el('span', '', t.name));
      const track = el('div', 'dk-track');
      t.bars.forEach((b, i) => {
        const bar = el('i', 'dk-bar' + (i === 0 && b.from_h === 0 && t.desk === data.lead ? ' lead' : ''));
        bar.style.left = (b.from_h / 24 * 100) + '%'; bar.style.width = ((b.to_h - b.from_h) / 24 * 100) + '%';
        track.append(bar);
      });
      r.append(track); box.append(r);
    });
    const ticks = el('div', 'dk-ticks'); ticks.append(el('span'));
    const labels = el('div');
    const now = new Date();
    for (let h = 0; h <= 24; h += 6) {
      const t = new Date(now.getTime() + h * 3600e3);
      labels.append(el('span', '', h === 0 ? 'now' : t.toLocaleTimeString('en-AU', { timeZone: 'Australia/Melbourne', hour: '2-digit', minute: '2-digit', hour12: false })));
    }
    ticks.append(labels); box.append(ticks);
  }

  function render(data) {
    $('now').textContent = data.now_melbourne + ' Melbourne' + (data.weekend ? ' · weekend: crypto only' : '');
    const lead = data.desks.find((d) => d.key === data.lead);
    $('k-lead').textContent = lead.name;
    $('k-lead-sub').textContent = lead.on_shift.toLocaleString() + ' agents on shift';
    const h = data.handoffs[0];
    $('k-next').textContent = h ? h.melbourne : '–';
    $('k-next-sub').textContent = h ? h.from_name + ' → ' + h.to_name : '';
    $('k-cov').textContent = data.coverage_hours + ' h';
    $('k-cov-sub').textContent = data.coverage_hours >= 24 ? 'round the clock' : data.weekend ? 'weekend gap: crypto only' : 'gap ahead';
    $('k-route').textContent = 'Paper desk';
    $('k-route-sub').textContent = 'MT5 demo: ' + data.route.mt5_demo + ' · real money locked';
    clock(data);
    const shown = one ? data.desks.filter((d) => d.key === one) : data.desks;
    $('desks').replaceChildren(...shown.map(deskCard));
    $('handoffs').replaceChildren(...data.handoffs.map((x) => {
      const it = el('div', 'pg-item'); it.append(el('span', 'sym', x.melbourne.split(' ')[0]));
      const t = el('span', 't', x.from_name + ' hands over to ' + x.to_name); it.append(t);
      it.append(el('span', 'g', x.melbourne.split(' ')[1])); return it;
    }));
    const table = $('book'); table.replaceChildren();
    const hr = el('tr'); ['Desk', 'Symbol', 'Side', 'Result', 'P&L'].forEach((c, i) => hr.append(el('th', i === 4 ? 'r' : '', c))); table.append(hr);
    const rows = [];
    shown.forEach((d) => d.book.recent.forEach((t) => rows.push([d.name, t])));
    rows.sort((a, b) => b[1].ts - a[1].ts);
    if (!rows.length) { const tr = el('tr'); const td = el('td', 'pg-empty', 'No closed trades yet.'); td.colSpan = 5; tr.append(td); table.append(tr); }
    rows.slice(0, 30).forEach(([name, t]) => {
      const tr = el('tr', t.won ? 'win' : 'loss');
      tr.append(el('td', '', name), el('td', 'o', t.symbol || '–'), el('td', '', t.side > 0 || t.side === 'buy' ? 'Buy' : 'Sell'),
        el('td', '', t.won ? 'Won' : 'Lost'), el('td', 'r ' + (t.pnl >= 0 ? 'up' : 'dn'), money(t.pnl)));
      table.append(tr);
    });
    if (one) {
      const d = shown[0];
      $('kicker').textContent = d.city + ' session · practice';
      $('title').replaceChildren(el('b', '', d.name), document.createTextNode(' desk'));
      $('lede').textContent = d.note + ' Hours ' + d.hours.melbourne + '.';
      document.title = 'ATS Matrix · ' + d.name + ' desk';
    }
  }

  async function load() {
    try {
      const r = await fetch(one ? '/api/desks/' + one : '/api/desks', { credentials: 'same-origin' });
      if (r.status === 401) { location.href = '/login'; return; }
      render(await r.json());
    } catch (e) { $('now').textContent = 'Could not load the desks. Retrying…'; }
  }
  tabs(); load(); setInterval(load, 30000);
})();
