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
        return {
            "comfyui": {"base_url": server.base_url, "up": server.is_up(), "managed": server.managed},
            "characters": list(ctx.base_settings.characters),
            "pipelines": list(ctx.base_settings.pipelines),
            "profile": ctx.user.profile,
            "defaults": ctx.user.defaults,
            "active": ctx.store.active_map,
            "active_chat_model": (text_conn.model if text_conn else None),
            "active_image_model": (image_conn.model if image_conn else ctx.user.defaults.get("image_model")),
        }

    @app.get("/api/server/info")
    def server_info() -> dict:
        import os
        import sys

        return {"uptime_s": round(time.time() - _SERVER_STARTED), "python": sys.version.split()[0], "pid": os.getpid()}

    @app.post("/api/server/restart")
    async def server_restart() -> dict:
        """Restart the backend (picks up code/config changes). Interrupts running
        jobs; managed ComfyUI keeps running. The response is sent first, then the
        process restarts.

        Under `uvicorn --reload` (dev — the default) we run inside a reload *worker*
        subprocess: execv there re-execs garbage argv and can't rebind the port the
        parent supervisor still holds, so it hangs. Instead touch a watched source
        file and let the reloader do the clean restart it's built for. Only the
        non-reload (prod) path re-execs in place."""
        import asyncio
        import os
        import sys
        from pathlib import Path

        reloading = bool(os.environ.get("LOOM_ROOT"))  # set only on the --reload path

        async def _restart():
            await asyncio.sleep(0.4)  # let this response flush
            if reloading:
                Path(__file__).touch()  # bump mtime → uvicorn reloads the worker
            else:
                os.execv(sys.executable, [sys.executable, "-m", "loom.cli", *sys.argv[1:]])

        asyncio.create_task(_restart())
        return {"ok": True, "restarting": True}
