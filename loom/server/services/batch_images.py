"""Batch image generation with RunPod auto-scaling.

Provides a batch_generate_image function that:
- Uses local ComfyUI for small batches (< 10 images)
- Spins up RunPod instances for large batches (10+ images)
- Allocates images evenly across available instances
- Spins down excess instances after completion
"""

from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ...runpod import get_image_queue


async def batch_generate_image(
    provider,
    workflow: dict,
    prompts: list[dict[str, Any]],
    ctx=None,
    out_prefix_template: str | None = None,
    latent: tuple[int, int] | None = None
) -> list[bytes | None]:
    """Generate multiple images with auto-scaling.

    Args:
        provider: Base ComfyUIProvider instance
        workflow: ComfyUI workflow JSON (will be copied per instance)
        prompts: List of generation params [{prompt, negative, ...}]
        ctx: AppContext for RunPod config
        out_prefix_template: Template for output prefix (will add index)
        latent: Canvas size (width, height)

    Returns:
        List of generated image bytes (None for failed renders)
    """
    image_count = len(prompts)
    cfg = ctx.runpod_config if ctx else {}

    runpod_on = cfg.get("enabled", True)

    # Serverless endpoint configured and RunPod enabled: fan jobs out to it.
    if runpod_on and cfg.get("serverless_endpoint_id") and cfg.get("api_key"):
        return await _generate_runpod_serverless(provider, workflow, prompts, ctx, out_prefix_template, latent)

    # Small batch, RunPod disabled, or no RunPod at all: use the local single-instance flow.
    if not runpod_on or image_count < 10 or not cfg.get("api_key"):
        return await _generate_single_instance(provider, workflow, prompts, ctx, out_prefix_template, latent)

    # Large batch with only an API key: spin up and manage whole GPU pods.
    return await _generate_runpod_scaled(provider, workflow, prompts, ctx, out_prefix_template, latent)


async def _generate_runpod_serverless(
    provider,
    workflow: dict,
    prompts: list[dict[str, Any]],
    ctx,
    out_prefix_template: str | None = None,
    latent: tuple[int, int] | None = None
) -> list[bytes | None]:
    """Render a batch on a RunPod Serverless ComfyUI endpoint.

    Each prompt is one endpoint job; the endpoint auto-scales workers with the
    queue depth. Jobs run concurrently (capped at max_instances) and each gets a
    fresh-seeded copy of the workflow so repeated prompts don't collapse to one
    identical image.
    """
    from fastapi.concurrency import run_in_threadpool

    from ...providers.runpod_serverless_provider import RunPodServerlessProvider
    from .images import _randomize_seeds

    cfg = ctx.runpod_config
    cap = max(1, int(cfg.get("max_instances", 10)))
    sem = asyncio.Semaphore(cap)

    async def _one(idx: int, p: dict) -> bytes | None:
        async with sem:
            wf = copy.deepcopy(workflow)
            _randomize_seeds(wf)
            sp = RunPodServerlessProvider({
                "endpoint_id": cfg["serverless_endpoint_id"],
                "api_key": cfg["api_key"],
                "workflow": wf,
                "inputs": provider.inputs,
                "output_node": provider.output_node,
                "timeout_s": provider.timeout_s,
            })

            def _run() -> bytes | None:
                res = sp.generate_image(
                    prompt=p.get("prompt", ""),
                    negative_prompt=p.get("negative_prompt"),
                    init_image=p.get("init_image"),
                    out_prefix=f"{out_prefix_template}_{idx}" if out_prefix_template else None,
                    latent=p.get("latent", latent),
                )
                return res.images[0] if res.images else None

            try:
                return await run_in_threadpool(_run)
            except Exception as e:  # noqa: BLE001
                print(f"RunPod serverless job {idx} failed: {e}")
                return None

    return await asyncio.gather(*[_one(i, p) for i, p in enumerate(prompts)])


async def _generate_single_instance(
    provider,
    workflow: dict,
    prompts: list[dict[str, Any]],
    ctx=None,
    out_prefix_template: str | None = None,
    latent: tuple[int, int] | None = None
) -> list[bytes | None]:
    """Generate images on a single ComfyUI instance (existing behavior)."""
    from fastapi.concurrency import run_in_threadpool

    results = [None] * len(prompts)

    def _one(idx: int, p: dict) -> tuple[int, bytes | None]:
        try:
            import copy
            wf = copy.deepcopy(workflow)

            # Inject prompt into workflow
            for node in wf.values():
                if isinstance(node, dict):
                    ins = node.get("inputs")
                    if isinstance(ins, dict):
                        # Substitute {{image}} token or set positive node
                        prompt_val = p.get("prompt", "")
                        neg_val = p.get("negative_prompt")
                        found_token = False

                        for k, v in ins.items():
                            if isinstance(v, str) and "{{image}}" in v:
                                ins[k] = v.replace("{{image}}", prompt_val)
                                found_token = True

                        if not found_token and prompt_val:
                            pos = provider.inputs.get("positive", {})
                            if pos:
                                node_id, field = str(pos["node"]), pos["field"]
                                if node_id in wf:
                                    wf[node_id].setdefault("inputs", {})[field] = prompt_val

                        if neg_val:
                            neg = provider.inputs.get("negative", {})
                            if neg:
                                node_id, field = str(neg["node"]), neg["field"]
                                if node_id in wf:
                                    wf[node_id].setdefault("inputs", {})[field] = neg_val

            provider.workflow = wf
            res = provider.generate_image(
                prompt=p.get("prompt", ""),
                negative_prompt=p.get("negative_prompt"),
                init_image=p.get("init_image"),
                out_prefix=f"{out_prefix_template}_{idx}" if out_prefix_template else None,
                latent=latent
            )
            return (idx, res.images[0] if res.images else None)
        except Exception:  # noqa: BLE001
            return (idx, None)

    # Parallel rendering with limited workers
    with ThreadPoolExecutor(max_workers=3) as ex:
        for idx, png in ex.map(_one, enumerate(prompts)):
            results[idx] = png

    return results


async def _generate_runpod_scaled(
    provider,
    workflow: dict,
    prompts: list[dict[str, Any]],
    ctx,
    out_prefix_template: str | None = None,
    latent: tuple[int, int] | None = None
) -> list[bytes | None]:
    """Generate images using RunPod auto-scaling.

    Spins up additional instances as needed, allocates 10 images per instance,
    and spins down excess after completion.
    """
    from ...providers.comfyui_provider import ComfyUIProvider

    queue = get_image_queue(ctx.runpod_config)

    # Prepare prompts with out_prefix
    enriched_prompts = []
    for idx, p in enumerate(prompts):
        enriched = {**p}
        enriched["out_prefix"] = f"{out_prefix_template}_{idx}" if out_prefix_template else None
        enriched["latent"] = latent
        enriched_prompts.append(enriched)

    # Get workflow options from current provider
    workflow_options = {
        "workflow": workflow,  # Already a dict
        "inputs": provider.inputs,
        "output_node": provider.output_node,
        "timeout_s": provider.timeout_s,
    }

    try:
        results = await queue.process_batch(
            workflow=workflow,
            prompts=enriched_prompts,
            comfyui_provider_class=ComfyUIProvider,
            workflow_options=workflow_options
        )

        # Spin down excess instances
        await queue.scale_down()

        return results
    except Exception as e:  # noqa: BLE001
        # Fallback to single instance on error
        print(f"RunPod scaling failed, falling back to single instance: {e}")
        return await _generate_single_instance(provider, workflow, prompts, ctx, out_prefix_template, latent)
