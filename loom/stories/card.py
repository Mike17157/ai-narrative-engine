"""The story CONTEXT CARD — the one layered spine of a story's context.

Every generator READS a projection of this card, and narrative functions MUTATE it:

  • image generation  — sprites + location scenes stack its layers into prompts
    (the `/characters/{key}/sprite-stack` endpoint is the cast layer's deep zoom);
  • narrative context — play assembles its lanes from the same layers
    (play_context reads cast/relationships/map; the per-thread State doc is the
    RUNTIME accumulator that consolidates back into these authored layers);
  • narrative functions — plot authoring and play mutate layers through
    `LAYER_FIELDS`-routed patches (apply via ctx.update_story_fields, validated),
    or richer graph_ops functions whose `writes` names a layer.

The card accumulates tab by tab — each layer is OWNED by the tab that authors it:

    overview       art style (L0 of every image) + premise/tone/themes (the frame)
    plot           arcs / beats — plot points that DEMAND locations + characters
    relationships  the roster: who exists, where they live, how they bond
    map            locations + scene prompts + rendered scene images
    cast           per-character identity/outfits/emotions (sprite coverage)

`build_card` is PURE (plain dicts in → card out) so it self-checks without an app
context; the router endpoint gathers the inputs. Each layer reports `todo` — the
unfinished work at that step — so the card is checkable stage by stage.
"""
from __future__ import annotations



# Layer taxonomy — ordered by CREATIVE DEPENDENCY (the Snowflake/Truby "expand from the seed"
# discipline), not by artifact type: the controlling idea first, then the WORLD it happens in,
# then the CAST + their bonds, then the PLOT that emerges from those people under pressure, then
# PRODUCTION (rendered assets). Grouped into three tiers:
#   • bible       — the constant truth (premise/theme, world, cast, fixed relationships)
#   • progression — the staged plan that unfolds (arcs, scenes)
#   • production  — rendered assets (sprites, wardrobe, scene images)
# RUNTIME (intimacy drift, world state, memory) is play, evolved live — not authored here.
# `mutators` declares which surfaces may write the layer (author = the tab UIs; play = the runtime).
LAYERS: tuple[dict, ...] = (
    {"id": "overview",      "tier": "bible",       "label": "Premise & theme", "source": "overview",      "mutators": ("author",)},
    {"id": "map",           "tier": "bible",       "label": "World",           "source": "map",           "mutators": ("author", "play")},
    {"id": "relationships", "tier": "bible",       "label": "Cast & bonds",    "source": "relationships", "mutators": ("author", "play")},
    {"id": "plot",          "tier": "progression", "label": "Arc & scenes",    "source": "plot",          "mutators": ("author", "play")},
    {"id": "cast",          "tier": "production",  "label": "Production",      "source": "cast",          "mutators": ("author", "play")},
)

# The tiers in order, with display labels — the queue/UI groups sections under these.
TIERS: tuple[tuple[str, str], ...] = (
    ("bible", "Bible — the constant truth"),
    ("progression", "Progression — the staged plan"),
    ("production", "Production — rendered assets"),
)

# Per-layer story fields a patch may touch — the mutation whitelist. Cast portrait
# manifests are NOT story fields (they live per-character); the cast layer mutates
# through the character endpoints instead.
LAYER_FIELDS: dict[str, tuple[str, ...]] = {
    "overview":      ("premise", "tone", "themes", "art_style", "premise_parts", "storyboard"),
    "plot":          ("arcs", "chapters", "scenes", "storyboard"),
    "relationships": ("relationships", "cast"),
    "map":           ("locations", "start", "connections", "conditions", "world"),
}


