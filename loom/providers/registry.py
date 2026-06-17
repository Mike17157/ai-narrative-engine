"""Provider registry — maps a provider name to its adapter class.

To add a backend: write the adapter, then register it here. Nothing else in the
engine needs to know it exists. Constructors are imported lazily so that, e.g.,
a project that only uses cloud LLMs never needs the image stack installed.
"""

from __future__ import annotations

from typing import Any, Callable

from ..config.schema import ModelDef
from .base import ImageProvider, TextProvider

# name -> factory(options) -> provider instance
_TEXT_PROVIDERS: dict[str, Callable[[dict[str, Any]], TextProvider]] = {}
_IMAGE_PROVIDERS: dict[str, Callable[[dict[str, Any]], ImageProvider]] = {}
# Fallback for any text provider not explicitly registered (OpenAI-compatible).
_TEXT_DEFAULT: Callable[[dict[str, Any]], TextProvider] | None = None


def _register_defaults() -> None:
    if _TEXT_PROVIDERS:
        return

    def _anthropic(options: dict[str, Any]) -> TextProvider:
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(options)

    def _openai_compat(options: dict[str, Any]) -> TextProvider:
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(options)

    def _comfyui(options: dict[str, Any]) -> ImageProvider:
        from .comfyui_provider import ComfyUIProvider

        return ComfyUIProvider(options)

    def _runpod_serverless(options: dict[str, Any]) -> ImageProvider:
        from .runpod_serverless_provider import RunPodServerlessProvider

        return RunPodServerlessProvider(options)

    # Anthropic uses its own SDK; every other text provider is OpenAI-compatible
    # and goes through one adapter (the base_url distinguishes them).
    _TEXT_PROVIDERS["anthropic"] = _anthropic
    global _TEXT_DEFAULT
    _TEXT_DEFAULT = _openai_compat
    _IMAGE_PROVIDERS["comfyui"] = _comfyui
    _IMAGE_PROVIDERS["runpod_serverless"] = _runpod_serverless


def build_provider(model: ModelDef) -> TextProvider | ImageProvider:
    """Instantiate the provider backing a model definition."""
    _register_defaults()
    if model.kind == "text":
        factory = _TEXT_PROVIDERS.get(model.provider) or _TEXT_DEFAULT
    else:
        factory = _IMAGE_PROVIDERS.get(model.provider)
    if factory is None:
        raise ValueError(f"no {model.kind} provider for '{model.provider}'")
    return factory(model.options)
