from __future__ import annotations

import json
import re as _re
from pathlib import Path

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...comfy.server import get_server
from ..services import config_files
from ..services.jobs_util import _cancel_job


class LoraPromptsRequest(BaseModel):
    count: int = 60
    theme: str = "anime characters, diverse scenes, varied lighting"
    model: str | None = None       # text model for prompt generation


class LoraGenRequest(BaseModel):
    model: str                     # image model
    prompts: list[str]
    variations: int = 5


class LoraSaveRequest(BaseModel):
    name: str
    base_model: str | None = None
    images: list[dict]             # [{ src: dataURI, caption?: str }]


class PromptSetRequest(BaseModel):
    name: str
    prompts: list[str]


# Vision captioner config defaults — copied verbatim from create_app (shared
# with the trainer domain; not yet hoisted into a service).
CAPTIONER_DEFAULT = {
    "enabled": True,
    "model": "",  # an OpenRouter vision-capable model id
    "system": (
        "You are an expert anime image tagger writing captions to train a LoRA for an "
        "Illustrious-based SDXL model, which understands Danbooru tags. Caption the image "
        "as ONE line of lowercase, comma-separated booru tags. Use spaces inside multi-word "
        "tags (e.g. \"long hair\", \"looking at viewer\").\n\n"
        "Tag only what is clearly visible, roughly in this order:\n"
        "1. count/subject: 1girl, 1boy, 2girls, solo, multiple girls…\n"
        "2. character name as a booru tag ONLY if you clearly recognize them "
        "(e.g. \"hatsune miku\"); otherwise omit the name.\n"
        "3. appearance: hair colour, length and style (e.g. \"aqua hair\", \"long hair\", "
        "\"twintails\"), eye colour, notable body features.\n"
        "4. clothing and accessories (e.g. \"school uniform\", \"detached sleeves\", \"thighhighs\").\n"
        "5. expression, then pose/action (e.g. \"smile\", \"looking at viewer\", \"arms up\", \"sitting\").\n"
        "6. setting/background (e.g. \"classroom\", \"night\", \"cherry blossoms\", \"simple background\").\n"
        "7. framing/camera: portrait, upper body, cowboy shot, full body, and angle tags like "
        "\"from above\", \"from side\", \"dutch angle\" when clear.\n\n"
        "Critical for a STYLE LoRA: tag only the CONTENT. Do NOT tag the art style, shading, "
        "line art, colour palette, level of detail, medium, \"anime\", \"illustration\", or any "
        "quality words (masterpiece, best quality, highres…). Whatever you tag is treated as "
        "already-known and is NOT absorbed into the LoRA — leaving the style untagged is exactly "
        "how the LoRA learns it.\n\n"
        "Be specific and accurate to THIS image; never invent details you can't see. No artist "
        "names, no full sentences, no trailing period. Output only the tags on one line."
    ),
}

# Trainer config defaults — copied verbatim from create_app (the wd14 routes
# read load_trainer().get("python"); shared with the trainer domain).
TRAINER_DEFAULT = {"sd_scripts_dir": "", "python": "", "checkpoints_dir": "", "loras_dir": ""}


