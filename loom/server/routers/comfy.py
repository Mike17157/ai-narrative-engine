from __future__ import annotations

import json
import os
import re

import yaml
from fastapi import File, UploadFile
from fastapi.responses import JSONResponse

from ...comfy.server import get_server
from ..services import config_files


def register(app, ctx):
    # -- trainer config loader (create_app-local in app.py; copied verbatim) ---
    TRAINER_DEFAULT = {"sd_scripts_dir": "", "python": "", "checkpoints_dir": "", "loras_dir": ""}

    def load_trainer() -> dict:
        path = ctx.root / "configs" / "trainer.json"
        cfg = dict(TRAINER_DEFAULT)
        if path.is_file():
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        return cfg

    @app.get("/api/comfy/models")
    def comfy_models():
        """Signature-classified index of the entire ComfyUI model tree: every
        file tagged with kind (checkpoint/diffusion/vae/clip/lora/…) and arch
        (sdxl/sd15/flux/dit/…) read from its tensor header, not its folder."""
        from ...comfy.scan import scan_models
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found", "items": []}, status_code=404)
        return scan_models(models_dir)

    @app.post("/api/comfy/models/resolve")
    def model_resolve(body: dict):
        """Is a model with this filename already installed for this kind? If so,
        return its folder-relative name (so we reference it instead of uploading)."""
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return {"found": False}
        base = os.path.basename(((body or {}).get("filename") or "").replace("\\", "/"))
        if not base:
            return {"found": False}
        for folder in config_files._model_kind_folders((body or {}).get("kind", "")):
            d = md / folder
            if d.is_dir():
                for p in d.rglob(base):
                    if p.is_file():
                        # native separator so it matches ComfyUI's combo options
                        return {"found": True, "rel": str(p.relative_to(d))}
        return {"found": False}

    @app.post("/api/comfy/models/upload")
    async def model_upload(kind: str, file: UploadFile = File(...)):
        """Stream an uploaded model file into the correct folder for its kind."""
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        folders = config_files._model_kind_folders(kind)
        if not folders:
            return JSONResponse({"error": f"unknown model kind '{kind}'"}, status_code=400)
        base = os.path.basename((file.filename or "").replace("\\", "/"))
        if not base or base.startswith("."):
            return JSONResponse({"error": "bad filename"}, status_code=400)
        folder, rel = folders[0], base
        if kind == "ultralytics":  # ComfyUI splits detectors into bbox/ and segm/
            sub = "segm" if "seg" in base.lower() else "bbox"
            folder, rel = f"ultralytics/{sub}", os.path.join(sub, base)  # native sep
        target = md / folder / base
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            with open(tmp, "wb") as out:
                while chunk := await file.read(1 << 20):
                    out.write(chunk)
            tmp.replace(target)
        except Exception as exc:  # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            return JSONResponse({"error": str(exc)}, status_code=500)
        return {"ok": True, "rel": rel, "name": base}

    @app.get("/api/comfy/catalog")
    def comfy_catalog():
        """Browse ComfyUI Manager's cached model catalog (URLs + install folders),
        each tagged with whether it's already installed."""
        from ...comfy.catalog import read_catalog
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not bd or not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI base not found", "entries": []}, status_code=404)
        return read_catalog(bd, md)

    @app.post("/api/comfy/catalog/install")
    async def comfy_catalog_install(body: dict):
        """Download a catalog model into its correct folder, streaming progress."""
        from fastapi.responses import StreamingResponse

        import httpx

        from ...comfy.catalog import install_target
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "models dir not found"}, status_code=404)
        url, rel = (body or {}).get("url"), (body or {}).get("rel")
        if not url or not rel:
            return JSONResponse({"error": "url and rel required"}, status_code=400)
        target = install_target(md, rel)
        if target is None:
            return JSONResponse({"error": "path escapes models dir"}, status_code=400)

        async def events():
            tmp = target.with_suffix(target.suffix + ".part")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                    async with client.stream("GET", url) as r:
                        if r.status_code != 200:
                            yield f"data: {json.dumps({'type': 'error', 'error': f'HTTP {r.status_code} from source'})}\n\n"
                            return
                        total = int(r.headers.get("content-length", 0))
                        done, last = 0, 0
                        with open(tmp, "wb") as f:
                            async for chunk in r.aiter_bytes(1 << 20):
                                f.write(chunk)
                                done += len(chunk)
                                if done - last >= (5 << 20):
                                    last = done
                                    yield f"data: {json.dumps({'type': 'progress', 'done': done, 'total': total})}\n\n"
                tmp.replace(target)
                yield f"data: {json.dumps({'type': 'done', 'rel': rel})}\n\n"
            except Exception as exc:  # noqa: BLE001
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/comfy/librarian")
    def librarian_plan():
        """Proposed moves to file misfiled / loose models into arch-correct
        folders (surgical — leaves arch-consistent folders alone)."""
        from ...comfy.librarian import plan_moves
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found", "moves": []}, status_code=404)
        # LoRA classifications drive the folder layout (<family>/<classification>/).
        classifications = {l.name.replace("\\", "/"): l.type for l in ctx.base_settings.loras.library}
        return plan_moves(md, classifications)

    @app.post("/api/comfy/librarian/apply")
    def librarian_apply(body: dict):
        """Execute the chosen moves and rewrite references in Loom configs
        (loras.yaml, character cards, workflows/), then reload settings."""
        from ...comfy.librarian import apply_moves
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        res = apply_moves(md, ctx.root, (body or {}).get("moves") or [])
        if res.get("moved"):
            ctx.reload_settings()  # pick up rewritten lora/checkpoint names
        return res

    @app.get("/api/comfy/choices")
    def comfy_choices():
        """Available files/options from ComfyUI (for the LoRA/checkpoint/sampler
        pickers). Empty lists if ComfyUI is unreachable."""
        import httpx

        out = {"checkpoints": [], "loras": [], "vaes": [], "upscale_models": [],
               "samplers": [], "schedulers": []}
        try:
            r = httpx.get(ctx.active_comfy_url().rstrip("/") + "/object_info", timeout=15)
            r.raise_for_status()
            info = r.json()
        except Exception:  # noqa: BLE001
            return out

        def opts(cls: str, key: str) -> list:
            req = info.get(cls, {}).get("input", {}).get("required", {})
            v = req.get(key)
            return v[0] if isinstance(v, list) and v and isinstance(v[0], list) else []

        out["checkpoints"] = opts("CheckpointLoaderSimple", "ckpt_name")
        out["loras"] = opts("LoraLoader", "lora_name")
        out["vaes"] = opts("VAELoader", "vae_name")
        out["upscale_models"] = opts("UpscaleModelLoader", "model_name")
        out["samplers"] = opts("KSampler", "sampler_name")
        out["schedulers"] = opts("KSampler", "scheduler")
        return out

    @app.get("/api/comfy/object_info")
    def comfy_object_info():
        """Slim node schema from ComfyUI — input names/types (with the multiline
        flag) and output names — so the graph editor can label slots and size
        widgets. Empty dict if ComfyUI is unreachable."""
        import httpx

        try:
            r = httpx.get(ctx.active_comfy_url().rstrip("/") + "/object_info", timeout=20)
            r.raise_for_status()
            info = r.json()
        except Exception:  # noqa: BLE001
            return {}

        slim = {}
        for cls, spec in info.items():
            inp = spec.get("input", {}) or {}
            inputs = []
            for req_flag, group in (("required", inp.get("required", {}) or {}), ("optional", inp.get("optional", {}) or {})):
                for name, val in group.items():
                    t, multiline, widget, default, options = "COMBO", False, True, None, None
                    if isinstance(val, list) and val:
                        spec0 = val[0]
                        o = val[1] if len(val) > 1 and isinstance(val[1], dict) else {}
                        if isinstance(spec0, list):  # COMBO — a list of choices
                            t = "COMBO"
                            options = [x for x in spec0 if isinstance(x, (str, int, float, bool))][:2000]
                            default = o.get("default", options[0] if options else None)
                        elif isinstance(spec0, str):
                            t = spec0
                            if spec0 in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                                multiline = bool(o.get("multiline"))
                                default = o.get("default", "" if spec0 == "STRING" else (False if spec0 == "BOOLEAN" else 0))
                            else:
                                widget = False  # a connection slot, not a widget
                    inputs.append({"name": name, "type": t, "widget": widget, "multiline": multiline,
                                   "default": default, "options": options, "required": req_flag == "required"})
            out_types = spec.get("output", []) or []
            out_names = spec.get("output_name", []) or []
            outputs = []
            for i, ot in enumerate(out_types):
                nm = out_names[i] if i < len(out_names) and out_names[i] else (ot if isinstance(ot, str) else f"out{i}")
                outputs.append({"name": nm, "type": ot if isinstance(ot, str) else "COMBO"})
            slim[cls] = {"inputs": inputs, "outputs": outputs, "category": spec.get("category", "")}
        return slim

    # -- LoRA subsystem (typed library + named, routable stacks) -------------
    @app.get("/api/loras")
    def get_loras():
        """The whole LoRA subsystem config: { library, stacks }."""
        return ctx.base_settings.loras.model_dump()

    @app.post("/api/loras")
    def save_loras(body: dict):
        """Persist configs/loras.yaml (library + stacks), then reload."""
        try:
            from ...config.schema import LoraConfig
            cfg = LoraConfig(**(body or {}))
            path = ctx.root / "configs" / "loras.yaml"
            path.write_text(yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"could not save: {exc}"}, status_code=400)
        ctx.reload_settings()
        return {"ok": True}

    @app.post("/api/loras/resolve")
    async def resolve_lora_stack(body: dict):
        """Resolve a stack (by `stack` name, or inline def) against `text` +
        optional `theme`. State LoRAs auto-route by matching the scene to their
        own embedded tags; detail/theme are constant. Returns the effective
        checkpoint + LoRA list plus the per-candidate routing scores."""
        from fastapi.concurrency import run_in_threadpool

        from ...comfy import tags as tagmod
        from ...comfy.stack import resolve_stack
        body = body or {}
        library = [l.model_dump() for l in ctx.base_settings.loras.library]
        stack = body.get("stack")
        if isinstance(stack, str):
            sd = next((s for s in ctx.base_settings.loras.stacks if s.name == stack), None)
            stack = sd.model_dump() if sd else {}
        elif not isinstance(stack, dict):
            stack = {}

        members = stack.get("loras", [])
        identity = [m for m in members if m.get("role") == "identity"]
        state_cands = [m for m in members if m.get("role") == "state"]

        loras_dir = str(config_files._loras_out_dir(load_trainer(), ctx.comfy_base_dir(), ctx.root))
        thr = float(body.get("threshold", 0.25))
        try:
            routed = await run_in_threadpool(
                tagmod.route_state, ctx.root, loras_dir, body.get("text", ""), state_cands, thr)
        except Exception as exc:  # noqa: BLE001 — embedding/index failure shouldn't 500 the bench
            routed = [{"name": s.get("name"), "weight": s.get("weight", 0.7), "fired": False,
                       "score": 0.0, "matched": [], "why": f"router error: {exc}"} for s in state_cands]

        active = [r for r in routed if r.get("fired")]
        res = resolve_stack({"checkpoint": stack.get("checkpoint"), "identity": identity},
                            library, body.get("theme"), active)
        res["routed"] = routed
        return res

    @app.get("/api/loras/bases")
    def lora_bases():
        """Each image workflow with its base model (checkpoint or UNet) and that
        model's real architecture, classified from its tensor signature via the
        scan. Triage picks one of these; LoRAs are filtered to the matching arch."""
        from ...comfy.scan import classify
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        out = []
        for key, md in ctx.base_settings.models.items():
            if md.kind != "image":
                continue
            base_name, folder = None, None
            wf = md.options.get("workflow")
            try:
                p = ctx.root / wf if wf else None
                if p and p.is_file():
                    g = json.loads(p.read_text(encoding="utf-8"))
                    for node in g.values():
                        ct = node.get("class_type")
                        if ct == "CheckpointLoaderSimple":
                            base_name, folder = node.get("inputs", {}).get("ckpt_name"), "checkpoints"
                            break
                        if ct in ("UNETLoader", "UnetLoaderGGUF"):
                            base_name, folder = node.get("inputs", {}).get("unet_name"), "diffusion_models"
                            break
            except Exception:  # noqa: BLE001
                pass
            arch = "unknown"
            if base_name and models_dir and folder:
                fp = models_dir / folder / base_name.replace("\\", "/")
                if fp.is_file():
                    arch = classify(str(fp)).get("arch") or "unknown"
            out.append({"key": key, "base": base_name,
                        # only a real checkpoint can drive the minimal triage render
                        "checkpoint": base_name if folder == "checkpoints" else None,
                        "arch": arch})
        return out

    @app.get("/api/loras/clusters")
    async def lora_clusters():
        """Installed LoRAs as a similarity-ordered list (variants folded, nearest
        neighbours per entry), for the Network pane."""
        from fastapi.concurrency import run_in_threadpool

        from ...comfy import tags as tagmod
        loras_dir = str(config_files._loras_out_dir(load_trainer(), ctx.comfy_base_dir(), ctx.root))
        try:
            return await run_in_threadpool(tagmod.similarity_list, ctx.root, loras_dir)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"entries": [], "error": str(exc)}, status_code=500)

    @app.post("/api/loras/tagify")
    def tagify_scene(body: dict):
        """Natural-language scene → comma-separated booru tags via the Prompt Gen
        LLM. This is the prose→tags step; state routing then matches these tags
        against each LoRA's own tags. (The LLM does the understanding; the router
        does the matching.)"""
        pg = config_files.load_promptgen(ctx.root)
        provider = ctx.text_provider_for((body or {}).get("model") or pg.get("model"))
        if provider is None:
            return JSONResponse({"error": "no text connection — set one up in Connection first"}, status_code=400)
        scene = (body or {}).get("scene", "").strip()
        if not scene:
            return JSONResponse({"error": "scene is empty"}, status_code=400)
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
            "required": ["tags"], "additionalProperties": False,
        }
        instruction = (
            "Convert this scene into Danbooru / Stable-Diffusion tags: subject count, "
            "appearance, hair, clothing/outfit, setting, lighting, pose, expression. Use "
            f"canonical lowercase danbooru spellings. Scene: {scene}. Return the `tags` array."
        )
        try:
            res = provider.generate_text(system=pg.get("system"), prompt=instruction, emits=schema)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        # Flatten (models often cram comma-lists into single array items), strip
        # decoration, drop non-latin / symbol-only noise, de-dupe.
        tags, seen = [], set()
        for item in (res.data.get("tags") or []):
            if not isinstance(item, str):
                continue
            for piece in re.split(r"[,\n;]", item):
                t = piece.strip().strip("=-_ ").lower()
                if not t or len(t) > 60 or not re.search(r"[a-z0-9]", t):
                    continue
                if t not in seen:
                    seen.add(t)
                    tags.append(t)
        return {"tags": tags, "text": ", ".join(tags)}

    @app.post("/api/loras/triage-render")
    async def triage_render(body: dict):
        """Render ONE LoRA through a minimal txt2img graph with an explicit
        checkpoint — for classification triage. The caller passes a checkpoint
        matching the LoRA's architecture (its folder); a minimal graph avoids the
        heavy production workflow's custom-node fragility."""
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        from ...comfy.generate import stream_generate

        body = body or {}
        ckpt, lora = body.get("checkpoint"), body.get("lora")
        if not ckpt or not lora:
            return JSONResponse({"error": "checkpoint and lora are required"}, status_code=400)
        prompt = body.get("prompt") or "1girl, solo, standing, simple background, looking at viewer"
        neg = body.get("negative") or "lowres, worst quality, bad anatomy, text, watermark"
        w = float(body.get("weight", 0.8))
        steps = int(body.get("steps", 22))
        base = ctx.active_comfy_url()
        graph = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "10": {"class_type": "LoraLoader", "inputs": {"lora_name": lora, "strength_model": w, "strength_clip": w, "model": ["4", 0], "clip": ["4", 1]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["10", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["10", 1]}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
            "3": {"class_type": "KSampler", "inputs": {"seed": int(body.get("seed", 0)), "steps": steps, "cfg": 6.0, "sampler_name": "euler_ancestral", "scheduler": "normal", "denoise": 1.0, "model": ["10", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "triage", "images": ["8", 0]}},
        }
        try:
            await run_in_threadpool(get_server(base).ensure_up)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        async def events():
            try:
                async for ev in stream_generate(base, graph, "9", 240):
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.delete("/api/loras/file")
    def delete_lora_file(name: str):
        """Delete a LoRA file from the loras directory (for pruning redundant /
        overlapping LoRAs). Path-checked to stay inside the loras dir."""
        try:
            loras_dir = config_files._loras_out_dir(load_trainer(), ctx.comfy_base_dir(), ctx.root).resolve()
            target = (loras_dir / name.replace("\\", "/")).resolve()
            if target != loras_dir and loras_dir not in target.parents:
                return JSONResponse({"error": "path outside loras dir"}, status_code=400)
            if not target.is_file():
                return JSONResponse({"error": "not found"}, status_code=404)
            target.unlink()
            return {"ok": True, "deleted": name}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/comfy/embeddings")
    def comfy_embeddings():
        """List textual-inversion embeddings (for the text-encode node's picker).
        Scans the ComfyUI models/embeddings folder directly — ComfyUI's own
        /embeddings route is hijacked to an HTML page by the lora-manager node."""
        base = ctx.comfy_base_dir()
        if base is None:
            return []
        d = base / "models" / "embeddings"
        if not d.is_dir():
            return []
        exts = {".safetensors", ".pt", ".bin", ".ckpt"}
        return sorted(p.stem for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts)

    @app.post("/api/comfy/interrupt")
    async def comfy_interrupt():
        """Stop ComfyUI's current execution (used to cancel a test render)."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(ctx.active_comfy_url().rstrip("/") + "/interrupt")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/comfy/up")
    def comfy_up():
        server = get_server(ctx.comfy_url)
        try:
            server.ensure_up()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
        return {"ok": True, "up": server.is_up(), "launched": server.we_launched_it}
