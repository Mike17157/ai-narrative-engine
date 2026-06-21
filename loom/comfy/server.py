"""Managed ComfyUI lifecycle — connect to a running server, or launch one headless.

This is what makes "you never open ComfyUI's UI" real. Loom either:

  * connects to a ComfyUI already serving on the configured URL, or
  * (managed mode) launches ComfyUI headless itself — the bundled `main.py` run
    with the base-dir `.venv` Python and `--base-directory` pointed at the user's
    own ComfyUI data folder — waits for the HTTP API, and shuts it down on exit.

We only ever stop a process *we* started, so an already-running desktop instance
is never killed. For RunPod, set `managed: false` and point `base_url` at the pod
— the same connect path applies, no launch.
"""

from __future__ import annotations

import atexit
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx


@dataclass
class LaunchConfig:
    python: str | None = None           # interpreter to run main.py with
    main_py: str | None = None          # path to ComfyUI's main.py
    base_directory: str | None = None   # ComfyUI base dir (models/custom_nodes/output)
    front_end_root: str | None = None   # static frontend dir (ComfyUI Desktop ships
                                        # the frontend as a folder, not a pip pkg)
    listen: str = "127.0.0.1"
    extra_args: list[str] = field(default_factory=list)

    def complete(self) -> bool:
        return bool(self.python and self.main_py)


def detect_desktop_install() -> LaunchConfig | None:
    """Best-effort autodetect of a Windows ComfyUI Desktop install.

    Code lives in the app bundle; the base dir (models/custom_nodes/output) and
    its `.venv` live under the user's Documents. Returns None if not found — the
    caller falls back to connect-only.
    """
    import os

    home = Path.home()
    main_candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        main_candidates.append(Path(local) / "Programs" / "ComfyUI" / "resources" / "ComfyUI" / "main.py")
    main_candidates += [home / "ComfyUI" / "main.py", home / "Documents" / "ComfyUI" / "main.py"]

    base_dir = home / "Documents" / "ComfyUI"
    python_candidates = [
        base_dir / ".venv" / "Scripts" / "python.exe",
        base_dir / ".venv" / "bin" / "python",
        base_dir / "venv" / "Scripts" / "python.exe",
    ]

    main_py = next((p for p in main_candidates if p.is_file()), None)
    python = next((p for p in python_candidates if p.is_file()), None)
    if not (main_py and python):
        return None

    # ComfyUI Desktop ships the frontend as a static folder next to the code and
    # passes it via --front-end-root; the base-dir .venv has no frontend pip pkg,
    # so headless launch needs this or it exits at startup.
    fe = main_py.parent / "web_custom_versions" / "desktop_app"
    return LaunchConfig(
        python=str(python),
        main_py=str(main_py),
        base_directory=str(base_dir) if base_dir.is_dir() else None,
        front_end_root=str(fe) if fe.is_dir() else None,
        # ComfyUI Desktop's DynamicVRAM/aimdo weight-offloader (cu12.8+ builds) faults
        # mid-sample on newer GPUs — observed as "Fault failed: 2" / access violations
        # rendering the Anima DiT on an RTX 5070 (sm_120). Disabling it falls back to the
        # classic ModelPatcher (models fit fine without offload here). Recent ComfyUI only;
        # this is the Desktop autodetect path so the arg is always supported.
        extra_args=["--disable-dynamic-vram"],
    )


