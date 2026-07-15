"""Server startup hooks — called once from create_app() after routers are registered.

Each hook is isolated: a failure in one never blocks the others or the server boot.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Scan cache warm-up
# ---------------------------------------------------------------------------

def warm_scan_cache(ctx) -> None:
    """Pre-run the ComfyUI model scan so the first /api/comfy/models request
    is instant instead of hitting a cold filesystem walk."""
    try:
        from ..comfy.scan import cached_scan
        bd = ctx.comfy_base_dir()
        if not bd:
            return
        models_dir = bd / "models"
        if models_dir.is_dir():
            cached_scan(models_dir)
            log.info("startup: model scan cache warmed")
    except Exception:  # noqa: BLE001
        log.exception("startup: scan warm-up failed")


# ---------------------------------------------------------------------------
# 2. Stale job reaping
# ---------------------------------------------------------------------------

def reap_stale_jobs() -> None:
    """Mark any jobs stuck in 'running' state as 'error'.

    The REGISTRY is in-memory so it's always empty on a fresh boot; this is a
    safety net for if job state is ever persisted across restarts."""
    try:
        from .jobhub import REGISTRY
        reaped = 0
        for job in REGISTRY.all():
            if job.status == "running":
                job.status = "error"
                job._emit({"type": "done", "status": "error",
                           "message": "interrupted by server restart"})
                reaped += 1
        if reaped:
            log.warning("startup: reaped %d stale job(s)", reaped)
    except Exception:  # noqa: BLE001
        log.exception("startup: job reaping failed")


# ---------------------------------------------------------------------------
# 2b. Warm the managed ComfyUI (background, non-blocking)
# ---------------------------------------------------------------------------

def warm_comfyui(ctx) -> None:
    """Bring the managed ComfyUI up at boot so the first render isn't a cold launch.
    Runs in a daemon thread — never blocks server start. No-op unless the server is managed
    and `comfyui.warm_on_start` is set; `ensure_up()` only launches if it isn't already up."""
    import threading

    try:
        if not getattr(ctx.user.comfyui, "warm_on_start", True):
            return
        from ..comfy.server import get_server
        server = get_server(ctx.comfy_url)
        if not getattr(server, "managed", False):
            return   # connect-only setup — nothing for Loom to launch

        def _run():
            try:
                server.ensure_up()
                log.info("startup: managed ComfyUI is up")
            except Exception:  # noqa: BLE001 — a launch failure must never crash boot
                log.exception("startup: managed ComfyUI warm-start failed")

        threading.Thread(target=_run, name="comfyui-warm-start", daemon=True).start()
    except Exception:  # noqa: BLE001
        log.exception("startup: ComfyUI warm-start could not start")


# ---------------------------------------------------------------------------
# 3. LoRA stack validation
# ---------------------------------------------------------------------------

def validate_lora_stacks(ctx) -> None:
    """Warn about any LoRA library entries whose model file doesn't exist locally.

    Catches breakage early (e.g. after a librarian reorganize or a file delete)
    so the user knows which stacks will fail before they try to render."""
    try:
        bd = ctx.comfy_base_dir()
        loras_dir = (bd / "models" / "loras") if bd else None
        if not loras_dir or not loras_dir.is_dir():
            return

        library = getattr(ctx.base_settings.loras, "library", []) or []
        missing = []
        for entry in library:
            name = getattr(entry, "name", None) or ""
            if not name:
                continue
            local = loras_dir / Path(*name.replace("\\", "/").split("/"))
            if not local.exists():
                missing.append(name)

        if missing:
            log.warning(
                "startup: %d LoRA(s) in library not found on disk:\n%s",
                len(missing),
                "\n".join(f"  - {m}" for m in missing),
            )
        else:
            log.info("startup: all %d LoRA library entries resolved", len(library))
    except Exception:  # noqa: BLE001
        log.exception("startup: LoRA stack validation failed")


# ---------------------------------------------------------------------------
# 4. Image-preset LoRA auto-download (Civitai)
# ---------------------------------------------------------------------------

def download_preset_loras(ctx) -> None:
    """Fetch any image-preset LoRA missing locally from Civitai (configs/civitai_loras.json),
    in a background thread so large downloads never block boot. No-op without a managed
    ComfyUI loras dir or a CIVITAI_API_TOKEN. On success, refreshes the scan cache."""
    try:
        from ..comfy.civitai import ensure_preset_loras
        bd = ctx.comfy_base_dir()
        loras_dir = (bd / "models" / "loras") if bd else None
        if not loras_dir:
            return

        def _run() -> None:
            try:
                summary = ensure_preset_loras(ctx.root, loras_dir)
                if summary.get("downloaded"):
                    from ..comfy.scan import invalidate_scan_cache
                    invalidate_scan_cache()
                    ctx._loras_cache = None
                    log.info("startup: civitai preset-LoRA download complete — %d new file(s)",
                             len(summary["downloaded"]))
            except Exception:  # noqa: BLE001
                log.exception("startup: civitai preset-LoRA download failed")

        import threading
        threading.Thread(target=_run, name="civitai-preset-loras", daemon=True).start()
    except Exception:  # noqa: BLE001
        log.exception("startup: civitai preset-LoRA download could not start")
