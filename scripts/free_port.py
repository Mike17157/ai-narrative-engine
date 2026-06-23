#!/usr/bin/env python
"""Free a TCP port by terminating whatever is LISTENING on it.

So a fresh dev server can always bind cleanly and we never end up debugging a *stale
orphan* left behind by a previous Claude-preview run (the "duplicate server" footgun:
preview reuses a ghost on the port and your changes never show). Cross-platform
(Windows / macOS / Linux); a no-op when the port is already free.

    python scripts/free_port.py <port> [<port> ...]
    from scripts.free_port import free_port; free_port(8000)
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys


def _pids_on_port(port: int) -> set[int]:
    """PIDs holding a LISTEN socket on `port`. Best-effort; never raises."""
    pids: set[int] = set()
    needle = f":{port}"
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True,
            ).stdout
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            return pids
        for line in out.splitlines():
            parts = line.split()
            # cols: Proto  Local Address  Foreign Address  State  PID
            if (len(parts) >= 5 and parts[0].upper().startswith("TCP")
                    and parts[3].upper() == "LISTENING"
                    and parts[1].endswith(needle)):
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    else:
        try:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True,
            ).stdout
        except FileNotFoundError:
            out = ""
        for tok in out.split():
            try:
                pids.add(int(tok))
            except ValueError:
                pass
    pids.discard(0)
    pids.discard(os.getpid())  # never shoot ourselves
    return pids


def _kill(pid: int) -> None:
    try:
        if os.name == "nt":
            # /T also reaps the child tree (npm -> node -> vite, etc.)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, text=True)
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 — best-effort
        pass


def free_port(port: int) -> list[int]:
    """Terminate every listener on `port`. Returns the PIDs killed (possibly empty)."""
    killed: list[int] = []
    for pid in _pids_on_port(port):
        _kill(pid)
        killed.append(pid)
    return killed


def main(argv: list[str]) -> int:
    ports = [int(a) for a in argv if a.strip().isdigit()]
    if not ports:
        print("usage: free_port.py <port> [<port> ...]", file=sys.stderr)
        return 2
    for p in ports:
        killed = free_port(p)
        print(f"[free_port] {p}: {'terminated ' + str(killed) if killed else 'already free'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
