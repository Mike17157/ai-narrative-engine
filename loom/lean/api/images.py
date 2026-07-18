"""Small HTTP boundary for Story-scoped Krea2 image rendering."""

from __future__ import annotations

from fastapi.responses import FileResponse, JSONResponse

from .. import images as capability


def register(app, ctx) -> None:
    @app.get("/api/lean/images/status")
    def image_status():
        enabled = bool(getattr(app.state, "lean_comfy_enabled", True))
        return {
            "enabled": enabled,
            "models": capability.available_krea_models(ctx),
            "roles": ["scene", "base", "sprite", "chat"],
            "message": (
                "Krea2/Comfy rendering is disabled for this lean app. Set LOOM_LEAN_COMFY=1 to enable it."
                if not enabled else "Krea2/Comfy renders start only when requested."
            ),
        }

    @app.get("/api/stories/{key}/images")
    def story_images(key: str):
        if key not in ctx.base_settings.stories:
            return JSONResponse({"error": "no such story"}, status_code=404)
        return {"images": capability.list_story_images(ctx.root, key)}

    @app.get("/api/stories/{key}/images/{image_name}")
    def story_image_file(key: str, image_name: str):
        if key not in ctx.base_settings.stories:
            return JSONResponse({"error": "no such story"}, status_code=404)
        path = capability.story_image_path(ctx.root, key, image_name)
        if path is None:
            return JSONResponse({"error": "no such story image"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/stories/{key}/images/render")
    async def render_story_image(key: str, body: dict | None = None):
        if key not in ctx.base_settings.stories:
            return JSONResponse({"error": "no such story"}, status_code=404)
        if not bool(getattr(app.state, "lean_comfy_enabled", True)):
            return JSONResponse({
                "error": "Krea2/Comfy rendering is disabled for this lean app",
                "retryable": False,
            }, status_code=503)
        body = body or {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "story image request must be an object"}, status_code=400)
        try:
            image = await capability.render_story_image(
                ctx,
                story_key=key,
                prompt=body.get("prompt") or "",
                role=body.get("role") or "scene",
                model=body.get("model"),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not render story image: {exc}", "retryable": True}, status_code=502)
        return {"ok": True, "image": image}
