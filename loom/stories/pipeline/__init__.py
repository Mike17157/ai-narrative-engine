"""Loom story pipeline — four modules covering the full authoring pipeline.

  storyboard  — storyboard_inputs, parse_storyboard
  locations   — extract_locations
  characters  — extract_protagonist, revise_character, extract_characters,
                compose_base_prompt, compose_expressions, compose_affect_range
  wardrobe    — plan_scene_wardrobe, plan_story_wardrobe, plan_wardrobe,
                compose_outfit_prompt, refine_outfits,
                compose_poses, compose_outfit_poses,
                apply_manifest, plan_and_apply

Shared schemas + helpers live in _helpers.py.
"""

from ._helpers import (
    DEFAULT_SYSTEMS, NEEDS_IMAGE, STAGES,
    CHARACTERS_SCHEMA, LOCATIONS_SCHEMA, PROTAGONIST_SCHEMA, ROSTER_SCHEMA,
    WARDROBE_SCHEMA, SCENE_WARDROBE_SCHEMA,
    _APPEARANCE_RULE, _OUTFIT_RULE, _TAG_RULE,
    _arr, _call, _card_context, _slug, _str_arr, _sys,
)
from .storyboard import parse_storyboard, storyboard_inputs
from .locations import extract_locations
from .characters import (
    extract_characters, extract_protagonist, revise_character,
    compose_base_prompt,
    compose_expressions, compose_affect_range,
)
from .wardrobe import (
    plan_scene_wardrobe, plan_story_wardrobe, plan_wardrobe,
    compose_outfit_prompt, refine_outfits,
    compose_poses, compose_outfit_poses,
    apply_manifest, plan_and_apply,
)

__all__ = [
    # helpers / constants
    "DEFAULT_SYSTEMS", "NEEDS_IMAGE", "STAGES",
    "CHARACTERS_SCHEMA", "LOCATIONS_SCHEMA", "PROTAGONIST_SCHEMA", "ROSTER_SCHEMA",
    "WARDROBE_SCHEMA", "SCENE_WARDROBE_SCHEMA",
    "_APPEARANCE_RULE", "_OUTFIT_RULE", "_TAG_RULE",
    "_arr", "_call", "_card_context", "_slug", "_str_arr", "_sys",
    # storyboard
    "storyboard_inputs", "parse_storyboard",
    # locations
    "extract_locations",
    # characters
    "extract_protagonist", "revise_character", "extract_characters",
    "compose_base_prompt", "compose_expressions", "compose_affect_range",
    # wardrobe
    "plan_scene_wardrobe", "plan_story_wardrobe", "plan_wardrobe",
    "compose_outfit_prompt", "refine_outfits",
    "compose_poses", "compose_outfit_poses",
    "apply_manifest", "plan_and_apply",
]
