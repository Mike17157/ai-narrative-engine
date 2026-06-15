"""The single self-contained SPA page served when no built frontend exists.

INDEX_HTML moved VERBATIM (byte-for-byte) from app.py — same r-string literal.
"""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Loom</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.5 system-ui,sans-serif; background:#14161a; color:#e6e8ec; }
  header { padding:12px 18px; background:#1b1e24; border-bottom:1px solid #2a2e36;
           display:flex; align-items:center; gap:14px; }
  header h1 { font-size:18px; margin:0; letter-spacing:.5px; }
  .badge { font-size:12px; padding:3px 9px; border-radius:999px; border:1px solid #2a2e36; }
  .badge.up { background:#14331f; color:#7ee0a0; } .badge.down { background:#371a1a; color:#f0a0a0; }
  button { font:inherit; background:#2a6df0; color:#fff; border:0; padding:7px 12px; border-radius:7px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  button.ghost { background:#232730; color:#cfd3da; border:1px solid #2a2e36; }
  button.sm { padding:3px 8px; font-size:12px; }
  main { display:grid; grid-template-columns:320px 1fr; height:calc(100vh - 51px); }
  aside { border-right:1px solid #2a2e36; background:#161920; display:flex; flex-direction:column; min-height:0; }
  .tabs { display:flex; border-bottom:1px solid #2a2e36; }
  .tabs button { flex:1; background:none; color:#8b93a1; border:0; border-bottom:2px solid transparent;
                 border-radius:0; padding:11px 0; }
  .tabs button.active { color:#e6e8ec; border-bottom-color:#2a6df0; }
  .panel { padding:16px; overflow:auto; display:none; } .panel.active { display:block; }
  label { display:block; font-size:12px; color:#8b93a1; margin:10px 0 4px; }
  input, select { width:100%; font:inherit; background:#232730; color:#e6e8ec;
                  border:1px solid #2a2e36; border-radius:7px; padding:8px; }
  .row { display:flex; gap:8px; align-items:center; }
  .status { font-size:13px; margin:10px 0; min-height:18px; }
  .ok { color:#7ee0a0; } .err { color:#f0a0a0; }
  .item { padding:6px 8px; border-radius:7px; cursor:pointer; display:flex; align-items:center; gap:8px; }
  .item:hover { background:#1f2430; } .item.sel { background:#22304a; }
  .item .kind { font-size:11px; color:#8b93a1; margin-left:auto; }
  .chat { display:flex; flex-direction:column; min-height:0; }
  .controls { padding:10px 16px; border-bottom:1px solid #2a2e36; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  .controls .lbl { font-size:12px; color:#8b93a1; } .controls b { color:#cfd3da; font-weight:600; }
  .controls select { width:auto; }
  .log { flex:1; overflow:auto; padding:18px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:720px; } .msg.user { align-self:flex-end; }
  .bubble { padding:10px 14px; border-radius:12px; white-space:pre-wrap; }
  .msg.user .bubble { background:#2a6df0; color:#fff; } .msg.bot .bubble { background:#232730; }
  .msg img { max-width:360px; border-radius:10px; margin-top:8px; display:block; }
  .composer { display:flex; gap:10px; padding:14px 16px; border-top:1px solid #2a2e36; }
  textarea { flex:1; font:inherit; background:#232730; color:#e6e8ec; border:1px solid #2a2e36;
             border-radius:9px; padding:10px; resize:none; height:52px; }
  .hint { font-size:12px; color:#8b93a1; }
</style>
</head>
<body>
<header>
  <h1>Loom</h1>
  <span id="comfyBadge" class="badge down">ComfyUI: …</span>
  <span id="who" style="margin-left:auto;color:#8b93a1;font-size:13px"></span>
</header>
<main>
  <aside>
    <div class="tabs">
      <button data-tab="conn" class="active">Connection</button>
      <button data-tab="chars">Characters</button>
      <button data-tab="image">Image</button>
    </div>

    <div id="tab-conn" class="panel active">
      <label>Provider</label>
      <select id="provider"></select>
      <div id="baseWrap"><label>Base URL</label><input id="baseUrl"/></div>
      <label>API key</label>
      <input id="apiKey" type="password" placeholder="paste your key"/>
      <div class="row" style="margin-top:10px">
        <button id="connectBtn">Connect</button>
        <button id="saveBtn" class="ghost" disabled>Save</button>
      </div>
      <div id="connStatus" class="status"></div>
      <label>Model</label>
      <select id="modelSel"><option value="">— connect first —</option></select>
      <label style="margin-top:16px">Saved connections</label>
      <div id="connList"></div>
    </div>

    <div id="tab-chars" class="panel">
      <div class="hint">Pick the persona the chat model plays.</div>
      <div id="charList" style="margin-top:8px"></div>
    </div>

    <div id="tab-image" class="panel">
      <div class="row" style="justify-content:space-between">
        <span id="comfyBadge2" class="badge down">ComfyUI: …</span>
        <button id="comfyBtn" class="ghost sm">Start ComfyUI</button>
      </div>
      <label style="margin-top:14px">Image model (pipeline)</label>
      <select id="imageSel"></select>
      <div id="imageInfo" class="hint" style="margin-top:8px"></div>
    </div>
  </aside>

  <div class="chat">
    <div class="controls">
      <span><span class="lbl">Pipeline</span> <select id="pipeline"></select></span>
      <span><span class="lbl">Chat:</span> <b id="curChat">—</b></span>
      <span><span class="lbl">Character:</span> <b id="curChar">none</b></span>
      <span><span class="lbl">Image:</span> <b id="curImage">—</b></span>
    </div>
    <div id="logEl" class="log"></div>
    <div class="composer">
      <textarea id="input" placeholder="Message…  (Enter to send, Shift+Enter for newline)"></textarea>
      <button id="send">Send</button>
    </div>
  </div>
</main>
<script>
const $ = s => document.querySelector(s);
const state = { providers: [], activeChar: '', activeImage: '', activeConn: null, activeModel: null };
function esc(s){ return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function add(role, html){ const m=document.createElement('div'); m.className='msg '+role;
  m.innerHTML='<div class="bubble">'+html+'</div>'; $('#logEl').appendChild(m); $('#logEl').scrollTop=1e9; return m; }

// tabs
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); $('#tab-'+b.dataset.tab).classList.add('active');
});

async function loadProviders(){
  state.providers = await (await fetch('/api/providers')).json();
  $('#provider').innerHTML = state.providers.map(p=>`<option value="${p.id}">${p.label}</option>`).join('');
  syncProvider();
}
function syncProvider(){
  const p = state.providers.find(x=>x.id===$('#provider').value);
  if (!p) return;
  $('#baseUrl').value = p.default_base_url;
  $('#baseWrap').style.display = p.base_url_editable ? '' : 'none';
}
$('#provider').onchange = syncProvider;

$('#connectBtn').onclick = async () => {
  const st = $('#connStatus'); st.textContent='Connecting…'; st.className='status';
  $('#connectBtn').disabled=true;
  try{
    const r = await fetch('/api/connections/test',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({provider:$('#provider').value, api_key:$('#apiKey').value, base_url:$('#baseUrl').value})});
    const d = await r.json();
    if(!d.ok){ st.textContent='✗ '+d.error; st.className='status err'; return; }
    st.textContent='✓ Connected — '+d.count+' models'; st.className='status ok';
    $('#modelSel').innerHTML = d.models.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
    $('#saveBtn').disabled=false;
  }catch(e){ st.textContent='✗ '+e; st.className='status err'; }
  finally{ $('#connectBtn').disabled=false; }
};

$('#saveBtn').onclick = async () => {
  await fetch('/api/connections',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({provider:$('#provider').value, api_key:$('#apiKey').value,
      base_url:$('#baseUrl').value, model:$('#modelSel').value})});
  $('#apiKey').value='';
  await refresh(); await loadConnections();
};

async function loadConnections(){
  const d = await (await fetch('/api/connections')).json();
  $('#connList').innerHTML = d.connections.map(c=>{
    const act = c.id===d.active ? 'sel':'';
    return `<div class="item ${act}" data-act="${c.id}">${c.id}
      <span class="kind">${esc(c.model||'')} ${c.has_key?'🔑':''}</span>
      <button class="ghost sm" data-del="${c.id}">✕</button></div>`;
  }).join('') || '<div class="hint">none yet</div>';
  $('#connList').querySelectorAll('[data-act]').forEach(el=>el.onclick=async ev=>{
    if(ev.target.dataset.del) return;
    await fetch('/api/connections/'+el.dataset.act+'/activate',{method:'POST'}); refresh(); loadConnections();
  });
  $('#connList').querySelectorAll('[data-del]').forEach(el=>el.onclick=async ev=>{
    ev.stopPropagation();
    await fetch('/api/connections/'+el.dataset.del,{method:'DELETE'}); refresh(); loadConnections();
  });
}

async function loadModels(){
  const d = await (await fetch('/api/models')).json();
  $('#imageSel').innerHTML = d.image.map(m=>`<option value="${m.key}">${m.key} (${m.provider})</option>`).join('');
  if(!state.activeImage && d.image[0]) state.activeImage = d.image[0].key;
  if(state.activeImage) $('#imageSel').value = state.activeImage;
  const im = d.image.find(m=>m.key===state.activeImage);
  $('#imageInfo').textContent = im ? `provider: ${im.provider}` : '';
  $('#curImage').textContent = state.activeImage || '—';
}
$('#imageSel').onchange = () => { state.activeImage=$('#imageSel').value; loadModels(); };

async function refresh(){
  const h = await (await fetch('/api/health')).json();
  for(const id of ['#comfyBadge','#comfyBadge2']){ const b=$(id);
    b.textContent='ComfyUI: '+(h.comfyui.up?'up':'down'); b.className='badge '+(h.comfyui.up?'up':'down'); }
  $('#comfyBtn').style.display = h.comfyui.up?'none':'';
  $('#who').textContent = h.profile?.name ? ('You: '+h.profile.name) : '';
  state.activeConn = h.active_connection; state.activeModel = h.active_model;
  $('#curChat').textContent = h.active_model || h.active_connection || '(none — connect)';
  $('#pipeline').innerHTML = h.pipelines.map(p=>`<option>${p}</option>`).join('');
  if(h.defaults?.pipeline) $('#pipeline').value = h.defaults.pipeline;
  if(!state.activeChar && h.defaults?.character) state.activeChar = h.defaults.character;
  $('#curChar').textContent = state.activeChar || 'none';
  $('#charList').innerHTML = ['<div class="item '+(state.activeChar?'':'sel')+'" data-char="">(none)</div>']
    .concat(h.characters.map(c=>`<div class="item ${c===state.activeChar?'sel':''}" data-char="${c}">${c}</div>`)).join('');
  $('#charList').querySelectorAll('[data-char]').forEach(el=>el.onclick=()=>{
    state.activeChar = el.dataset.char; $('#curChar').textContent = state.activeChar||'none'; refresh(); });
}

$('#comfyBtn').onclick = async () => {
  $('#comfyBtn').disabled=true; $('#comfyBtn').textContent='Starting…';
  try{ const d=await (await fetch('/api/comfy/up',{method:'POST'})).json();
    if(!d.ok) add('bot','<span class="err">ComfyUI failed: '+esc(d.error)+'</span>'); }
  finally{ $('#comfyBtn').disabled=false; $('#comfyBtn').textContent='Start ComfyUI'; refresh(); }
};

async function send(){
  const text=$('#input').value.trim(); if(!text) return;
  $('#input').value=''; add('user',esc(text)); const t=add('bot','…');
  try{
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({pipeline:$('#pipeline').value, character:state.activeChar||null,
        chat_model: state.activeConn||null, image_model: state.activeImage||null, message:text})});
    const d=await r.json();
    if(d.error){ t.querySelector('.bubble').innerHTML='<span class="err">'+esc(d.error)+'</span>'; }
    else{ let html=esc(d.reply||''); (d.images||[]).forEach(s=>html+='<img src="'+s+'"/>');
      t.querySelector('.bubble').innerHTML = html||'<span class="hint">(no reply)</span>'; }
  }catch(e){ t.querySelector('.bubble').innerHTML='<span class="err">'+esc(''+e)+'</span>'; }
  $('#logEl').scrollTop=1e9;
}
$('#send').onclick=send;
$('#input').addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); send(); }});

loadProviders(); loadConnections(); loadModels(); refresh(); setInterval(refresh, 6000);
</script>
</body>
</html>
"""
