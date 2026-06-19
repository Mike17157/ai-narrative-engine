"""Batch image generation for LoRA dataset building.

Generates N variations per prompt (each with a fresh random seed so they differ),
streaming each finished image as a {row, col} event so the UI can fill a grid and
let the user pick the winner of each row.
"""

from __future__ import annotations

import random
from typing import Any, AsyncIterator

from .generate import stream_generate

_SEED_MAX = 2**31 - 1


def randomize_seeds(graph: dict) -> dict:
    """Set every literal seed in the graph to a fresh random value so repeated
    runs of the same prompt produce different images."""
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        ins = node.get("inputs", {}) or {}
        for key in ("seed", "noise_seed"):
            if key in ins and not isinstance(ins[key], list):
                ins[key] = random.randint(0, _SEED_MAX)
        # PrimitiveInt seed nodes (wired into KSampler.seed)
        if node.get("class_type") == "PrimitiveInt" and not isinstance(ins.get("value"), list):
            title = (node.get("_meta", {}) or {}).get("title", "").lower()
            if "seed" in title:
                ins["value"] = random.randint(0, _SEED_MAX)
    return graph


async def stream_batch(
    base_url: str, provider, prompts: list[str], variations: int,
    output_node: str | None, timeout: float,
) -> AsyncIterator[dict[str, Any]]:
    total = len(prompts) * variations
    done = 0
    for row, prompt in enumerate(prompts):
        for col in range(variations):
            graph, _out = provider._inject(prompt, None)
            graph = randomize_seeds(graph)
            got = False
            async for ev in stream_generate(base_url, graph, output_node, timeout):
                if ev["type"] == "progress":
                    yield {"type": "progress", "row": row, "col": col, "value": ev["value"], "max": ev["max"]}
                elif ev["type"] == "image":
                    got = True
                    src = ev["images"][0] if ev["images"] else None
                    yield {"type": "image", "row": row, "col": col, "src": src}
                elif ev["type"] == "error":
                    yield {"type": "error", "row": row, "col": col, "error": ev["error"]}
            done += 1
            yield {"type": "count", "done": done, "total": total}
            if not got:
                yield {"type": "error", "row": row, "col": col, "error": "no image returned"}
    yield {"type": "done"}
