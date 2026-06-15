"""OpenAI-compatible text provider — drives OpenRouter (and OpenAI, and any
OpenAI-compatible endpoint) via /chat/completions.

OpenRouter is the default target: one key, hundreds of models. The API key and
model usually come from a saved Connection (see loom/connections.py), passed in
through `options`.

Model `options`:
    base_url:   default "https://openrouter.ai/api/v1"
    api_key:    bearer key (falls back to OPENROUTER_API_KEY / OPENAI_API_KEY)
    model:      e.g. "anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini"
    max_tokens: int (default 1024)
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import httpx

from .base import TextResult


class OpenAICompatProvider:
    def __init__(self, options: dict[str, Any]):
        self.base_url: str = options.get("base_url", "https://openrouter.ai/api/v1").rstrip("/")
        self.api_key: str = (
            options.get("api_key")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        self.model: str = options.get("model", "openai/gpt-4o-mini")
        self.max_tokens: int = int(options.get("max_tokens", 1024))

    def _err(self, resp) -> str:
        """Surface the provider's real error body, not a bare 'HTTP 404'."""
        try:
            j = resp.json()
            err = j.get("error")
            msg = err.get("message") if isinstance(err, dict) else (err or j.get("message"))
            msg = msg or resp.text
        except Exception:  # noqa: BLE001
            msg = resp.text or ""
        return f"{self.model} → HTTP {resp.status_code}: {str(msg).strip()[:300]}"

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if "openrouter" in self.base_url:
            # OpenRouter likes these for attribution; harmless elsewhere.
            h["HTTP-Referer"] = "http://localhost"
            h["X-Title"] = "Loom"
        return h

    def generate_text(
        self,
        *,
        system: str | None,
        prompt: str,
        emits: dict[str, Any] | None = None,
        on_delta: Callable[[str], None] | None = None,
        images: list[str] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> TextResult:
        if not self.api_key:
            raise RuntimeError("no API key for this connection — connect first in the Connection tab")

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if images:
            # Multimodal: OpenRouter/OpenAI take content as text + image_url parts.
            # Each image is a data URI (data:image/png;base64,...).
            content: list[dict] = [{"type": "text", "text": prompt}]
            for uri in images:
                content.append({"type": "image_url", "image_url": {"url": uri}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        url = f"{self.base_url}/chat/completions"

        # Structured path — OpenAI-style json_schema response_format.
        if emits is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "loom_out", "strict": True, "schema": emits},
            }
            # Non-streaming (no live consumer) — one blocking request.
            if on_delta is None:
                resp = httpx.post(url, json=body, headers=self._headers(), timeout=120)
                if resp.status_code >= 400:
                    raise RuntimeError(self._err(resp))
                content = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(content) if content else {}
                return TextResult(text=data.get("reply") or data.get("text") or "", data=data)

            # Streaming structured: stream the JSON tokens (so a UI can watch it form), accumulate,
            # then parse the whole thing. The schema still constrains the final object.
            body["stream"] = True
            chunks: list[str] = []
            with httpx.stream("POST", url, json=body, headers=self._headers(), timeout=120) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise RuntimeError(self._err(resp))
                for line in resp.iter_lines():
                    if cancel and cancel():
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        chunks.append(delta)
                        on_delta(delta)
            content = "".join(chunks)
            try:
                data = json.loads(content) if content else {}
            except json.JSONDecodeError:
                # streamed JSON arrived malformed/truncated — fall back to one clean blocking call
                body.pop("stream", None)
                resp = httpx.post(url, json=body, headers=self._headers(), timeout=120)
                if resp.status_code >= 400:
                    raise RuntimeError(self._err(resp))
                content = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(content) if content else {}
            return TextResult(text=data.get("reply") or data.get("text") or "", data=data)

        # Streaming path.
        body["stream"] = True
        chunks: list[str] = []
        with httpx.stream("POST", url, json=body, headers=self._headers(), timeout=120) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise RuntimeError(self._err(resp))
            for line in resp.iter_lines():
                if cancel and cancel():
                    break  # caller asked to stop — closing the stream halts upstream
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                if delta:
                    chunks.append(delta)
                    if on_delta:
                        on_delta(delta)
        return TextResult(text="".join(chunks))
