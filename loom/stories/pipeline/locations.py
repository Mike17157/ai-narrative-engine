"""Locations: extract distinct neutral background locations from the storyboard."""

from __future__ import annotations

from ._helpers import LOCATIONS_SCHEMA, _call, _slug, _sys


def extract_locations(provider, *, board: dict | None = None, premise: str = "",
                      systems: dict | None = None) -> dict:
    """Derive distinct neutral locations. Either from a storyboard `board` (consolidate the
    places its beats visit) OR — in the character-first flow — straight from a `premise`
    string (infer the places such a story would naturally visit)."""
    board = board or {}
    if (premise or "").strip() and not board.get("beats"):
        prompt = (f"PREMISE:\n{premise.strip()}\n\n"
                  "From this premise, infer 4-7 KEENLY DISTINCT neutral locations such a story "
                  "would naturally visit — strongly varied, specific places (not generic). "
                  "Pick the one it would most naturally OPEN in as `start`.")
    else:
        seen, places = set(), []
        for b in board.get("beats", []):
            loc = (b.get("location") or "").strip()
            if loc and loc.lower() not in seen:
                seen.add(loc.lower()); places.append(loc)
        place_lines = "\n".join(f"- {p}" for p in places) or "(infer from the logline)"
        prompt = (f"LOGLINE: {board.get('logline','')}\nTONE: {board.get('tone','')}\n"
                  f"PLACES THE STORY VISITS:\n{place_lines}\n\n"
                  "Consolidate these into a tight set of KEENLY DISTINCT neutral locations.")
    out = _call(provider, _sys(systems or {}, "locations"), prompt, LOCATIONS_SCHEMA, "locations")

    locations, id_map = [], {}
    for loc in out.get("locations", []):
        lid = _slug(loc.get("id") or loc.get("name"), f"place-{len(id_map)+1}")
        base, n = lid, 2
        while lid in id_map.values():
            lid, n = f"{base}-{n}", n + 1
        id_map[(loc.get("name") or lid).lower()] = lid
        locations.append({"id": lid, "name": loc.get("name", lid),
                          "description": loc.get("description", ""),
                          "background_prompt": loc.get("background_prompt", "")})
    raw_start = (out.get("start") or "").lower()
    start = id_map.get(raw_start) or next(
        (l["id"] for l in locations if l["id"] == out.get("start")), None
    ) or (locations[0]["id"] if locations else None)
    return {"start": start, "locations": locations}
