"""Author-visible / model-hidden text boundaries.

Story text is normally canonical context.  Sometimes an author needs to keep a
note on the card for themselves while guaranteeing that a narrator, director,
organizer, or interviewer cannot read it.  ``[[hidden]] … [[/hidden]]`` is
that boundary.  It is preserved in storage and UI payloads, then removed by
the server immediately before a text model is called.

This is deliberately a *redaction*, not a prompt instruction.  Passing a tag
and asking a model to ignore its contents is not a visibility boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any


# ``[[model-hidden]]`` is accepted as a more explicit spelling, but the short
# form is the documented author-facing syntax.  The parser is case-insensitive
# and supports nesting so authors can safely hide a quoted block that itself
# contains a hidden annotation.
_TAG = re.compile(r"\[\[\s*(?P<close>/)?\s*(?:model[-_\s]?)?hidden\s*\]\]", re.I)


def strip_model_hidden(text: str | None) -> str | None:
    """Return a model-safe view of a text string.

    Everything inside a paired hidden block disappears, including the wrapper.
    An unmatched opening tag is fail-closed and hides the remainder of the
    string; an unmatched closing tag is removed but does not hide visible text.
    Storage is never changed by this function, so the author still sees exactly
    what they wrote in the UI.
    """
    if not isinstance(text, str) or "hidden" not in text.lower():
        return text
    out: list[str] = []
    cursor = 0
    depth = 0
    for match in _TAG.finditer(text):
        if not match.group("close"):
            if depth == 0:
                out.append(text[cursor:match.start()])
            depth += 1
            cursor = match.end()
            continue
        if depth:
            depth -= 1
            if depth == 0:
                cursor = match.end()
            continue
        # A stray close marker is not secret data.  Keep its surrounding
        # visible content but remove the marker itself.
        out.append(text[cursor:match.start()])
        cursor = match.end()
    if depth == 0:
        out.append(text[cursor:])
    # With an unmatched opener we intentionally append nothing after it.
    return "".join(out)


def extract_model_hidden(text: str | None) -> list[str]:
    """Return top-level hidden block contents for author-side persistence.

    This is intentionally separate from :func:`strip_model_hidden`: the latter
    is the fail-closed model projection, while this helper lets an interface
    keep a user's typed private note in an author-only card field.  An unclosed
    opening tag contributes the remainder as a private note as well.
    """
    if not isinstance(text, str):
        return []
    blocks: list[str] = []
    depth = 0
    start = 0
    for match in _TAG.finditer(text):
        if not match.group("close"):
            if depth == 0:
                start = match.end()
            depth += 1
            continue
        if not depth:
            continue
        depth -= 1
        if depth == 0:
            content = text[start:match.start()]
            if content.strip():
                blocks.append(content)
    if depth:
        content = text[start:]
        if content.strip():
            blocks.append(content)
    return blocks


def wrap_model_hidden(text: str) -> str:
    """Store a private note in the canonical, visibly marked wrapper."""
    return f"[[hidden]]{text}[[/hidden]]"


def _hidden_blocks(text: str) -> list[str]:
    """Return each top-level wrapper verbatim, including an unclosed tail.

    This small internal companion to :func:`extract_model_hidden` is used when
    a model has been allowed to rewrite the public part of a card field.  The
    model sees a redacted projection, so its replacement cannot be allowed to
    quietly erase author-only material from the stored source.
    """
    if not isinstance(text, str):
        return []
    blocks: list[str] = []
    depth = 0
    start = 0
    for match in _TAG.finditer(text):
        if not match.group("close"):
            if depth == 0:
                start = match.start()
            depth += 1
            continue
        if not depth:
            continue
        depth -= 1
        if depth == 0:
            blocks.append(text[start:match.end()])
    if depth:
        blocks.append(text[start:])
    return blocks


def preserve_model_hidden(source: Any, rewritten: Any) -> Any:
    """Restore author-only wrappers after a model rewrites public card text.

    Providers receive redacted projections, which is the desired confidentiality
    rule.  A whole-field rewrite (such as *Organize card*) would otherwise also
    remove the unseen private span.  This non-mutating merge keeps the model's
    public rewrite while retaining source wrappers at their corresponding data
    locations.  A model can never delete an author-only note; the author must
    do that through a direct edit.
    """
    if isinstance(source, str):
        blocks = _hidden_blocks(source)
        if not blocks:
            return deepcopy(rewritten)
        if not isinstance(rewritten, str):
            # A malformed model replacement must not turn a private text field
            # into a deletion of the only copy of its hidden material.
            return deepcopy(source)
        missing = [block for block in blocks if block not in rewritten]
        if not missing:
            return rewritten
        separator = "\n\n" if rewritten and not rewritten.endswith(("\n", " ")) else ""
        return f"{rewritten}{separator}{''.join(missing)}"
    if isinstance(source, Mapping) and isinstance(rewritten, Mapping):
        result = {key: deepcopy(value) for key, value in rewritten.items()}
        for key, old_value in source.items():
            if key in result:
                result[key] = preserve_model_hidden(old_value, result[key])
            elif has_model_hidden(old_value):
                # An omitted key cannot be permitted to be the accidental
                # deletion route for private author data.
                result[key] = deepcopy(old_value)
        return result
    if isinstance(source, (list, tuple)) and isinstance(rewritten, (list, tuple)):
        result = [deepcopy(value) for value in rewritten]
        for index, old_value in enumerate(source):
            if index < len(result):
                result[index] = preserve_model_hidden(old_value, result[index])
            elif has_model_hidden(old_value):
                result.append(deepcopy(old_value))
        return tuple(result) if isinstance(rewritten, tuple) else result
    return deepcopy(rewritten)


def model_view(value: Any) -> Any:
    """Recursively project JSON-safe story data into a model-safe view.

    Keys and non-text scalars remain intact.  This is useful where a complete
    Story card is serialized into a prompt; normal prose prompts can use
    :func:`strip_model_hidden` directly.
    """
    if isinstance(value, str):
        return strip_model_hidden(value)
    if isinstance(value, Mapping):
        return {key: model_view(item) for key, item in value.items()}
    if isinstance(value, list):
        return [model_view(item) for item in value]
    if isinstance(value, tuple):
        return tuple(model_view(item) for item in value)
    return deepcopy(value)


def has_model_hidden(value: Any) -> bool:
    """Whether a text/data value contains author-only markup (for UI hints)."""
    if isinstance(value, str):
        return bool(_TAG.search(value))
    if isinstance(value, Mapping):
        return any(has_model_hidden(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_model_hidden(item) for item in value)
    return False


class ModelVisibilityProvider:
    """Thin adapter that redacts hidden blocks for every text provider call.

    The wrapper forwards every other attribute so provider-specific metadata,
    options, and tests continue to work.  It is intentionally idempotent via
    :func:`model_visibility_provider`.
    """
    def __init__(self, provider: Any):
        self._provider = provider

    def generate_text(self, *, system: str | None, prompt: str, **kwargs):
        return self._provider.generate_text(
            system=strip_model_hidden(system),
            prompt=strip_model_hidden(prompt) or "",
            **kwargs,
        )

    def __getattr__(self, name: str):
        return getattr(self._provider, name)


class ModelVisibilityImageProvider:
    """The same boundary for image-model prompts.

    An image workflow is still a model invocation.  If an author keeps a
    private note beside visual world prose, it must not sneak into a generated
    prompt simply because the downstream model is a diffusion provider rather
    than a chat provider.
    """
    def __init__(self, provider: Any):
        self._provider = provider

    def generate_image(self, *, prompt: str, negative_prompt: str | None = None, **kwargs):
        return self._provider.generate_image(
            prompt=strip_model_hidden(prompt) or "",
            negative_prompt=strip_model_hidden(negative_prompt),
            **kwargs,
        )

    def __deepcopy__(self, memo):
        """Keep batch image rendering compatible with the redaction wrapper.

        ``copy.deepcopy`` otherwise asks ``__getattr__`` for ``__deepcopy__``;
        forwarding that lookup to the wrapped provider re-enters the wrapper
        indefinitely. Batch rendering deliberately clones each provider so
        concurrent Comfy jobs cannot mutate the same workflow graph.
        """
        clone = type(self)(deepcopy(self._provider, memo))
        memo[id(self)] = clone
        return clone

    def __getattr__(self, name: str):
        return getattr(self._provider, name)


def model_visibility_provider(provider: Any) -> Any:
    """Wrap a text or image provider once; accepts ``None`` for routing."""
    if provider is None or isinstance(provider, (ModelVisibilityProvider, ModelVisibilityImageProvider)):
        return provider
    if callable(getattr(provider, "generate_image", None)):
        return ModelVisibilityImageProvider(provider)
    return ModelVisibilityProvider(provider)


__all__ = [
    "ModelVisibilityProvider",
    "ModelVisibilityImageProvider",
    "extract_model_hidden",
    "has_model_hidden",
    "model_view",
    "model_visibility_provider",
    "preserve_model_hidden",
    "strip_model_hidden",
    "wrap_model_hidden",
]
