<script>
  // A relationship-graph node: a character CARD with their base image. Source/target handles are
  // centred so bond edges run card-to-card. data._onClick(data) → open the character's detail modal.
  import { Handle, Position } from '@xyflow/svelte';
  let { data } = $props();
  // Handles MUST sit at the component ROOT (not inside .cnode) — a handle inside the styled card
  // isn't measured against the Svelte Flow node wrapper, so edges can't resolve their endpoints
  // and get silently dropped. This is the exact gotcha the workflow SectionNode documents.
  const H = 'opacity:0;border:0;width:8px;height:8px;';
</script>

<Handle type="target" position={Position.Left} id="t" style={H} />
<Handle type="source" position={Position.Right} id="s" style={H} />
<div class="cnode" class:focus={data.focus} class:primary={data.primary} onclick={() => data._onClick?.(data)}>
  <div class="av">
    {#if data.img}<img src={data.img} alt={data.name} />{:else}<span class="ph">🎭</span>{/if}
  </div>
  <div class="cn-name">{data.primary ? '★ ' : ''}{data.name}</div>
  {#if data.role}<div class="cn-role">{data.role}</div>{/if}
</div>

<style>
  .cnode {
    width: 132px; box-sizing: border-box; cursor: pointer;
    background: var(--panel, #1a1d27); border: 1.5px solid var(--border-soft, rgba(255,255,255,.1));
    border-radius: 12px; padding: 10px 8px 9px; display: flex; flex-direction: column; align-items: center; gap: 6px;
    box-shadow: 0 2px 12px rgba(0,0,0,.4); transition: border-color .15s, box-shadow .15s, transform .1s;
  }
  .cnode:hover { border-color: var(--accent, #6d8cff); transform: translateY(-2px);
                 box-shadow: 0 6px 22px rgba(109,140,255,.18), 0 2px 8px rgba(0,0,0,.4); }
  .cnode.primary { border-color: color-mix(in srgb, var(--accent, #6d8cff) 55%, transparent); }
  .cnode.focus { border-color: var(--accent, #6d8cff);
                 box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, #6d8cff) 45%, transparent), 0 4px 16px rgba(0,0,0,.45); }

  .av { width: 84px; height: 84px; border-radius: 50%; overflow: hidden; flex: none;
        background: var(--elev-2, #2a2f44); display: grid; place-items: center;
        border: 2px solid color-mix(in srgb, var(--accent, #6d8cff) 30%, transparent); }
  .av img { width: 100%; height: 100%; object-fit: cover; }
  .ph { font-size: 30px; }
  .cn-name { font-size: 13px; font-weight: 700; color: var(--text, #e0e4f0); text-align: center; line-height: 1.2; }
  .cn-role { font-size: 10.5px; color: var(--muted, #8a92b0); text-align: center; line-height: 1.2;
             max-width: 116px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
