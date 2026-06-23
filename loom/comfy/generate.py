"""Stream a ComfyUI generation with live progress.

Submits a graph and listens on ComfyUI's websocket, yielding progress/node/image
events so the UI can show a real progress bar instead of a spinner. Used by the
"Test workflow" button.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any, AsyncIterator

import httpx
from websockets.asyncio.client import connect as ws_connect


def _ws_url(base_url: str, client_id: str) -> str:
    b = base_url.rstrip("/")
    if b.startswith("https://"):
        b = "wss://" + b[len("https://"):]
    elif b.startswith("http://"):
        b = "ws://" + b[len("http://"):]
    return f"{b}/ws?clientId={client_id}"


async def stream_generate(
    base_url: str, graph: dict, output_node: str | None = None, timeout: float = 600
) -> AsyncIterator[dict[str, Any]]:
    base = base_url.rstrip("/")
    client_id = str(uuid.uuid4())
    prompt_id: str | None = None
    finished = False

    async with httpx.AsyncClient(base_url=base, timeout=30) as http:
      try:
        async with ws_connect(_ws_url(base, client_id), max_size=None) as ws:
            resp = await http.post("/prompt", json={"prompt": graph, "client_id": client_id})
            if resp.status_code >= 400:
                detail = resp.text
                try:
                    body = resp.json()
                    err = body.get("error") or {}
                    detail = (err.get("message") if isinstance(err, dict) else err) or detail
                    for nid, ne in (body.get("node_errors") or {}).items():
                        msgs = "; ".join(
                            (e.get("message", "") + (f" — {e.get('details')}" if e.get("details") else ""))
                            for e in (ne.get("errors") or [])
                        )
                        detail += f"\n• node {nid} ({ne.get('class_type', '?')}): {msgs}"
                except Exception:  # noqa: BLE001
                    pass
                yield {"type": "error", "error": f"ComfyUI rejected the workflow: {detail[:900]}"}
                return
            prompt_id = resp.json()["prompt_id"]

            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    yield {"type": "error", "error": "timed out waiting for ComfyUI"}
                    return
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    yield {"type": "error", "error": "timed out waiting for ComfyUI"}
                    return
                if isinstance(msg, (bytes, bytearray)):
                    continue  # binary preview frames — ignore
                data = json.loads(msg)
                kind, d = data.get("type"), data.get("data") or {}
                pid = d.get("prompt_id")
                if pid is not None and pid != prompt_id:
                    continue  # a different job
                if kind == "progress":
                    yield {"type": "progress", "value": d.get("value", 0), "max": d.get("max", 0)}
                elif kind == "executing":
                    if d.get("node") is None:
                        break  # this prompt finished
                    yield {"type": "node", "node": d.get("node")}
                elif kind == "execution_success":
                    break
                elif kind == "execution_error":
                    yield {"type": "error", "error": d.get("exception_message") or "execution error"}
                    return

            # collect the rendered images
            hist = (await http.get(f"/history/{prompt_id}")).json()
            outputs = hist.get(prompt_id, {}).get("outputs", {})
            node_ids = [output_node] if output_node else list(outputs)
            images: list[str] = []
            videos: list[str] = []
            for nid in node_ids:
                out = outputs.get(nid, {})
                for img in out.get("images", []):
                    r = await http.get("/view", params={
                        "filename": img["filename"],
                        "subfolder": img.get("subfolder", ""),
                        "type": img.get("type", "output"),
                    })
                    images.append("data:image/png;base64," + base64.b64encode(r.content).decode())
                # VHS_VideoCombine emits its file under the "gifs" key (mp4/webm/gif).
                for vid in (out.get("gifs", []) + out.get("videos", [])):
                    r = await http.get("/view", params={
                        "filename": vid["filename"],
                        "subfolder": vid.get("subfolder", ""),
                        "type": vid.get("type", "output"),
                    })
                    fn = vid["filename"].lower()
                    mime = ("video/mp4" if fn.endswith(".mp4") else
                            "video/webm" if fn.endswith(".webm") else
                            "image/gif" if fn.endswith(".gif") else "video/mp4")
                    videos.append(f"data:{mime};base64," + base64.b64encode(r.content).decode())
            finished = True
            yield {"type": "image", "images": images, "videos": videos}
      finally:
        # If we're torn down before finishing (client closed the SSE / pressed ✕,
        # an error, or a timeout), tell ComfyUI to stop the in-flight render so the
        # GPU isn't left cooking a job nobody is waiting for.
        if not finished:
            try:
                async with httpx.AsyncClient(base_url=base, timeout=5) as kill:
                    await kill.post("/interrupt")
            except Exception:  # noqa: BLE001
                pass
