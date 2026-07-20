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
