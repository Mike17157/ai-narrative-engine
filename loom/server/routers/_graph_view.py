"""Static HTML for the tag-similarity-graph visualization (served at GET /api/tags/graph/view).

A self-contained force-directed canvas browser for the tag graph: search a tag (autocomplete via
GET /api/tags/search), explore its neighbourhood (GET /api/tags/graph), click nodes to re-center,
shift-click to collect tags. No build step, no external libraries.
"""

GRAPH_VIEW_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Loom - tag similarity graph</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;height:100vh;display:flex;flex-direction:column;background:#0e1014;color:#e6e8ec;
       font:14px/1.45 system-ui,sans-serif;overflow:hidden}
  header{display:flex;gap:10px;align-items:center;padding:10px 14px;background:#161922;
         border-bottom:1px solid #262b36;flex-wrap:wrap}
  header b{letter-spacing:.4px}
  input,select,button{font:inherit;background:#1d212b;color:#e6e8ec;border:1px solid #2b313d;
       border-radius:8px;padding:7px 10px}
  input{min-width:240px}
  button{background:#2a6df0;border:0;cursor:pointer;font-weight:600}
  button.ghost{background:#222734;border:1px solid #2b313d;font-weight:500}
  .lo{color:#8b93a1;font-size:12px}
  .tray{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:7px 14px;background:#12151c;
        border-bottom:1px solid #262b36;min-height:40px}
  .chip{display:inline-flex;align-items:center;gap:6px;background:#222b3d;border:1px solid #2f3a52;
        border-radius:999px;padding:3px 6px 3px 10px;font-size:12.5px}
  .chip b{cursor:pointer;color:#9aa3b2}
  .chip b:hover{color:#ff8a8a}
  .wrap{flex:1;position:relative;min-height:0}
  canvas{display:block;width:100%;height:100%}
  .legend{position:absolute;top:10px;right:12px;background:rgba(20,23,30,.82);border:1px solid #262b36;
          border-radius:10px;padding:8px 10px;font-size:11.5px;max-width:160px}
  .legend .row{display:flex;align-items:center;gap:7px;margin:2px 0}
  .legend .sw{width:11px;height:11px;border-radius:3px;flex:none}
  .hint{position:absolute;bottom:10px;left:12px;color:#6f7787;font-size:11.5px}
  .stat{position:absolute;bottom:10px;right:12px;color:#6f7787;font-size:11.5px}
</style></head>
<body>
<header>
  <b>Tag graph</b>
  <input id="q" list="ac" placeholder="search a tag (e.g. witch hat)" autocomplete="off"/>
  <datalist id="ac"></datalist>
  <select id="kind"><option value="clothing">clothing</option><option value="appearance">appearance</option></select>
  <button onclick="goInput()">Explore</button>
  <span class="lo">click a node to explore - shift-click to collect - drag to move</span>
</header>
<div class="tray" id="tray"><span class="lo">collected tags:</span>
  <span id="chips"></span>
  <button class="ghost" onclick="copyPicked()">Copy</button>
  <button class="ghost" onclick="clearPicked()">Clear</button>
</div>
<div class="wrap">
  <canvas id="cv"></canvas>
  <div class="legend" id="legend"></div>
  <div class="hint" id="hint"></div>
  <div class="stat" id="stat"></div>
</div>
<script>
const COLORS = {
  hair:'#e0b04a', eyes:'#4ab0e0', skin:'#e08a6a', body:'#e06aa0', ears_tail:'#b06ae0', face:'#6ae0b0',
  swimwear:'#4ad6e0', piercing:'#e04a6a', makeup:'#e04ab0', dress:'#9a6ae0', top:'#5a8cf0',
  bottom:'#46c08a', outerwear:'#e0883a', legwear:'#c0c04a', footwear:'#a07a5a', headwear:'#e0c04a',
  sleeves:'#7ac0e0', accessories:'#8fa0b5', other:'#5a626f'
};
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let DPR = Math.max(1, window.devicePixelRatio||1), W=0, H=0;
let nodes=[], edges=[], adj=new Map(), hover=null, drag=null, dragged=false;
let alpha=1, running=true, picked=[];
function resize(){ const r=cv.parentElement.getBoundingClientRect(); W=r.width; H=r.height;
  cv.width=W*DPR; cv.height=H*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); }
window.addEventListener('resize', ()=>{ resize(); heat(); });
function heat(){ alpha=1; running=true; }

const qp = new URLSearchParams(location.search);
document.getElementById('q').value = qp.get('tags') || 'witch hat';
document.getElementById('kind').value = (qp.get('kind')==='appearance')?'appearance':'clothing';

// --- autocomplete over the real vocabulary -------------------------------------
let acTimer=null;
document.getElementById('q').addEventListener('input', e=>{
  const v=e.target.value.trim(); clearTimeout(acTimer);
  if(v.length<2) return;
  acTimer=setTimeout(async()=>{
    try{ const r=await fetch('/api/tags/search?limit=10&q='+encodeURIComponent(v)); const d=await r.json();
      document.getElementById('ac').innerHTML=(d.tags||[]).map(t=>'<option value="'+t.tag+'">').join('');
    }catch(_){}
  }, 140);
});
document.getElementById('q').addEventListener('keydown', e=>{ if(e.key==='Enter') goInput(); });
function goInput(){ explore(document.getElementById('q').value.trim()); }

async function explore(tags){
  const kind = document.getElementById('kind').value;
  document.getElementById('q').value = tags;
  history.replaceState(null,'', '?tags='+encodeURIComponent(tags)+'&kind='+kind);
  document.getElementById('stat').textContent='loading...';
  try{ const r=await fetch('/api/tags/graph?tags='+encodeURIComponent(tags)+'&kind='+kind); build(await r.json()); }
  catch(e){ document.getElementById('stat').textContent='error: '+e; }
}
function build(d){
  resize();
  const maxpc = Math.max(1, ...(d.nodes||[]).map(n=>n.post_count||0));
  nodes = (d.nodes||[]).map((n,i)=>{ const ang=i*2.399, rad=40+i*4;
    return {...n, r: 5 + 9*Math.sqrt((n.post_count||1)/maxpc) + (n.seed?6:0),
      x: W/2 + Math.cos(ang)*rad, y: H/2 + Math.sin(ang)*rad, vx:0, vy:0,
      col: COLORS[n.facet]||COLORS.other }; });
  const byId = new Map(nodes.map(n=>[n.id,n]));
  edges = (d.edges||[]).map(e=>({a:byId.get(e.source), b:byId.get(e.target), w:e.weight})).filter(e=>e.a&&e.b);
  adj = new Map(nodes.map(n=>[n.id,new Set()]));
  edges.forEach(e=>{ adj.get(e.a.id).add(e.b.id); adj.get(e.b.id).add(e.a.id); });
  const facets=[...new Set(nodes.map(n=>n.facet))];
  document.getElementById('legend').innerHTML = facets.map(f=>
    '<div class="row"><span class="sw" style="background:'+(COLORS[f]||COLORS.other)+'"></span>'+f+'</div>').join('');
  document.getElementById('stat').textContent = nodes.length+' nodes - '+edges.length+' edges';
  heat();
}
// --- calm force layout: high damping, cooling, freezes when settled -------------
function step(){
  if(!running) return;
  const REP=1500, SPRING=0.02, GRAV=0.02, DAMP=0.9, LEN=80, VMAX=18;
  for(let i=0;i<nodes.length;i++){ const a=nodes[i];
    for(let j=i+1;j<nodes.length;j++){ const b=nodes[j];
      let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy+0.01, d=Math.sqrt(d2);
      let f=REP/d2, fx=f*dx/d, fy=f*dy/d; a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy; } }
  edges.forEach(e=>{ let dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.sqrt(dx*dx+dy*dy)+.01;
    let f=SPRING*(d-LEN)*(0.4+e.w*3), fx=f*dx/d, fy=f*dy/d;
    e.a.vx+=fx;e.a.vy+=fy;e.b.vx-=fx;e.b.vy-=fy; });
  nodes.forEach(n=>{ if(n===drag) return;
    n.vx+=(W/2-n.x)*GRAV; n.vy+=(H/2-n.y)*GRAV; n.vx*=DAMP; n.vy*=DAMP;
    const sp=Math.hypot(n.vx,n.vy); if(sp>VMAX){ n.vx*=VMAX/sp; n.vy*=VMAX/sp; }
    n.x+=n.vx*alpha; n.y+=n.vy*alpha;
    n.x=Math.max(n.r,Math.min(W-n.r,n.x)); n.y=Math.max(n.r,Math.min(H-n.r,n.y)); });
  alpha*=0.985; if(alpha<0.03) running=false;   // settle and stop
}
function draw(){
  ctx.clearRect(0,0,W,H);
  const hi = hover ? adj.get(hover.id) : null;
  edges.forEach(e=>{ const on = hover && (e.a===hover||e.b===hover);
    ctx.strokeStyle = on?'rgba(150,180,255,.55)':'rgba(120,130,150,'+(0.05+e.w*0.5)+')';
    ctx.lineWidth = on?1.6:Math.max(.4,e.w*4);
    ctx.beginPath(); ctx.moveTo(e.a.x,e.a.y); ctx.lineTo(e.b.x,e.b.y); ctx.stroke(); });
  const pickset=new Set(picked);
  nodes.forEach(n=>{ const dim = hover && n!==hover && !(hi&&hi.has(n.id));
    ctx.globalAlpha = dim?0.28:1;
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,7); ctx.fillStyle=n.col; ctx.fill();
    if(pickset.has(n.id)){ ctx.lineWidth=3; ctx.strokeStyle='#7ee0a0'; ctx.stroke(); }
    else if(n.seed){ ctx.lineWidth=2.5; ctx.strokeStyle='#fff'; ctx.stroke(); }
    const big = n.seed || n.r>9 || n===hover || (hi&&hi.has(n.id)) || pickset.has(n.id);
    if(big){ ctx.globalAlpha = dim?0.4:1; ctx.fillStyle='#eef0f4';
      ctx.font=(n.seed?'600 12px':'11px')+' system-ui,sans-serif';
      ctx.fillText(n.id, n.x+n.r+3, n.y+3); }
    ctx.globalAlpha=1; });
}
function loop(){ step(); if(nodes.length) draw(); requestAnimationFrame(loop); }
function at(mx,my){ let best=null,bd=1e9; nodes.forEach(n=>{ let d=(n.x-mx)*(n.x-mx)+(n.y-my)*(n.y-my);
  if(d<bd && d<(n.r+4)*(n.r+4)){bd=d;best=n;} }); return best; }
cv.addEventListener('mousemove',e=>{ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  if(drag){ drag.x=mx; drag.y=my; drag.vx=drag.vy=0; dragged=true; heat(); }
  else { hover=at(mx,my); cv.style.cursor=hover?'pointer':'default'; } });
cv.addEventListener('mousedown',e=>{ const r=cv.getBoundingClientRect(); drag=at(e.clientX-r.left,e.clientY-r.top); dragged=false; });
window.addEventListener('mouseup',()=>{ drag=null; });
cv.addEventListener('click',e=>{ if(dragged){dragged=false;return;}
  const r=cv.getBoundingClientRect(); const n=at(e.clientX-r.left,e.clientY-r.top); if(!n) return;
  if(e.shiftKey) pick(n.id); else explore(n.id); });
// --- collected tags tray -------------------------------------------------------
function pick(t){ if(!picked.includes(t)) picked.push(t); renderPicked(); }
function unpick(t){ picked=picked.filter(x=>x!==t); renderPicked(); }
function clearPicked(){ picked=[]; renderPicked(); }
function copyPicked(){ navigator.clipboard&&navigator.clipboard.writeText(picked.join(', '));
  document.getElementById('stat').textContent='copied '+picked.length+' tags'; }
function renderPicked(){ document.getElementById('chips').innerHTML = picked.map(t=>
  '<span class="chip">'+t+' <b data-t="'+t+'">x</b></span>').join(''); }
document.getElementById('chips').addEventListener('click',e=>{ const t=e.target.getAttribute('data-t'); if(t) unpick(t); });

document.getElementById('kind').addEventListener('change', goInput);
document.getElementById('hint').textContent='node size = popularity - edge = co-occurrence strength - color = facet';
resize(); loop(); goInput();
</script>
</body></html>
"""
