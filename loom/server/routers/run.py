from __future__ import annotations

import base64

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...config.schema import ModelDef
from ...engine import Runner
from ..services import config_files


class RunRequest(BaseModel):
    pipeline: str
    message: str
    character: str | None = None
    scenario: str | None = None  # the experience; resolves its primary cast character
    chat_model: str | None = None
    image_model: str | None = None
    use_reference: bool = False  # img2img: seed image steps from the character's reference image


def register(app, ctx):
    @app.post("/api/run")
    def run(body: RunRequest):
        settings = ctx.effective_settings()
        if body.pipeline not in settings.pipelines:
            return JSONResponse({"error": f"unknown pipeline '{body.pipeline}'"}, status_code=400)

        chat_model = body.chat_model or ctx.store.active_id("text")
        image_conn = ctx.store.active("image")
        image_model = ctx.role_model("chat", body.image_model)

        # If the active image connection points at a different ComfyUI (e.g. a
        # RunPod URL), override that image model's base_url for this run.
        if image_conn and image_conn.base_url and image_model in settings.models:
            md = settings.models[image_model].model_copy()
            md.options = {**md.options, "base_url": image_conn.base_url}
            settings.models[image_model] = md

        # Image-prompt generator: its own connection + system prompt. The model
        # comes from the dedicated image_prompt connection; it falls back to the
        # chat connection (then the chat model) when none is configured.
        promptgen = config_files.load_promptgen(ctx.root)
        ip_conn = ctx.store.active("image_prompt") or ctx.store.active("text")
        ip_model = (ip_conn.model if ip_conn else None) or promptgen.get("model") or chat_model
        promptgen = {**promptgen, "model": ip_model}

        # Register the image-prompt model id (run through its own connection's
        # credentials) if it isn't already a registered model key.
        if ip_model and ip_model not in settings.models and ip_conn:
            settings.models[ip_model] = ModelDef(
                provider=ip_conn.provider, kind="text",
                options={"model": ip_model, "api_key": ip_conn.api_key, "base_url": ip_conn.base_url},
            )

        # Resolve the focal character: explicit wins, else the scenario's primary
        # cast member — so the reference image / portraits track the right person.
        focal_char = body.character
        if focal_char is None and body.scenario:
            scen = settings.scenarios.get(body.scenario)
            if scen and scen.cast:
                primary = next((m for m in scen.cast if m.primary), scen.cast[0])
                focal_char = primary.character

        # img2img: seed image steps from the character's reference image. Only the
        # img2img workflow (one with a LoadImage node) uses it; others ignore it.
        init_image = None
        if body.use_reference and focal_char:
            ref = ctx.reference_path(focal_char)
            if ref is not None:
                init_image = ref.read_bytes()

        try:
            result = Runner(settings).run(
                body.pipeline, user_message=body.message, character=body.character,
                scenario=body.scenario,
                chat_model=chat_model, image_model=image_model, promptgen=promptgen,
                chat_system=config_files.load_chatgen(ctx.root).get("system"), init_image=init_image,
            )
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        images = [f"data:image/png;base64,{base64.b64encode(b).decode()}" for b in result.images]
        return {"reply": result.text, "images": images}
