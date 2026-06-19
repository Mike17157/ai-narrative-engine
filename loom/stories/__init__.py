"""Story domain — everything for authoring a story experience from a card.

Holds the whole story/character authoring engine (`pipeline/`, the numbered
storyboard → locations → cast → wardrobe → expressions steps) and the HTTP
surface (`router.py`). The authoring engine doubles as the character-authoring
engine, so other domains (characters, full_gen) import stage functions from
`loom.stories.pipeline` directly.

Shared, cross-domain helpers (prompt composition, the emotion taxonomy, shot
geometry, image rendering) deliberately stay in `loom/server/services/` — they
back characters/personas/images too, so they are not story-owned.

This package re-exports the pipeline's public stage API for convenience, so
`from loom.stories import revise_character` works as the old scenario shim did.
"""

from .pipeline import (
    STAGES,
    extract_characters,
    extract_locations,
    extract_protagonist,
    parse_storyboard,
    plan_wardrobe,
    revise_character,
    storyboard_inputs,
)

__all__ = ["STAGES", "storyboard_inputs", "parse_storyboard", "extract_locations",
           "extract_characters", "extract_protagonist", "plan_wardrobe", "revise_character"]
