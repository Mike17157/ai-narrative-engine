"""Local neural TTS via Kokoro (kokoro-82M). Lazily imported so the server runs fine without it —
`available()` is false until `pip install kokoro soundfile` (+ the model downloads on first use),
and the /api/tts route returns 503 so the frontend falls back to browser Web Speech.

Kokoro yields 24 kHz mono float audio per chunk; we concatenate and return a WAV.
"""
from __future__ import annotations

import importlib.util
import io
import threading

DEFAULT_VOICE = "bm_george"         # deep, measured British male — the "Jarvis" default
# A curated subset of Kokoro's voices (a=American, b=British; f=female, m=male).
VOICES = [
    "bm_george", "bm_lewis", "bf_emma", "bf_isabella",
    "am_michael", "am_adam", "am_echo", "am_liam",
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
]

_pipelines: dict[str, object] = {}   # lang_code -> KPipeline; British voices need 'b' G2P, American 'a'
_lock = threading.Lock()
_available: bool | None = None


def available() -> bool:
    """True if Kokoro + soundfile are importable (cached). Does not load the model."""
    global _available
    if _available is None:
        _available = bool(importlib.util.find_spec("kokoro")) and bool(importlib.util.find_spec("soundfile"))
    return _available


def _bind_espeak() -> None:
    """Point phonemizer at the bundled espeak-ng (no system install needed). misaki uses espeak as the
    G2P fallback for out-of-dictionary words — character names etc. — without it those words crash."""
    try:
        import espeakng_loader as _L
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(_L.get_library_path())
        EspeakWrapper.set_data_path(_L.get_data_path())
    except Exception:  # noqa: BLE001 — dictionary words still work; only OOV fallback is lost
        pass


def _get_pipeline(voice: str):
    """The KPipeline for this voice's language, cached per lang. British voices (bf_/bm_) phonemize
    with lang_code='b'; using the American pipeline on them mangles the accent."""
    lang = "b" if voice.startswith(("bf_", "bm_")) else "a"
    pipe = _pipelines.get(lang)
    if pipe is None:
        with _lock:
            pipe = _pipelines.get(lang)
            if pipe is None:
                _bind_espeak()
                from kokoro import KPipeline
                _pipelines[lang] = pipe = KPipeline(lang_code=lang)
    return pipe


def synth_wav(text: str, voice: str | None = None) -> bytes:
    """Render `text` → WAV bytes (24 kHz mono). Raises if Kokoro isn't installed."""
    import numpy as np
    import soundfile as sf

    v = voice or DEFAULT_VOICE
    pipe = _get_pipeline(v)
    chunks = [audio for _gs, _ps, audio in pipe(text, voice=v)]
    if not chunks:
        return b""
    wav = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, wav, 24000, format="WAV")
    return buf.getvalue()
