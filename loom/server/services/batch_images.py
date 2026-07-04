"""Image batch generation — the ONE fan-out every multi-image flow goes through.

``render_batch`` is the single chokepoint: given a base provider and a list of prompts it
renders them concurrently, reports per-image progress, and honours cancellation —
propagated into in-flight RunPod jobs so they stop billing. It picks the backend:

  * a RunPod **serverless** endpoint is configured  → one endpoint job per prompt, fanned
    out up to ``max_instances``; the endpoint auto-scales workers to the queue depth.
  * otherwise (**local** ComfyUI)                   → a small thread pool against the one
    local GPU (ComfyUI serialises on the GPU anyway).

It runs **synchronously**: every caller is a ``work(emit, cancelled)`` body that the job hub
already runs in a worker thread (see ``services.jobs_util._start_stream_job``), so there is no
asyncio bridge. Progress and cancellation flow through the ``on_progress`` and ``cancel``
callbacks, which the caller wires to its BaseJob (``emit`` and ``cancelled``). Each task renders
from its own fresh-seeded copy of the workflow, so repeated prompts don't collapse to one image
and parallel tasks never stomp shared state.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

ProgressCb = Callable[[int, int], None]   # (done, total)
CancelCb = Callable[[], bool]


def render_batch(
    provider,
    prompts: list[dict[str, Any]],
    *,
    ctx=None,
    out_prefix_template: str | None = None,
    latent: tuple[int, int] | None = None,
    on_progress: ProgressCb | None = None,
    cancel: CancelCb | None = None,
    seed: int | None = None,
) -> list[bytes | None]:
    """Render ``prompts`` concurrently; return one PNG (bytes) per prompt, in order
    (``None`` for a failed or cancelled render).

    Each prompt dict may carry: ``prompt``, ``negative_prompt``, ``init_image``, ``latent``.
    ``on_progress(done, total)`` fires as each image finishes; ``cancel()`` is polled and
    aborts pending + in-flight jobs.
    """
    total = len(prompts)
    if total == 0:
        return []

    cfg = (getattr(ctx, "runpod_config", None) or {}) if ctx else {}
    serverless = bool(
        cfg.get("enabled", True) and cfg.get("serverless_endpoint_id") and cfg.get("api_key")
    )

    if serverless:
        cap = max(1, int(cfg.get("max_instances", 10)))
        make = _serverless_factory(provider, cfg, seed)
    else:
        cap = 2   # one local GPU; deepcopy-per-task just avoids shared-workflow races
        make = _local_factory(provider, seed)

    return _fan_out(prompts, make, cap, out_prefix_template, latent, on_progress, cancel)


# --------------------------------------------------------------------------- #
# Per-task provider factories — each returns a provider with a FRESH-seeded workflow.
# --------------------------------------------------------------------------- #
def _seed_fn(seed):
    from .images import _randomize_seeds, _set_seeds
    return (lambda wf: _set_seeds(wf, seed)) if seed is not None else _randomize_seeds


def _serverless_factory(provider, cfg, seed=None) -> Callable[[], Any]:
    from ...providers.runpod_serverless_provider import RunPodServerlessProvider
    seed_wf = _seed_fn(seed)

    def make() -> Any:
        wf = copy.deepcopy(provider.workflow)
        seed_wf(wf)
        return RunPodServerlessProvider({
            "endpoint_id": cfg["serverless_endpoint_id"],
            "api_key": cfg["api_key"],
            "workflow": wf,
            "inputs": provider.inputs,
            "output_node": provider.output_node,
            "output_variant": getattr(provider, "output_variant", None),
            "timeout_s": provider.timeout_s,
        })

    return make


def _local_factory(provider, seed=None) -> Callable[[], Any]:
    seed_wf = _seed_fn(seed)

    def make() -> Any:
        # deepcopy so parallel tasks each own their workflow (the provider mutates it per render)
        clone = copy.deepcopy(provider)
        seed_wf(clone.workflow)
        return clone

    return make


def _fan_out(
    prompts: list[dict[str, Any]],
    make_provider: Callable[[], Any],
    cap: int,
    out_prefix_template: str | None,
    latent: tuple[int, int] | None,
    on_progress: ProgressCb | None,
    cancel: CancelCb | None,
) -> list[bytes | None]:
    total = len(prompts)
    results: list[bytes | None] = [None] * total

    def _one(idx: int, p: dict) -> tuple[int, bytes | None]:
        if cancel and cancel():
            return idx, None
        prov = make_provider()
        wc = p.get("wildcard")            # per-item FaceDetailer face-prompt (detailer workflows only)
        if wc and hasattr(prov, "workflow"):
            for n in prov.workflow.values():
                if isinstance(n, dict) and n.get("class_type") == "FaceDetailer":
                    n.setdefault("inputs", {})["wildcard"] = wc
        res = prov.generate_image(
            prompt=p.get("prompt", ""),
            negative_prompt=p.get("negative_prompt"),
            init_image=p.get("init_image"),
            out_prefix=f"{out_prefix_template}_{idx}" if out_prefix_template else None,
            latent=p.get("latent", latent),
            cancel=cancel,
        )
        return idx, (res.images[0] if res.images else None)

    done = 0
    with ThreadPoolExecutor(max_workers=cap) as ex:
        futures = [ex.submit(_one, i, p) for i, p in enumerate(prompts)]
        for fut in as_completed(futures):
            try:
                idx, png = fut.result()
                results[idx] = png
            except Exception as exc:  # noqa: BLE001
                print(f"render_batch: a render failed: {exc}")
            done += 1
            if on_progress:
                on_progress(done, total)

    return results
