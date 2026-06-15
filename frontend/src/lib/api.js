// Thin fetch wrappers over the FastAPI JSON API. Paths are relative ("/health"),
// prefixed with /api here, so the Vite dev proxy (and prod same-origin) just work.

export async function get(path) {
  const r = await fetch('/api' + path);
  return r.json();
}

export async function post(path, body, opts = {}) {
  let r;
  try {
    r = await fetch('/api' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: opts.signal
    });
  } catch (e) {
    // Aborted (job cancelled) or network drop — surface as a non-ok result, never throw.
    return { ok: false, status: 0, aborted: e?.name === 'AbortError', data: null };
  }
  let data = null;
  try { data = await r.json(); } catch { /* no body */ }
  return { ok: r.ok, status: r.status, data };
}

export async function put(path, body) {
  const r = await fetch('/api' + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let data = null;
  try { data = await r.json(); } catch { /* no body */ }
  return { ok: r.ok, status: r.status, data };
}

export async function del(path) {
  const r = await fetch('/api' + path, { method: 'DELETE' });
  return r.json();
}
