"""Local neural TTS via Kokoro (kokoro-82M). Lazily imported so the server runs fine without it —
`available()` is false until `pip install kokoro soundfile` (+ the model downloads on first use),
and the /api/tts route returns 503 so the frontend falls back to browser Web Speech.

Kokoro yields 24 kHz mono float audio per chunk; we concatenate and return a WAV.
"""
from __future__ import annotations

import importlib.util
import io
import threading

DEFAULT_VOICE = "af_heart"          # warm female American English
# A curated subset of Kokoro's voices (a=American, b=British; f=female, m=male).
VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
    "am_michael", "am_adam", "am_echo", "am_liam",
    "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
]

_pipeline = None
_lock = threading.Lock()
_available: bool | None = None


def available() -> bool:
    """True if Kokoro + soundfile are importable (cached). Does not load the model."""
    global _available
    if _available is None:
        _available = bool(importlib.util.find_spec("kokoro")) and bool(importlib.util.find_spec("soundfile"))
    return _available


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _lock:
            if _pipeline is None:
                from kokoro import KPipeline
                _pipeline = KPipeline(lang_code="a")     # 'a' = American English
    return _pipeline


def synth_wav(text: str, voice: str | None = None) -> bytes:
    """Render `text` → WAV bytes (24 kHz mono). Raises if Kokoro isn't installed."""
    import numpy as np
    import soundfile as sf

    pipe = _get_pipeline()
    chunks = [audio for _gs, _ps, audio in pipe(text, voice=(voice or DEFAULT_VOICE))]
    if not chunks:
        return b""
    wav = np.concatenate(chunks)
    buf = io.BytesIO()
    sf.write(buf, wav, 24000, format="WAV")
    return buf.getvalue()
