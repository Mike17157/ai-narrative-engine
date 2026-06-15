"""LoRA training run (kohya) — runs through the shared job hub.

Wraps a training (or trainer-setup) subprocess: streams its stdout (log + tqdm
progress) to any connected client, retains a bounded history for reconnect, and
shows up in Activity. Cancel terminates the process.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from .jobhub import REGISTRY, BaseJob


def _utf8_env() -> dict[str, str]:
    """Child env that forces UTF-8 stdout.

    On Windows a piped child's sys.stdout defaults to the locale codec (cp1252),
    so kohya/accelerate crash with UnicodeEncodeError the moment they print a
    non-Latin-1 glyph (model names, progress bars). PYTHONUTF8/PYTHONIOENCODING
    make the child emit UTF-8, which we decode on our side.
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env

# tqdm progress lines look like:  steps:   4%|▍ | 10/250 [00:05<02:00,  1.9it/s]
_PROG = re.compile(r"(\d+)\s*/\s*(\d+)\s*\[")


class TrainJob(BaseJob):
    def __init__(self, cmd: list[str], cwd: str, output_name: str, output_path: str):
        self.cmd = cmd
        self.cwd = cwd
        self.output_name = output_name
        self.output_path = output_path
        setup = output_name == "trainer setup"
        super().__init__("train", "Trainer setup" if setup else "LoRA training",
                         label=None if setup else output_name, unit="steps", log_cap=600)
        self._emit({"type": "start", "cmd": " ".join(cmd), "output_name": output_name})

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.cmd, cwd=self.cwd, env=_utf8_env(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self._emit({"type": "log", "line": f"failed to launch trainer: {exc}"})
            self._emit({"type": "done", "status": "error", "code": -1})
            return

        assert self.proc.stdout is not None
        # tqdm draws its bar with carriage returns (\r) and only emits \n at the
        # end of an epoch — so readline() would stall and the step counter would
        # never move. Read in chunks and split on BOTH \r and \n: \r segments are
        # progress refreshes (update done/total, don't spam the log); \n segments
        # are real log lines.
        buf = b""
        last_done = -1
        while True:
            chunk = await self.proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            while True:
                nl, cr = buf.find(b"\n"), buf.find(b"\r")
                if nl == -1 and cr == -1:
                    break
                if cr != -1 and (nl == -1 or cr < nl):
                    idx, term = cr, "\r"
                else:
                    idx, term = nl, "\n"
                seg, buf = buf[:idx], buf[idx + 1:]
                if term == "\r" and buf[:1] == b"\n":  # \r\n is one real newline
                    buf, term = buf[1:], "\n"
                text = seg.decode("utf-8", "replace").rstrip()
                if not text:
                    continue
                m = _PROG.search(text)
                if m:
                    self.done, self.total = int(m.group(1)), int(m.group(2))
                    if self.done != last_done:
                        last_done = self.done
                        self._emit({"type": "progress", "done": self.done, "total": self.total})
                if term == "\n":
                    self._emit({"type": "log", "line": text})
        if buf.strip():
            self._emit({"type": "log", "line": buf.decode("utf-8", "replace").rstrip()})

        code = await self.proc.wait()
        if self._cancel.is_set():
            self.status = "cancelled"
        elif code == 0:
            self.status = "done"
        else:
            self.status = "error"
        artifact = self.output_path if (self.status == "done" and Path(self.output_path).is_file()) else None
        self._emit({"type": "done", "status": self.status, "code": code, "artifact": artifact})


def current() -> TrainJob | None:
    return REGISTRY.latest("train")  # type: ignore[return-value]
