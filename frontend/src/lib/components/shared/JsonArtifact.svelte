<script>
  // A generic, editable JSON artifact view — the fallback canvas for FlowConsole when an
  // artifact isn't node-shaped (locations, cast, outfits, any document). Pretty-prints the
  // value, lets the user edit it as JSON, and emits parsed changes; re-syncs from the value
  // when it changes externally (e.g. a function applied an op) and the user isn't mid-edit.
  let { value = {}, onChange = null, label = 'Document' } = $props();

  const isPrim = (v) => v === null || typeof v !== 'object';
  function inlineFlat(v) {
    return Array.isArray(v)
      ? '[' + v.map((x) => JSON.stringify(x)).join(', ') + ']'
      : '{' + Object.entries(v).map(([k, x]) => JSON.stringify(k) + ': ' + JSON.stringify(x)).join(', ') + '}';
  }
  function pretty(v, indent = 0, limit = 80) {
    if (isPrim(v)) return JSON.stringify(v);
    const vals = Array.isArray(v) ? v : Object.values(v);
    if (vals.every(isPrim)) { const s = inlineFlat(v); if (s.length <= limit) return s; }
    const pad = '  '.repeat(indent), padIn = '  '.repeat(indent + 1);
    if (Array.isArray(v)) return v.length ? '[\n' + v.map((x) => padIn + pretty(x, indent + 1, limit)).join(',\n') + '\n' + pad + ']' : '[]';
    const keys = Object.keys(v);
    return keys.length ? '{\n' + keys.map((k) => padIn + JSON.stringify(k) + ': ' + pretty(v[k], indent + 1, limit)).join(',\n') + '\n' + pad + '}' : '{}';
  }

  let text = $state('');
  let err = $state(null);
  let editing = $state(false);
  let timer = null;

  // Re-sync from the external value unless the user is actively editing.
  $effect(() => {
    const snap = $state.snapshot(value);
    if (!editing) text = pretty(snap ?? {});
  });

  function onInput() {
    editing = true;
    clearTimeout(timer);
    timer = setTimeout(() => {
      try { const j = JSON.parse(text); err = null; onChange?.(j); editing = false; }
      catch (e) { err = String(e).replace(/^SyntaxError:\s*/, ''); }
    }, 600);
  }
  function format() { try { text = pretty(JSON.parse(text)); err = null; } catch (e) { err = String(e); } }
</script>

<div class="ja">
  <div class="jahead"><span class="jalbl">{label}</span>
    {#if err}<span class="jaerr">⚠ {err}</span>{:else}<span class="jaok">JSON</span>{/if}
    <button class="jafmt" onclick={format} title="Reformat">⤓ format</button>
  </div>
  <textarea class="jatext" bind:value={text} oninput={onInput} onblur={() => (editing = false)} spellcheck="false"></textarea>
</div>

<style>
  .ja { display: flex; flex-direction: column; height: 100%; min-height: 280px; }
  .jahead { display: flex; align-items: center; gap: 8px; padding: 6px 8px; font-size: 11px; }
  .jalbl { font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .jaok { color: rgba(100,210,130,.8); font-family: ui-monospace, monospace; }
  .jaerr { color: var(--bad); }
  .jafmt { margin-left: auto; font-size: 11px; padding: 3px 8px; border-radius: 6px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .jafmt:hover { color: var(--text); }
  .jatext { flex: 1; width: 100%; resize: none; padding: 8px 10px; border: 0; background: var(--bg); color: var(--text);
    font: 12.5px/1.5 ui-monospace, "Cascadia Code", Consolas, monospace; white-space: pre; tab-size: 2; }
  .jatext:focus { outline: none; }
</style>
