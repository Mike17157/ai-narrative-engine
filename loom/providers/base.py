"""Provider interfaces.

A provider is a thin adapter over one backend. Adding a new backend (OpenAI,
Automatic1111, a hosted image API) is a single new file implementing one of
these protocols plus a registry entry — the engine and configs never change.

Text and image are deliberately separate protocols. The engine only ever hands
a chat step to a TextProvider and an image step to an ImageProvider, and the
config validator guarantees the wiring matches. That separation is the whole
point of the project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class TextResult:
    text: str
    # Populated when the step requested structured output (`emits`); the parsed
    # JSON object the model returned.
    data: dict[str, Any] = field(default_factory=dict)
    # Populated when the step passed `tools`; the model's native tool calls,
    # normalized to [{"fn": <name>, "params": {...}}] across providers.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImageResult:
    # Raw bytes of each generated image, plus the content type for saving/serving.
    images: list[bytes] = field(default_factory=list)
    content_type: str = "image/png"
    # Anything the backend wants to surface (seed, node outputs, timing).
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TextProvider(Protocol):
    def generate_text(
        self,
        *,
        system: str | None,
        prompt: str,
        emits: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
        images: list[str] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> TextResult:
        """Produce a text turn.

        `emits` is an optional JSON Schema; when given, the result's `.data`
        holds the validated object the model returned. `tools` is an optional
        list of tool specs ({"name", "description", "parameters": <JSON Schema>});
        when given, the model may call them and the result's `.tool_calls` holds
        the calls as [{"fn", "params"}]. `on_delta` receives streamed text chunks
        when supported. `images` (data URIs) make the turn multimodal for
        vision-capable models.
        """
        ...


@runtime_checkable
class ImageProvider(Protocol):
    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str | None = None,
        init_image: bytes | None = None,
    ) -> ImageResult:
        ...
