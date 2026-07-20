"""Loom CLI — validate configs, list models, manage ComfyUI, generate characters.

    loom validate                       # load + cross-check all configs
    loom models                         # list configured models (chat vs image)
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from .comfy.server import ComfyServer, LaunchConfig, detect_desktop_install, get_server, register_server
from .config import load_settings, load_user
from . import cli_story

app = typer.Typer(add_completion=False, help="Loom — local story studio backend tooling.")
comfy_app = typer.Typer(help="Manage the ComfyUI image backend (connect or launch headless).")
app.add_typer(comfy_app, name="comfy")
app.add_typer(cli_story.app, name="story", help="Inspect/edit story data directly (no server).")


def _load(root: Path):
    try:
        return load_settings(root)
    except Exception as exc:  # noqa: BLE001 — surface config errors plainly
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _setup_comfy(root: Path):
    """Register the ComfyUI server described in user.yaml so providers (and the
    comfy commands) share one managed lifecycle. Returns the loaded UserConfig."""
    user = load_user(root)
    c = user.comfyui
    launch = None
    if c.managed:
        if c.python or c.main_py or c.base_directory:
            launch = LaunchConfig(python=c.python, main_py=c.main_py, base_directory=c.base_directory)
        else:
            launch = detect_desktop_install()
    register_server(
        ComfyServer(
            c.base_url,
            managed=c.managed,
            launch=launch,
            startup_timeout=c.startup_timeout_s,
            log_path=Path(root) / "logs" / "comfyui.log",
            new_console=c.console,
        )
    )
    return user


@app.command()
def validate(root: Path = typer.Option(Path("."), "--root", help="Project root containing configs/.")):
    """Load and validate every config file."""
    settings = _load(root)
    typer.secho(
        f"OK — {len(settings.models)} models, {len(settings.characters)} characters, "
        f"{len(settings.pipelines)} pipelines",
        fg=typer.colors.GREEN,
    )


@app.command()
def models(root: Path = typer.Option(Path("."), "--root")):
    """List configured models, grouped by what they produce."""
    settings = _load(root)
    for key, model in settings.models.items():
        typer.echo(f"  {key:16} {model.kind:6} {model.provider}")
@app.command("generate-character")
def generate_character(
    key: str = typer.Argument(..., help="Character key (the global card library in configs/stories.db)."),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Generate a full character end-to-end, headless: flesh → base prompt → base image → one outfit
    → expressions + poses → the full emotion sprite set. Saves to configs/characters/<key>.ref.png and
    configs/characters/portraits/<key>/. Needs ComfyUI + a text model reachable."""
    from .server import build_context
    from .server.services.full_gen import generate_full_character

    # Windows consoles default to cp1252, which can't encode phase glyphs (arrows/em dashes) — force
    # UTF-8 so live progress never crashes the run.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    _setup_comfy(root)                       # register the ComfyUI backend (connect/launch)
    try:
        ctx = build_context(root)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"context error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if key not in ctx.base_settings.characters:
        typer.secho(f"unknown character '{key}' (have: {sorted(ctx.base_settings.characters)})",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    def emit(ev):
        if ev.get("type") == "phase":
            typer.secho(f"  > {ev['label']}", fg=typer.colors.CYAN)
        elif ev.get("type") == "item":
            typer.echo(f"    {ev.get('name')}: {(ev.get('text') or '')[:80]}")
    try:
        out = generate_full_character(ctx, key, emit)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"generation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho(f"OK — {out.get('name')}: base + 1 outfit + {out.get('sprites', 0)} sprites", fg=typer.colors.GREEN)


def _resolve_dev(dev_flag: bool | None, root: Path) -> bool:
    """Decide whether to launch in dev mode. Dev is the DEFAULT; opt out with --prod,
    LOOM_MODE=prod, or LOOM_DEV=0. The explicit --dev/--prod flag always wins."""
    import os

    from .server.app import _load_dotenv

    if dev_flag is not None:
        return dev_flag
    _load_dotenv(root)   # so the mode can live in .env alongside the other settings
    mode = os.environ.get("LOOM_MODE", "").strip().lower()
    if mode in ("prod", "production"):
        return False
    if mode in ("dev", "development"):
        return True
    if os.environ.get("LOOM_DEV", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return True   # dev by default


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser on start."),
    root: Path = typer.Option(Path("."), "--root"),
    reload: bool = typer.Option(False, "--reload", help="Auto-restart the backend on code changes (dev)."),
    dev: bool = typer.Option(None, "--dev/--prod",
        help="Dev mode (DEFAULT) serves the live Vite dev server (hot reload — no rebuild) and "
             "points the browser at it. Opt out with --prod, LOOM_MODE=prod, or LOOM_DEV=0."),
    no_vite: bool = typer.Option(False, "--no-vite", help="In dev, don't launch Vite from here "
        "(the backend still runs with reload) — for when Vite runs in its own window."),
):
    """Run the Loom web app and open it in your browser.

    **Dev is the default**: the CLI starts Vite (``npm run dev`` on :5173, which proxies /api
    back here) so frontend edits hot-reload with no rebuild, and the backend auto-reloads too;
    the browser opens the Vite URL. Use **--prod** (or LOOM_MODE=prod / LOOM_DEV=0) to instead
    serve the prebuilt SPA in ``frontend/build`` on ``--port`` (needs ``npm run build``)."""
    import os
    import subprocess
    import threading
    import webbrowser

    import uvicorn

    # ``create_app`` loads .env, but external bind validation happens before the
    # server is constructed so a public listener can never start unauthenticated.
    from .server.app import _load_dotenv
    from .server.security import require_token_for_bind

    _load_dotenv(root)
    try:
        require_token_for_bind(host)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    dev = _resolve_dev(dev, root)
    front_port = 5173
    vite = None

    if dev:
        reload = True   # hot-reload the backend as well
        frontend = (root / "frontend").resolve()
        browse_url = f"http://{host}:{front_port}"
        typer.secho(f"Loom (dev) — UI http://{host}:{front_port} (Vite HMR) · API http://{host}:{port}",
                    fg=typer.colors.GREEN)
        if not no_vite:
            try:
                # shell=True so Windows resolves npm.cmd; Vite logs stream to this console.
                vite = subprocess.Popen("npm run dev", cwd=str(frontend), shell=True)
            except OSError as exc:
                typer.secho(f"Could not start Vite ({exc}); is Node/npm installed and `npm install` run?",
                            fg=typer.colors.RED)
    else:
        browse_url = f"http://{host}:{port}"
        typer.secho(f"Loom running at {browse_url}" + (" (reload)" if reload else ""), fg=typer.colors.GREEN)

    if open_browser:
        # Give Vite a beat to boot before opening the page in dev.
        threading.Timer(3.5 if dev else 1.5, lambda: webbrowser.open(browse_url)).start()

    try:
        if reload:
            # Reload needs an import string + factory so each restart re-imports the app.
            # Watch only the package source; LOOM_ROOT carries --root into the factory.
            os.environ["LOOM_ROOT"] = str(root)
            uvicorn.run("loom.server.app:dev_app", factory=True, host=host, port=port,
                        reload=True, reload_dirs=[str(Path(__file__).resolve().parent)], log_level="info")
        else:
            from .server import create_app
            uvicorn.run(create_app(root), host=host, port=port, log_level="info")
    finally:
        if vite is not None:
            # Tear down the whole Vite process tree (shell=True means node is a child of cmd).
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(vite.pid)],
                                   capture_output=True)
                else:
                    vite.terminate()
            except Exception:
                pass


