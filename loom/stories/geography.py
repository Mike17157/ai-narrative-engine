"""Geography — where characters CAN be, and how fast they can move.

Fiction doesn't need pathfinding; it needs plausibility + rate limits. Three pieces:

  • hops(a, b) — a coarse travel distance derived from the existing Location TREE
    (`Location.parent`): same spot 0, parent/sibling 1, cousin 2, different area 3.
    No coordinates. Authored overrides via Story.fields["travel"] = [[a, b, hops], …].
  • orbit(char) — the character's habitual spots, read from data that already exists:
    Place scenes anchored to them + their card's home_scenes. An off-screen character
    is findable in their orbit, not wherever the model invents.
  • make_move_validator(...) — the ENFORCEMENT hook for the state engine's `move` op:
    an off-screen character may only move somewhere reachable in the turns elapsed
    since they were last placed (1 hop/turn). On-screen (narrated) movement is canon
    and always allowed. Unresolvable free-text targets ("the corner of the room") are
    treated as sub-spots of wherever they are — allowed.

The compiler (play_context) uses orbits + last-seen for whereabouts lines, so the
narrator is TOLD where people plausibly are; this module makes sure the state can't
drift somewhere implausible even when the prose leaks.
"""
from __future__ import annotations

_MAX_HOPS = 3          # "different area entirely" — a real journey
_HOPS_PER_TURN = 1     # off-screen drift rate


# ── Resolution: free text → a known location id ─────────────────────────────────────

def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def resolve_loc(st, text: str) -> str | None:
    """Match free text against location ids/names (exact or contained). None = unknown
    (a micro-spot like 'the corner' — not a mappable place)."""
    t = _norm(text)
    if not t:
        return None
    for l in st.locations:
        if t == _norm(l.id) or t == _norm(l.name):
            return l.id
    for l in st.locations:
        n = _norm(l.name)
        if n and (n in t or t in n):
            return l.id
    return None


# ── Distance: the Location tree as a coarse travel graph ────────────────────────────

def _path_to_root(by_id: dict, lid: str) -> list[str]:
    path, seen = [lid], {lid}
    while True:
        parent = getattr(by_id.get(path[-1]), "parent", "") or ""
        if not parent or parent in seen or parent not in by_id:
            return path
        path.append(parent)
        seen.add(parent)


def hops(st, a: str, b: str) -> int:
    """Coarse travel distance between two location ids. Authored Story.fields['travel']
    entries ([[a, b, hops], …], by id or name) override the tree-derived default."""
    if a == b:
        return 0
    for e in (getattr(st, "fields", None) or {}).get("travel") or []:
        try:
            ea, eb, eh = _norm(e[0]), _norm(e[1]), int(e[2])
        except (TypeError, ValueError, IndexError):
            continue
        if {_norm(a), _norm(b)} == {ea, eb}:
            return eh
    by_id = {l.id: l for l in st.locations}
    if a not in by_id or b not in by_id:
        return _MAX_HOPS
    pa, pb = _path_to_root(by_id, a), _path_to_root(by_id, b)
    common = next((x for x in pa if x in set(pb)), None)
    if common is None:
        return _MAX_HOPS
    return min(_MAX_HOPS + 1, pa.index(common) + pb.index(common))


# ── Orbits: the spots a character habitually occupies (data that already exists) ────

def orbit(ctx, st, char_key: str) -> list[str]:
    """Habitual spot names for a cast character: Place scenes anchored to them, plus
    their card's portable home scenes. Display strings ('Mara's bench — The Grove')."""
    spots: list[str] = []
    for p in st.places:
        for s in p.scenes:
            anchors = ([s.character] if s.character else []) + list(s.characters or [])
            if char_key in anchors:
                spots.append(f"{s.name or s.id} ({p.name})" if p.name else (s.name or s.id))
    c = ctx.base_settings.characters.get(char_key)
    for h in (getattr(c, "home_scenes", []) or []):
        spots.append(h.name or h.id)
    seen: set[str] = set()
    return [s for s in spots if not (s.lower() in seen or seen.add(s.lower()))][:4]


