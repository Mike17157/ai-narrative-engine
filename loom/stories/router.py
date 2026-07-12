"""Composition root for the story HTTP feature.

Route families live in ``loom.stories.api``; this module only registers them.
"""

from __future__ import annotations

from .api import builder, genesis, drafting, authoring, arcs, library, play, assets, manuscript, play_state


_ROUTE_FAMILIES = (
    builder,
    genesis,
    drafting,
    authoring,
    arcs,
    library,
    play,
    assets,
    manuscript,
    play_state,
)


def register(app, ctx) -> None:
    """Attach all story routes to the application.

    Each family owns its FastAPI handlers; registration order is cosmetic because
    story routes have distinct paths.
    """
    for family in _ROUTE_FAMILIES:
        family.register(app, ctx)
