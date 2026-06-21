"""Triage render job — runs a sequence of single-LoRA render cells through the job hub.

Used by the Grid Tester (checkpoints × LoRAs) and the Classify grid so every render
is visible in the Activity panel, errors are logged, and the whole batch is cancellable.

Each cell dict supports two render paths:

  Minimal graph (has ``checkpoint``):
    key, checkpoint, lora, weight, prompt, negative, steps, seed, cache

  Model-based (no ``checkpoint``; injects into a real workflow):
    key, model, loras, checkpoint_override, prompt, negative, cache

Events emitted (all carry ``key``):
  {type:"cell_start",    key, index, total}
  {type:"cell_progress", key, value, max}
  {type:"cell_image",    key, images:[dataURL, ...]}
  {type:"cell_error",    key, error}
  {type:"done",          status}
"""

from __future__ import annotations

import asyncio
import random

from .jobhub import REGISTRY, BaseJob


def _minimal_graph(ckpt: str, lora: str, weight: float,
                   prompt: str, neg: str, steps: int, seed: int) -> dict:
    return {
        "4":  {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": ckpt}},
        "10": {"class_type": "LoraLoader",
               "inputs": {"lora_name": lora, "strength_model": weight, "strength_clip": weight,
                          "model": ["4", 0], "clip": ["4", 1]}},
        "6":  {"class_type": "CLIPTextEncode",
               "inputs": {"text": prompt, "clip": ["10", 1]}},
        "7":  {"class_type": "CLIPTextEncode",
               "inputs": {"text": neg, "clip": ["10", 1]}},
        "5":  {"class_type": "EmptyLatentImage",
               "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "3":  {"class_type": "KSampler",
               "inputs": {"seed": seed, "steps": steps, "cfg": 6.0,
                          "sampler_name": "euler_ancestral", "scheduler": "normal",
                          "denoise": 1.0, "model": ["10", 0],
                          "positive": ["6", 0], "negative": ["7", 0],
                          "latent_image": ["5", 0]}},
        "8":  {"class_type": "VAEDecode",
               "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9":  {"class_type": "SaveImage",
               "inputs": {"filename_prefix": "triage", "images": ["8", 0]}},
    }


class TriageJob(BaseJob):
    def __init__(self, cells: list[dict], base_url: str, ctx) -> None:
        self._cells = cells
        self._base_url = base_url
        self._ctx = ctx
        n = len(cells)
        super().__init__(
            "triage", "LoRA render",
            label=f"{n} render{'s' if n != 1 else ''}",
            unit="renders", total=n,
        )

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def on_cancel(self) -> None:
        import httpx
        from fastapi.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(
                lambda: httpx.post(self._base_url.rstrip("/") + "/interrupt", timeout=5))
        except Exception:  # noqa: BLE001
            pass

    async def _run(self) -> None:
        from ..comfy.generate import stream_generate
        from ..comfy.lora import randomize_seeds
        from ..comfy.stack import inject_models
        from ..providers.comfyui_provider import ComfyUIProvider
        from .services import triage_cache as tc

        for i, cell in enumerate(self._cells):
            if self._cancel.is_set():
                break

            key = cell.get("key") or str(i)
            self._emit({"type": "cell_start", "key": key, "index": i, "total": len(self._cells)})

            try:
                seed = int(cell.get("seed") or random.randint(0, 2 ** 31))

                if cell.get("checkpoint"):
                    # Minimal txt2img graph — fastest, no custom nodes.
                    graph = _minimal_graph(
                        ckpt=cell["checkpoint"],
                        lora=cell.get("lora") or "",
                        weight=float(cell.get("weight", 0.8)),
                        prompt=cell.get("prompt") or "1girl, solo, standing, simple background, looking at viewer",
                        neg=cell.get("negative") or "lowres, worst quality, bad anatomy, text, watermark",
                        steps=int(cell.get("steps", 22)),
                        seed=seed,
                    )
                    base_url = self._base_url
                    out_node = "9"
                else:
                    # Inject into a real workflow (supports split-loaders, Flux, etc.).
                    model_key = cell.get("model") or "anima"
                    md = self._ctx.base_settings.models.get(model_key)
                    if md is None or md.kind != "image":
                        self._emit({"type": "cell_error", "key": key,
                                    "error": f"no image model '{model_key}'"})
                        self.done = i + 1
                        continue
                    opts = dict(md.options)
                    conn = self._ctx.store.active("image")
                    if conn and conn.base_url:
                        opts["base_url"] = conn.base_url
                    provider = ComfyUIProvider(opts)
                    loras = list(cell.get("loras") or [])
                    if cell.get("lora") and not loras:
                        loras = [{"name": cell["lora"], "weight": float(cell.get("weight", 1.0))}]
                    provider.workflow = inject_models(
                        provider.workflow, cell.get("checkpoint_override") or None, loras)
                    prompt = cell.get("prompt") or "masterpiece, best quality, 1girl"
                    graph, out_node = provider._inject(prompt, cell.get("negative"))
                    graph = randomize_seeds(graph)
                    base_url = provider.base_url

                async for ev in stream_generate(base_url, graph, out_node, 240):
                    if self._cancel.is_set():
                        break
                    t = ev.get("type")
                    if t == "progress":
                        self._emit({"type": "cell_progress", "key": key,
                                    "value": ev.get("value"), "max": ev.get("max")})
                    elif t == "image":
                        images = ev.get("images") or []
                        self._emit({"type": "cell_image", "key": key, "images": images})
                        cache = cell.get("cache")
                        if cache and images:
                            try:
                                tc.save_render(
                                    self._ctx.root,
                                    cache.get("scope", ""), cache.get("lora", ""),
                                    images[0],
                                    prompt=cache.get("prompt", ""),
                                    weight=cache.get("weight"),
                                )
                            except Exception:  # noqa: BLE001
                                pass
                    elif t == "error":
                        self._emit({"type": "cell_error", "key": key,
                                    "error": ev.get("error") or "ComfyUI error"})

                self.done = i + 1
            except Exception as exc:  # noqa: BLE001
                self._emit({"type": "cell_error", "key": key, "error": str(exc)})
                self.done = i + 1

        self.status = "cancelled" if self._cancel.is_set() else "done"
        self._emit({"type": "done", "status": self.status})


def current() -> TriageJob | None:
    return REGISTRY.latest("triage")  # type: ignore[return-value]
