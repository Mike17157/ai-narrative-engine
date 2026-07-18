"""Composition root for the replacement Story-only Loom application.

This app deliberately does *not* import or register the legacy general-chat,
workflow editor, LoRA, training, audio, tag, or admin route families.  It
reuses the mature Story APIs while those APIs are progressively narrowed, so
the replacement can be exercised beside the existing app before anything is
retired.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..comfy.server import ComfyServer, LaunchConfig, detect_desktop_install, register_server
from ..config import load_settings, load_user
from ..connections import ConnectionStore
from ..server.context import AppContext
from ..server.routing import register_domain
from ..server.security import configure_api_auth
from ..stories.api import library, runtime
from . import images as image_capability
from .api import cast as cast_api, images as image_api, session as session_api
from .routing import register_filtered_domain


def _load_dotenv(root: Path) -> None:
    """Load project-local environment values without importing the legacy app."""
    path = root / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _register_optional_comfy(user, root: Path, *, enabled: bool) -> str:
    """Register ComfyUI only as an on-demand image capability.

    Unlike the legacy app this does not warm, scan, manage presets, or start
    any image-admin job at startup.  A render is the only operation that may
    ask the registered server to launch.
    """
    config = user.comfyui
    if not enabled:
        return config.base_url
    launch = None
    if config.managed:
        if config.python or config.main_py or config.base_directory:
            launch = LaunchConfig(
                python=config.python,
                main_py=config.main_py,
                base_directory=config.base_directory,
            )
        else:
            launch = detect_desktop_install()
    register_server(
        ComfyServer(
            config.base_url,
            managed=config.managed,
            launch=launch,
            startup_timeout=config.startup_timeout_s,
            log_path=root / "logs" / "lean-comfyui.log",
            new_console=config.console,
        )
    )
    return config.base_url


_STORY_RUNTIME_ROUTES = frozenset({
    ("post", "/api/stories/{key}/task"),
    ("get", "/api/stories/{key}/state"),
    ("put", "/api/stories/{key}/state"),
    ("post", "/api/stories/{key}/state/reset"),
    ("post", "/api/stories/{key}/play"),
    ("post", "/api/stories/{key}/prologue"),
    ("get", "/api/stories/{key}/arc"),
    ("post", "/api/stories/{key}/arc"),
    ("post", "/api/stories/{key}/arc/design"),
    ("post", "/api/stories/{key}/day/suggest"),
    ("get", "/api/stories/{key}/manuscript"),
    ("post", "/api/stories/{key}/manuscript/bake"),
    ("post", "/api/stories/{key}/manuscript/edit"),
    ("get", "/api/stories/{key}/state-card"),
    ("get", "/api/stories/{key}/live-cards"),
})


_STORY_LIBRARY_ROUTES = frozenset({
    ("post", "/api/stories/new"),
    ("get", "/api/stories"),
    ("get", "/api/stories/{key}"),
    ("get", "/api/stories/{key}/control-graph"),
    ("get", "/api/stories/{key}/interview-history"),
    ("put", "/api/stories/{key}"),
    ("patch", "/api/stories/{key}/inline-text"),
    ("get", "/api/stories/{key}/play-readiness"),
    ("get", "/api/stories/{key}/card/gaps"),
    ("get", "/api/stories/{key}/director-preview"),
    ("put", "/api/stories/{key}/director/scenes/{scene_id}/schedule"),
    ("put", "/api/stories/{key}/director/scenes/{scene_id}/placement"),
    ("post", "/api/stories/{key}/activate"),
    ("post", "/api/stories/{key}/card/organize"),
    ("post", "/api/stories/{key}/card/review"),
    ("post", "/api/stories/{key}/card/develop"),
    ("post", "/api/stories/{key}/interview"),
    ("get", "/api/stories/{key}/architect/public/context"),
    ("post", "/api/stories/{key}/architect/public/model"),
    ("post", "/api/stories/{key}/architect/public/commit"),
    ("post", "/api/stories/{key}/architect/turn"),
    ("post", "/api/stories/{key}/card/{layer}/chat"),
    ("delete", "/api/stories/{key}"),
})


_STORY_PATH = re.compile(r"^/api/stories/([^/]+)(?:/|$)")


def _story_runtime_route(path: str, method: str) -> bool:
    """Admit the Story product contract, never adjacent debug/tool/image routes."""
    return (method, path) in _STORY_RUNTIME_ROUTES


def _story_library_route(path: str, method: str) -> bool:
    """Admit Story authoring/Architect operations, never retired generators."""
    return (method, path) in _STORY_LIBRARY_ROUTES


def _story_agent_status(ctx) -> dict:
    """Return a small safe health projection, never raw provider route config."""
    provider, route = ctx.story_agent_provider({})
    route = route if isinstance(route, dict) else {}
    return {
        "available": provider is not None,
        "model": str(route.get("selected_model") or route.get("model") or "active"),
        "connection": str(route.get("selected_connection") or route.get("connection") or "active"),
        "using_fallback": bool(route.get("used_fallback")),
    }


def _story_integrity(ctx) -> dict:
    """Surface legacy duplicate cast keys before they can confuse flat old helpers."""
    from ..server.services import story_store

    duplicates = story_store.duplicate_character_keys(ctx.root)
    return {
        "ready": not bool(duplicates),
        "duplicate_character_keys": duplicates,
    }


def build_lean_context(root: str | Path = ".", *, comfy_enabled: bool | None = None) -> AppContext:
    """Construct the Story-only context without legacy app startup hooks."""
    root = Path(root)
    _load_dotenv(root)
    enabled = _env_flag("LOOM_LEAN_COMFY", True) if comfy_enabled is None else comfy_enabled
    user = load_user(root)
    return AppContext(
        root=root,
        # The full app seeds a local Ollama profile as a convenience. The lean
        # app is intentionally non-mutating at boot: it uses configured
        # connections, but never creates a secrets file just by being started.
        store=ConnectionStore(root, ensure_local=False),
        base_settings=load_settings(root),
        user=user,
        comfy_url=_register_optional_comfy(user, root, enabled=enabled),
    )


def create_lean_app(root: str | Path = ".", *, comfy_enabled: bool | None = None) -> FastAPI:
    """Create the parallel Story/Architect/Play application surface.

    The current Svelte Story routes can proxy to this API during migration.  No
    legacy SPA is mounted here: the lean app is API-first until the reduced
    frontend shell is ready.
    """
    root = Path(root)
    effective_comfy = _env_flag("LOOM_LEAN_COMFY", True) if comfy_enabled is None else comfy_enabled
    ctx = build_lean_context(root, comfy_enabled=effective_comfy)
    app = FastAPI(title="Loom Story")
    configure_api_auth(app)
    app.state.lean_context = ctx
    app.state.lean_comfy_enabled = bool(effective_comfy)

    @app.middleware("http")
    async def enforce_story_session_scope(request: Request, call_next):
        """Lean play has exactly one session: ``play-<story key>``.

        The mature runtime still accepts an arbitrary ``sid`` for the old
        multi-console shell.  Keep that implementation, but reject a session
        that belongs to a different Story before its handler can load it.
        """
        match = _STORY_PATH.match(request.url.path)
        if match and match.group(1) != "session":
            key = match.group(1)
            expected = f"play-{key}"
            query_sid = request.query_params.get("sid")
            if query_sid and query_sid != expected:
                return JSONResponse({"error": "session does not belong to this story"}, status_code=400)
            if request.headers.get("content-type", "").split(";", 1)[0] == "application/json":
                raw = await request.body()
                try:
                    body = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    body = None  # Let FastAPI return its usual malformed-body response.
                if isinstance(body, dict) and body.get("sid") and body["sid"] != expected:
                    return JSONResponse({"error": "session does not belong to this story"}, status_code=400)
        return await call_next(request)

    @app.get("/api/lean/health")
    def lean_health():
        image_models = image_capability.available_krea_models(ctx)
        comfy = {"enabled": bool(effective_comfy), "configured": bool(image_models)}
        return {
            "ok": True,
            "surface": "story",
            "story_agent": _story_agent_status(ctx),
            "integrity": _story_integrity(ctx),
            "images": comfy,
        }

    # Existing Story APIs are the migration substrate.  They are explicitly
    # admitted rather than wholesale registered: notably the destructive
    # global-cast regeneration/job route stays behind with the legacy shell.
    # The image-heavy legacy `assets` family is purposefully absent; the lean
    # image capability below is its replacement.
    register_filtered_domain(library, app, ctx, "stories", _story_library_route)
    register_filtered_domain(runtime, app, ctx, "stories", _story_runtime_route)
    register_domain(cast_api, app, ctx, "stories")
    register_domain(image_api, app, ctx, "images")
    register_domain(session_api, app, ctx, "stories")
    return app


def dev_lean_app() -> FastAPI:
    """Zero-argument factory for ``uvicorn --factory`` / reload workflows."""
    return create_lean_app(os.environ.get("LOOM_ROOT", "."))