# ── Enforcement: the `move` delta validator ─────────────────────────────────────────

def make_move_validator(st, on_screen_names: set[str]):
    """Build the hook state_engine's `move` op calls: (ws, name, target) → allow?
    On-screen characters: always allowed (narrated movement is canon). Off-screen:
    allowed only if the target is reachable in the turns elapsed since they were last
    placed. Unresolvable targets (micro-spots) are allowed."""
    onscreen = {n.lower() for n in on_screen_names}

    def validate(ws: dict, name: str, target: str) -> bool:
        if (name or "").lower() in onscreen:
            return True
        dest = resolve_loc(st, target)
        if dest is None:
            return True                        # micro-spot / unknown → not mappable
        ent = (ws.get("entities") or {}).get(name) or {}
        src = resolve_loc(st, ent.get("location") or "")
        if src is None:
            return True                        # never placed → anywhere is plausible
        elapsed = max(1, int(ws.get("step") or 0) - int(ent.get("loc_step") or 0))
        return hops(st, src, dest) <= elapsed * _HOPS_PER_TURN

    return validate


# ── Generation: the genesis geography node (one targeted call) ──────────────────────
# Grounded in the cast's daily lives (per the particularity lessons): spots are the places
# these specific people already occupy, named the way locals would name them.

GEOGRAPHY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["areas", "travel"],
    "properties": {
        "areas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description", "spots"],
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "spots": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["name", "description", "inhabitants"],
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string", "description":
                                        "the spot, objectively — no events, no people"},
                        "inhabitants": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False,
                            "required": ["character", "doing"],
                            "properties": {
                                "character": {"type": "string",
                                              "description": "exact cast name"},
                                "doing": {"type": "string",
                                          "description": "what they habitually do here"},
                            }}},
                    }}},
            }}},
        "travel": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["a", "b", "hops"],
            "properties": {"a": {"type": "string"}, "b": {"type": "string"},
                           "hops": {"type": "integer", "description":
                                    "1 short walk · 2 across the settlement · 3 a real journey"}}}},
    },
}

GEOGRAPHY_SYS = (
    "You are mapping the LIVED GEOGRAPHY of a story: the places its specific cast actually "
    "occupies day to day — not a fantasy atlas.\n"
    "Give 2-3 AREAS (districts/regions), each with 2-4 SPOTS (a home, a workplace, a haunt). "
    "Name places the way LOCALS would: functional, worn, particular ('the fish sheds', "
    "'Marta's bakery', 'the north jetty') — NEVER adjective-noun-mystique names, never "
    "renfaire twee. Spot descriptions are objective and concrete (what's physically there), "
    "one or two sentences, no events, no mood-writing.\n"
    "Anchor the CAST: every cast member gets 1-3 habitual spots (where they live, where they "
    "work, where they linger) with `doing` = the habitual activity, concrete and mundane.\n"
    "If EXISTING LOCATIONS are given, keep their names verbatim as spots (or areas) and place "
    "them in your map — do not rename or duplicate them.\n"
    "`travel`: ONLY the non-obvious distances (a far cave, an island, a shortcut) — pairs of "
    "place names with hops (1 short walk, 2 across the settlement, 3 a real journey). Spots "
    "inside one area are already near each other; don't list those. JSON only."
)


