from __future__ import annotations

import time

from ...comfy.server import get_server

_SERVER_STARTED = time.time()  # for uptime in Settings → Server


def register(app, ctx):
    @app.get("/api/health")
    def health() -> dict:
        server = get_server(ctx.comfy_url)
        text_conn = ctx.store.active("text")
        image_conn = ctx.store.active("image")
        ip_conn = ctx.store.active("image_prompt") or text_conn
        return {
            "comfyui": {"base_url": server.base_url, "up": server.is_up(), "managed": server.managed},
            "characters": list(ctx.base_settings.characters),
            "pipelines": list(ctx.base_settings.pipelines),
            "profile": ctx.user.profile,
            "defaults": ctx.user.defaults,
            "active": ctx.store.active_map,
            "active_chat_model": (text_conn.model if text_conn else None),
            "active_image_model": (image_conn.model if image_conn else ctx.user.defaults.get("image_model")),
            "promptgen_model": (ip_conn.model if ip_conn else None),
        }

    @app.get("/api/server/info")
    def server_info() -> dict:
        import os
        import sys

        return {"uptime_s": round(time.time() - _SERVER_STARTED), "python": sys.version.split()[0], "pid": os.getpid()}

    @app.post("/api/server/restart")
    async def server_restart() -> dict:
        """Re-exec the server process in place (picks up code/config changes).
        Interrupts running jobs; managed ComfyUI keeps running. The response is
        sent first, then the process replaces itself."""
        import asyncio
        import os
        import sys

        async def _restart():
            await asyncio.sleep(0.4)  # let this response flush
            os.execv(sys.executable, [sys.executable, "-m", "loom.cli", *sys.argv[1:]])

        asyncio.create_task(_restart())
        return {"ok": True, "restarting": True}
