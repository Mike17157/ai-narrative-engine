"""Anthropic (Claude) text provider — the chat brain.

Defaults to Claude Opus 4.8 (`claude-opus-4-8`) with adaptive thinking, which is
the recommended configuration for current Claude models. Note that on Opus
4.8/4.7 the sampling parameters (temperature/top_p/top_k) and fixed thinking
budgets are not part of the request surface — thinking depth is controlled via
`effort`, not a token budget.

Model `options` (from models.yaml):
    model:      model id (default "claude-opus-4-8")
    max_tokens: int (default 4096)
    effort:     "low" | "medium" | "high" | "xhigh" | "max" (default "high")
    thinking:   "adaptive" | "disabled" (default "adaptive")
"""

from __future__ import annotations

import json
from typing import Any, Callable

import anthropic

from .base import TextResult


class AnthropicProvider:
    def __init__(self, options: dict[str, Any]):
        # Key from the saved connection if present, else ANTHROPIC_API_KEY env.
        key = options.get("api_key")
        self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.model: str = options.get("model", "claude-opus-4-8")
        self.max_tokens: int = int(options.get("max_tokens", 4096))
        self.effort: str = options.get("effort", "high")
        self.thinking: str = options.get("thinking", "adaptive")

    def _thinking_param(self) -> dict[str, str]:
        return {"type": "adaptive"} if self.thinking == "adaptive" else {"type": "disabled"}

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
        if images:
            raise NotImplementedError("image captioning routes through an OpenRouter vision model")
        messages = [{"role": "user", "content": prompt}]

        # Structured path: constrain the response to the caller's JSON Schema so a
        # chat step can return, e.g., {"reply": "...", "wants_image": true,
        # "image_prompt": "..."} in one call. No streaming here — we need the
        # whole object.
        if emits is not None:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking=self._thinking_param(),
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": emits},
                },
                system=system or anthropic.NOT_GIVEN,
                messages=messages,
            )
            text = next((b.text for b in resp.content if b.type == "text"), "")
            data = json.loads(text) if (text and text.strip()) else {}
            # Prefer an explicit "reply"/"text" field as the human-facing turn.
            reply = data.get("reply") or data.get("text") or ""
            return TextResult(text=reply, data=data)

        # Plain streaming path for an ordinary chat turn.
        chunks: list[str] = []
        with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking=self._thinking_param(),
            output_config={"effort": self.effort},
            system=system or anthropic.NOT_GIVEN,
            messages=messages,
        ) as stream:
            for delta in stream.text_stream:
                if cancel and cancel():
                    break  # caller asked to stop
                chunks.append(delta)
                if on_delta:
                    on_delta(delta)
        return TextResult(text="".join(chunks))
