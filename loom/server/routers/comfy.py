from __future__ import annotations

import json
import os
import re

import yaml
from fastapi import File, Form, UploadFile
from fastapi.responses import JSONResponse

from ...comfy.server import get_server
from ..safe_downloads import DOWNLOAD_TIMEOUT, DownloadError, get_public_response, max_model_download_bytes, validate_content_length
from ..services import config_files


def register(app, ctx):
    def load_trainer() -> dict:
        return config_files.load_trainer(ctx.root)

    @app.get("/api/comfy/models")
    def comfy_models():
        """Signature-classified index of the entire ComfyUI model tree: every
        file tagged with kind (checkpoint/diffusion/vae/clip/lora/…), arch
        (sdxl/sd15/flux/dit/…) read from its tensor header, and a sub-family
        (illustrious/pony/anima/…) layering manual overrides on the folder/arch guess."""
        from ...comfy.scan import cached_scan
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found", "items": []}, status_code=404)
        res = cached_scan(models_dir)
        # apply manual family overrides (configs/families.json) keyed by folder-relative name
        ov = config_files.load_families(ctx.root)
        if ov:
            by_family: dict[str, dict] = {}
            for it in res.get("items", []):
                if it.get("family") and ov.get(it["rel"]):
                    it["family"] = ov[it["rel"]]
                if it.get("family"):
                    fam = by_family.setdefault(it["family"], {"base": 0, "lora": 0})
                    fam["lora" if it["kind"] == "lora" else "base"] += 1
            res["by_family"] = by_family
        return res

    @app.delete("/api/comfy/models/file")
    def delete_model_file(folder: str, rel: str):
        """Delete one model file by its scan `folder` (checkpoints/loras/vae/…) + `rel`
        path. Path-checked to stay inside that folder; invalidates the scan cache."""
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        try:
            base = (md / folder.replace("\\", "/")).resolve()
            target = (base / rel.replace("\\", "/")).resolve()
            if target == base or base not in target.parents:
                return JSONResponse({"error": "path outside model folder"}, status_code=400)
            if not target.is_file():
                return JSONResponse({"error": "not found"}, status_code=404)
            target.unlink()
            from ...comfy.scan import invalidate_scan_cache
            invalidate_scan_cache()
            return {"ok": True, "deleted": f"{folder}/{rel}"}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

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

    @app.post("/api/comfy/models/smart-upload")
    async def smart_upload(file: UploadFile = File(...), expect: str = Form(None)):
        """Upload a .safetensors file, classify it from tensor headers, place it in
        the correct kind folder under the detected family subfolder (e.g. loras/Anima/).
        Returns {ok, kind, arch, family, rel}.

        When ``expect`` is given ('lora' or 'checkpoint') the classified kind must match
        — this backs the separate, type-specific import buttons so a checkpoint dropped
        into the LoRA importer (or vice-versa) is rejected instead of silently misfiled.
        A bundled diffusion model counts as a 'checkpoint' for this check."""
        import shutil, tempfile
        from ...comfy.scan import classify
        from ...comfy.family import family_of, FOLDER
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        base = os.path.basename((file.filename or "").replace("\\", "/"))
        if not base or base.startswith("."):
            return JSONResponse({"error": "bad filename"}, status_code=400)
        # Stream to temp file first so we can classify before committing a location.
        suffix = os.path.splitext(base)[1]
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name
                while chunk := await file.read(1 << 20):
                    tmp.write(chunk)
            info = classify(tmp_path)
            kind = info.get("kind", "unknown")
            arch = info.get("arch", "") or ""
            base_folder = {"lora": "loras", "checkpoint": "checkpoints",
                           "diffusion": "diffusion_models"}.get(kind)
            if not base_folder:
                return JSONResponse({"error": f"unrecognised model kind '{kind}' — expected lora or checkpoint"}, status_code=400)
            # Type-specific importers: reject a file that classifies as the other kind.
            if expect:
                # The checkpoints axis covers both bundled checkpoints and split diffusion models.
                got_group = "checkpoint" if kind in ("checkpoint", "diffusion") else kind
                if got_group != expect:
                    return JSONResponse(
                        {"error": f"this is a {kind}, not a {expect} — use the {got_group} importer"},
                        status_code=400)
            family = family_of(base, arch=arch)
            family_dir = FOLDER.get(family)
            if family_dir:
                # Prefer an already-existing same-family subfolder (case-insensitive) so
                # new uploads land next to existing models rather than creating a second
                # folder with different casing (e.g. anima/ vs Anima/).
                base_type_dir = md / base_folder
                if base_type_dir.is_dir():
                    for existing in base_type_dir.iterdir():
                        if existing.is_dir() and existing.name.lower() == family_dir.lower():
                            family_dir = existing.name
                            break
                rel = f"{family_dir}/{base}"
                target = md / base_folder / family_dir / base
            else:
                rel = base
                target = md / base_folder / base
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(tmp_path, target)
            tmp_path = None
            from ...comfy.scan import invalidate_scan_cache
            invalidate_scan_cache()

            # Auto-push to RunPod network volume if creds are configured.
            runpod_job_id = None
            try:
                from ...runpod.volume import VolumeConfig
                from ..routers.runpod import VolumeUploadJob
                vcfg = VolumeConfig()
                if vcfg.configured:
                    models_rel = f"{base_folder}/{rel}"
                    job = VolumeUploadJob([(models_rel, target)], vcfg)
                    job.start()
                    runpod_job_id = job.id
            except Exception:  # noqa: BLE001
                pass

            return {"ok": True, "kind": kind, "arch": arch, "family": family, "rel": rel, "name": base,
                    "runpod_job_id": runpod_job_id}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:  # noqa: BLE001
                    pass

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
                max_bytes = max_model_download_bytes()
                async with httpx.AsyncClient(follow_redirects=False, timeout=DOWNLOAD_TIMEOUT) as client:
                    r = await get_public_response(client, str(url))
                    try:
                        if r.status_code != 200:
                            yield f"data: {json.dumps({'type': 'error', 'error': f'HTTP {r.status_code} from source'})}\n\n"
                            return
                        validate_content_length(r, max_bytes)
                        total = int(r.headers.get("content-length", 0))
                        done, last = 0, 0
                        with open(tmp, "wb") as f:
                            async for chunk in r.aiter_bytes(1 << 20):
                                done += len(chunk)
                                if done > max_bytes:
                                    raise DownloadError(f"download exceeds the {max_bytes} byte limit")
                                f.write(chunk)
                                if done - last >= (5 << 20):
                                    last = done
                                    yield f"data: {json.dumps({'type': 'progress', 'done': done, 'total': total})}\n\n"
                    finally:
                        await r.aclose()
                tmp.replace(target)
                from ...comfy.scan import invalidate_scan_cache
                invalidate_scan_cache()
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

    @app.get("/api/comfy/librarian/family")
    def librarian_plan_family():
        """Proposed moves to file every model into its FAMILY folder (Illustrious/, Pony/, Anima/,
        SDXL/, …) derived from its name/folder/arch + manual overrides — the "Categorize by name"
        action. Reviewed then run via /librarian/apply (same move + reference-rewrite path)."""
        from ...comfy.librarian import plan_family_moves
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found", "moves": []}, status_code=404)
        return plan_family_moves(md, config_files.load_families(ctx.root))

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
            from ...comfy.scan import invalidate_scan_cache
            invalidate_scan_cache()
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
            slim[cls] = {"inputs": inputs, "outputs": outputs, "category": spec.get("category", ""),
                         "description": (spec.get("description") or "").strip()}
        return slim

    # -- LoRA subsystem (typed library + named, routable stacks) -------------
    @app.get("/api/loras")
    def get_loras():
        """The whole LoRA subsystem config: { library, stacks }."""
        return ctx.base_settings.loras.model_dump()

    @app.get("/api/loras/metadata")
    async def lora_metadata(name: str):
        """Civitai metadata for one LoRA (by rel path or bare name): model name, description,
        trigger words, and the source page URL. Resolved via configs/civitai_loras.json, else by
        file hash. Cached server-side. `name` is the scan rel or LoraManager bare name."""
        from fastapi.concurrency import run_in_threadpool
        from ...comfy.civitai import lora_metadata as _meta, local_trigger_words
        bd = ctx.comfy_base_dir()
        loras_dir = (bd / "models" / "loras") if bd else None
        res = await run_in_threadpool(_meta, ctx.root, name, loras_dir)
        # Civitai is the primary trigger source; fall back to the file's own metadata so
        # local/private LoRAs still auto-suggest a trigger word.
        if not res.get("trained_words"):
            tw = await run_in_threadpool(local_trigger_words, loras_dir, name)
            if tw:
                res["trained_words"] = tw
        return res

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
        """Each image workflow with its base model (checkpoint or UNet), that model's real
        architecture (from its tensor signature), and the resolved sub-family
        (illustrious/pony/anima/…). Triage picks one of these; LoRAs are filtered to the matching
        family (soft — same-arch cross-family stays available behind a toggle)."""
        from ...comfy.scan import classify
        from ...comfy.family import family_of
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        ov = config_files.load_families(ctx.root)
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
            # family: override by workflow key first, else by the base file's name/arch
            family = family_of(base_name, arch=arch, override=ov.get(key) or (ov.get(base_name) if base_name else None))
            out.append({"key": key, "base": base_name,
                        # only a real checkpoint can drive the minimal triage render
                        "checkpoint": base_name if folder == "checkpoints" else None,
                        "arch": arch, "family": family})
        return out

    @app.get("/api/families")
    def get_families():
        """The family registry (containers) + current manual overrides. Used to group models/LoRAs
        and drive soft compatibility filtering in the UI."""
        from ...comfy.family import FAMILIES
        return {"families": [{"id": f["id"], "label": f["label"], "arch": f["arch"]} for f in FAMILIES],
                "overrides": config_files.load_families(ctx.root)}

    @app.post("/api/families")
    def set_family(body: dict):
        """Set or clear one manual family override. Body: {name, family}. An empty/falsey `family`
        removes the override (back to folder/arch detection)."""
        from ...comfy.family import FAMILY_IDS
        body = body or {}
        name = (body.get("name") or "").strip()
        fam = (body.get("family") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        if fam and fam not in FAMILY_IDS:
            return JSONResponse({"error": f"unknown family '{fam}'"}, status_code=400)
        ov = config_files.load_families(ctx.root)
        if fam:
            ov[name] = fam
        else:
            ov.pop(name, None)
        config_files.save_families(ctx.root, ov)
        return {"ok": True, "overrides": ov}

    @app.post("/api/families/enrich")
    def enrich_families(body: dict):
        """Best-effort: ask the comfyui-lora-manager plugin for each scanned LoRA/checkpoint's civitai
        base_model and persist a family override where it sharpens a generic (sdxl/unknown) guess.
        Body: {base_url?}. No-op when the plugin/ComfyUI is unreachable."""
        from ...comfy.scan import scan_models
        from ...comfy.family import family_of
        from ...comfy.lora_manager import lm_base_model
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        # talk to the same ComfyUI an image workflow points at (where lora-manager runs)
        img = next((m for m in ctx.base_settings.models.values() if m.kind == "image"), None)
        base_url = ((body or {}).get("base_url")
                    or (img.options.get("base_url") if img else None)
                    or "http://127.0.0.1:8188")
        ov = config_files.load_families(ctx.root)
        items = scan_models(models_dir).get("items", [])
        added = 0
        for it in items:
            if it.get("kind") not in ("checkpoint", "diffusion", "lora") or it["rel"] in ov:
                continue
            # only spend a call when the folder/arch guess is generic (no specific lineage yet)
            cur = it.get("family") or ""
            if cur not in ("sdxl", "unknown", ""):
                continue
            kind = "loras" if it["kind"] == "lora" else "checkpoints"
            base = lm_base_model(ctx.root, base_url, kind, it["rel"])
            if not base:
                continue
            fam = family_of(it["rel"], arch=it.get("arch"), civitai_base=base)
            if fam and fam not in ("unknown", cur):
                ov[it["rel"]] = fam
                added += 1
        if added:
            config_files.save_families(ctx.root, ov)
        return {"ok": True, "added": added, "overrides": ov}

    @app.get("/api/loras/tags")
    def lora_tags_index():
        """The tags your installed LoRAs were trained on — a cheap read of each
        .safetensors `ss_tag_frequency` header (no embeddings, no weight load).
        Per LoRA: its top tags by frequency. Globally: the most-used tags across
        the whole folder. Useful for picking a universal test prompt that the
        LoRAs actually speak, and for triage."""
        from ...comfy import tags as tagmod

        loras_dir = str(config_files._loras_out_dir(load_trainer(), ctx.comfy_base_dir(), ctx.root))
        try:
            per = {}
            import glob as _glob
            import os as _os
            for p in sorted(_glob.glob(_os.path.join(loras_dir, "**", "*.safetensors"), recursive=True)):
                ts = tagmod.lora_tags(p)
                if not ts:
                    continue
                rel = _os.path.relpath(p, loras_dir).replace("\\", "/")
                top = sorted(ts, key=lambda t: -ts[t])[:20]
                per[rel] = [{"tag": t, "count": ts[t]} for t in top]
            vocab = tagmod.build_vocab(loras_dir)
            global_top = sorted(vocab, key=lambda t: -vocab[t])[:60]
            return {"per": per, "global": [{"tag": t, "count": vocab[t]} for t in global_top]}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/loras/tagify")
    def tagify_scene(body: dict):
        """Natural-language scene → comma-separated booru tags via the Prompt Gen
        LLM. This is the prose→tags step; state routing then matches these tags
        against each LoRA's own tags. (The LLM does the understanding; the router
        does the matching.)"""
        provider = ctx.text_provider_for((body or {}).get("model"))
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
            res = provider.generate_text(system=None, prompt=instruction, emits=schema)
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

        from ..services import triage_cache
        cache = body.get("cache") or None

        async def events():
            try:
                async for ev in stream_generate(base, graph, "9", 240):
                    if cache and ev.get("type") == "image" and (ev.get("images") or []):
                        try:
                            triage_cache.save_render(ctx.root, cache.get("scope", ""), cache.get("lora", ""),
                                                     ev["images"][0], prompt=cache.get("prompt", ""), weight=cache.get("weight"))
                        except Exception:  # noqa: BLE001 — caching is best-effort
                            pass
                    yield f"data: {json.dumps(ev)}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/loras/grid-render")
    async def grid_render(body: dict):
        """Start a TriageJob for one or more render cells.

        Every caller — Grid Tester, Classify grid, TestBench — posts here.
        The job appears in Activity, errors are logged, and it's cancellable.
        Returns {ok, id, total}; client subscribes to /api/jobs/{id}/stream.

        Body: {cells: [{key, checkpoint?, lora?, loras?, model?,
                         weight?, prompt, negative?, steps?, seed?, cache?}]}
        """
        from fastapi.concurrency import run_in_threadpool

        from ..triage_job import TriageJob

        body = body or {}
        cells = body.get("cells") or []
        if not cells:
            return JSONResponse({"error": "cells is required"}, status_code=400)

        base = ctx.active_comfy_url()
        try:
            await run_in_threadpool(get_server(base).ensure_up)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=500)

        job = TriageJob(cells, base, ctx)
        job.start()
        return {"ok": True, "id": job.id, "total": job.total}

    @app.get("/api/loras/triage-cache")
    def triage_cache_list(scope: str = ""):
        """Cached single-LoRA triage renders for a workflow scope — lets the
        Classify grid rehydrate instead of re-rendering on every visit."""
        from ..services import triage_cache
        return {"loras": triage_cache.list_renders(ctx.root, scope)}

    @app.get("/api/loras/triage-cache/img")
    def triage_cache_img(scope: str = "", lora: str = ""):
        from fastapi.responses import FileResponse, Response
        from ..services import triage_cache
        p = triage_cache.render_path(ctx.root, scope, lora)
        if p is None:
            return Response(status_code=404)
        return FileResponse(p, media_type="image/png")

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