def build_card(story: dict, manifests: dict[str, dict] | None = None,
               global_style: str = "") -> dict:
    """Assemble the full card from a story dict (+ per-character portrait manifests).

    Returns {layers: [{id, source, mutators, content, todo}], story: key?}. `todo`
    is the layer's unfinished work — the per-step checklist the tabs surface."""
    story = story or {}
    manifests = manifests or {}
    cast = story.get("cast") or []
    locations = story.get("locations") or []
    loc_ids = {l.get("id") for l in locations}
    rels = story.get("relationships") or []
    bonded = {r.get("source") for r in rels} | {r.get("target") for r in rels}

    # overview — the frame + L0 style + the premise's structured components
    style = (story.get("art_style") or "").strip()
    parts = {k: v for k, v in (story.get("premise_parts") or {}).items() if (v or "").strip()}
    part_ids = ("root", "question", "creeds", "tragedy", "protagonist", "stakes", "texture")
    overview = {
        "art_style": style or global_style,
        "art_style_source": "story" if style else "global",
        "premise": story.get("premise", ""), "tone": story.get("tone", ""),
        "themes": story.get("themes") or [],
        "premise_parts": parts,
        "premise_parts_total": len(part_ids),
    }
    missing_parts = [p for p in part_ids if p not in parts]
    overview_todo = [t for t, missing in (
        ("write a premise", not overview["premise"]),
        (f"components to write: {', '.join(missing_parts)}", bool(missing_parts)),
        ("set the story art style (using the global default)", not style),
    ) if missing]

    # plot — arcs / beats (the demand side: plot points that call for places + people)
    arcs = story.get("arcs") or []
    beats = ((story.get("storyboard") or {}).get("beats")) or []
    plot = {"arcs": [{"id": a.get("id"), "name": a.get("name"),
                      "beats": len(a.get("nodes") or {})} for a in arcs],
            "flat_beats": len(beats)}
    plot_todo = ["no plot yet — draft arcs or a storyboard"] if not (arcs or beats) else []

    # relationships — who exists, where they live, how they bond
    members = [{"character": m.get("character"), "primary": bool(m.get("primary")),
                "home": m.get("home", "")} for m in cast]
    relationships = {"cast": members, "bonds": len(rels)}
    rel_todo = []
    unplaced = [m["character"] for m in members if not m["home"] or m["home"] not in loc_ids]
    lonely = [m["character"] for m in members if m["character"] not in bonded]
    if unplaced:
        rel_todo.append(f"place {', '.join(unplaced)} on the map")
    if lonely:
        rel_todo.append(f"no bonds yet: {', '.join(lonely)}")

    # map — locations + scene prompts + rendered images
    map_locs = [{"id": l.get("id"), "name": l.get("name"), "parent": l.get("parent", ""),
                 "has_prompt": bool((l.get("background_prompt") or l.get("description") or "").strip()),
                 "has_image": bool(l.get("background"))} for l in locations]
    conditions = [{"id": c.get("id"), "name": c.get("name"), "kind": c.get("kind", "")}
                  for c in (story.get("conditions") or []) if (c.get("name") or "").strip()]
    map_todo = []
    if not locations:
        map_todo.append("no locations yet")
    unrendered = [l["name"] or l["id"] for l in map_locs if l["has_prompt"] and not l["has_image"]]
    if unrendered:
        map_todo.append(f"scene image missing: {', '.join(unrendered)}")
    if not conditions:
        map_todo.append("no setting conditions yet — the world has no stages to bring out the cast")

    # cast — sprite coverage per character × outfit (deep zoom = /sprite-stack)
    cast_chars, cast_todo = [], []
    for m in members:
        ck = m["character"]
        man = manifests.get(ck) or {}
        outfits = []
        for o in man.get("outfits") or []:
            rng = o.get("range") or []
            done = len(o.get("expressions") or {})
            outfits.append({"id": o.get("id"), "range": len(rng), "rendered": done})
            if rng and done < len(rng):
                cast_todo.append(f"{ck}/{o.get('id')}: {done}/{len(rng)} sprites")
        if not outfits:
            cast_todo.append(f"{ck}: no wardrobe yet")
        cast_chars.append({"character": ck, "outfits": outfits})

    contents = {"overview": (overview, overview_todo), "plot": (plot, plot_todo),
                "relationships": (relationships, rel_todo),
                "map": ({"locations": map_locs, "conditions": conditions}, map_todo),
                "cast": ({"characters": cast_chars}, cast_todo)}
    return {"layers": [{**spec, "mutators": list(spec["mutators"]),
                        "content": contents[spec["id"]][0], "todo": contents[spec["id"]][1]}
                       for spec in LAYERS]}


def layer_patch_fields(layer: str, patch: dict) -> dict:
    """The mutation chokepoint's filter: keep only the fields `layer` is allowed to touch.
    Raises KeyError for a layer with no story-field mapping (cast mutates elsewhere)."""
    allowed = LAYER_FIELDS[layer]
    return {k: v for k, v in (patch or {}).items() if k in allowed}


if __name__ == "__main__":   # ponytail: one runnable check — build + todo + patch filter
    story = {
        "premise": "p", "tone": "t", "themes": ["x"], "art_style": "",
        "premise_parts": {"protagonist": "a woodcutter", "lie": ""},
        "cast": [{"character": "a", "primary": True, "home": "l1"},
                 {"character": "b", "home": ""}],
        "locations": [{"id": "l1", "name": "Home", "background_prompt": "cozy", "background": None}],
        "relationships": [{"source": "a", "target": "b"}],
        "arcs": [], "storyboard": {"beats": []},
    }
    man = {"a": {"outfits": [{"id": "o1", "range": ["happy", "sad"], "expressions": {"happy": "h.png"}}]}}
    card = build_card(story, man, global_style="G.")
    ls = {l["id"]: l for l in card["layers"]}
    assert [l["id"] for l in card["layers"]] == [s["id"] for s in LAYERS]
    assert ls["overview"]["content"]["art_style"] == "G." and ls["overview"]["content"]["art_style_source"] == "global"
    assert any("art style" in t for t in ls["overview"]["todo"])
    assert ls["overview"]["content"]["premise_parts"] == {"protagonist": "a woodcutter"}
    assert any(t.startswith("components to write: root, question, creeds, tragedy,") for t in ls["overview"]["todo"]), ls["overview"]["todo"]
    assert ls["plot"]["todo"], "empty plot must todo"
    assert any("place b" in t for t in ls["relationships"]["todo"]), ls["relationships"]["todo"]
    assert any("scene image missing: Home" in t for t in ls["map"]["todo"])
    assert any("no setting conditions" in t for t in ls["map"]["todo"]), ls["map"]["todo"]
    assert any("a/o1: 1/2" in t for t in ls["cast"]["todo"]) and any("b: no wardrobe" in t for t in ls["cast"]["todo"])
    assert layer_patch_fields("map", {"locations": [], "premise": "nope"}) == {"locations": []}
    try:
        layer_patch_fields("cast", {})
        raise AssertionError("cast must not map to story fields")
    except KeyError:
        pass

    print("ok — card build + per-layer todo + patch filter")
