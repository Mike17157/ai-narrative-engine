"""Composition root for the story HTTP feature.

Route families live in ``loom.stories.api``; this module only registers them.
"""

from __future__ import annotations

from .api import assets, creation, library, runtime


_ROUTE_FAMILIES = (
    creation,
    runtime,
    library,
    assets,
)


def register(app, ctx) -> None:
    """Attach all story routes to the application.

    Each family owns its FastAPI handlers; registration order is cosmetic because
    story routes have distinct paths.
    """
    for family in _ROUTE_FAMILIES:
        family.register(app, ctx)
