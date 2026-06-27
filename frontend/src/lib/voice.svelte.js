// Voice input for the agentic surfaces — a thin wrapper over the browser Web Speech API behind a
// tiny interface so a local Whisper recognizer can swap in later without touching callers.
// `voice` is reactive: components read voice.state / voice.partial to render the bubble live.
const SR = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition);
const SS = typeof window !== 'undefined' && window.speechSynthesis;

export const voice = $state({
  supported: !!SR,
  state: 'idle',      // 'idle' | 'listening' | 'thinking' | 'acting'
  partial: '',        // live interim transcript while listening
  ttsSupported: !!SS,
  tts: true,          // speak the agent's confirmations aloud
  handsFree: false,   // VAD loop: auto-listen after each turn (endpointing does the VAD)
});

// ── TTS voice selection ──────────────────────────────────────────────────────
// The OS default voice is robotic; pick a natural-sounding English one. getVoices() is often empty
// until `voiceschanged` fires, so we re-read lazily and cache the chosen one.
let _voices = [];
let _picked = null;
function loadVoices() { try { _voices = SS ? SS.getVoices() : []; } catch { _voices = []; } _picked = null; }
if (SS) { loadVoices(); try { SS.addEventListener('voiceschanged', loadVoices); } catch { /* noop */ } }
function pickVoice() {
  if (_picked) return _picked;
  if (!_voices.length) loadVoices();
  const en = _voices.filter((v) => /^en(-|$)/i.test(v.lang));
  const pool = en.length ? en : _voices;
  // Prefer modern "natural/neural/online" voices, then Google's, then known-good named ones, then
  // the cleaner classic female (Zira/Samantha) over the robotic default (David). Last: any en.
  const PREF = [
    /natural|neural|online/i,
    /google (us|uk)?\s?english|google.*english/i,
    /aria|jenny|ava|emma|libby|sonia|natasha|ryan|guy/i,
    /samantha|serena|karen|moira|alex|fiona/i,
    /zira/i,
    /google/i,
  ];
  for (const re of PREF) { const m = pool.find((v) => re.test(v.name)); if (m) { _picked = m; return m; } }
  _picked = pool.find((v) => /en-US/i.test(v.lang)) || pool[0] || null;
  return _picked;
}

// Speak the agent's response. Prefers LOCAL Kokoro (nicer neural voice, served by the backend at
// /api/tts) and falls back to browser Web Speech when Kokoro isn't installed.
let _audio = null;            // current Kokoro <audio>
let _kokoro = null;           // null=unknown, true/false (cached)
async function kokoroReady() {
  if (_kokoro !== null) return _kokoro;
  try { const r = await fetch('/api/tts/status'); _kokoro = !!(await r.json()).available; }
  catch { _kokoro = false; }
  if (_kokoro) voice.ttsSupported = true;   // backend TTS works even without browser Web Speech
  return _kokoro;
}
if (typeof window !== 'undefined') kokoroReady();   // probe early so the toggle reflects it

export async function speak(text) {
  if (!voice.tts || !text) return;
  stopSpeaking();
  if (await kokoroReady()) {
    try {
      const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                          body: JSON.stringify({ text: String(text) }) });
      if (r.ok) {
        const url = URL.createObjectURL(await r.blob());
        _audio = new Audio(url);
        _audio.onended = _audio.onerror = () => { try { URL.revokeObjectURL(url); } catch { /* noop */ } };
        await _audio.play();
        return;
      }
    } catch { /* fall through to Web Speech */ }
  }
  if (!SS) return;
  try {
    SS.cancel();
    const u = new SpeechSynthesisUtterance(String(text));
    const v = pickVoice();
    if (v) u.voice = v;
    u.rate = 1.02; u.pitch = 1.02;
    SS.speak(u);
  } catch { /* noop */ }
}
export function stopSpeaking() {
  try { SS && SS.cancel(); } catch { /* noop */ }
  if (_audio) { try { _audio.pause(); } catch { /* noop */ } _audio = null; }
}
export function isSpeaking() {
  if (_audio && !_audio.paused && !_audio.ended) return true;
  try { return !!(SS && (SS.speaking || SS.pending)); } catch { return false; }
}
export function toggleTts() {
  voice.tts = !voice.tts;
  if (!voice.tts) stopSpeaking();
}
export function toggleHandsFree() { voice.handsFree = !voice.handsFree; }

let rec = null;

// Start listening; calls onFinal(text) once with the final transcript, then returns to idle.
// Returns false if speech recognition isn't available (caller shows the text-input fallback).
export function startListening(onFinal) {
  if (!SR) { voice.supported = false; return false; }
  stopSpeaking();                       // barge-in: talking cuts off the agent's voice
  rec = new SR();
  rec.lang = 'en-US';
  rec.interimResults = true;
  rec.continuous = false;
  // VAD barge-in: the instant the recognizer detects speech, kill any lingering TTS.
  rec.onspeechstart = () => stopSpeaking();
  rec.onresult = (e) => {
    const r = e.results[e.results.length - 1];
    voice.partial = r[0].transcript;
    if (r.isFinal) {
      const text = r[0].transcript.trim();
      voice.state = 'idle';
      if (text) onFinal(text);
    }
  };
  rec.onerror = () => { voice.state = 'idle'; };
  rec.onend = () => { if (voice.state === 'listening') voice.state = 'idle'; };
  voice.state = 'listening';
  voice.partial = '';
  try { rec.start(); } catch { voice.state = 'idle'; return false; }
  return true;
}

export function stopListening() {
  try { rec?.stop(); } catch { /* already stopped */ }
  voice.state = 'idle';
}
