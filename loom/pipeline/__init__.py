"""Loom pipeline — numbered step modules for the story + character authoring process.

Steps run in order but can be invoked independently:
  s01_storyboard  — storyboard_inputs, parse_storyboard
  s02_locations   — extract_locations
  s03_characters  — extract_protagonist, revise_character, extract_characters
  s04_base_prompt — compose_base_prompt
  s05_wardrobe_plan   — plan_wardrobe
  s06_wardrobe_refine — compose_outfit_prompt, refine_outfits
  s07_manifest    — apply_manifest, plan_and_apply
  s08_expressions — compose_expressions
  s09_poses       — compose_poses
  s10_affect      — compose_affect_range

Shared schemas + helpers live in _helpers.py.
"""

from ._helpers import (
    DEFAULT_SYSTEMS, NEEDS_IMAGE, STAGES,
    CHARACTERS_SCHEMA, LOCATIONS_SCHEMA, PROTAGONIST_SCHEMA, ROSTER_SCHEMA, WARDROBE_SCHEMA,
    _APPEARANCE_RULE, _OUTFIT_RULE, _TAG_RULE,
    _arr, _call, _card_context, _slug, _str_arr, _sys,
)
from .s01_storyboard import parse_storyboard, storyboard_inputs
from .s02_locations import extract_locations
from .s03_characters import extract_characters, extract_protagonist, revise_character
from .s04_base_prompt import compose_base_prompt
from .s05_wardrobe_plan import plan_wardrobe
from .s06_wardrobe_refine import compose_outfit_prompt, refine_outfits
from .s07_manifest import apply_manifest, plan_and_apply
from .s08_expressions import compose_expressions
from .s09_poses import compose_poses
from .s10_affect import compose_affect_range

__all__ = [
    # helpers / constants
    "DEFAULT_SYSTEMS", "NEEDS_IMAGE", "STAGES",
    "CHARACTERS_SCHEMA", "LOCATIONS_SCHEMA", "PROTAGONIST_SCHEMA", "ROSTER_SCHEMA", "WARDROBE_SCHEMA",
    "_APPEARANCE_RULE", "_OUTFIT_RULE", "_TAG_RULE",
    "_arr", "_call", "_card_context", "_slug", "_str_arr", "_sys",
    # s01
    "storyboard_inputs", "parse_storyboard",
    # s02
    "extract_locations",
    # s03
    "extract_protagonist", "revise_character", "extract_characters",
    # s04
    "compose_base_prompt",
    # s05
    "plan_wardrobe",
    # s06
    "compose_outfit_prompt", "refine_outfits",
    # s07
    "apply_manifest", "plan_and_apply",
    # s08
    "compose_expressions",
    # s09
    "compose_poses",
    # s10
    "compose_affect_range",
]
