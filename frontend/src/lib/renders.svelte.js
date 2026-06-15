// In-memory store for generated image CANDIDATES (backgrounds, base images, sprites).
// Keyed by a context string so they survive navigation within the session — clicking
// away from a generation pipeline and back no longer loses the images you generated.
// Not persisted to disk (base64 blobs are large); cleared on reload.
export const renders = $state({});   // key -> { busy, cands: [dataURI], err }

const EMPTY = { busy: false, cands: [], err: false };
export function rget(key) { return renders[key] ?? EMPTY; }   // read-only (templates)
export function rensure(key) {                                 // mutating (handlers)
  if (!renders[key]) renders[key] = { busy: false, cands: [], err: false };
  return renders[key];
}