@app.command("story-serve")
def story_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8001, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the Story UI on start."),
    root: Path = typer.Option(Path("."), "--root"),
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Reload the lean API on code changes."),
    no_vite: bool = typer.Option(False, "--no-vite", help="Do not launch the existing Story frontend dev server."),
    comfy: bool = typer.Option(True, "--comfy/--no-comfy", help="Enable the optional on-demand Krea2/Comfy runner."),
):
    """Run the parallel Story/Architect application without legacy domains.

    The command launches the existing Svelte Story routes through Vite by
    default, pointing their ``/api`` proxy at the lean API on ``--port``.  It
    never starts ComfyUI at boot; ``--comfy`` only permits an on-demand image
    render to connect to or launch the configured local runner.
    """
    import os
    import subprocess
    import threading
    import webbrowser

    import uvicorn

    from .lean import create_lean_app
    from .lean.app import _load_dotenv
    from .server.security import require_token_for_bind

    root = root.resolve()
    _load_dotenv(root)
    try:
        require_token_for_bind(host)
    except RuntimeError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    # A wildcard listener is valid for Uvicorn but not a browser/proxy target.
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    origin_host = f"[{browser_host}]" if ":" in browser_host and not browser_host.startswith("[") else browser_host
    api_origin = f"http://{origin_host}:{port}"
    vite = None
    if no_vite:
        browse_url = f"{api_origin}/docs"
        typer.secho(f"Loom Story API running at {api_origin}", fg=typer.colors.GREEN)
    else:
        frontend = (root / "frontend").resolve()
        browse_url = f"http://{origin_host}:5173"
        typer.secho(
            f"Loom Story — UI {browse_url} · lean API {api_origin}"
            + (" · Krea2/Comfy on demand" if comfy else " · image runner disabled"),
            fg=typer.colors.GREEN,
        )
        try:
            vite_env = {**os.environ, "LOOM_API_ORIGIN": api_origin, "VITE_LEAN_STORY": "1"}
            # shell=True lets Windows resolve npm.cmd; Vite logs stay visible.
            vite = subprocess.Popen("npm run dev", cwd=str(frontend), shell=True, env=vite_env)
        except OSError as exc:
            typer.secho(f"Could not start Vite ({exc}); is Node/npm installed and `npm install` run?",
                        fg=typer.colors.RED)

    if open_browser:
        threading.Timer(3.5 if vite is not None else 1.5, lambda: webbrowser.open(browse_url)).start()

    try:
        os.environ["LOOM_ROOT"] = str(root)
        os.environ["LOOM_LEAN_COMFY"] = "1" if comfy else "0"
        if reload:
            uvicorn.run("loom.lean.app:dev_lean_app", factory=True, host=host, port=port,
                        reload=True, reload_dirs=[str(Path(__file__).resolve().parent)], log_level="info")
        else:
            uvicorn.run(create_lean_app(root, comfy_enabled=comfy), host=host, port=port, log_level="info")
    finally:
        if vite is not None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(vite.pid)], capture_output=True)
                else:
                    vite.terminate()
            except Exception:
                pass


