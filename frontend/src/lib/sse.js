// Shared streaming helper for the app's fetch-SSE transport.
//
// Several endpoints answer a POST with a response *body* that is itself an SSE
// stream (request-scoped jobs: workshop, spine, storyboard, chapter regen,
// workflow test, lora batch, …). Every event is framed as `data: <json>\n\n`
// where the JSON is an object carrying a `type` field.
//
// This was hand-rolled in ~10 places; consumeSse() is the single implementation.
// Malformed frames are skipped (partial JSON never reaches us — frames are split
// on the \n\n boundary so each chunk is whole). Resolves when the stream ends.
//
// Note: this does NOT inspect res.ok — callers that surface HTTP errors should
// check res.ok (and read res.json()) before calling, as they did before.

/**
 * @param {Response} res   a fetch Response whose body is an SSE stream
 * @param {(ev: any) => void} onEvent  called with each parsed event object
 */
export async function consumeSse(res, onEvent) {
  if (!res?.body) return;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const dataLine = chunk.split('\n').find((l) => l.startsWith('data:'));
      if (!dataLine) continue;
      let ev;
      try { ev = JSON.parse(dataLine.slice(5).trim()); } catch { continue; }
      onEvent(ev);
    }
  }
}

// Subscribe to the shared jobhub stream for a background job (`/api/jobs/<id>/stream`,
// an EventSource of JSON events). `onEvent(ev)` fires per event; `onDone()` fires once
// when the job ends naturally (a `done` event) or the connection errors. Returns a handle:
//   .cancel()  — stop listening AND tell the server to cancel the job (use on user-cancel
//                or component unmount). Does NOT fire onDone.
// This replaces the per-component _attachJobStream/_closeJob boilerplate in the lora cards.
export function jobStream(jobId, onEvent, onDone) {
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  let ended = false;
  const end = (fireDone) => {
    if (ended) return;
    ended = true;
    es.close();
    if (fireDone) onDone?.();
  };
  es.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    onEvent?.(ev);
    if (ev.type === 'done') end(true);
  };
  es.onerror = () => end(true);
  return {
    jobId,
    cancel() {
      if (!ended) { ended = true; es.close(); }
      fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' }).catch(() => {});
    },
  };
}
