"""Templating context for a pipeline run.

Steps reference each other and the run inputs through Jinja2 templates, e.g.
`{{ character.system }}`, `{{ user_message }}`, `{{ reply.data.image_prompt }}`.
The context is a plain dict that accumulates each step's output under its id as
the run proceeds, so a later step can read an earlier one's result.
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, StrictUndefined

# StrictUndefined: referencing a field that doesn't exist raises instead of
# silently rendering "" — consistent with the "flexible but never silently
# broken" stance of the config layer.
_ENV = Environment(undefined=StrictUndefined, autoescape=False)


def render(template: str, context: dict[str, Any]) -> str:
    return _ENV.from_string(template).render(**context)


def is_truthy(rendered: str) -> bool:
    """How a step's `when` condition is interpreted once rendered."""
    return rendered.strip().lower() not in ("", "false", "no", "0", "none")