@comfy_app.command("status")
def comfy_status(root: Path = typer.Option(Path("."), "--root")):
    """Report whether the configured ComfyUI backend is reachable."""
    user = _setup_comfy(root)
    server = get_server(user.comfyui.base_url)
    up = server.is_up()
    typer.secho(
        f"{server.base_url}: {'UP' if up else 'down'}  (managed={server.managed})",
        fg=typer.colors.GREEN if up else typer.colors.YELLOW,
    )
    if not up and server.managed:
        launch = server.launch or detect_desktop_install()
        detail = "launch ready" if (launch and launch.complete()) else "NO install detected — set comfyui.python/main_py"
        typer.echo(f"  managed launch: {detail}")


@comfy_app.command("up")
def comfy_up(root: Path = typer.Option(Path("."), "--root")):
    """Ensure ComfyUI is running — connect, or launch headless if managed.

    If Loom launches it, this command stays in the foreground keeping it alive
    until Ctrl+C (a managed instance is tied to the Loom process). If ComfyUI is
    already running, it's left untouched and the command returns immediately.
    """
    import time

    user = _setup_comfy(root)
    server = get_server(user.comfyui.base_url)
    try:
        server.ensure_up()
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"could not bring ComfyUI up: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if not server.we_launched_it:
        typer.secho(f"ComfyUI already running at {server.base_url} — left untouched.", fg=typer.colors.GREEN)
        return

    typer.secho(f"ComfyUI launched headless at {server.base_url}. Press Ctrl+C to stop.", fg=typer.colors.GREEN)
    try:
        while server.is_up():
            time.sleep(2)
        typer.secho("ComfyUI stopped unexpectedly.", fg=typer.colors.RED, err=True)
    except KeyboardInterrupt:
        typer.echo("\nstopping ComfyUI...")
        server.shutdown()


@comfy_app.command("down")
def comfy_down(root: Path = typer.Option(Path("."), "--root")):
    """Stop a ComfyUI instance Loom launched (never affects your own running one)."""
    user = _setup_comfy(root)
    get_server(user.comfyui.base_url).shutdown()
    typer.echo("shutdown requested (only affects a Loom-launched instance)")


if __name__ == "__main__":
    app()
