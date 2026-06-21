"""Loom web server — connection flow + decoupled chat/image selection.

`create_app()` is a thin assembler: it builds the shared `AppContext` (root, connection
store, settings, user, ComfyUI url) and wires each domain's router onto the FastAPI app.
The actual endpoints live in `loom/server/routers/<domain>.py`; the state-bound helpers
they share live on `AppContext` (`loom/server/context.py`); pure helpers live under
`loom/server/services/`. This file owns only process bootstrap (.env, ComfyUI registration),
the SPA static mount, and startup cleanup.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..comfy.server import ComfyServer, LaunchConfig, detect_desktop_install, register_server
from ..config import load_settings, load_user
from ..connections import ConnectionStore

from .context import AppContext
from .index_html import INDEX_HTML
from . import startup
from ..stories import router as stories  # story domain lives in loom/stories/, not routers/
from .routers import (
    characters,
    chat,
    comfy,
    jobs,
    lora,
    lorebooks,
    models_conn,
    personas,
    runpod,
    server,
    tags,
    trainer,
    workflow,
)


def _load_dotenv(root: Path) -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines, existing env wins."""
    import os

    path = root / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _register_comfy(user, root: Path) -> str:
    c = user.comfyui
    launch = None
    if c.managed:
        if c.python or c.main_py or c.base_directory:
            launch = LaunchConfig(python=c.python, main_py=c.main_py, base_directory=c.base_directory)
        else:
            launch = detect_desktop_install()
    register_server(
        ComfyServer(
            c.base_url, managed=c.managed, launch=launch,
            startup_timeout=c.startup_timeout_s,
            log_path=Path(root) / "logs" / "comfyui.log", new_console=c.console,
        )
    )
    return c.base_url


# Every domain router exposes `register(app, ctx)`. Order is cosmetic (paths are distinct).
_ROUTERS = (
    server,
    characters,
    chat,
    stories,
    tags,
    models_conn,
    workflow,
    lora,
    trainer,
    comfy,
    personas,
    jobs,
    runpod,
    lorebooks,
)


def _mount_spa(app: FastAPI, root: Path) -> None:
    """Serve the built Svelte SPA at "/" if present (prod); otherwise fall back to the
    bundled single-file page so the server works with no Node build."""
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    build_dir = root / "frontend" / "build"
    if build_dir.is_dir():
        class SPAStaticFiles(StaticFiles):
            """Serve real files, but fall back to index.html for unknown paths so
            client-side routes (e.g. /images/lora) survive a refresh/deep-link."""
            async def get_response(self, path, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        app.mount("/", SPAStaticFiles(directory=str(build_dir), html=True), name="frontend")
    else:
        @app.get("/", response_class=HTMLResponse)
        def index() -> str:
            return INDEX_HTML


def build_context(root: str | Path = ".") -> AppContext:
    """Construct the AppContext (config + connections + comfy) without a web server — so the CLI and
    headless jobs can drive the same generation logic the routes use."""
    root = Path(root)
    _load_dotenv(root)
    user = load_user(root)
    return AppContext(
        root=root,
        store=ConnectionStore(root),
        base_settings=load_settings(root),
        user=user,
        comfy_url=_register_comfy(user, root),
    )


def dev_app() -> FastAPI:
    """Zero-arg factory for `uvicorn --reload` (which needs an import string, not an app
    instance). Honors LOOM_ROOT so `loom serve --reload --root <path>` still works."""
    import os
    return create_app(os.environ.get("LOOM_ROOT", "."))


def create_app(root: str | Path = ".") -> FastAPI:
    root = Path(root)
    ctx = build_context(root)

    app = FastAPI(title="Loom")
    for mod in _ROUTERS:
        mod.register(app, ctx)

    _mount_spa(app, root)

    # Clean up any generated characters left storyless by past deletions (e.g. a story removed
    # before this cascade existed) so the library doesn't accumulate orphans.
    try:
        ctx.prune_orphan_characters()
    except Exception:  # noqa: BLE001 — never block startup on cleanup
        pass

    # Seed a default 'You' persona if none exist (empty configs/personas/) so the Personas
    # page opens with a row, mirroring the pre-overhaul localStorage default.
    try:
        if not ctx.base_settings.personas:
            ctx.write_persona("you", {"name": "You", "description": ""})
    except Exception:  # noqa: BLE001 — never block startup on seeding
        pass

    # Embed any lorebook entries that lack a semantic vector (seeded/imported/legacy), in
    # the background so the first request isn't blocked by model load. No-op without an embedder.
    try:
        import threading

        from .services import lorebook_store as _LS_emb
        threading.Thread(target=lambda: _LS_emb.backfill_embeddings(root), daemon=True).start()
    except Exception:  # noqa: BLE001 — never block boot on indexing
        pass

    # Startup hooks — each is isolated; a failure never blocks the others or the boot.
    startup.warm_scan_cache(ctx)
    startup.reap_stale_jobs()
    startup.regenerate_manifest(ctx)
    startup.validate_lora_stacks(ctx)

    # Reconcile the RunPod volume against the saved LoRA grid selection so the remote
    # worker always matches what's configured here without any manual sync step.
    try:
        runpod.start_reconcile_if_configured(ctx)
    except Exception:  # noqa: BLE001 — never block startup on sync
        pass

    return app
