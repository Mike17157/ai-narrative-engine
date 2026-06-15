"""ComfyUI image provider — the local image model, driven over its HTTP API.

This is the deliberately-flexible part. Rather than hard-coding a generation
graph, you point the model at a workflow exported from ComfyUI ("Save (API
Format)") and declare where the prompt text gets injected. Any graph works:
SDXL, Pony, Flux, a multi-stage upscale — Loom only needs to know which node
input receives the positive/negative prompt and which node emits the image.

Model `options` (from models.yaml):
    base_url:    ComfyUI server (default "http://127.0.0.1:8188")
    workflow:    path to an API-format workflow JSON (required)
    inputs:      where to inject prompts, e.g.
                 { positive: {node: "6", field: "text"},
                   negative: {node: "7", field: "text"} }
    output_node: node id whose SaveImage/output images we collect
                 (optional; if omitted, all output images are returned)
    timeout_s:   max seconds to wait for a generation (default 180)
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .base import ImageResult


class ComfyUIProvider:
    def __init__(self, options: dict[str, Any]):
        self.base_url: str = options.get("base_url", "http://127.0.0.1:8188").rstrip("/")
        workflow_path = options.get("workflow")
        if not workflow_path:
            raise ValueError("comfyui model requires options.workflow (path to an API-format workflow JSON)")
        self.workflow: dict = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
        self.inputs: dict[str, dict] = options.get("inputs", {})
        self.output_node: str | None = options.get("output_node")
        self.timeout_s: float = float(options.get("timeout_s", 180))

    # The dynamic value the chat model writes each turn. Insert it into any CLIP
    # text field as this token (the UI's ⚡ picker does this); at generation it's
    # substituted wherever it appears. If a workflow uses no token at all, we
    # fall back to overwriting the designated positive node (legacy behaviour).
    IMAGE_TOKEN = "{{image}}"

    def _substitute(self, graph: dict, value: str) -> bool:
        """Replace the image token in every string input. Returns True if any
        field contained it."""
        found = False
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            ins = node.get("inputs")
            if not isinstance(ins, dict):
                continue
            for k, v in ins.items():
                if isinstance(v, str) and self.IMAGE_TOKEN in v:
                    ins[k] = v.replace(self.IMAGE_TOKEN, value)
                    found = True
        return found

    def _inject(self, prompt: str, negative_prompt: str | None) -> dict:
        graph = copy.deepcopy(self.workflow)
        value = prompt

        used_token = self._substitute(graph, value)

        # Negative still targets its designated node (not tokenised).
        if negative_prompt is not None:
            neg = self.inputs.get("negative")
            if neg and str(neg["node"]) in graph:
                graph[str(neg["node"])].setdefault("inputs", {})[neg["field"]] = negative_prompt

        # Legacy fallback: no {{image}} anywhere -> overwrite the positive node.
        if not used_token:
            pos = self.inputs.get("positive")
            if pos:
                node_id, field = str(pos["node"]), pos["field"]
                if node_id not in graph:
                    raise ValueError(f"workflow has no node '{node_id}' for input 'positive'")
                graph[node_id].setdefault("inputs", {})[field] = value

        # Honour `BREAK` region separators by splitting the positive text into separately-encoded
        # regions chained with core ConditioningConcat (so each region conditions independently,
        # rather than the word "BREAK" being tokenised into the image). Never fatal: any failure
        # falls back to collapsing BREAK to commas so the render always proceeds.
        try:
            self._apply_breaks(graph)
        except Exception:  # noqa: BLE001
            self._strip_breaks(graph)
        return graph

    _BREAK_RE = re.compile(r"\bBREAK\b")

    def _strip_breaks(self, graph: dict) -> None:
        for n in graph.values():
            ins = n.get("inputs") if isinstance(n, dict) else None
            if isinstance(ins, dict):
                for k, v in ins.items():
                    if isinstance(v, str) and "BREAK" in v:
                        ins[k] = self._BREAK_RE.sub(", ", v).strip(" ,")

    def _apply_breaks(self, graph: dict) -> None:
        """For each CLIPTextEncode whose text uses BREAK: keep region 1 on the node, add a
        CLIPTextEncode (sharing its clip) per further region, chain them with ConditioningConcat,
        and rewire the original encode's consumers to the final concat."""
        targets = [nid for nid, n in graph.items()
                   if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode"
                   and isinstance(n.get("inputs"), dict) and "BREAK" in str(n["inputs"].get("text", ""))]
        seq = 0
        for pid in targets:
            node = graph[pid]
            segs = [s.strip(" ,") for s in self._BREAK_RE.split(node["inputs"]["text"])]
            segs = [s for s in segs if s]
            clip = node["inputs"].get("clip")
            if len(segs) < 2 or clip is None:
                node["inputs"]["text"] = ", ".join(segs)   # nothing to chain (or no clip) → collapse
                continue
            # original consumers of this encode's conditioning output, captured BEFORE we add nodes
            consumers = [(nid, k) for nid, n in graph.items() if isinstance(n, dict)
                         for k, v in (n.get("inputs") or {}).items()
                         if isinstance(v, list) and len(v) == 2 and str(v[0]) == str(pid) and v[1] == 0]
            node["inputs"]["text"] = segs[0]
            prev = pid
            for seg in segs[1:]:
                enc, cc = f"loom_break_e{seq}", f"loom_break_c{seq}"; seq += 1
                graph[enc] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": seg}}
                graph[cc] = {"class_type": "ConditioningConcat",
                             "inputs": {"conditioning_to": [prev, 0], "conditioning_from": [enc, 0]}}
                prev = cc
            for nid, k in consumers:
                graph[nid]["inputs"][k] = [prev, 0]

    def _set_init_image(self, client: httpx.Client, graph: dict, image_bytes: bytes) -> None:
        """Upload a source image to ComfyUI and point the workflow's LoadImage node
        at it (image-to-image). No-op if the workflow has no LoadImage node — so a
        plain text-to-image workflow simply ignores the init image."""
        node_id = next((nid for nid, n in graph.items()
                        if isinstance(n, dict) and n.get("class_type") == "LoadImage"), None)
        if node_id is None:
            return
        resp = client.post("/upload/image",
                           files={"image": ("loom_init.png", image_bytes, "image/png")},
                           data={"overwrite": "true"})
        resp.raise_for_status()
        info = resp.json()
        name = info.get("name") or "loom_init.png"
        if info.get("subfolder"):
            name = f"{info['subfolder']}/{name}"
        graph[node_id].setdefault("inputs", {})["image"] = name

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str | None = None,
        init_image: bytes | None = None,
    ) -> ImageResult:
        # Make sure ComfyUI is reachable — connect to a running instance, or
        # (managed mode) launch it headless. Never touches the user's UI.
        from ..comfy.server import get_server

        get_server(self.base_url).ensure_up()

        graph = self._inject(prompt, negative_prompt)
        with httpx.Client(base_url=self.base_url, timeout=60) as client:
            if init_image:
                self._set_init_image(client, graph, init_image)
            queued = client.post("/prompt", json={"prompt": graph})
            if queued.status_code >= 400:
                # ComfyUI returns the useful detail (which node + why) in the body,
                # not the status line — surface it instead of a bare "400".
                detail = queued.text
                try:
                    j = queued.json()
                    ne = j.get("node_errors") or {}
                    bits = []
                    for nid, info in ne.items():
                        ct = (graph.get(nid, {}) or {}).get("class_type", "?")
                        msgs = "; ".join(e.get("message", "") for e in info.get("errors", []))
                        bits.append(f"node {nid} ({ct}): {msgs}")
                    detail = (j.get("error", {}) or {}).get("message", "") or detail
                    if bits:
                        detail = f"{detail} — " + " | ".join(bits) if detail else " | ".join(bits)
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError(f"ComfyUI rejected the workflow: {detail}")
            prompt_id = queued.json()["prompt_id"]

            history = self._await_history(client, prompt_id)
            outputs = history["outputs"]
            node_ids = [self.output_node] if self.output_node else list(outputs.keys())

            images: list[bytes] = []
            for node_id in node_ids:
                for img in outputs.get(node_id, {}).get("images", []):
                    resp = client.get(
                        "/view",
                        params={
                            "filename": img["filename"],
                            "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output"),
                        },
                    )
                    resp.raise_for_status()
                    images.append(resp.content)

        return ImageResult(images=images, meta={"prompt_id": prompt_id})

    def _await_history(self, client: httpx.Client, prompt_id: str) -> dict:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            resp = client.get(f"/history/{prompt_id}")
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(1.0)
        raise TimeoutError(f"ComfyUI generation {prompt_id} did not finish within {self.timeout_s}s")
