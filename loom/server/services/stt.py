"""Local speech-to-text via faster-whisper. Lazily imported so the server runs fine without it —
`available()` is false until `pip install faster-whisper`, and /api/stt returns 503 so the frontend
falls back to the browser Web Speech recognizer.

CPU int8 by default: `base.en` transcribes a few-second utterance in well under a second and avoids
the CUDA/cuDNN build matrix (the Blackwell card needs a matched ctranslate2 — not worth it for STT).
faster-whisper decodes the uploaded blob (webm/opus from MediaRecorder) via its bundled PyAV.
"""
from __future__ import annotations

import importlib.util
import io
import threading

MODEL_SIZE = "base.en"      # english-only; good speed/accuracy on CPU
_model = None
_lock = threading.Lock()
_available: bool | None = None


def available() -> bool:
    """True if faster-whisper is importable (cached). Does not load the model."""
    global _available
    if _available is None:
        _available = bool(importlib.util.find_spec("faster_whisper"))
    return _available


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def transcribe(data: bytes, language: str = "en") -> str:
    """Audio blob → text. `vad_filter` trims silence (Silero, via onnxruntime — already present).
    Raises if faster-whisper isn't installed."""
    model = _get_model()
    segments, _info = model.transcribe(io.BytesIO(data), language=language or None, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()