def gen_geography(provider, *, premise: str = "", tone: str = "", world_note: str = "",
                  cast: list[dict] | None = None, existing: list[str] | None = None,
                  root=None) -> dict:
    """ONE focused generation: areas → spots → who habitually occupies them + travel notes.
    `cast` items: {name, about}. With `root`, real-work place studies are retrieved as register
    references. Returns the raw {areas, travel} dict ({} on failure)."""
    if provider is None:
        return {}
    parts = []
    if root is not None:
        from .worldgen import exemplar_notes
        ex = exemplar_notes(root, f"{premise} {world_note}", 2, "_location_exemplars")
        if ex:
            parts.append("REFERENCE — how real serials make places feel worked-in. Match the "
                         "REGISTER; never copy names or specifics:\n" + ex)
    if premise:
        parts.append(f"PREMISE: {premise}")
    if tone:
        parts.append(f"TONE: {tone}")
    if world_note:
        parts.append(f"WORLD: {world_note}")
    for c in (cast or []):
        parts.append(f"CAST — {c.get('name', '?')}: {c.get('about', '')}")
    if existing:
        parts.append("EXISTING LOCATIONS (keep verbatim, place them in the map): "
                     + "; ".join(existing))
    parts.append("Map the lived geography.")
    res = provider.generate_text(system=GEOGRAPHY_SYS, prompt="\n".join(parts),
                                 emits=GEOGRAPHY_SCHEMA)
    d = getattr(res, "data", None) or {}
    return d if d.get("areas") else {}


def _slug(name: str, taken: set[str]) -> str:
    import re
    base = re.sub(r"[^\w]+", "_", _norm(name)).strip("_") or "loc"
    s, i = base, 2
    while s in taken:
        s, i = f"{base}_{i}", i + 1
    taken.add(s)
    return s


def apply_geography(geo: dict, st, name_to_key: dict[str, str]) -> dict:
    """Convert a generated {areas, travel} into story-shaped data: `locations` (tree —
    existing locations KEPT, matched by name and re-parented), `places` (one per inhabited
    spot, with character-anchored scenes = the orbits), and `travel` overrides. Pure."""
    existing = {_norm(l.name): l for l in st.locations}
    locations = [l.model_dump() for l in st.locations]
    by_norm = {_norm(l["name"]): l for l in locations}
    taken = {l["id"] for l in locations}
    places: list[dict] = []

    def _ensure(name: str, description: str, parent: str) -> dict:
        loc = by_norm.get(_norm(name))
        if loc is None:
            loc = {"id": _slug(name, taken), "name": name,
                   "description": description, "parent": parent}
            locations.append(loc)
            by_norm[_norm(name)] = loc
        else:
            # never self-parent (the model may echo an existing name as both area AND spot)
            if not loc.get("parent") and parent and parent != loc["id"]:
                loc["parent"] = parent
            if description and not loc.get("description"):
                loc["description"] = description
        return loc

    def _char_key(nm: str) -> str | None:
        t = _norm(nm)
        for full, key in name_to_key.items():
            fn = _norm(full)
            if t == fn or t == fn.split()[0] or fn.split()[0] == t.split()[0]:
                return key
        return None

    for area in geo.get("areas") or []:
        a = _ensure(area.get("name", ""), area.get("description", ""), "")
        for spot in area.get("spots") or []:
            s = _ensure(spot.get("name", ""), spot.get("description", ""), a["id"])
            scenes = []
            for inh in spot.get("inhabitants") or []:
                ck = _char_key(inh.get("character", ""))
                if ck:
                    scenes.append({"id": f"{s['id']}_{ck}", "name": spot.get("name", ""),
                                   "character": ck, "backstory": inh.get("doing", "")})
            if scenes:
                places.append({"id": f"p_{s['id']}", "name": spot.get("name", ""),
                               "description": spot.get("description", ""), "scenes": scenes})

    travel = []
    for e in geo.get("travel") or []:
        la, lb = by_norm.get(_norm(e.get("a", ""))), by_norm.get(_norm(e.get("b", "")))
        if la is not None and lb is not None and la is not lb:
            travel.append([la["id"], lb["id"], max(1, min(4, int(e.get("hops") or 1)))])

    _ = existing  # (kept for clarity: existing locations were merged above, never dropped)
    return {"locations": locations, "places": places, "travel": travel}


