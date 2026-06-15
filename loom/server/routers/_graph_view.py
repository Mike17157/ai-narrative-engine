"""Static HTML for the tag-similarity-graph visualization (served at GET /api/tags/graph/view).

A self-contained force-directed canvas view: it reads ?tags=&kind= from its own URL and fetches
GET /api/tags/graph for the neighbourhood. No build step, no external libraries.
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
  input{min-width:280px}
  button{background:#2a6df0;border:0;cursor:pointer;font-weight:600}
  .lo{color:#8b93a1;font-size:12px}
  .wrap{flex:1;position:relative;min-height:0}
  canvas{display:block;width:100%;height:100%}
  .legend{position:absolute;top:10px;right:12px;background:rgba(20,23,30,.82);border:1px solid #262b36;
          border-radius:10px;padding:8px 10px;font-size:11.5px;max-width:170px}
  .legend .row{display:flex;align-items:center;gap:7px;margin:2px 0}
  .legend .sw{width:11px;height:11px;border-radius:3px;flex:none}
  .hint{position:absolute;bottom:10px;left:12px;color:#6f7787;font-size:11.5px}
  .stat{position:absolute;bottom:10px;right:12px;color:#6f7787;font-size:11.5px}
</style></head>
<body>
<header>
  <b>Tag similarity graph</b>
  <input id="seeds" placeholder="seed tags, comma-separated (e.g. witch hat, black dress)"/>
  <select id="kind"><option value="clothing">clothing</option><option value="appearance">appearance</option></select>
  <button onclick="go()">Navigate</button>
  <span class="lo">click a node to re-center - drag to move a node</span>
</header>
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
let nodes=[], edges=[], adj=new Map(), hover=null, drag=null, alpha=1;
function resize(){ const r=cv.parentElement.getBoundingClientRect(); W=r.width; H=r.height;
  cv.width=W*DPR; cv.height=H*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); }
window.addEventListener('resize', resize);

const qp = new URLSearchParams(location.search);
document.getElementById('seeds').value = qp.get('tags') || 'witch hat, black dress';
document.getElementById('kind').value = (qp.get('kind')==='appearance')?'appearance':'clothing';

async function go(){
  const tags = document.getElementById('seeds').value.trim();
  const kind = document.getElementById('kind').value;
  history.replaceState(null,'', '?tags='+encodeURIComponent(tags)+'&kind='+kind);
  document.getElementById('stat').textContent = 'loading...';
  try {
    const r = await fetch('/api/tags/graph?tags='+encodeURIComponent(tags)+'&kind='+kind);
    build(await r.json());
  } catch(e){ document.getElementById('stat').textContent = 'error: '+e; }
}
function build(d){
  resize();
  const seeds = new Set(d.seeds||[]);
  const maxpc = Math.max(1, ...(d.nodes||[]).map(n=>n.post_count||0));
  nodes = (d.nodes||[]).map((n,i)=>({
    ...n, r: 5 + 9*Math.sqrt((n.post_count||1)/maxpc) + (n.seed?6:0),
    x: W/2 + Math.cos(i)*160*Math.random() + (Math.random()-.5)*80,
    y: H/2 + Math.sin(i)*160*Math.random() + (Math.random()-.5)*80, vx:0, vy:0,
    col: COLORS[n.facet]||COLORS.other
  }));
  const byId = new Map(nodes.map(n=>[n.id,n]));
  edges = (d.edges||[]).map(e=>({a:byId.get(e.source), b:byId.get(e.target), w:e.weight}))
                       .filter(e=>e.a&&e.b);
  adj = new Map(nodes.map(n=>[n.id,new Set()]));
  edges.forEach(e=>{ adj.get(e.a.id).add(e.b.id); adj.get(e.b.id).add(e.a.id); });
  const facets=[...new Set(nodes.map(n=>n.facet))];
  document.getElementById('legend').innerHTML = facets.map(f=>
    '<div class="row"><span class="sw" style="background:'+(COLORS[f]||COLORS.other)+'"></span>'+f+'</div>').join('');
  document.getElementById('stat').textContent = nodes.length+' nodes - '+edges.length+' edges';
  alpha=1;
}
function step(){
  const REP=2600, SPRING=0.012, GRAV=0.015, DAMP=0.86, LEN=70;
  for(let i=0;i<nodes.length;i++){ const a=nodes[i];
    for(let j=i+1;j<nodes.length;j++){ const b=nodes[j];
      let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy+0.01, d=Math.sqrt(d2);
      let f=REP/d2, fx=f*dx/d, fy=f*dy/d; a.vx+=fx;a.vy+=fy;b.vx-=fx;b.vy-=fy; } }
  edges.forEach(e=>{ let dx=e.b.x-e.a.x, dy=e.b.y-e.a.y, d=Math.sqrt(dx*dx+dy*dy)+.01;
    let f=SPRING*(d-LEN)*(0.4+e.w*3), fx=f*dx/d, fy=f*dy/d;
    e.a.vx+=fx;e.a.vy+=fy;e.b.vx-=fx;e.b.vy-=fy; });
  nodes.forEach(n=>{ n.vx+=(W/2-n.x)*GRAV; n.vy+=(H/2-n.y)*GRAV;
    if(n!==drag){ n.vx*=DAMP; n.vy*=DAMP; n.x+=n.vx*alpha; n.y+=n.vy*alpha; }
    n.x=Math.max(n.r,Math.min(W-n.r,n.x)); n.y=Math.max(n.r,Math.min(H-n.r,n.y)); });
  if(alpha>0.05) alpha*=0.998;
}
function draw(){
  ctx.clearRect(0,0,W,H);
  const hi = hover ? adj.get(hover.id) : null;
  edges.forEach(e=>{ const on = hover && (e.a===hover||e.b===hover);
    ctx.strokeStyle = on?'rgba(150,180,255,.55)':'rgba(120,130,150,'+(0.04+e.w*0.5)+')';
    ctx.lineWidth = on?1.6:Math.max(.4,e.w*4);
    ctx.beginPath(); ctx.moveTo(e.a.x,e.a.y); ctx.lineTo(e.b.x,e.b.y); ctx.stroke(); });
  nodes.forEach(n=>{ const dim = hover && n!==hover && !(hi&&hi.has(n.id));
    ctx.globalAlpha = dim?0.28:1;
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,7); ctx.fillStyle=n.col; ctx.fill();
    if(n.seed){ ctx.lineWidth=2.5; ctx.strokeStyle='#fff'; ctx.stroke(); }
    const big = n.seed || n.r>9 || n===hover || (hi&&hi.has(n.id));
    if(big){ ctx.globalAlpha = dim?0.4:1; ctx.fillStyle='#eef0f4';
      ctx.font=(n.seed?'600 12px':'11px')+' system-ui,sans-serif';
      ctx.fillText(n.id, n.x+n.r+3, n.y+3); }
    ctx.globalAlpha=1; });
}
function loop(){ if(nodes.length){ step(); draw(); } requestAnimationFrame(loop); }
function at(mx,my){ let best=null,bd=1e9; nodes.forEach(n=>{ let d=(n.x-mx)*(n.x-mx)+(n.y-my)*(n.y-my);
  if(d<bd && d<(n.r+4)*(n.r+4)){bd=d;best=n;} }); return best; }
cv.addEventListener('mousemove',e=>{ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  if(drag){ drag.x=mx; drag.y=my; drag.vx=drag.vy=0; alpha=Math.max(alpha,.4); }
  else { hover=at(mx,my); cv.style.cursor=hover?'pointer':'default'; } });
cv.addEventListener('mousedown',e=>{ const r=cv.getBoundingClientRect(); drag=at(e.clientX-r.left,e.clientY-r.top); });
window.addEventListener('mouseup',()=>{ drag=null; });
cv.addEventListener('click',e=>{ const r=cv.getBoundingClientRect(); const n=at(e.clientX-r.left,e.clientY-r.top);
  if(n){ document.getElementById('seeds').value=n.id; go(); } });
document.getElementById('seeds').addEventListener('keydown',e=>{ if(e.key==='Enter') go(); });
document.getElementById('hint').textContent='node size = popularity - edge = co-occurrence strength - color = facet';
resize(); loop(); go();
</script>
</body></html>
"""
