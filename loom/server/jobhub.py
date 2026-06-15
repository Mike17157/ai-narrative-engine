"""The job hub — one registry every server-side workload routes through.

Long-running work (LoRA image batches, training, trainer setup, dataset
captioning) all runs as a BaseJob: a background task that retains its events so a
client can replay/reconnect, can be cancelled, and — by virtue of registering
here — automatically appears in the Activity panel with progress. Adding a new
kind of workload means subclassing BaseJob (or running a producer); it shows up
in Activity, gets a stream and a cancel, for free.

Single-user app, so the hub is a simple in-process registry: jobs keyed by id,
with helpers to find the running/latest job of a category. Finished jobs linger
briefly (so you can still see "✓ done") then get pruned.
"""

from __future__ import annotations

import asyncio
import json


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"


def _kill_proc_tree(pid: int) -> None:
    """Force-kill a process AND its descendants. kohya runs via `accelerate`,
    which spawns a child training process; terminating only the parent leaves
    that child alive (holding the GPU + the stdout pipe), so the job never
    finalizes and lingers as a 'cancelling' zombie. Kill the whole tree instead.
    Fire-and-forget so we never block the event loop."""
    import os
    import signal
    import subprocess
    import sys

    try:
        if sys.platform == "win32":
            subprocess.Popen(["taskkill", "/F", "/T", "/PID", str(pid)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                os.kill(pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass


class BaseJob:
    def __init__(self, category: str, kind: str, *, label: str | None = None, unit: str = "",
                 total: int = 0, screen: str = "images", cancellable: bool = True, log_cap: int = 0):
        self.id = REGISTRY.next_id(category)
        self.category = category
        self.kind = kind
        self.label = label
        self.unit = unit
        self.total = total
        self.done = 0
        self.screen = screen
        self.cancellable = cancellable
        self._log_cap = log_cap
        self.status = "running"  # running | done | cancelled | error
        self.events: list[dict] = []
        self._subs: list[asyncio.Queue] = []
        self._cancel = asyncio.Event()
        self.proc: asyncio.subprocess.Process | None = None  # set by subprocess jobs
        self._task: asyncio.Task | None = None
        REGISTRY.add(self)

    # --- emit / subscribe -------------------------------------------------
    def _emit(self, ev: dict) -> None:
        self.events.append(ev)
        if self._log_cap and ev.get("type") == "log":
            logs = sum(1 for e in self.events if e.get("type") == "log")
            if logs > self._log_cap:
                for i, e in enumerate(self.events):
                    if e.get("type") == "log":
                        del self.events[i]
                        break
        for q in self._subs:
            q.put_nowait(ev)

    # --- cancel -----------------------------------------------------------
    def cancel(self) -> None:
        self._cancel.set()
        if self.proc and self.proc.returncode is None:
            _kill_proc_tree(self.proc.pid)  # tree-kill so the run actually stops + finalizes

    async def on_cancel(self) -> None:
        """Optional async hook fired after cancel() (e.g. ComfyUI /interrupt)."""

    @property
    def cancelling(self) -> bool:
        return self._cancel.is_set()

    # --- consume ----------------------------------------------------------
    async def stream(self):
        """Replay retained events, then live ones until terminal. Snapshot +
        subscribe with no await between them, so no event is lost or dropped."""
        q: asyncio.Queue = asyncio.Queue()
        backlog = list(self.events)
        self._subs.append(q)
        try:
            for ev in backlog:
                yield sse(ev)
                if ev.get("type") == "done":
                    return
            while True:
                ev = await q.get()
                yield sse(ev)
                if ev.get("type") == "done":
                    return
        finally:
            if q in self._subs:
                self._subs.remove(q)

    def snapshot(self) -> dict:
        return {"id": self.id, "category": self.category, "kind": self.kind, "label": self.label,
                "status": self.status, "done": self.done, "total": self.total, "unit": self.unit,
                "screen": self.screen, "cancellable": self.cancellable, "cancelling": self.cancelling,
                "cancel": f"/jobs/{self.id}/cancel"}


class Registry:
    def __init__(self):
        self._jobs: dict[str, BaseJob] = {}
        self._counters: dict[str, int] = {}

    def next_id(self, category: str) -> str:
        self._counters[category] = self._counters.get(category, 0) + 1
        return f"{category}-{self._counters[category]}"

    def add(self, job: BaseJob) -> None:
        self._jobs[job.id] = job
        self._prune()

    def get(self, job_id: str) -> BaseJob | None:
        return self._jobs.get(job_id)

    def all(self) -> list[BaseJob]:
        return list(self._jobs.values())

    def running_in(self, category: str) -> BaseJob | None:
        return next((j for j in self._jobs.values()
                     if j.category == category and j.status == "running"), None)

    def latest(self, category: str) -> BaseJob | None:
        cat = [j for j in self._jobs.values() if j.category == category]
        return cat[-1] if cat else None

    def _prune(self, keep_finished: int = 12) -> None:
        finished = [j for j in self._jobs.values() if j.status != "running"]
        for j in finished[: max(0, len(finished) - keep_finished)]:
            self._jobs.pop(j.id, None)


REGISTRY = Registry()
