"""Loom CLI — drive a pipeline from the terminal.

    loom validate                       # load + cross-check all configs
    loom models                         # list configured models (chat vs image)
    loom run chat_with_optional_image -m "hi" -c aria

Phase 1 surface: enough to prove the engine and the chat/image decoupling
end-to-end before any web UI exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from .comfy.server import ComfyServer, LaunchConfig, detect_desktop_install, get_server, register_server
from .config import load_settings, load_user
from .engine import Runner

app = typer.Typer(add_completion=False, help="Declarative chat + image pipelines.")
comfy_app = typer.Typer(help="Manage the ComfyUI image backend (connect or launch headless).")
app.add_typer(comfy_app, name="comfy")


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


@app.command()
def run(
    pipeline: str = typer.Argument(..., help="Pipeline name (filename stem under configs/pipelines)."),
    message: str = typer.Option(..., "--message", "-m", help="The user message."),
    character: str | None = typer.Option(None, "--character", "-c", help="Character/persona key."),
    out_dir: Path = typer.Option(Path("out"), "--out", help="Where generated images are written."),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Run a pipeline and print the reply; save any generated images."""
    settings = _load(root)
    if pipeline not in settings.pipelines:
        typer.secho(f"unknown pipeline '{pipeline}' (have: {sorted(settings.pipelines)})", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    # Register the ComfyUI backend (connect-or-launch) before any image step runs.
    _setup_comfy(root)

    runner = Runner(settings)
    # Stream the chat reply to stdout as it arrives.
    result = runner.run(
        pipeline,
        user_message=message,
        character=character,
        on_delta=lambda chunk: (sys.stdout.write(chunk), sys.stdout.flush()),
    )
    sys.stdout.write("\n")

    # If the reply came from a structured step (no streaming), print it now.
    if result.text and not _streamed(result):
        typer.echo(result.text)

    images = result.images
    if images:
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, data in enumerate(images):
            path = out_dir / f"{pipeline}_{i}.png"
            path.write_bytes(data)
            typer.secho(f"image -> {path}", fg=typer.colors.CYAN)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser on start."),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Run the Loom web app and open it in your browser."""
    import threading
    import webbrowser

    import uvicorn

    from .server import create_app

    application = create_app(root)
    url = f"http://{host}:{port}"
    typer.secho(f"Loom running at {url}", fg=typer.colors.GREEN)
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(application, host=host, port=port, log_level="info")


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


def _streamed(result) -> bool:
    """A structured chat step returns data; a streamed one doesn't."""
    for o in result.outcomes:
        if o.type == "chat":
            return not o.data
    return False


if __name__ == "__main__":
    app()