def register(app, ctx):
    @app.get("/api/lora/sets")
    def lora_sets() -> dict:
        d = ctx.sets_dir()
        names = sorted(p.stem for p in d.glob("*.json")) if d.is_dir() else []
        return {"sets": names}

    @app.get("/api/lora/sets/{name}")
    def lora_set(name: str):
        safe = _re.sub(r"[^\w\-]+", "_", name)
        path = ctx.sets_dir() / f"{safe}.json"
        if not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return json.loads(path.read_text(encoding="utf-8"))

    @app.post("/api/lora/sets")
    def save_lora_set(body: PromptSetRequest):
        safe = _re.sub(r"[^\w\-]+", "_", body.name).strip("_") or "set"
        d = ctx.sets_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{safe}.json").write_text(
            json.dumps({"name": safe, "prompts": body.prompts}, indent=2), encoding="utf-8")
        return {"ok": True, "name": safe}

    # -- LoRA dataset builder --------------------------------------------
    @app.post("/api/lora/prompts")
    def lora_prompts(body: LoraPromptsRequest):
        pg = config_files.load_promptgen(ctx.root)
        provider = ctx.text_provider_for(body.model or pg.get("model"))
        if provider is None:
            return JSONResponse({"error": "no text connection — set one up in Connection first"}, status_code=400)
        schema = {
            "type": "object",
            "properties": {"prompts": {"type": "array", "items": {"type": "string"}}},
            "required": ["prompts"], "additionalProperties": False,
        }
        instruction = (
            f"Produce exactly {body.count} distinct image prompts for a LoRA training set. "
            f"Each must depict a DIFFERENT subject in a DIFFERENT scene with DIFFERENT lighting. "
            f"Theme: {body.theme}. Each prompt is comma-separated Stable-Diffusion tags "
            f"(subject, appearance, setting, lighting, composition). Return them in `prompts`."
        )
        try:
            res = provider.generate_text(system=pg.get("system"), prompt=instruction, emits=schema)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        prompts = [p for p in (res.data.get("prompts") or []) if isinstance(p, str) and p.strip()]
        return {"prompts": prompts[: body.count]}

    # Random characters sampled straight from danbooru_character.csv — no LLM.
    # Bias toward characters with enough solo posts that the base model knows
    # them, then drop each into a varied scene/lighting/framing.
    _DAN_SCENES = [
        "concert stage", "bamboo forest at night", "manor garden", "snowy forest",
        "rocky wasteland", "fountain plaza", "ruined city wall", "ancient overgrown ruins",
        "ship deck", "dim library", "sunset clubroom", "golden wheat field",
        "misty battlefield meadow", "city street at dusk", "student council room",
        "flower field", "wisteria garden", "training grounds", "guild hall",
        "riverside town", "desert highway", "laboratory interior", "cozy kitchen",
        "empty classroom", "rooftop at night", "seaside cliff", "autumn shrine path",
        "neon alley", "cherry blossom courtyard", "mountain lake",
    ]
    _DAN_LIGHT = [
        "neon stage lighting", "moonlight", "soft morning light", "pale winter light",
        "dramatic glow", "bright daylight", "overcast light", "soft mystical light",
        "tropical sunlight", "lamplight", "warm window light", "golden hour",
        "dawn light", "blue hour", "candlelight", "dappled light", "torchlight",
        "clear daylight", "harsh sun", "cool lab light",
    ]
    _DAN_FRAME = ["upper body", "full body", "cowboy shot", "portrait", "dynamic pose"]

    @app.post("/api/lora/danbooru")
    def lora_danbooru(body: dict):
        import csv as _csv
        import random as _random

        count = max(1, min(int((body or {}).get("count") or 30), 200))
        min_solo = int((body or {}).get("min_solo") or 400)
        path = ctx.root / "danbooru_character.csv"
        if not path.is_file():
            return JSONResponse({"error": "danbooru_character.csv not found in project root"}, status_code=404)

        pool: list[str] = []
        with path.open(encoding="utf-8", newline="") as fh:
            for r_ in _csv.DictReader(fh):
                try:
                    if int(r_.get("solo_count") or 0) < min_solo:
                        continue
                except ValueError:
                    continue
                trig = (r_.get("trigger") or "").strip()
                if trig:
                    pool.append(trig)
        if not pool:
            return JSONResponse({"error": f"no characters with solo_count ≥ {min_solo}"}, status_code=400)

        def esc(t: str) -> str:
            return t.replace("(", "\\(").replace(")", "\\)")

        picks = _random.sample(pool, min(count, len(pool)))
        prompts = [
            f"{esc(t)}, solo, {_random.choice(_DAN_SCENES)}, "
            f"{_random.choice(_DAN_LIGHT)}, {_random.choice(_DAN_FRAME)}"
            for t in picks
        ]
        return {"prompts": prompts, "name": "random danbooru prompt"}

    @app.post("/api/lora/generate")
    async def lora_generate(body: LoraGenRequest):
        from fastapi.concurrency import run_in_threadpool

        from ...providers.comfyui_provider import ComfyUIProvider
        from ..jobs import LoraJob, current

        running = current()
        if running and running.status == "running":
            return JSONResponse(
                {"error": "a batch is already running — check or cancel it first",
                 "running": True}, status_code=409)

        md = ctx.base_settings.models.get(body.model)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image model '{body.model}'"}, status_code=404)
        opts = dict(md.options)
        conn = ctx.store.active("image")
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url
        try:
            provider = ComfyUIProvider(opts)
            await run_in_threadpool(get_server(provider.base_url).ensure_up)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        job = LoraJob(provider.base_url, provider, body.prompts, body.variations,
                      provider.output_node, provider.timeout_s)
        job.start()
        return {"ok": True, "id": job.id, "total": job.total}

    @app.get("/api/lora/stream")
    def lora_stream():
        """Replay + live SSE for the current/last batch. 204 if there's none."""
        from fastapi.responses import StreamingResponse

        from ..jobs import current

        job = current()
        if job is None:
            from starlette.responses import Response
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.get("/api/lora/job")
    def lora_job():
        """Lightweight status (no images) so the UI can tell on mount whether a
        batch is in flight and reattach to it."""
        from ..jobs import current

        job = current()
        return job.snapshot() if job else {"status": "idle"}

    @app.post("/api/lora/cancel")
    async def lora_cancel():
        from ..jobs import current

        return await _cancel_job(current())

    @app.post("/api/lora/save")
    def lora_save(body: LoraSaveRequest):
        import base64 as _b64
        import re

        name = re.sub(r"[^\w\-]+", "_", body.name or "loraset").strip("_") or "loraset"
        out = ctx.root / "datasets" / name
        out.mkdir(parents=True, exist_ok=True)
        saved = 0
        for item in body.images:
            src = item.get("src") or ""
            if not isinstance(src, str) or "," not in src:
                continue
            data = _b64.b64decode(src.split(",", 1)[1])
            (out / f"{saved:03d}.png").write_bytes(data)
            caption = item.get("caption")
            if caption:
                (out / f"{saved:03d}.txt").write_text(caption, encoding="utf-8")
            saved += 1
        (out / "loraset.json").write_text(
            json.dumps({"name": name, "count": saved, "base_model": body.base_model}, indent=2),
            encoding="utf-8",
        )
        return {"ok": True, "path": str(out), "count": saved}

    # -- dataset viewer + vision captioning ------------------------------
    def load_captioner() -> dict:
        path = ctx.root / "configs" / "captioner.json"
        cfg = dict(CAPTIONER_DEFAULT)
        if path.is_file():
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        return cfg

    @app.get("/api/captioner")
    def get_captioner() -> dict:
        return load_captioner()

    @app.post("/api/captioner")
    def set_captioner(body: dict):
        cfg = {**CAPTIONER_DEFAULT, **(body or {})}
        path = ctx.root / "configs" / "captioner.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    @app.get("/api/lora/datasets/{name}")
    def lora_dataset(name: str) -> dict:
        d = ctx.dataset_dir(name)
        if d is None:
            return JSONResponse({"error": f"dataset not found: {name}"}, status_code=404)
        images = []
        for png in sorted(d.glob("*.png")):
            txt = png.with_suffix(".txt")
            images.append({"file": png.name,
                           "caption": txt.read_text(encoding="utf-8") if txt.is_file() else ""})
        return {"name": d.name, "count": len(images), "images": images}

    @app.get("/api/lora/datasets/{name}/img/{file}")
    def lora_dataset_image(name: str, file: str):
        from starlette.responses import FileResponse, Response

        d = ctx.dataset_dir(name)
        if d is None or not _re.fullmatch(r"[\w\-]+\.png", file):
            return Response(status_code=404)
        path = (d / file).resolve()
        if d not in path.parents or not path.is_file():
            return Response(status_code=404)
        return FileResponse(path, media_type="image/png")

    class CaptionEdit(BaseModel):
        dataset: str
        file: str
        caption: str

    @app.post("/api/lora/caption/edit")
    def lora_caption_edit(body: CaptionEdit):
        d = ctx.dataset_dir(body.dataset)
        if d is None or not _re.fullmatch(r"[\w\-]+\.png", body.file):
            return JSONResponse({"error": "bad dataset/file"}, status_code=404)
        (d / body.file).with_suffix(".txt").write_text(body.caption or "", encoding="utf-8")
        return {"ok": True}

    @app.post("/api/lora/caption")
    async def lora_caption(body: dict):
        """Re-caption a dataset with a vision model (routed through OpenRouter).
        Runs as a resident job so it survives navigation and shows in Activity."""
        import base64 as _b64

        from fastapi.concurrency import run_in_threadpool

        from ..caption_job import CaptionJob, current as cap_current

        running = cap_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a captioning run is already going", "running": True},
                                status_code=409)

        d = ctx.dataset_dir((body or {}).get("dataset", ""))
        if d is None:
            return JSONResponse({"error": "dataset not found"}, status_code=404)
        cfg = load_captioner()
        model = (body or {}).get("model") or cfg.get("model")
        if not model:
            # Don't silently fall back to the chat model — it's usually text-only.
            return JSONResponse({"error": "pick a vision-capable model in the Caption model box first"},
                                status_code=400)
        provider = ctx.text_provider_for(model)
        if provider is None:
            return JSONResponse({"error": "no text connection — connect OpenRouter first"}, status_code=400)
        if not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "selected model can't caption"}, status_code=400)

        pngs = sorted(d.glob("*.png"))
        system = cfg.get("system")

        async def producer(job):
            total = len(pngs)
            for i, png in enumerate(pngs):
                if job.cancelling:
                    break
                try:
                    uri = "data:image/png;base64," + _b64.b64encode(png.read_bytes()).decode()
                    res = await run_in_threadpool(
                        lambda: provider.generate_text(
                            system=system, prompt="Caption this image.", images=[uri]))
                    caption = (res.text or "").strip().replace("\n", " ")
                    png.with_suffix(".txt").write_text(caption, encoding="utf-8")
                    yield {"type": "caption", "file": png.name, "caption": caption, "index": i, "total": total}
                except Exception as exc:  # noqa: BLE001
                    yield {"type": "error", "file": png.name, "index": i, "total": total, "error": str(exc)}

        job = CaptionJob(d.name, "vlm", len(pngs))
        job.start(producer)
        return {"ok": True, "id": job.id, "total": len(pngs)}

    # -- WD14 local tagger (booru tags, runs in the trainer venv) --------
    def load_trainer() -> dict:
        path = ctx.root / "configs" / "trainer.json"
        cfg = dict(TRAINER_DEFAULT)
        if path.is_file():
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        return cfg

    @app.get("/api/lora/wd14/status")
    def wd14_status() -> dict:
        import subprocess

        py = load_trainer().get("python")
        if not py or not Path(py).exists():
            return {"available": False, "reason": "trainer venv not set up — WD14 runs in it"}
        try:
            r = subprocess.run([py, "-c", "import onnxruntime"], capture_output=True, timeout=40)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc)}
        if r.returncode == 0:
            return {"available": True, "python": py}
        return {"available": False, "reason": "onnxruntime not installed in the trainer venv",
                "need_install": True}

    @app.post("/api/lora/wd14/install")
    async def wd14_install():
        from fastapi.responses import StreamingResponse

        py = load_trainer().get("python")
        if not py or not Path(py).exists():
            return JSONResponse({"error": "set up the trainer first — WD14 uses its venv"}, status_code=400)

        async def events():
            import asyncio
            from ..train_job import _utf8_env
            proc = await asyncio.create_subprocess_exec(
                py, "-m", "pip", "install", "onnxruntime", env=_utf8_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            assert proc.stdout
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                yield f"data: {json.dumps({'type': 'log', 'line': raw.decode('utf-8', 'replace').rstrip()})}\n\n"
            code = await proc.wait()
            yield f"data: {json.dumps({'type': 'done', 'code': code})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/lora/caption/wd14")
    async def lora_caption_wd14(body: dict):
        from ..caption_job import CaptionJob, current as cap_current

        running = cap_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a captioning run is already going", "running": True},
                                status_code=409)

        d = ctx.dataset_dir((body or {}).get("dataset", ""))
        if d is None:
            return JSONResponse({"error": "dataset not found"}, status_code=404)
        py = load_trainer().get("python")
        if not py or not Path(py).exists():
            return JSONResponse({"error": "trainer venv not set up — WD14 runs in it"}, status_code=400)

        script = ctx.root / "scripts" / "wd14_tag.py"
        repo = (body or {}).get("repo") or "SmilingWolf/wd-vit-tagger-v3"
        gt = float((body or {}).get("general_thresh") or 0.35)
        ct = float((body or {}).get("character_thresh") or 0.85)
        cmd = [py, str(script), "--dataset-dir", str(d), "--repo", repo,
               "--general-thresh", str(gt), "--character-thresh", str(ct)]
        total = len(sorted(d.glob("*.png")))

        async def producer(job):
            import asyncio
            from ..train_job import _utf8_env
            proc = await asyncio.create_subprocess_exec(
                *cmd, env=_utf8_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            job.proc = proc
            assert proc.stdout
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("{"):
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        ev = {"type": "log", "line": line}
                    if ev.get("type") != "done":  # CaptionJob emits the terminal event
                        yield ev
                else:
                    yield {"type": "log", "line": line}
                if job.cancelling:
                    break
            await proc.wait()

        job = CaptionJob(d.name, "wd14", total)
        job.start(producer)
        return {"ok": True, "id": job.id, "total": total}

    @app.get("/api/lora/caption/stream")
    def lora_caption_stream():
        from fastapi.responses import StreamingResponse
        from starlette.responses import Response

        from ..caption_job import current as cap_current

        job = cap_current()
        if job is None:
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.get("/api/lora/caption/job")
    def lora_caption_job() -> dict:
        from ..caption_job import current as cap_current

        job = cap_current()
        return job.snapshot() if job else {"status": "idle"}

    @app.post("/api/lora/caption/cancel")
    async def lora_caption_cancel():
        from ..caption_job import current as cap_current

        return await _cancel_job(cap_current())

    @app.get("/api/lora/datasets")
    def lora_datasets() -> dict:
        d = ctx.root / "datasets"
        out = []
        if d.is_dir():
            for sub in sorted(d.iterdir()):
                if not sub.is_dir():
                    continue
                imgs = sorted(sub.glob("*.png"))
                if not imgs:
                    continue
                caps = sum(1 for i in imgs if i.with_suffix(".txt").is_file())
                out.append({"name": sub.name, "count": len(imgs), "captions": caps})
        return {"datasets": out}

    @app.post("/api/lora/sample")
    async def lora_sample(body: dict):
        """Generate a sample by hooking a checkpoint + LoRA stack into a real
        image workflow (default: the illustrious graph) — its checkpoint and LoRA
        chain are swapped for the tested set, the prompt is injected at the
        workflow's configured positive node, and it's rendered from its output
        node. Same inject-models mechanism a pipeline uses to apply a character's
        portrait preset; the sample just drives it on demand."""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ...comfy.generate import stream_generate
        from ...comfy.stack import inject_models
        from ...providers.comfyui_provider import ComfyUIProvider

        body = body or {}
        checkpoint = body.get("checkpoint")
        loras = body.get("loras")
        if loras is None and body.get("lora"):  # single-lora shorthand
            loras = [{"name": body["lora"], "weight": float(body.get("weight", 1.0))}]
        loras = [l for l in (loras or []) if l.get("name")]
        model_key = body.get("model") or "illustrious"
        prompt = body.get("prompt") or "masterpiece, best quality, 1girl, portrait, detailed"
        negative = body.get("negative")

        md = ctx.base_settings.models.get(model_key)
        if md is None or md.kind != "image":
            return JSONResponse({"error": f"no image workflow '{model_key}'"}, status_code=404)
        opts = dict(md.options)
        conn = ctx.store.active("image")
        if conn and conn.base_url:
            opts["base_url"] = conn.base_url

        try:
            provider = ComfyUIProvider(opts)
            provider.workflow = inject_models(provider.workflow, checkpoint, loras)
            await run_in_threadpool(get_server(provider.base_url).ensure_up)
            graph = provider._inject(prompt, negative)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        async def events():
            try:
                async for ev in stream_generate(provider.base_url, graph, provider.output_node, provider.timeout_s):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")
