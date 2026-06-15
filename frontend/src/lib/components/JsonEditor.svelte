<script>
  // Raw workflow JSON editor — the escape hatch. Edits the whole ComfyUI graph.
  let { workflow, onsave } = $props();

  let text = $state('');
  let error = $state(null);

  // ComfyUI-friendly pretty printer: keeps short primitive arrays (the
  // ["6", 0] node links) and small flat objects (_meta titles) INLINE, while
  // expanding the node/inputs structure. Far more readable than indent-2.
  const isPrim = (v) => v === null || typeof v !== 'object';
  function inlineFlat(v) {
    return Array.isArray(v)
      ? '[' + v.map((x) => JSON.stringify(x)).join(', ') + ']'
      : '{' + Object.entries(v).map(([k, x]) => JSON.stringify(k) + ': ' + JSON.stringify(x)).join(', ') + '}';
  }
  function pretty(value, indent = 0, limit = 90) {
    if (isPrim(value)) return JSON.stringify(value);
    const vals = Array.isArray(value) ? value : Object.values(value);
    if (vals.every(isPrim)) {
      const s = inlineFlat(value);
      if (s.length <= limit) return s; // short, all-primitive -> one line
    }
    const pad = '  '.repeat(indent), padIn = '  '.repeat(indent + 1);
    if (Array.isArray(value)) {
      if (!value.length) return '[]';
      return '[\n' + value.map((v) => padIn + pretty(v, indent + 1, limit)).join(',\n') + '\n' + pad + ']';
    }
    const keys = Object.keys(value);
    if (!keys.length) return '{}';
    return '{\n' + keys.map((k) => padIn + JSON.stringify(k) + ': ' + pretty(value[k], indent + 1, limit)).join(',\n') + '\n' + pad + '}';
  }

  // (Re)load the text whenever the underlying workflow object changes (e.g. a
  // different model is selected). Typing doesn't mutate workflow, so edits survive.
  $effect(() => { text = pretty($state.snapshot(workflow)); });

  function format() {
    try { text = pretty(JSON.parse(text)); error = null; }
    catch (e) { error = String(e); }
  }
  function save() {
    try { const j = JSON.parse(text); error = null; onsave(j); }
    catch (e) { error = 'Invalid JSON — ' + e; }
  }
</script>

<textarea class="json" bind:value={text} spellcheck="false"></textarea>
{#if error}<div class="err" style="margin:8px 0">{error}</div>{/if}
<div class="row" style="margin-top:10px">
  <button onclick={save}>Save JSON</button>
  <button class="ghost" onclick={format}>Format</button>
</div>

<style>
  .json {
    width: 100%; height: calc(100vh - 320px); min-height: 280px; resize: vertical;
    font: 12.5px/1.5 ui-monospace, "Cascadia Code", Consolas, monospace;
    white-space: pre; tab-size: 2;
  }
</style>
