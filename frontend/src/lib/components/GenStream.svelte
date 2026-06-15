<script>
  import { onDestroy } from 'svelte';

  // ONE reusable live view for any streaming generation job. Point it at a jobId returned by a
  // generator endpoint (regenerate-cast, plan-wardrobe, …); it consumes /api/jobs/<id>/stream
  // (the shared jobhub SSE) and shows phases, the live token text, and per-item results.
  // onResult(data) fires on the final `result` event; onError(msg) fires on an `error` event;
  // onDone() fires when the job finishes.
  let { jobId = null, title = 'Generating', onResult = null, onError = null, onDone = null } = $props();

  let lines = $state([]);   // [{kind:'phase'|'delta'|'item'|'error', text, name?}]
  let live = $state(true);  // false once the job is done
  let es = null;
  let box;

  function scroll() { queueMicrotask(() => { if (box) box.scrollTop = box.scrollHeight; }); }

  $effect(() => {
    const id = jobId;
    lines = []; live = true;
    if (!id) return;
    es = new EventSource('/api/jobs/' + id + '/stream');
    es.onmessage = (e) => {
      let ev; try { ev = JSON.parse(e.data); } catch { return; }
      if (ev.type === 'phase') { lines = [...lines, { kind: 'phase', text: ev.label }]; scroll(); }
      else if (ev.type === 'delta') {
        const last = lines[lines.length - 1];
        if (last && last.kind === 'delta') { last.text += ev.text; lines = [...lines]; }
        else lines = [...lines, { kind: 'delta', text: ev.text }];
        scroll();
      }
      else if (ev.type === 'item') { lines = [...lines, { kind: 'item', name: ev.name, text: ev.text }]; scroll(); }
      else if (ev.type === 'error') { lines = [...lines, { kind: 'error', text: ev.error }]; onError?.(ev.error); scroll(); }
      else if (ev.type === 'result') { onResult?.(ev.result); }
      else if (ev.type === 'done') { close(); live = false; onDone?.(); }
    };
    es.onerror = () => { /* the stream closes on completion; the `done` event drives onDone */ };
    return () => close();
  });

  function close() { if (es) { es.close(); es = null; } }
  onDestroy(close);
</script>

<div class="gs">
  <div class="gshead">
    {#if live}<span class="spin" aria-hidden="true"></span>{:else}<span class="tick">✓</span>{/if}
    <span class="ttl">{title}{live ? '…' : ' — done'}</span>
  </div>
  <div class="gsbox" bind:this={box}>
    {#each lines as l, i (i)}
      {#if l.kind === 'phase'}<div class="ph">▸ {l.text}</div>
      {:else if l.kind === 'item'}<div class="it"><b>{l.name}</b>{#if l.text} — <span class="lo">{l.text}</span>{/if}</div>
      {:else if l.kind === 'error'}<div class="er">⚠ {l.text}</div>
      {:else}<pre class="dl">{l.text}</pre>{/if}
    {/each}
  </div>
</div>

<style>
  .gs { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--panel); margin: 4px 0 12px; }
  .gshead { display: flex; align-items: center; gap: 9px; padding: 8px 12px; font-size: 13px; font-weight: 600;
            color: var(--text); background: rgba(109,140,255,.10); border-bottom: 1px solid var(--border-soft); }
  .ttl { color: var(--text); }
  .tick { color: var(--good, #8fc7a0); }
  .gsbox { max-height: 320px; overflow: auto; padding: 8px 12px; font-size: 12px; line-height: 1.5; }
  .ph { color: var(--accent); font-weight: 600; margin: 8px 0 2px; }
  .it { color: var(--text); padding: 2px 0; }
  .it .lo { color: var(--muted); }
  .er { color: var(--bad, #ff7a7a); }
  .dl { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, monospace;
        font-size: 11.5px; color: var(--muted); }
  .spin { width: 13px; height: 13px; flex: none; border-radius: 50%; border: 2px solid rgba(109,140,255,.35);
          border-top-color: var(--accent); animation: gsspin .7s linear infinite; }
  @keyframes gsspin { to { transform: rotate(360deg); } }
</style>
