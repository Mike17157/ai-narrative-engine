"""RunPod network-volume model sync endpoints.

  GET  /api/runpod/volume/status   — is the volume configured + reachable?
  GET  /api/runpod/volume/diff     — which local models are NOT on the volume?
  POST /api/runpod/volume/push     — upload one model {models_rel} → background job
  POST /api/runpod/volume/sync     — upload ALL missing models    → background job
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..jobhub import REGISTRY, BaseJob


class VolumeUploadJob(BaseJob):
    """Upload a batch of local model files to the RunPod network volume.

    Files list: [(models_rel, local_path), ...]  where models_rel is relative
    to the ComfyUI models dir (e.g. ``loras/anima/my.safetensors``).
    """

    def __init__(self, files: list[tuple[str, Path]], cfg) -> None:
        self._files = files
        self._cfg = cfg
        n = len(files)
        super().__init__(
            "runpod_upload", "RunPod upload",
            label=f"{n} file{'s' if n != 1 else ''} → volume",
            unit="files", total=n, screen="images",
        )

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        from ...runpod.volume import volume_key

        cfg = self._cfg
        try:
            s3 = await run_in_threadpool(cfg.client)
            xfer = cfg.transfer_config()
        except ImportError:
            self._emit({"type": "error", "error": "boto3 not installed — run: pip install boto3"})
            self.status = "error"
            self._emit({"type": "done", "status": self.status})
            return

        for i, (models_rel, local_path) in enumerate(self._files):
            key = volume_key(models_rel)
            size_mb = round(local_path.stat().st_size / 1e6, 1) if local_path.is_file() else 0
            self._emit({"type": "file_start", "index": i, "total": len(self._files),
                        "rel": models_rel, "key": key, "size_mb": size_mb})
            try:
                lp, vid = str(local_path), cfg.volume_id
                await run_in_threadpool(
                    lambda lp=lp, k=key: s3.upload_file(lp, vid, k, Config=xfer)
                )
                self._emit({"type": "file_done", "rel": models_rel, "key": key})
            except Exception as exc:  # noqa: BLE001
                self._emit({"type": "file_error", "rel": models_rel, "key": key, "error": str(exc)})
            self.done = i + 1

        self.status = "done"
        self._emit({"type": "done", "status": self.status})


class VolumeReconcileJob(BaseJob):
    """Reconcile RunPod volume diffusion_models/ and loras/ against a whitelist.

    Checkpoints and LoRAs NOT in the whitelist are deleted from the volume.
    Whitelisted entries missing from the volume are uploaded.
    Other model types (text_encoders, vae, embeddings …) are never touched.
    """

    # Volume key prefixes this job is allowed to manage.
    _MANAGED_PREFIXES = ("models/diffusion_models/", "models/loras/")

    def __init__(self, checkpoints: list[str], loras: list[str],
                 models_dir, cfg) -> None:
        # checkpoints: rel paths relative to diffusion_models/ (from scan i.rel)
        # loras:       rel paths relative to loras/           (from ComfyUI choices)
        self._checkpoints = checkpoints
        self._loras = loras
        self._models_dir = models_dir
        self._cfg = cfg
        super().__init__(
            "runpod_reconcile", "RunPod reconcile",
            label=f"{len(checkpoints)} ckpt · {len(loras)} LoRA → volume",
            unit="ops", total=0, screen="images",
        )

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        from ...runpod.volume import volume_key

        cfg = self._cfg
        models_dir = self._models_dir

        try:
            s3 = await run_in_threadpool(cfg.client)
            xfer = cfg.transfer_config()
        except ImportError:
            self._emit({"type": "error", "error": "boto3 not installed"})
            self.status = "error"
            self._emit({"type": "done", "status": self.status})
            return

        # Build the expected key set from the whitelist.
        want: dict[str, str] = {}  # volume_key → local models_rel
        for ckpt_rel in self._checkpoints:
            models_rel = f"diffusion_models/{ckpt_rel}"
            want[volume_key(models_rel)] = models_rel
        for lora_rel in self._loras:
            models_rel = f"loras/{lora_rel}"
            want[volume_key(models_rel)] = models_rel

        # List managed keys currently on the volume.
        def _list_managed() -> set[str]:
            current: set[str] = set()
            for prefix in self._MANAGED_PREFIXES:
                current |= cfg.list_keys(s3, prefix)
            return current

        self._emit({"type": "status", "message": "Scanning volume…"})
        current = await run_in_threadpool(_list_managed)

        to_delete = current - set(want.keys())
        to_upload = {k: v for k, v in want.items() if k not in current}
        self.total = len(to_delete) + len(to_upload)

        self._emit({"type": "plan",
                    "delete": len(to_delete), "upload": len(to_upload),
                    "message": f"Plan: {len(to_delete)} delete, {len(to_upload)} upload"})

        ops = 0
        for key in sorted(to_delete):
            self._emit({"type": "delete_start", "key": key})
            try:
                await run_in_threadpool(lambda k=key: cfg.delete_key(s3, k))
                self._emit({"type": "delete_done", "key": key})
            except Exception as exc:  # noqa: BLE001
                self._emit({"type": "delete_error", "key": key, "error": str(exc)})
            ops += 1
            self.done = ops

        for key, models_rel in sorted(to_upload.items()):
            local = models_dir.joinpath(*models_rel.split("/"))
            if not local.is_file():
                self._emit({"type": "upload_skip", "key": key,
                            "reason": f"not found locally: {models_rel}"})
                ops += 1
                self.done = ops
                continue
            size_mb = round(local.stat().st_size / 1e6, 1)
            self._emit({"type": "upload_start", "key": key, "size_mb": size_mb})
            try:
                lp, vid = str(local), cfg.volume_id
                await run_in_threadpool(
                    lambda lp=lp, k=key: s3.upload_file(lp, vid, k, Config=xfer)
                )
                self._emit({"type": "upload_done", "key": key})
            except Exception as exc:  # noqa: BLE001
                self._emit({"type": "upload_error", "key": key, "error": str(exc)})
            ops += 1
            self.done = ops

        self.status = "done"
        self._emit({"type": "done", "status": self.status})


_GRID_CONFIG_NAME = "lora_grid.json"


def _grid_config_path(ctx) -> Path:
    return ctx.root / "configs" / _GRID_CONFIG_NAME


def _read_grid_config(ctx) -> dict:
    p = _grid_config_path(ctx)
    if p.is_file():
        import json
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"checkpoints": [], "loras": []}


def _write_grid_config(ctx, data: dict) -> None:
    import json
    p = _grid_config_path(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def start_reconcile_if_configured(ctx) -> None:
    """Called at server startup: if RunPod is configured and a grid config exists,
    kick off a background reconcile so the volume stays in sync without any manual action."""
    from ...runpod.volume import VolumeConfig

    cfg = VolumeConfig()
    if not cfg.configured:
        return
    grid = _read_grid_config(ctx)
    if not grid.get("checkpoints") and not grid.get("loras"):
        return
    bd = ctx.comfy_base_dir()
    models_dir = (bd / "models") if bd else None
    if not models_dir or not models_dir.is_dir():
        return
    job = VolumeReconcileJob(grid["checkpoints"], grid["loras"], models_dir, cfg)
    job.start()


def register(app, ctx) -> None:
    from ...runpod.volume import VolumeConfig, volume_key

    @app.get("/api/runpod/grid-config")
    def get_grid_config():
        return _read_grid_config(ctx)

    @app.put("/api/runpod/grid-config")
    async def put_grid_config(body: dict):
        data = {
            "checkpoints": [str(x) for x in (body or {}).get("checkpoints") or []],
            "loras": [str(x) for x in (body or {}).get("loras") or []],
        }
        await run_in_threadpool(lambda: _write_grid_config(ctx, data))
        return {"ok": True}

    def _serverless():
        """(base_url, headers) for the serverless endpoint, or (None, reason)."""
        rp = ctx.runpod_config
        eid, key = rp.get("serverless_endpoint_id"), rp.get("api_key")
        if not (eid and key):
            return None, "RunPod serverless not configured (api_key + endpoint id)"
        return (f"https://api.runpod.ai/v2/{eid}",
                {"Authorization": f"Bearer {key}"}), eid

    @app.get("/api/runpod/status")
    def serverless_status():
        """Live serverless health: job counts (queued / in-progress / failed) + worker
        states. Shows whether jobs are piling up or the worker is just failing them."""
        import httpx
        conn, eid = _serverless()
        if conn is None:
            return {"configured": False, "reason": eid}
        base, headers = conn
        try:
            with httpx.Client(base_url=base, headers=headers, timeout=20) as c:
                h = c.get("/health"); h.raise_for_status()
                return {"configured": True, "endpoint_id": eid, **h.json()}
        except httpx.HTTPError as exc:
            return JSONResponse({"configured": True, "endpoint_id": eid, "error": str(exc)}, status_code=502)

    @app.post("/api/runpod/purge")
    def serverless_purge():
        """Clear the serverless endpoint's QUEUE (pending jobs). Does NOT stop a job already
        executing on a worker. Returns how many were removed + the post-purge job counts."""
        import httpx
        conn, eid = _serverless()
        if conn is None:
            return JSONResponse({"error": eid}, status_code=400)
        base, headers = conn
        try:
            with httpx.Client(base_url=base, headers=headers, timeout=20) as c:
                r = c.post("/purge-queue"); r.raise_for_status()
                out = r.json() if r.text else {}
                h = c.get("/health")
                return {"ok": True, "endpoint_id": eid, **out,
                        "jobs": (h.json().get("jobs") if h.status_code < 300 else None)}
        except httpx.HTTPError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)

    @app.get("/api/runpod/volume/status")
    def volume_status():
        cfg = VolumeConfig()
        if not cfg.configured:
            return {"configured": False}
        return {"configured": True, "endpoint": cfg.endpoint, "volume_id": cfg.volume_id}

    @app.post("/api/runpod/volume/push")
    async def push_model(body: dict):
        """Push a single local model to the volume. models_rel is relative to
        the ComfyUI models dir (e.g. loras/anima/my.safetensors)."""
        models_rel = ((body or {}).get("models_rel") or "").replace("\\", "/")
        if not models_rel:
            return JSONResponse({"error": "models_rel required"}, status_code=400)
        cfg = VolumeConfig()
        if not cfg.configured:
            return JSONResponse({"error": "RunPod volume not configured (see RUNPOD_VOLUME_ID etc.)"}, status_code=400)
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "ComfyUI models dir not found"}, status_code=404)
        local = models_dir.joinpath(*models_rel.split("/"))
        if not local.is_file():
            return JSONResponse({"error": f"not found locally: {models_rel}"}, status_code=404)
        job = VolumeUploadJob([(models_rel, local)], cfg)
        job.start()
        return {"ok": True, "id": job.id}

    @app.get("/api/runpod/volume/diff")
    async def volume_diff():
        """Compare every local .safetensors against the volume; return what's missing."""
        cfg = VolumeConfig()
        if not cfg.configured:
            return {"configured": False, "missing": []}
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "models dir not found"}, status_code=404)

        def _diff() -> list[dict]:
            s3 = cfg.client()
            remote = cfg.list_keys(s3)
            missing = []
            for p in sorted(models_dir.rglob("*.safetensors")):
                rel = str(p.relative_to(models_dir)).replace("\\", "/")
                key = volume_key(rel)
                if key not in remote:
                    missing.append({"rel": rel, "key": key,
                                    "size_mb": round(p.stat().st_size / 1e6, 1)})
            return missing

        try:
            missing = await run_in_threadpool(_diff)
        except ImportError:
            return JSONResponse({"error": "boto3 not installed"}, status_code=500)
        return {"configured": True, "missing": missing, "count": len(missing)}

    @app.post("/api/runpod/volume/reconcile")
    async def reconcile_volume(body: dict):
        """Reconcile the volume against the LoRA-matrix selection.

        Body: {checkpoints: [rel, ...], loras: [rel, ...]}
          checkpoints — rel paths from scan (relative to diffusion_models/)
          loras       — rel paths from ComfyUI choices (relative to loras/)

        Deletes managed volume keys not in the list; uploads missing ones.
        """
        body = body or {}
        checkpoints = [str(x) for x in (body.get("checkpoints") or [])]
        loras = [str(x) for x in (body.get("loras") or [])]
        cfg = VolumeConfig()
        if not cfg.configured:
            return JSONResponse({"error": "RunPod volume not configured"}, status_code=400)
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "models dir not found"}, status_code=404)
        job = VolumeReconcileJob(checkpoints, loras, models_dir, cfg)
        job.start()
        return {"ok": True, "id": job.id}

    @app.post("/api/runpod/volume/sync")
    async def sync_volume(body: dict):
        """Upload all local models that aren't on the volume yet.
        Pass {force: true} to re-upload everything."""
        cfg = VolumeConfig()
        if not cfg.configured:
            return JSONResponse({"error": "RunPod volume not configured"}, status_code=400)
        bd = ctx.comfy_base_dir()
        models_dir = (bd / "models") if bd else None
        if not models_dir or not models_dir.is_dir():
            return JSONResponse({"error": "models dir not found"}, status_code=404)
        force = bool((body or {}).get("force"))

        def _collect() -> list[tuple[str, Path]]:
            s3 = cfg.client()
            remote = set() if force else cfg.list_keys(s3)
            files = []
            for p in sorted(models_dir.rglob("*.safetensors")):
                rel = str(p.relative_to(models_dir)).replace("\\", "/")
                if volume_key(rel) not in remote:
                    files.append((rel, p))
            return files

        try:
            files = await run_in_threadpool(_collect)
        except ImportError:
            return JSONResponse({"error": "boto3 not installed"}, status_code=500)
        if not files:
            return {"ok": True, "id": None, "total": 0, "message": "Volume is already up to date"}
        job = VolumeUploadJob(files, cfg)
        job.start()
        return {"ok": True, "id": job.id, "total": len(files)}
