from __future__ import annotations

from fastapi import Response
from fastapi.responses import JSONResponse


def register(app, ctx):
    from ..services import tts as _tts

    @app.get("/api/tts/status")
    def tts_status():
        """Whether local Kokoro TTS is available + the voice list (frontend falls back to Web Speech
        when unavailable)."""
        return {"available": _tts.available(), "voice": _tts.DEFAULT_VOICE, "voices": _tts.VOICES}

    @app.post("/api/tts")
    async def tts_synth(body: dict):
        """text → WAV (audio/wav). 503 when Kokoro isn't installed so the client can fall back."""
        text = ((body or {}).get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "no text"}, status_code=400)
        if not _tts.available():
            return JSONResponse({"error": "kokoro not installed", "hint": "pip install kokoro soundfile"},
                                status_code=503)
        from fastapi.concurrency import run_in_threadpool
        try:
            wav = await run_in_threadpool(_tts.synth_wav, text[:1200], (body or {}).get("voice"))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"tts failed: {exc}"}, status_code=500)
        return Response(content=wav, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})
