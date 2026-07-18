// Voice input for the agentic surfaces — a thin wrapper over the browser Web Speech API behind a
// tiny interface so a local Whisper recognizer can swap in later without touching callers.
// `voice` is reactive: components read voice.state / voice.partial to render the bubble live.
import { stories } from '$lib/stories.svelte.js';
import { chars } from '$lib/characters.svelte.js';
import { isStoryHostDesktop } from '$lib/story-host-client.ts';

// The packaged desktop app is deliberately serverless.  Never probe the
// legacy FastAPI voice endpoints from that build: browser speech remains the
// local fallback until voice services are explicitly moved behind Story Host.
const legacyVoiceApiAvailable = () => import.meta.env.VITE_LEAN_STORY !== '1' && !isStoryHostDesktop();

const SR = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition);
const SS = typeof window !== 'undefined' && window.speechSynthesis;
const CAN_RECORD = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;

// The active story's cast NAMES — fed to Whisper as a bias prompt so it stops mangling them.
function sttHints() {
  const st = stories.current;
  if (!st?.cast?.length) return '';
  const names = st.cast.map((m) => chars.list?.find((c) => c.key === m.character)?.name).filter(Boolean);
  return names.join(', ').slice(0, 300);
}

export const voice = $state({
  supported: !!SR,
  state: 'idle',      // 'idle' | 'listening' | 'thinking' | 'acting'
  partial: '',        // live interim transcript while listening
  ttsSupported: !!SS,
  tts: true,          // speak the agent's confirmations aloud
  handsFree: false,   // VAD loop: auto-listen after each turn (endpointing does the VAD)
  voices: [],         // Kokoro voice ids (empty until /api/tts/status resolves)
  voiceId: '',        // chosen Kokoro voice; '' = backend default (bm_george, the "Jarvis" voice)
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
  if (!legacyVoiceApiAvailable()) {
    _kokoro = false;
    return false;
  }
  try {
    const j = await (await fetch('/api/tts/status')).json();
    _kokoro = !!j.available;
    if (_kokoro) { voice.voices = j.voices || []; if (!voice.voiceId) voice.voiceId = j.voice || ''; }
  } catch { _kokoro = false; }
  if (_kokoro) voice.ttsSupported = true;   // backend TTS works even without browser Web Speech
  return _kokoro;
}
if (typeof window !== 'undefined' && legacyVoiceApiAvailable()) kokoroReady();   // probe early so the toggle reflects it

export async function speak(text) {
  if (!voice.tts || !text) return;
  stopSpeaking();
  if (await kokoroReady()) {
    try {
      const r = await fetch('/api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                          body: JSON.stringify({ text: String(text), voice: voice.voiceId || undefined }) });
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

// ── Local Whisper STT (backend /api/stt) — preferred when installed; else browser Web Speech ──
// Web Speech gives live interim text but is Chromium-only + cloud; Whisper is local + works anywhere,
// at the cost of record-then-transcribe (no live partial). We endpoint with an energy VAD below so
// it auto-stops on silence, matching the hands-free loop.
let _whisper = null;          // null=unknown, true/false (cached)
async function whisperReady() {
  if (_whisper !== null) return _whisper;
  if (!legacyVoiceApiAvailable()) {
    _whisper = false;
    return false;
  }
  try { const r = await fetch('/api/stt/status'); _whisper = !!(await r.json()).available; }
  catch { _whisper = false; }
  if (_whisper && CAN_RECORD) voice.supported = true;
  return _whisper;
}
if (typeof window !== 'undefined' && legacyVoiceApiAvailable()) whisperReady();   // probe early so the mic reflects it

let _rec = null, _stream = null, _actx = null, _vadRAF = 0, _silenceT = 0;

function startWhisper(onFinal) {
  if (!CAN_RECORD) { voice.state = 'idle'; voice.supported = !!SR; return false; }
  navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
    _stream = stream;
    const chunks = [];
    const rec = new MediaRecorder(stream);
    _rec = rec;
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = async () => {
      stopVad();
      try { _stream?.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
      _stream = null; _rec = null;
      const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
      let text = '';
      if (blob.size) {
        try {
          const r = await fetch('/api/stt?hints=' + encodeURIComponent(sttHints()),
            { method: 'POST', headers: { 'Content-Type': blob.type }, body: blob });
          if (r.ok) text = ((await r.json()).text || '').trim();
        } catch { /* noop */ }
      }
      voice.state = 'idle';          // MUST be idle before onFinal (submit() ignores 'thinking')
      if (text) onFinal(text);
    };
    rec.start();
    voice.state = 'listening'; voice.partial = '';
    startVad(stream);
  }).catch(() => { voice.state = 'idle'; });
  return true;
}

// Energy-based endpointing: once speech is heard, auto-stop ~1.2s after it tapers to silence.
function startVad(stream) {
  try {
    _actx = new (window.AudioContext || window.webkitAudioContext)();
    const src = _actx.createMediaStreamSource(stream);
    const an = _actx.createAnalyser(); an.fftSize = 512;
    src.connect(an);
    const buf = new Uint8Array(an.fftSize);
    let spoke = false;
    const SIL_MS = 1200, SPEECH_RMS = 0.015;
    const tick = () => {
      an.getByteTimeDomainData(buf);
      let sum = 0; for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / buf.length);
      if (rms > SPEECH_RMS) {
        spoke = true; stopSpeaking();                       // barge-in
        if (_silenceT) { clearTimeout(_silenceT); _silenceT = 0; }
      } else if (spoke && !_silenceT) {
        _silenceT = setTimeout(stopListening, SIL_MS);
      }
      _vadRAF = requestAnimationFrame(tick);
    };
    _vadRAF = requestAnimationFrame(tick);
  } catch { /* no VAD → user taps the mic to stop */ }
}
function stopVad() {
  if (_vadRAF) { cancelAnimationFrame(_vadRAF); _vadRAF = 0; }
  if (_silenceT) { clearTimeout(_silenceT); _silenceT = 0; }
  try { _actx?.close(); } catch { /* noop */ } _actx = null;
}

let rec = null;

// Start listening; calls onFinal(text) once with the final transcript, then returns to idle.
// Returns false if speech recognition isn't available (caller shows the text-input fallback).
export function startListening(onFinal) {
  stopSpeaking();                       // barge-in: talking cuts off the agent's voice
  if (_whisper) return startWhisper(onFinal);              // local Whisper path (preferred)
  if (!SR) { voice.supported = false; return false; }
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
  if (_rec && _rec.state !== 'inactive') { try { _rec.stop(); } catch { /* noop */ } return; }  // whisper: onstop transcribes
  try { rec?.stop(); } catch { /* already stopped */ }
  voice.state = 'idle';
}
