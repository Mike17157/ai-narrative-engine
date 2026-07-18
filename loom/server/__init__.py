"""Public server factories.

Keep these imports lazy.  ``loom.server.context`` is also used by the smaller
Story application, and importing this package used to eagerly import the full
legacy application (and every one of its routers) before that smaller app had
a chance to choose its surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_context(root: Path | str = ".") -> Any:
    """Build the legacy application context on demand."""
    from .app import build_context as _build_context

    return _build_context(root)


def create_app(root: Path | str = ".") -> Any:
    """Build the legacy FastAPI application on demand."""
    from .app import create_app as _create_app

    return _create_app(root)


__all__ = ["build_context", "create_app"]
