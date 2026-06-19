"""Locations: extract distinct neutral background locations from the storyboard."""

from __future__ import annotations

from ._helpers import LOCATIONS_SCHEMA, _call, _slug, _sys


def extract_locations(provider, *, board: dict, systems: dict | None = None) -> dict:
    seen, places = set(), []
    for b in board.get("beats", []):
        loc = (b.get("location") or "").strip()
        if loc and loc.lower() not in seen:
            seen.add(loc.lower()); places.append(loc)
    place_lines = "\n".join(f"- {p}" for p in places) or "(infer from the logline)"
    out = _call(provider, _sys(systems or {}, "locations"),
                f"LOGLINE: {board.get('logline','')}\nTONE: {board.get('tone','')}\n"
                f"PLACES THE STORY VISITS:\n{place_lines}\n\n"
                "Consolidate these into a tight set of KEENLY DISTINCT neutral locations.",
                LOCATIONS_SCHEMA, "locations")

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
