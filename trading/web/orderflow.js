const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let DATA = {books:{}, houses:[]};
const pick = document.getElementById('pick');
pick.onchange = () => draw(pick.value);
function fmt(n){return Math.abs(n)>=1000?n.toFixed(0):(+n.toPrecision(3)).toString()}
function draw(sym){
  const s = DATA.books[sym]; if(!s) return;
  document.getElementById('sym').textContent = sym + (s.feed ? ' · ' + s.feed : '');
  const v = s.view, d = v.direction;
  const el = document.getElementById('dir');
  el.textContent = !v.ready ? 'Waiting for data' : (d>0.1?'Bullish ':d<-0.1?'Bearish ':'Neutral ') + (d>=0?'+':'') + d.toFixed(2);
  el.className = 'big ' + (d>0.1?'buy':d<-0.1?'sell':'');
  document.getElementById('why').innerHTML = (v.reasons||[]).map(r=>'<div>• '+esc(r)+'</div>').join('');
  const p = s.profile;
  document.getElementById('prof').innerHTML = p.poc==null ? 'No volume yet' :
    `POC <b>${fmt(p.poc)}</b><br>Value area ${fmt(p.val)} – ${fmt(p.vah)}<br>Volume ${fmt(p.volume)}`;
  // footprint
  const bars = s.bars; bars.forEach(b=>{b.lv={};Object.entries(b.levels).forEach(([k,v])=>b.lv[+k]=v);}); if(!bars.length){document.getElementById('fp').textContent='No bars yet';return;}
  const prices = new Set(); bars.forEach(b=>Object.keys(b.levels).forEach(k=>prices.add(+k)));
  const ps = [...prices].sort((a,b)=>b-a); const rowH=16, colW=96, left=70, profW=120;
  const H = ps.length*rowH+30, W = left+bars.length*colW+profW+10;
  const maxCell = Math.max(1,...bars.flatMap(b=>Object.values(b.levels).map(l=>Math.max(l.bid,l.ask))));
  const sess = {}; Object.entries(s.session_levels).forEach(([k,v])=>sess[+k]=v); const maxS = Math.max(1,...Object.values(sess));
  let g = `<svg width="${W}" height="${H}">`;
  ps.forEach((pr,i)=>{ g+=`<text x="4" y="${i*rowH+12}" class="${pr==p.poc?'t-poc':'t-mute'}">${fmt(pr)}</text>`; });
  bars.forEach((b,j)=>{
    const x = left + j*colW;
    ps.forEach((pr,i)=>{
      const l = b.lv[pr]; if(!l) return;
      const y=i*rowH, dl=l.ask-l.bid, a=Math.min(1,Math.abs(dl)/maxCell)*0.8+0.08;
      const col = dl>=0?'38,162,105':'229,83,75';
      const isPoc = pr==b.poc;
      g+=`<rect x="${x}" y="${y}" width="${colW-6}" height="${rowH-2}" rx="2" fill="rgba(${col},${a})" ${isPoc?'class="s-poc"':''}/>`;
      g+=`<text x="${x+4}" y="${y+11}">${fmt(l.bid)} x ${fmt(l.ask)}</text>`;
    });
    (b.stacked_buy||[]).forEach(r=>{const i=ps.indexOf(r[1]),k=ps.indexOf(r[0]); if(i>=0&&k>=0) g+=`<rect x="${x+colW-5}" y="${i*rowH}" width="3" height="${(k-i+1)*rowH-2}" class="f-buy"/>`;});
    (b.stacked_sell||[]).forEach(r=>{const i=ps.indexOf(r[1]),k=ps.indexOf(r[0]); if(i>=0&&k>=0) g+=`<rect x="${x-3}" y="${i*rowH}" width="3" height="${(k-i+1)*rowH-2}" class="f-sell"/>`;});
    const dc = b.delta>=0?'f-buy':'f-sell';
    g+=`<text x="${x+4}" y="${ps.length*rowH+14}" class="${dc}">Δ ${b.delta>=0?'+':''}${fmt(b.delta)}</text>`;
    if(b.big_trades.length) g+=`<text x="${x+4}" y="${ps.length*rowH+26}" class="t-mute">● ${b.big_trades.length} big</text>`;
  });
  const px = left + bars.length*colW + 4;
  ps.forEach((pr,i)=>{ const v=sess[pr]||0, w=v/maxS*(profW-10);
    const inVA = p.val!=null && pr>=p.val && pr<=p.vah;
    g+=`<rect x="${px}" y="${i*rowH+2}" width="${w}" height="${rowH-5}" class="${pr==p.poc?'f-poc':inVA?'f-va':'f-out'}"/>`; });
  g+='</svg>'; document.getElementById('fp').innerHTML = g;
  // CVD
  const c = s.cvd; const cw = Math.max(300, c.length*24), ch = 120;
  const lo = Math.min(0,...c), hi = Math.max(0,...c), sp = (hi-lo)||1;
  const pts = c.map((v,i)=>`${10+i*(cw-20)/Math.max(1,c.length-1)},${ch-10-(v-lo)/sp*(ch-20)}`).join(' ');
  const zy = ch-10-(0-lo)/sp*(ch-20);
  document.getElementById('cvd').innerHTML = `<svg width="${cw}" height="${ch}"><line x1="0" x2="${cw}" y1="${zy}" y2="${zy}" class="s-line"/><polyline class="${c[c.length-1]>=0?'l-buy':'l-sell'}" points="${pts}"/></svg>`;
}
function houses(){
const t = document.getElementById('houses');
t.innerHTML = '<tr><th>City</th><th>House</th><th>Hit rate</th><th>Latest call</th></tr>' + DATA.houses.map(h=>{
  const l=h.latest; const call = l ? `<span class="${l.direction>0?'buy':l.direction<0?'sell':''}">${esc(l.market)} ${l.direction>0?'bullish':l.direction<0?'bearish':'neutral'}</span> <span class="mute">${esc(l.summary).slice(0,90)}</span>` : '<span class="mute">no public note yet</span>';
  return `<tr><td>${esc(h.city)}</td><td>${esc(h.name)}</td><td>${h.scored_calls?Math.round(h.hit_rate*100)+'%':'<span class="mute">new</span>'}</td><td>${call}</td></tr>`;}).join('');
}
async function load(){
  try{
    const r = await fetch('/api/orderflow', {cache:'no-store'});
    if(r.status===401){ location.href='/login'; return; }
    DATA = await r.json();
  }catch(e){ return; }
  const keep = pick.value;
  pick.innerHTML='';
  Object.keys(DATA.books).forEach(s => { const o=document.createElement('option'); o.textContent=s; pick.appendChild(o); });
  if(keep && DATA.books[keep]) pick.value = keep;
  if(pick.options.length) draw(pick.value); else document.getElementById('dir').textContent='Waiting for prices';
  houses();
}
load(); setInterval(load, 10000);

