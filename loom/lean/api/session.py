"""The one deterministic play session owned by each lean Story."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def _sid(key: str) -> str:
    return f"play-{key}"


def register(app, ctx) -> None:
    @app.get("/api/stories/{key}/session")
    def get_story_session(key: str):
        from ...server.services.story_sessions import load_session

        if key not in ctx.base_settings.stories:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return load_session(ctx.root, _sid(key)) or {}

    @app.put("/api/stories/{key}/session")
    def put_story_session(key: str, body: dict | None = None):
        from ...server.services.story_sessions import load_session, save_session

        if key not in ctx.base_settings.stories:
            return JSONResponse({"error": "no such story"}, status_code=404)
        body = body or {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "session update must be an object"}, status_code=400)
        # The old generic session route replaced the document wholesale. Merge
        # this small UI update so setting lorebooks cannot erase play state.
        saved = save_session(ctx.root, _sid(key), {**(load_session(ctx.root, _sid(key)) or {}), **body})
        return {"ok": True, "session": saved}

    @app.delete("/api/stories/{key}/session")
    def delete_story_session(key: str):
        from ...server.services.story_sessions import delete_session

        if key not in ctx.base_settings.stories:
            return JSONResponse({"error": "no such story"}, status_code=404)
        delete_session(ctx.root, _sid(key))
        return {"ok": True}
