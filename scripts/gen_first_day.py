"""Generate an ORIGINAL green-city first-day ensemble via the character engine + bond-weave,
shaped to the same OPENING difficulty as Rewrite's common route — an ordinary school morning in a
showcase eco-city, an occult club that recruits the player, and a night side to the city that does
not obey its brochures — WITHOUT reusing a name, personality, or plot beat from that game.
Persists a real story into configs/stories.db through the production chokepoints, then ACTIVATES it
for play. Idempotent: if the story already exists it skips generation and only re-activates.
Run: python -m scripts.gen_first_day
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from loom.server.app import build_context, create_app
from loom.stories.characters.engine import generate_cast, generate_history, generate_setting

WORLD = (
    "Midorigaoka — a showcase eco-city that won awards for folding a forest into its streets: green "
    "roofs by law, a river park through the center, solar canopies over every schoolyard. The city "
    "markets itself as the place where nature and people finally agreed. Officially, that's all true. "
    "Unofficially: the central forest is closed after dark and nobody prints why; joggers report "
    "glimpsing shapes that the city calls deer; a school rumor says the old weather station in the "
    "trees still powers itself at night. Life runs on ordinary rails — homeroom, club hours, the "
    "shopping arcade, curfew. But the brochures photograph the city by day, and the day is only half "
    "of it."
)

# Function-level slots (roles, NOT characters) — the ensemble's STRUCTURAL jobs for a first-day
# opening: the constant companion, the recruiter, the rumor-carriers, the one who doesn't fit the
# day side of the city. Personalities are invented fresh by the engine from the archetypes below.
ROLES = [
    ("the classmate who has walked to school with the player since childhood and treats the "
     "city's weirdness as a running gag", "mundane"),
    ("the imperious third-year who runs the school's occult research society and is determined "
     "to recruit the player today, whether or not they agree", "mundane"),
    ("a soft-spoken underclassman who tends the school's rooftop garden and insists the plants "
     "lean toward the forest at night", "touched"),
    ("a broad-shouldered transfer student with a whispered reputation for violence who is only "
     "ever seen protecting strays and underclassmen", "touched"),
    ("a girl the player has only ever glimpsed at dusk near the forest gate, ribbon catching "
     "the light, who no one else seems to see", "heir"),
]

# One archetype per role (index-paired) — known chemistry, cast like a band.
ENSEMBLE = ["childhood friend", "chuuni", "dandere", "delinquent with a soft heart", "kuudere"]

LOCATIONS = [
    {"id": "home", "name": "A rowhouse bedroom above the river park",
     "description": "A small second-floor room; the window faces the tree canopy the city is "
                     "proud of, and at night the canopy moves before the wind does."},
    {"id": "school_gate", "name": "Sakuradai High front gate",
     "description": "Solar canopy over the entrance walk, award plaque for 'Greenest Campus' "
                     "bolt-headed to the pillar; students funnel under it every morning."},
    {"id": "classroom", "name": "Class 2-A homeroom",
     "description": "Third floor, corner room. The windows look straight into the central "
                     "forest's edge — closer than any map of the campus admits."},
    {"id": "occult_club", "name": "Occult Research Society room",
     "description": "A borrowed AV room: blackout curtains, a corkboard of pinned sighting "
                     "clippings, a kettle, and one chair that is clearly the president's throne."},
    {"id": "rooftop_garden", "name": "The rooftop garden",
     "description": "Planter boxes and a weather vane, open to club members only. All the "
                     "trellises lean a few degrees toward the forest."},
    {"id": "forest_gate", "name": "The central forest's north gate",
     "description": "Iron gates chained at dusk, a city notice about 'habitat protection hours', "
                     "and a path that lamplight never quite reaches the end of."},
    {"id": "arcade", "name": "The covered shopping arcade",
     "description": "The after-school artery — crepes, a stationery shop, a shrine supply store "
                     "that sells more charms than it should need to."},
]

HOMES = ["school_gate", "occult_club", "rooftop_garden", "classroom", "forest_gate"]

STORY_NAME = "Evergreen"
STORY_KEY = "evergreen"


def main() -> None:
    root = Path(".")
    ctx = build_context(root)

    existing = ctx.base_settings.stories.get(STORY_KEY)
    if existing is not None:
        print(f"story '{STORY_KEY}' already exists — skipping generation, re-activating only")
    else:
        prov = ctx.text_provider_for("minimax/minimax-m3", {"reasoning_effort": "low"})
        assert prov is not None, "no provider for minimax-m3"

        print("WORLD SEED (original green-city arc):\n" + WORLD + "\n", flush=True)
        t0 = time.time()
        setting = generate_setting(prov, world=WORLD)
        question, factions = setting["question"], setting["factions"]
        print(f"CENTRAL QUESTION:\n  {question}\n", flush=True)
        print("FACTIONS:")
        for f in factions:
            print(f"  · {f.get('NAME','')} — {f.get('STANCE','')}")

        history = generate_history(prov, world=WORLD, factions=factions, question=question,
                                   n=len(ROLES) + 2)
        print("\nWORLD HISTORY:")
        for h in history:
            print(f"  · {h.get('WHEN','')}: {h.get('EVENT','')} — {h.get('WHAT','')}")

        cast = generate_cast(prov, world=WORLD, roles=ROLES, history=history, factions=factions,
                             fantastical=False, question=question, backstory_provider=prov,
                             ensemble=ENSEMBLE)
        print(f"\n[cast of {len(cast)} in {time.time()-t0:.0f}s]")
        for c in cast:
            print(f"── {c['name']} · {c['persona'][:110]}…  [{c['archetype']} · {c['mode']}]", flush=True)

        skey = ctx.create_story(STORY_NAME, {
            "premise": question,
            "tone": "sunny ordinary day over a city that keeps its nights to itself",
            "type": "novel",
        }, character_keys=[], type_="novel")

        char_keys = []
        for (role_text, _mode), c in zip(ROLES, cast):
            if not c:
                continue
            npc = {"name": c["name"], "persona": c["persona"], "role": role_text,
                   "want": c.get("goal") or c.get("need") or "", "lie": c.get("lie", ""),
                   "wound": c.get("trauma", ""), "secret": c.get("secret", "")}
            char_keys.append(ctx.write_npc(npc, story_key=skey))

        cast_members = [{"character": ck, "home": HOMES[i % len(HOMES)]}
                        for i, ck in enumerate(char_keys)]
        ctx.update_story_fields(skey, {"locations": LOCATIONS, "cast": cast_members,
                                       "start": "home"})
        print(f"\n→ persisted story '{skey}' with {len(char_keys)} characters "
              f"+ {len(LOCATIONS)} locations", flush=True)

        app = create_app(".")
        client = TestClient(app)
        resp = client.post(f"/api/stories/{skey}/weave-bonds", json={})
        bonds = (resp.json() or {}).get("bonds", []) if resp.status_code == 200 else []
        print(f"weave-bonds → HTTP {resp.status_code}, {len(bonds)} bonds proposed", flush=True)
        if bonds:
            ctx2 = build_context(root)
            ctx2.update_story_fields(skey, {"relationships": bonds})
            print(f"→ accepted {len(bonds)} bonds into story.relationships")

    # --- weave the relationship web if it hasn't been woven yet ---
    st_now = build_context(root).base_settings.stories.get(STORY_KEY)
    app = create_app(".")
    client = TestClient(app)
    if st_now is not None and not (getattr(st_now, "relationships", None) or []):
        resp = client.post(f"/api/stories/{STORY_KEY}/weave-bonds", json={})
        bonds = (resp.json() or {}).get("bonds", []) if resp.status_code == 200 else []
        print(f"weave-bonds → HTTP {resp.status_code}, {len(bonds)} bonds proposed", flush=True)
        if bonds:
            ctx2 = build_context(root)
            ctx2.update_story_fields(STORY_KEY, {"relationships": bonds})
            print(f"→ accepted {len(bonds)} bonds into story.relationships")

    # --- activate for live play (compiles the runtime contract) ---
    act = client.post(f"/api/stories/{STORY_KEY}/activate")
    print(f"activate → HTTP {act.status_code}", flush=True)
    if act.status_code != 200:
        print((act.json() or {}).get("readiness") or act.json())
    else:
        print(f"→ '{STORY_KEY}' is ACTIVE and playable")


if __name__ == "__main__":
    main()
