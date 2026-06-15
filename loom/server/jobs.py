"""LoRA image batch job — runs through the shared job hub.

The dataset batch can take minutes and must outlive the request that started it
(the user navigates away; ComfyUI keeps rendering). It retains its events so a
client can replay/reconnect, and it shows up in Activity like any other workload.
"""

from __future__ import annotations

import asyncio

from ..comfy.lora import stream_batch
from .jobhub import REGISTRY, BaseJob


class LoraJob(BaseJob):
    def __init__(self, base_url, provider, prompts, variations, output_node, timeout):
        self.base_url = base_url
        self._provider = provider
        self._output_node = output_node
        self._timeout = timeout
        self.prompts = list(prompts)
        self.variations = int(variations)
        super().__init__("lora", "LoRA image batch", unit="images",
                         total=len(self.prompts) * self.variations)
        # Opening event: replayed first so a reconnecting client rebuilds the grid.
        self._emit({"type": "start", "prompts": self.prompts,
                    "variations": self.variations, "total": self.total})

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def on_cancel(self) -> None:
        # Stop the in-flight ComfyUI render promptly.
        import httpx
        from fastapi.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(
                lambda: httpx.post(self.base_url.rstrip("/") + "/interrupt", timeout=5))
        except Exception:  # noqa: BLE001
            pass

    async def _run(self) -> None:
        agen = stream_batch(self.base_url, self._provider, self.prompts,
                            self.variations, self._output_node, self._timeout)
        try:
            async for ev in agen:
                if ev.get("type") == "count":
                    self.done = ev.get("done", self.done)
                if ev.get("type") != "done":   # we emit our own terminal event
                    self._emit(ev)
                if self._cancel.is_set():
                    await agen.aclose()
                    break
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self._emit({"type": "done", "status": "error", "error": str(exc)})
            return
        self.status = "cancelled" if self._cancel.is_set() else "done"
        self._emit({"type": "done", "status": self.status})


def current() -> LoraJob | None:
    return REGISTRY.latest("lora")  # type: ignore[return-value]
