from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse

from ..services import config_files
from ..services.jobs_util import _cancel_job

def register(app, ctx):
    def load_trainer() -> dict:
        return config_files.load_trainer(ctx.root)

    @app.get("/api/trainer")
    def get_trainer() -> dict:
        from ...train import kohya

        cfg = load_trainer()
        st = kohya.trainer_status(cfg.get("sd_scripts_dir"), cfg.get("python"))
        ck = config_files._checkpoints_dir(cfg, ctx.comfy_base_dir())
        return {**cfg, **st, "defaults": kohya.DEFAULTS,
                "checkpoints_dir_resolved": str(ck) if ck else None,
                "loras_out_dir": str(config_files._loras_out_dir(cfg, ctx.comfy_base_dir(), ctx.root)),
                "setup_script": "scripts/setup_trainer.ps1"}

    @app.post("/api/trainer")
    def set_trainer(body: dict):
        cfg = load_trainer()
        for k in ("sd_scripts_dir", "python", "checkpoints_dir", "loras_dir"):
            if k in (body or {}):
                cfg[k] = (body[k] or "").strip()
        path = ctx.root / "configs" / "trainer.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return {"ok": True}

    @app.get("/api/trainer/detect")
    def trainer_detect() -> dict:
        from ...train import kohya

        return kohya.detect_cuda()

    @app.post("/api/trainer/setup")
    async def trainer_setup(body: dict):
        """Install/repair the kohya trainer from the UI — rebuilds the venv with
        the right Python + CUDA torch. Streams via the same /train/stream."""
        import shutil

        from ..train_job import TrainJob, current as train_current

        running = train_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a run/setup is already going — check or cancel it",
                                 "running": True}, status_code=409)

        ps = shutil.which("pwsh") or shutil.which("powershell")
        if not ps:
            return JSONResponse({"error": "PowerShell not found on PATH"}, status_code=400)
        script = ctx.root / "scripts" / "setup_trainer.ps1"
        if not script.is_file():
            return JSONResponse({"error": "scripts/setup_trainer.ps1 is missing"}, status_code=404)
        from ...train import kohya
        cuda = (body or {}).get("cuda") or "auto"
        if cuda == "auto":
            cuda = kohya.detect_cuda()["cuda"]
        if cuda not in ("cu121", "cu124", "cu128"):
            cuda = "cu124"

        # The install location is deterministic, so write the trainer config now —
        # it's correct the moment setup finishes (no reliance on the script's own
        # config write, which needs a newer PowerShell).
        sd_dir = ctx.root / "trainer" / "sd-scripts"
        cfg = load_trainer()
        cfg["sd_scripts_dir"] = str(sd_dir)
        cfg["python"] = str(sd_dir / "venv" / "Scripts" / "python.exe")
        (ctx.root / "configs").mkdir(parents=True, exist_ok=True)
        (ctx.root / "configs" / "trainer.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-Recreate", "-Cuda", cuda]
        job = TrainJob(cmd, cwd=str(ctx.root), output_name="trainer setup", output_path="")
        job.start()
        return {"ok": True, "id": job.id}

    @app.post("/api/train/start")
    async def train_start(body: dict):
        from ...train import kohya
        from ..train_job import TrainJob, current as train_current

        running = train_current()
        if running and running.status == "running":
            return JSONResponse({"error": "a training run is already going — check or cancel it",
                                 "running": True}, status_code=409)

        cfg = load_trainer()
        st = kohya.trainer_status(cfg.get("sd_scripts_dir"), cfg.get("python"))
        if not st["installed"]:
            return JSONResponse({"error": "trainer not set up: " + "; ".join(st["issues"]),
                                 "needs_setup": True}, status_code=400)

        dataset = (body or {}).get("dataset")
        ddir = ctx.root / "datasets" / (dataset or "")
        if not dataset or not ddir.is_dir() or not any(ddir.glob("*.png")):
            return JSONResponse({"error": f"dataset not found or empty: {dataset}"}, status_code=404)

        ckname = (body or {}).get("base_model")
        ckdir = config_files._checkpoints_dir(cfg, ctx.comfy_base_dir())
        if not ckdir:
            return JSONResponse({"error": "can't resolve the checkpoints folder — set checkpoints_dir "
                                          "in the trainer config"}, status_code=400)
        ckpt = (ckdir / ckname) if ckname else None
        if not ckpt or not ckpt.is_file():
            return JSONResponse({"error": f"checkpoint not found: {ckpt}"}, status_code=404)

        p = kohya.merge_params(body or {})
        import re as _re2
        name = _re2.sub(r"[^\w\-]+", "_", (body or {}).get("output_name") or dataset).strip("_") or "lora"
        out_dir = config_files._loras_out_dir(cfg, ctx.comfy_base_dir(), ctx.root)
        out_dir.mkdir(parents=True, exist_ok=True)
        work = ddir / "_train"
        work.mkdir(exist_ok=True)
        toml = work / "dataset.toml"
        kohya.write_dataset_toml(toml, ddir, p["resolution"], p["train_batch_size"], p["num_repeats"])
        cmd = kohya.build_command(st["python"], cfg["sd_scripts_dir"], ckpt, toml, out_dir, name, p)

        job = TrainJob(cmd, cwd=cfg["sd_scripts_dir"], output_name=name,
                       output_path=str(out_dir / f"{name}.safetensors"))
        job.start()
        return {"ok": True, "id": job.id, "output_name": name,
                "out": str(out_dir / f"{name}.safetensors")}

    @app.post("/api/train/anima-config")
    def train_anima_config(body: dict):
        """Anima is a DiT — kohya can't train it. Generate the native Anima
        trainer's config (Anima_lora_configs.toml + dataset TOML) from our dataset
        + the scanned model files, and hand back the command to run it there.
        Loom doesn't run this trainer (it's a separate, GUI-equipped install)."""
        from ...train import anima as animamod
        from ...train import kohya
        body = body or {}
        dataset = body.get("dataset")
        ddir = ctx.root / "datasets" / (dataset or "")
        if not dataset or not ddir.is_dir():
            return JSONResponse({"error": f"dataset not found: {dataset}"}, status_code=404)
        bd = ctx.comfy_base_dir()
        md = (bd / "models") if bd else None
        if not md or not md.is_dir():
            return JSONResponse({"error": "ComfyUI models directory not found"}, status_code=404)
        dit = body.get("dit")
        te = body.get("text_encoder") or "qwen_3_06b_base.safetensors"
        vae = body.get("vae") or "qwen_image_vae.safetensors"
        if not dit:
            return JSONResponse({"error": "pick an Anima DiT model"}, status_code=400)
        dit_path = md / "diffusion_models" / dit.replace("\\", "/")
        qwen_path = md / "text_encoders" / te.replace("\\", "/")
        vae_path = md / "vae" / vae.replace("\\", "/")
        missing = [str(x) for x in (dit_path, qwen_path, vae_path) if not x.is_file()]
        if missing:
            return JSONResponse({"error": "missing model file(s): " + ", ".join(missing)}, status_code=404)

        p = animamod.merge_params(body)
        import re as _re3
        name = _re3.sub(r"[^\w\-]+", "_", body.get("output_name") or dataset).strip("_") or "anima-lora"
        work = ddir / "_train"
        work.mkdir(exist_ok=True)
        ds_toml = work / "anima_dataset.toml"
        kohya.write_dataset_toml(ds_toml, ddir, p["resolution"], p["train_batch_size"], p["num_repeats"])
        out_dir = config_files._loras_out_dir(load_trainer(), ctx.comfy_base_dir(), ctx.root)
        out_dir.mkdir(parents=True, exist_ok=True)
        config_path = work / "Anima_lora_configs.toml"
        text = animamod.write_config(config_path, dit_path=dit_path, qwen_path=qwen_path, vae_path=vae_path,
                                     dataset_toml=ds_toml, output_dir=out_dir, output_name=name, p=p)
        return {"ok": True, "config_path": str(config_path), "dataset_toml": str(ds_toml),
                "output": str(out_dir / f"{name}.safetensors"), "config_text": text,
                "command": f'python anima_train_network.py --config_file "{config_path}"'}

    @app.get("/api/train/stream")
    def train_stream():
        from fastapi.responses import StreamingResponse
        from starlette.responses import Response

        from ..train_job import current as train_current

        job = train_current()
        if job is None:
            return Response(status_code=204)
        return StreamingResponse(job.stream(), media_type="text/event-stream")

    @app.get("/api/train/job")
    def train_job_status() -> dict:
        from ..train_job import current as train_current

        job = train_current()
        return job.snapshot() if job else {"status": "idle"}

    @app.post("/api/train/cancel")
    async def train_cancel():
        from ..train_job import current as train_current

        return await _cancel_job(train_current())
