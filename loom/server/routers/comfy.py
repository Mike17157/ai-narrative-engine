"""ComfyUI lifecycle endpoint (`/api/comfy/up`).

The model/LoRA library management endpoints that used to live here (catalog,
librarian, families, triage/grid renders, file upload) served the deleted
Images/Training sections and are gone. Image *generation* for story portraits
and cast sprites does not pass through HTTP here — it uses the provider /
service layer directly (`loom.comfy.server`, `loom.comfy.stack`, …). All that
remains is the "launch ComfyUI" button used by Settings → System and the
Activity menu.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

from ...comfy.server import get_server


def register(app, ctx):
    @app.post("/api/comfy/up")
    def comfy_up():
        server = get_server(ctx.comfy_url)
        try:
            server.ensure_up()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
        return {"ok": True, "up": server.is_up(), "launched": server.we_launched_it}
