"""Streaming render helpers shared by the workflow-tester endpoints.

The local ComfyUI path streams fine-grained progress over ComfyUI's websocket
(``loom.comfy.generate.stream_generate``). The RunPod **serverless** path can't —
the worker only exposes a coarse ``/status`` poll — so this module wraps a
serverless render as the same SSE event shape the tester UI already consumes
(``progress`` / ``node`` / ``image`` / ``error``), with one ``node`` stage event
up front and the finished media at the end.

It is cancellable: if the client disconnects (presses ✕ / navigates away), the
StreamingResponse throws ``CancelledError`` into the generator, which flips a flag
the in-flight RunPod job polls — so the remote job is cancelled and stops billing
instead of being orphaned.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, AsyncIterator


def classify_media(raw: bytes) -> tuple[str, str]:
    """Sniff a render's bytes → (kind, mime). Wan I2V returns an mp4 (or webm)
    clip; still workflows return a png. The tester renders ``image`` in an <img>
    and ``video`` in a <video>."""
    head = raw[:12]
    if head[4:8] == b"ftyp":                       # ISO-BMFF / mp4
        return "video", "video/mp4"
    if head[:4] == b"\x1aE\xdf\xa3":               # EBML / webm
        return "video", "video/webm"
    return "image", "image/png"


async def serverless_render_events(
    provider, *, prompt: str, negative: str | None = None,
    init_image: bytes | None = None, latent: tuple[int, int] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one RunPod serverless render, yielding SSE-shaped dict events.

    The provider's blocking ``generate_image`` runs in a worker thread; we await it
    while staying responsive to client disconnect. On disconnect the ``cancel`` flag
    flips and the provider cancels the remote job on its next status poll."""
    from fastapi.concurrency import run_in_threadpool

    cancelled = {"v": False}

    def _do():
        return provider.generate_image(
            prompt=prompt, negative_prompt=(negative or None),
            init_image=init_image, latent=latent,
            cancel=lambda: cancelled["v"],
        )

    task = asyncio.ensure_future(run_in_threadpool(_do))
    # No granular progress from RunPod — show a single running stage so the UI
    # swaps its "queued…" spinner for "running…" instead of looking frozen.
    yield {"type": "node", "node": "RunPod serverless"}
    try:
        while not task.done():
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        cancelled["v"] = True       # tell the in-flight job to stop billing
        raise
    finally:
        cancelled["v"] = True

    result = task.result()          # re-raises a failed/cancelled job → caught upstream
    raw = result.images[0] if result.images else None
    if not raw:
        yield {"type": "error", "error": "RunPod returned no image"}
        return
    kind, mime = classify_media(raw)
    data_url = f"data:{mime};base64," + base64.b64encode(raw).decode()
    if kind == "video":
        yield {"type": "image", "images": [], "videos": [data_url]}
    else:
        yield {"type": "image", "images": [data_url], "videos": []}
