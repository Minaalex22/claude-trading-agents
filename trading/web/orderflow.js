const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let DATA = {books:{}, houses:[]};
const pick = document.getElementById('pick');
let CUR = null;
const fpDraw = window.Footprint ? Footprint.attach(document.getElementById('fpc'), () => DATA.books[CUR]) : () => {};
addEventListener('resize', () => fpDraw());
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
  // footprint: the shared pro chart (footprint.js)
  CUR = sym; fpDraw();
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

