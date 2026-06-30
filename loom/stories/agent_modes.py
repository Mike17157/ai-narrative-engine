"""The Author agent's MODES — hard-coded in the backend (not data-driven JSON).

A mode is a facet of the Author: a persona + the set of registered tool FUNCTIONS it offers +
how the request routes to it (trigger keywords / semantic) + which craft section + grounding to
inject. The function names resolve straight from the CODE registry (scripts.py / stage_tools.py),
so the whole tool surface is defined in code like a normal backend — `configs/story_agent.json`
keeps only the editable PROSE/params (base system, craft anchors, routing thresholds, tool_policy).

`agent_config.load_config` injects `MODES` as the config's `modes`, overriding any stale JSON copy.
"""
from __future__ import annotations

MODES: dict[str, dict] = {
    "_smith_tools": {
        "label": "characters",
        "triggers": ["character", "persona", "cast member", "villain", "protagonist", "npc",
                     "create a character", "add a character", "new character", "someone new",
                     "make a person", "edit character", "change character", "rename character",
                     "update character", "appearance", "looks like", "describe", "their backstory",
                     "their personality", "relationship", "bond", "feels about", "feel toward",
                     "rival", "lover", "ally", "enemy", "friend", "resent", "trust", "dynamic between",
                     "how they feel", "strained", "hostile", "warm", "devoted", "distant", "closer",
                     "reconcile", "drift apart"],
        "persona": "Work on the CAST. NEW people: build REAL, idiosyncratic characters — never "
                   "archetypes (a specific wound, the lie it bred, a want vs a deeper need, a "
                   "contradiction, a distinct voice), with the rich create_character tool; never a bare "
                   "add. EDITS: keep an existing character internally consistent — change only what's "
                   "asked, concrete over adjectives. RELATIONSHIPS: prose in 2-3 words (the dynamic) + a "
                   "coarse stance for colour, never numbers; bonds are asymmetric and specific — how they "
                   "actually act around each other.",
        "craft_section": "character",
        "inject": ["concreteness", "psyche"],
        "functions": ["create_character", "add_character", "set_character_field", "remove_character",
                      "design_wardrobe", "record_scene", "generate_image", "set_relationship",
                      "remove_relationship", "set_world_field"],
    },
    "_location_fns": {
        "label": "locations",
        "triggers": ["location", "place", "setting", "room", "city", "map", "where it happens"],
        "persona": "Build places that feel lived-in and specific, not stage sets. Name concrete "
                   "materials, light, sound, smell, and wear; show who uses the space through what "
                   "they've left in it and what it's shaped to. Let the place carry the story's tone and "
                   "the world's era — without baking in people or events. Concrete particulars over "
                   "mood-words; one off-detail that makes it this place and no other.",
        "craft_section": "",
        "inject": ["concreteness"],
        "functions": ["add_location", "set_location_field", "remove_location", "set_start", "generate_image"],
    },
    "_scene_fns": {
        "label": "scenes & places",
        "triggers": ["scene", "connect scenes", "connect", "link", "navigate", "move to", "area",
                     "sub-scene", "path between"],
        "persona": "Wire scenes and places into a navigable map; anchor character sub-scenes to the "
                   "places they belong to. Make each transition concrete — what you cross, see, and pass "
                   "through to get from one to the next, not just an arrow.",
        "craft_section": "",
        "inject": ["concreteness"],
        "functions": ["connect_scenes", "disconnect_scenes", "set_location_area", "generate_image"],
    },
    "_wardrobe_fns": {
        "label": "wardrobe",
        "triggers": ["outfit", "wardrobe", "clothes", "dress", "costume", "attire", "what they wear"],
        "persona": "Design outfits as CHARACTERIZATION, not costume. A wardrobe is a small coherent set "
                   "(everyday, role/work, formal, plus any scene-specific looks) where every garment "
                   "reveals the person — their status and self-image, what they're performing or hiding, "
                   "and the one off-note that betrays their wound or contradiction. Ground each piece in "
                   "the world's era, materials, and climate; give image-ready garment descriptors (cut, "
                   "fabric, colour, condition, how it's worn). No generic filler, no fantasy-catalogue "
                   "clichés.",
        "craft_section": "character",
        "inject": ["concreteness", "psyche"],
        "functions": ["design_wardrobe", "plan_cast_outfit", "generate_image"],
    },
    "_story_tools": {
        "label": "shaping the story",
        "triggers": ["storyboard", "title", "rename the story", "call the story", "name the story",
                     "premise", "theme", "arc", "plot", "beats", "cover", "outline", "ending", "climax",
                     "finale"],
        "persona": "Work as a developmental editor: find the truest story latent in the cast — wound, "
                   "want vs need, human inevitable conflict, every beat costs something. The climax turns "
                   "on the protagonist's self-revelation, with the world's fate bound to their choice — "
                   "never a generic greater good. Treat the premise as a FOUNDATION to build outward from "
                   "and surprise, not a spec. Concrete over abstract; no theme-word salad.",
        "craft_section": "arc",
        "inject": [],
        "functions": ["set_story_title", "generate_story_cover", "storyboard", "set_spine", "add_beat",
                      "set_beat_field", "connect", "disconnect", "insert_between", "move_beat",
                      "reorder_beats", "delete_beat"],
    },
}
