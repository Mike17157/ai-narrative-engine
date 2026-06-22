"""RunPod Serverless provider for ComfyUI workflows.

Sends an API-format ComfyUI workflow to a RunPod Serverless endpoint that runs
ComfyUI in an auto-scaling worker (the de-facto standard image is blib-la's
`runpod-worker-comfy`). Unlike :class:`ComfyUIProvider`, which talks to one
long-lived ComfyUI server, this provider is stateless: each render is one
endpoint job, and RunPod spins workers up and down with demand.

The graph itself is prepared by the exact same rules as the local provider —
prompt-token substitution, out_prefix / latent overrides, and BREAK-region
conditioning — via the shared :mod:`loom.providers._workflow` helpers, so a
workflow renders identically whether it runs locally or on RunPod.

Model `options` (from models.yaml):
    endpoint_id: RunPod serverless endpoint id (required)
    api_key:     RunPod API key (required)
    workflow:    path to an API-format workflow JSON, or an inline dict
    inputs:      where to inject prompts, e.g.
                 { positive: {node: "6", field: "text"},
                   negative: {node: "7", field: "text"} }
    output_node: node id whose images to collect (optional; worker dependent)
    timeout_s:   max seconds to wait for a job to finish (default 600 — cold
                 starts plus generation can be slow)
    poll_s:      seconds between status polls (default 2)

The worker request shape (runpod-worker-comfy):
    { "input": { "workflow": {...}, "images": [{"name","image"}] } }
and it returns, on success, one of several output shapes which
:func:`_extract_images` normalises to raw PNG bytes.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from . import _workflow
from .base import ImageResult

# The filename the worker saves an uploaded init image under; we point the
# graph's LoadImage node at the same name.
_INIT_IMAGE_NAME = "loom_init.png"


class RunPodServerlessProvider:
    def __init__(self, options: dict[str, Any]):
        self.endpoint_id: str = options.get("endpoint_id", "")
        self.api_key: str = options.get("api_key", "")
        self.inputs: dict[str, dict] = options.get("inputs", {})
        self.output_node: str | None = options.get("output_node")
        self.output_variant: str | None = options.get("output_variant")
        self.timeout_s: float = float(options.get("timeout_s", 600))
        self.poll_s: float = float(options.get("poll_s", 2))

        if not self.endpoint_id:
            raise ValueError("runpod_serverless model requires options.endpoint_id")
        if not self.api_key:
            raise ValueError("runpod_serverless model requires options.api_key")

        # Workflow may be a path (consistent with the comfyui provider / models.yaml)
        # or an inline dict (e.g. handed straight from a batch caller).
        wf = options.get("workflow")
        if isinstance(wf, str):
            self.workflow: dict = json.loads(Path(wf).read_text(encoding="utf-8"))
        elif isinstance(wf, dict):
            self.workflow = wf
        else:
            raise ValueError("runpod_serverless model requires options.workflow (path or dict)")

        self.base_url = f"https://api.runpod.ai/v2/{self.endpoint_id}"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str | None = None,
        init_image: bytes | None = None,
        out_prefix: str | None = None,
        latent: tuple[int, int] | None = None,
        flags: dict[str, bool] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> ImageResult:
        """Generate an image via the RunPod serverless endpoint.

        `cancel`, if given, is polled while waiting; when it turns true the remote
        job is cancelled (so it stops billing) and the call raises."""
        graph = _workflow.inject(
            self.workflow, self.inputs, prompt, negative_prompt, out_prefix, latent, flags
        )
        out_node = _workflow.apply_output_variant(graph, self.output_node, self.output_variant)
        # The worker is Linux; a Windows-authored graph may carry backslash model
        # paths (e.g. nested LoRA folders) that won't resolve there.
        _workflow.normalize_model_paths(graph)

        payload: dict[str, Any] = {"input": {"workflow": graph}}

        # img2img: hand the worker the source bytes and point the LoadImage node at
        # the name it saves them under. No-op if the graph is text-to-image.
        if init_image:
            node_id = _workflow.find_load_image_node(graph)
            if node_id is not None:
                payload["input"]["images"] = [{
                    "name": _INIT_IMAGE_NAME,
                    "image": base64.b64encode(init_image).decode(),
                }]
                graph[node_id].setdefault("inputs", {})["image"] = _INIT_IMAGE_NAME

        with httpx.Client(base_url=self.base_url, headers=self.headers, timeout=60) as client:
            job_id = self._submit(client, payload)
            output = self._await_output(client, job_id, cancel=cancel)

        images = _extract_images(output)
        return ImageResult(images=images, meta={"endpoint_id": self.endpoint_id, "job_id": job_id})

    def _submit(self, client: httpx.Client, payload: dict) -> str:
        """Queue an async job; return its id."""
        resp = client.post("/run", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"RunPod rejected the job ({resp.status_code}): {resp.text}")
        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            raise RuntimeError(f"RunPod /run returned no job id: {data}")
        # Some endpoints return a terminal status immediately (warm worker).
        if data.get("status") == "COMPLETED" and "output" in data:
            # Stash so _await_output can short-circuit on the first poll.
            self._primed = (job_id, data)
        return job_id

    def _cancel_remote(self, client: httpx.Client, job_id: str) -> None:
        """Best-effort cancel so we never leave an orphan job billing."""
        try:
            client.post(f"/cancel/{job_id}")
        except httpx.HTTPError:
            pass

    def _await_output(self, client: httpx.Client, job_id: str,
                      cancel: Callable[[], bool] | None = None) -> Any:
        """Poll /status until the job finishes; return its `output`, or raise.
        If `cancel()` turns true mid-wait, cancel the remote job and raise."""
        primed = getattr(self, "_primed", None)
        if primed and primed[0] == job_id:
            self._primed = None
            return primed[1].get("output")

        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if cancel and cancel():
                self._cancel_remote(client, job_id)
                raise RuntimeError(f"RunPod job {job_id} cancelled by caller")
            resp = client.get(f"/status/{job_id}")
            if resp.status_code == 429:  # rate limited — back off and retry
                time.sleep(min(self.poll_s * 2, 10))
                continue
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            if status == "COMPLETED":
                return data.get("output")
            if status == "FAILED":
                raise RuntimeError(f"RunPod job {job_id} failed: {data.get('error', data)}")
            if status in ("CANCELLED", "TIMED_OUT"):
                raise RuntimeError(f"RunPod job {job_id} {status.lower().replace('_', ' ')}")
            # IN_QUEUE / IN_PROGRESS — keep waiting.
            time.sleep(self.poll_s)

        self._cancel_remote(client, job_id)  # don't leave an orphan job billing
        raise TimeoutError(f"RunPod job {job_id} did not finish within {self.timeout_s}s")


def _extract_images(output: Any) -> list[bytes]:
    """Normalise the assorted runpod-worker-comfy output shapes to raw image bytes.

    Seen in the wild:
      - a bare base64 string
      - a list of base64 strings, or of {data|image, ...} dicts
      - {"images": [{"data"|"image", "type"}]}  (type "base64" or "s3_url")
      - {"message": "<base64 or http url>"}      (single-image workers)
      - {"image": "<base64 or http url>"}
    URLs are fetched; anything unparseable is skipped rather than fatal.
    """
    items: list[Any] = []
    if isinstance(output, str):
        items = [output]
    elif isinstance(output, list):
        items = output
    elif isinstance(output, dict):
        if isinstance(output.get("images"), list):
            items = output["images"]
        else:
            for key in ("message", "image", "data"):
                if key in output:
                    items = [output[key]]
                    break

    images: list[bytes] = []
    for item in items:
        payload = item.get("data") or item.get("image") or item.get("message") if isinstance(item, dict) else item
        if not isinstance(payload, str):
            continue
        data = _decode(payload)
        if data:
            images.append(data)
    return images


def _decode(payload: str) -> bytes | None:
    """Turn a single output entry — http(s) URL or base64 (optionally a data: URI) — into bytes."""
    if payload.startswith(("http://", "https://")):
        try:
            resp = httpx.get(payload, timeout=60)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError:
            return None
    if payload.startswith("data:"):  # data:image/png;base64,XXXX
        payload = payload.split(",", 1)[-1]
    try:
        return base64.b64decode(payload)
    except Exception:  # noqa: BLE001
        return None
