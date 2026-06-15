"""Dataset captioning job (vision model or WD14) — runs through the shared hub.

Driven by a producer (an async generator that yields events): VLM captions each
image, or the WD14 subprocess streams tag lines. Retains events for reconnect,
shows in Activity, cancellable.
"""

from __future__ import annotations

import asyncio

from .jobhub import REGISTRY, BaseJob


class CaptionJob(BaseJob):
    def __init__(self, dataset: str, method: str, total: int):
        self.dataset = dataset
        self.method = method            # 'vlm' | 'wd14'
        kind = "Captioning (WD14)" if method == "wd14" else "Captioning (vision)"
        super().__init__("caption", kind, label=dataset, unit="images", total=total, log_cap=400)
        self._emit({"type": "start", "dataset": dataset, "method": method, "total": total})

    def start(self, producer) -> None:
        """producer(job) is an async generator yielding events."""
        self._task = asyncio.create_task(self._run(producer))

    async def _run(self, producer) -> None:
        had_fatal = False
        try:
            async for ev in producer(self):
                t = ev.get("type")
                if t == "fatal":
                    had_fatal = True
                if t in ("caption", "error") and ev.get("file") and ev.get("index") is not None:
                    self.done = ev["index"] + 1
                if t != "done":
                    self._emit(ev)
                if self._cancel.is_set():
                    break
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self._emit({"type": "error", "error": str(exc)})
            self._emit({"type": "done", "status": "error"})
            return
        self.status = ("cancelled" if self._cancel.is_set()
                       else "error" if had_fatal else "done")
        self._emit({"type": "done", "status": self.status})

    def snapshot(self) -> dict:
        return {**super().snapshot(), "dataset": self.dataset, "method": self.method}


def current() -> CaptionJob | None:
    return REGISTRY.latest("caption")  # type: ignore[return-value]