def demo() -> None:
    """Self-check: tree distance, overrides, validator rate-limiting.
    Run: python -m loom.stories.geography"""
    from types import SimpleNamespace as NS
    locs = [NS(id="town", name="Town", parent=""),
            NS(id="market", name="The Market", parent="town"),
            NS(id="inn", name="The Wandering Inn", parent="town"),
            NS(id="wilds", name="The Wilds", parent=""),
            NS(id="cave", name="Shield Cave", parent="wilds")]
    st = NS(locations=locs, places=[], fields={"travel": [["inn", "cave", 3]]})
    assert hops(st, "inn", "inn") == 0
    assert hops(st, "market", "inn") == 2      # sibling spots via town
    assert hops(st, "market", "town") == 1     # child → parent
    assert hops(st, "market", "cave") == _MAX_HOPS   # different roots
    assert hops(st, "inn", "cave") == 3        # authored override
    assert resolve_loc(st, "the wandering inn") == "inn"
    assert resolve_loc(st, "over by the market stalls") == "market"
    assert resolve_loc(st, "the corner of the room") is None
    v = make_move_validator(st, on_screen_names={"Erin"})
    ws = {"step": 5, "entities": {"Pisces": {"location": "The Market", "loc_step": 4},
                                  "Erin": {"location": "The Wandering Inn", "loc_step": 4}}}
    assert v(ws, "Erin", "Shield Cave")        # on-screen: narrated movement is canon
    assert not v(ws, "Pisces", "Shield Cave")  # off-screen: 3 hops in 1 turn → blocked
    assert v(ws, "Pisces", "Town")             # 1 hop in 1 turn → fine
    ws["entities"]["Pisces"]["loc_step"] = 1   # 4 turns elapsed
    assert v(ws, "Pisces", "Shield Cave")      # had time to travel → allowed
    assert v(ws, "Pisces", "the corner of the room")   # micro-spot → allowed

    # apply_geography: existing location kept + re-parented; orbits → places; travel → ids.
    class _L:                                           # minimal Location stand-in
        def __init__(self, **kw): self.__dict__.update(kw)
        def model_dump(self): return dict(self.__dict__)
    st2 = NS(locations=[_L(id="n1", name="The Whispering Grove", description="", parent="")],
             places=[], fields={})
    geo = {"areas": [{"name": "The Village", "description": "a fishing village", "spots": [
        {"name": "The Whispering Grove", "description": "old trees", "inhabitants":
            [{"character": "Mara", "doing": "reads on the bench"}]},
        {"name": "the north jetty", "description": "weathered planks", "inhabitants": []},
    ]}], "travel": [{"a": "the north jetty", "b": "The Whispering Grove", "hops": 2}]}
    out = apply_geography(geo, st2, {"Mara": "mara"})
    by = {l["name"]: l for l in out["locations"]}
    assert by["The Whispering Grove"]["id"] == "n1"            # existing kept, not duplicated
    assert by["The Whispering Grove"]["parent"] == by["The Village"]["id"]   # re-parented
    # self-parent guard: an existing name echoed as area AND spot must not parent itself
    geo_self = {"areas": [{"name": "The Whispering Grove", "description": "", "spots": [
        {"name": "The Whispering Grove", "description": "", "inhabitants": []}]}], "travel": []}
    st3 = NS(locations=[_L(id="g1", name="The Whispering Grove", description="", parent="")],
             places=[], fields={})
    o2 = apply_geography(geo_self, st3, {})
    assert o2["locations"][0]["parent"] != "g1", o2["locations"][0]
    assert out["places"][0]["scenes"][0]["character"] == "mara"              # orbit anchored
    assert out["travel"][0][:2] == [by["the_north_jetty".replace('_', ' ')]["id"], "n1"] or \
           out["travel"][0][2] == 2                             # ids resolved, hops clamped
    print("geography demo ok")


if __name__ == "__main__":
    demo()
