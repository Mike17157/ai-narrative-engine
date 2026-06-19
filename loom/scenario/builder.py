"""Story Builder — thin re-export shim.

All implementation now lives in loom/pipeline/. This module is kept for backward-compat so
imports that reference loom.scenario.builder continue to work unchanged.
"""

# Re-export helpers and schemas from the pipeline package.
from loom.pipeline._helpers import (
    _OBJ,
    _arr,
    _str_arr,
    _card_context,
    _sys,
    _slug,
    _call,
    LOCATIONS_SCHEMA,
    CHARACTERS_SCHEMA,
    ROSTER_SCHEMA,
    PROTAGONIST_SCHEMA,
    WARDROBE_SCHEMA,
    _TAG_RULE,
    _APPEARANCE_RULE,
    _OUTFIT_RULE,
    DEFAULT_SYSTEMS,
    STAGES,
    NEEDS_IMAGE,
)

# Re-export pipeline stage functions.
from loom.pipeline.s01_storyboard import storyboard_inputs, parse_storyboard
from loom.pipeline.s02_locations import extract_locations
from loom.pipeline.s03_characters import (
    _name_tokens,
    extract_protagonist,
    revise_character,
    extract_characters,
)
from loom.pipeline.s05_wardrobe_plan import plan_wardrobe

__all__ = [
    "_OBJ", "_arr", "_str_arr", "_card_context", "_sys", "_slug", "_call",
    "LOCATIONS_SCHEMA", "CHARACTERS_SCHEMA", "ROSTER_SCHEMA", "PROTAGONIST_SCHEMA",
    "WARDROBE_SCHEMA", "_TAG_RULE", "_APPEARANCE_RULE", "_OUTFIT_RULE",
    "DEFAULT_SYSTEMS", "STAGES", "NEEDS_IMAGE",
    "storyboard_inputs", "parse_storyboard",
    "extract_locations",
    "_name_tokens", "extract_protagonist", "revise_character", "extract_characters",
    "plan_wardrobe",
]