def _port_from_url(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 8188


class ComfyServer:
    def __init__(
        self,
        base_url: str,
        *,
        managed: bool = False,
        launch: LaunchConfig | None = None,
        startup_timeout: float = 180,
        health_path: str = "/system_stats",
        log_path: str | Path | None = None,
        new_console: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.managed = managed
        self.launch = launch
        self.startup_timeout = startup_timeout
        self.health_path = health_path
        # Where a Loom-launched ComfyUI's output goes. A log file (default) keeps
        # auto-launch debuggable; new_console pops a visible window instead.
        self.log_path = Path(log_path) if log_path else None
        self.new_console = new_console
        self._proc: subprocess.Popen | None = None
        self._port = _port_from_url(self.base_url)

    # -- status -----------------------------------------------------------
    def is_up(self, timeout: float = 2.0) -> bool:
        try:
            resp = httpx.get(self.base_url + self.health_path, timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

    @property
    def we_launched_it(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- lifecycle --------------------------------------------------------
    def ensure_up(self) -> None:
        """Guarantee the server is reachable, launching it if allowed."""
        if self.is_up():
            return
        if not self.managed:
            raise RuntimeError(
                f"ComfyUI is not reachable at {self.base_url} and managed launch is off. "
                f"Start ComfyUI, or set comfyui.managed: true in user.yaml."
            )
        self._start()

    def _start(self) -> None:
        launch = self.launch or detect_desktop_install()
        if not launch or not launch.complete():
            raise RuntimeError(
                "managed launch requested but no ComfyUI install was detected. "
                "Set comfyui.python and comfyui.main_py in user.yaml."
            )
        cmd = [launch.python, launch.main_py, "--listen", launch.listen, "--port", str(self._port)]
        if launch.base_directory:
            # ComfyUI's prestartup scans <base>/custom_nodes and crashes if it's
            # absent (e.g. a fresh base dir on a new pod). Seed it.
            Path(launch.base_directory, "custom_nodes").mkdir(parents=True, exist_ok=True)
            cmd += ["--base-directory", launch.base_directory]
        if launch.front_end_root:
            cmd += ["--front-end-root", launch.front_end_root]
        if "--log-stdout" not in launch.extra_args:
            cmd += ["--log-stdout"]
        cmd += launch.extra_args

        popen_kwargs: dict = {"cwd": str(Path(launch.main_py).parent)}
        log_handle = None
        if self.new_console and hasattr(subprocess, "CREATE_NEW_CONSOLE"):
            # Visible window; output goes there, not to a file.
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        else:
            # Capture to a log file so a headless launch is always debuggable.
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log_handle = open(self.log_path, "w", encoding="utf-8")
                popen_kwargs["stdout"] = log_handle
                popen_kwargs["stderr"] = subprocess.STDOUT
            else:
                popen_kwargs["stdout"] = subprocess.DEVNULL
                popen_kwargs["stderr"] = subprocess.STDOUT

        self._proc = subprocess.Popen(cmd, **popen_kwargs)
        self._log_handle = log_handle
        # Make sure we don't leave an orphaned server if the process exits.
        atexit.register(self.shutdown)
        self._wait_until_up()

    def _wait_until_up(self) -> None:
        hint = f" (see {self.log_path})" if self.log_path else ""
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"ComfyUI exited during startup (code {self._proc.returncode}){hint}"
                )
            if self.is_up():
                return
            time.sleep(1.0)
        self.shutdown()
        raise TimeoutError(f"ComfyUI did not become ready within {self.startup_timeout:.0f}s{hint}")

    def shutdown(self) -> None:
        """Stop ComfyUI — but only if *we* started it."""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        handle = getattr(self, "_log_handle", None)
        if handle:
            handle.close()
            self._log_handle = None

    def __enter__(self) -> "ComfyServer":
        self.ensure_up()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()


# --------------------------------------------------------------------------- #
# Process-wide registry — one server per base_url, shared across image models.
# --------------------------------------------------------------------------- #
_SERVERS: dict[str, ComfyServer] = {}


def register_server(server: ComfyServer) -> None:
    _SERVERS[server.base_url.rstrip("/")] = server


def get_server(base_url: str) -> ComfyServer:
    """Fetch the server for a URL, creating a connect-only one if unregistered.

    The app entrypoint (CLI/server) registers a managed server from user.yaml
    before running; providers that just need the URL up call this and get either
    that managed instance or a plain connect-only one.
    """
    key = base_url.rstrip("/")
    if key not in _SERVERS:
        _SERVERS[key] = ComfyServer(key, managed=False)
    return _SERVERS[key]


def shutdown_all() -> None:
    for server in _SERVERS.values():
        server.shutdown()
