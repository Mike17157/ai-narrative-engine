from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


def register(app, ctx):
    from ..services import stt as _stt

    @app.get("/api/stt/status")
    def stt_status():
        """Whether local Whisper STT is available (frontend falls back to Web Speech when not)."""
        return {"available": _stt.available(), "model": _stt.MODEL_SIZE}

    @app.post("/api/stt")
    async def stt_transcribe(request: Request):
        """Raw audio blob (request body) → {text}. 503 when faster-whisper isn't installed so the
        client can fall back. Body is the MediaRecorder Blob posted directly — no multipart."""
        if not _stt.available():
            return JSONResponse({"error": "faster-whisper not installed", "hint": "pip install faster-whisper"},
                                status_code=503)
        data = await request.body()
        if not data:
            return JSONResponse({"error": "no audio"}, status_code=400)
        from fastapi.concurrency import run_in_threadpool
        try:
            text = await run_in_threadpool(_stt.transcribe, data)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"stt failed: {exc}"}, status_code=500)
        return {"text": text}
