"""Throwaway: generate an ORIGINAL school-arc ensemble via the nakige character engine + bond-weave,
built to the same STRUCTURAL difficulty as Rewrite's common route (one anchor setting, a hidden
private thread per character, a real relationship web) WITHOUT reusing a single name, personality,
or plot beat from that game. Persists a real story (cast + locations + weave-bonds relationships)
into configs/stories.db, through the actual production endpoint, so the current architecture can be
inspected end-to-end in the running app.
Run: python -m scripts.gen_school_arc
"""
from __future__ import annotations

import html as _html
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from fastapi.testclient import TestClient

from loom.server import build_context
from loom.server.app import create_app
from loom.stories.characters.engine import generate_cast, generate_history, generate_setting

WORLD = (
    "A regional high school at the edge of a town where the land won't stay tamed. The school's back "
    "fence gives out onto a stand of trees nobody has managed to clear in three redevelopment attempts — "
    "contractors quit, surveys come back wrong, saplings return within a season. Nobody calls it strange, "
    "exactly; it's just the grove, it's just how that corner is. Life runs on ordinary rails: homeroom, "
    "club hours, part-time shifts, the long walk home past a shrine gate nobody uses anymore. But small "
    "things keep not adding up — a survey map that doesn't match the ground, a family that's held the "
    "same lease since before the town kept records, a transfer student whose sending school nobody can "
    "find on file."
)

# Function-level slots (roles, NOT characters) — the ensemble's STRUCTURAL jobs, spanning the
# mundane/touched/heir spectrum like Rewrite's common route does. Personalities are NOT specified
# here; the engine invents them fresh from the assigned archetype below.
ROLES = [
    ("a laid-back second-year who deflects everything with a joke and keeps people at arm's length", "mundane"),
    ("a recent transfer who spends every free period in the overgrown grove behind the school and "
     "knows it better than the groundskeeper", "heir"),
    ("the underclassman who runs the school's fieldwork club and keeps dragging people into her "
     "surveys of the grove", "mundane"),
    ("a classmate whose family has held half the town's leases for generations and who carries "
     "herself like she's always being watched", "touched"),
    ("the class rep who runs everyone else's schedule down to the minute but guards her own fiercely", "mundane"),
    ("an upperclassman fixated on a recurring discrepancy in the town's old survey records that "
     "nobody else takes seriously", "heir"),
    ("a blunt exchange student who reacts to danger like she's drilled for it and never explains why", "touched"),
]

# One archetype per role (index-paired) — known chemistry, cast like a band.
ENSEMBLE = ["deadpan loner", "dandere", "chuuni", "tsundere", "mother-hen", "childhood friend",
            "delinquent with a soft heart"]

LOCATIONS = [
    {"id": "classroom", "name": "Class 2-C homeroom",
     "description": "Rows of desks, a window that looks past the gym roof toward the tree line; "
                     "where the day officially starts and ends."},
    {"id": "clubroom", "name": "The Fieldwork Club room",
     "description": "A cramped storage room turned clubroom — filing cabinets of old survey "
                     "photocopies, a kettle, a radio that only hisses static near the grove."},
    {"id": "rooftop", "name": "The rooftop landing",
     "description": "Technically locked; the latch has been broken for years. Quiet, windy, a good "
                     "place to not be found."},
    {"id": "grove", "name": "The grove behind the fence",
     "description": "Overgrown ground the school gave up trying to clear. A cracked stone marker "
                     "sits somewhere in the middle of it, half-swallowed by roots."},
    {"id": "estate", "name": "The Sagami leaseholding office",
     "description": "A family office that's outlasted three generations of town planners; ledgers "
                     "older than the school itself."},
    {"id": "home", "name": "The room above the corner store",
     "description": "A narrow flat over a convenience store, one floor up from the register bell."},
    {"id": "station_street", "name": "Station street",
     "description": "The short row of shops between the train stop and the school gate — the town's "
                     "main foot traffic."},
]

HOMES = ["home", "grove", "clubroom", "estate", "classroom", "clubroom", "station_street"]

STORY_NAME = "Undergrowth"


def show(c: dict) -> None:
    if not c:
        print("  (empty gen)\n")
        return
    print(f"── {c['name']} · {c['persona']}  [{c['archetype']} · {c['mode']}]")
    print(f"   READS AS  {c['reads_as']}")
    print(f"   BACKSTORY {c['backstory']}")
    print(f"   TRAUMA    {c['trauma']}")
    print(f"   BOND      {c['bond']}")
    print(f"   SECRET    {c['secret']}")
    print(f"   LIE       {c['lie']}")
    print(f"   NEED      {c['need']}")
    print(f"   GOAL      {c['goal']}")
    print(f"   ENGINE    {c['engine']}")
    print(f"   PLAYS_OFF {c['plays_off']}")
    for i in c["idiosyncrasies"]:
        print(f"   quirk     {i['quirk']}  →  ({i['hidden_source']})")
    print(f"   REVEAL    {c['reveal']}\n")


def write_html(path: str, setting: dict, history: list[dict], cast: list[dict],
               locations: list[dict], bonds: list[dict]) -> None:
    e = _html.escape
    css = ("body{font:15px/1.6 -apple-system,Segoe UI,sans-serif;max-width:860px;margin:2rem auto;"
           "padding:0 1rem;color:#1a1a1a;background:#faf9f6}h1{font-size:20px}h2{font-size:16px;margin:.3rem 0}"
           ".fac{display:flex;gap:10px;margin:1rem 0}.fac div{flex:1;background:#eef;border-radius:8px;"
           "padding:.5rem .7rem;font-size:13px}.card{border:1px solid #ddd;border-radius:12px;"
           "padding:1rem 1.2rem;margin:1rem 0;background:#fff}.lbl{font-size:11px;letter-spacing:.04em;"
           "text-transform:uppercase;color:#999;margin-top:.6rem}.rev{background:#eef;border-radius:8px;"
           "padding:.5rem .7rem;margin-top:.5rem}.mode{font-size:11px;color:#888;float:right}"
           ".persona{font-style:italic;color:#444}.src{color:#667}.loc{border-left:3px solid #ccd;"
           "padding-left:.6rem;margin:.5rem 0}.bond{background:#f6f2ff;border-radius:8px;padding:.6rem .8rem;"
           "margin:.5rem 0}")
    p = [f"<!doctype html><meta charset=utf-8><title>{e(STORY_NAME)}</title><style>{css}</style>",
         f"<h1>{e(STORY_NAME)} — {e(setting['question'])}</h1><div class=fac>"
         + "".join(f"<div><b>{e(f['NAME'])}</b><br>{e(f['STANCE'])}</div>" for f in setting["factions"]) + "</div>"]
    if history:
        p.append("<div class=lbl>history</div><ul>"
                 + "".join(f"<li>{e(h['WHEN'])}: <b>{e(h['EVENT'])}</b> — {e(h['WHAT'])}</li>" for h in history) + "</ul>")
    p.append("<div class=lbl>locations</div>")
    for l in locations:
        p.append(f"<div class=loc><b>{e(l['name'])}</b> — {e(l['description'])}</div>")
    for c in cast:
        p.append(f"<div class=card><span class=mode>{e(c['mode'])} · {e(c.get('event') or 'unrelated')}</span>"
                 f"<h2>{e(c['name'])}</h2><p class=persona>{e(c['persona'])}</p>"
                 f"<div class=lbl>backstory</div>{e(c['backstory'])}<div class=lbl>trauma</div>{e(c['trauma'])}"
                 f"<div class=lbl>reads as</div>{e(c['reads_as'])}")
        for q in c["idiosyncrasies"]:
            p.append(f"<div class=lbl>quirk</div>{e(q['quirk'])} <span class=src>// {e(q['hidden_source'])}</span>")
        p.append(f"<div class=rev><b>reveal:</b> {e(c['reveal'])}</div></div>")
    if bonds:
        p.append("<div class=lbl>relationship web</div>")
        for b in bonds:
            p.append(f"<div class=bond><b>{e(b['source'])} → {e(b['target'])}</b> [{e(b['nature'])}]<br>"
                     f"{e(b['dynamic'])} / {e(b['target_dynamic'])}<br>"
                     f"<i>potential:</i> {e(b['potential'])}<br><i>trajectory:</i> {e(b['trajectory'])}</div>")
    Path(path).write_text("".join(p), encoding="utf-8")


def main() -> None:
    root = Path(".")
    ctx = build_context(root)
    prov = ctx.text_provider_for("minimax/minimax-m3", {"reasoning_effort": "low"})
    bprov = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "low"})   # prose + cheap large-context

    print("WORLD SEED (original school arc):\n" + WORLD + "\n")
    setting = generate_setting(prov, world=WORLD)
    question, factions = setting["question"], setting["factions"]
    print("CENTRAL QUESTION:\n  " + question + "\n")
    print("FACTIONS:")
    for f in factions:
        print(f"  · {f.get('NAME','')} — {f.get('STANCE','')}")
    print()

    history = generate_history(prov, world=WORLD, factions=factions, question=question, n=len(ROLES) + 2)
    print("WORLD HISTORY:")
    for h in history:
        print(f"  · {h.get('WHEN','')}: {h.get('EVENT','')} — {h.get('WHAT','')}")
    print()

    t0 = time.time()
    cast = generate_cast(prov, world=WORLD, roles=ROLES, history=history, factions=factions,
                         fantastical=False, question=question, backstory_provider=bprov,
                         ensemble=ENSEMBLE)
    dt = time.time() - t0
    for c in cast:
        show(c)
    print(f"[cast of {len(cast)} generated in {dt:.0f}s wall — characters ran concurrently]")

    # --- persist as a real story: the same create_story → write_npc → update_story_fields chokepoints
    # the app itself uses (see [[per-story-database]] / new-story-as-DB) ---
    skey = ctx.create_story(STORY_NAME, {
        "premise": question,
        "tone": "quiet, ordinary surface over a slow-building unease",
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

    cast_members = [{"character": ck, "home": HOMES[i % len(HOMES)]} for i, ck in enumerate(char_keys)]
    ctx.update_story_fields(skey, {"locations": LOCATIONS, "cast": cast_members, "start": "classroom"})
    print(f"\n→ persisted story '{skey}' with {len(char_keys)} characters + {len(LOCATIONS)} locations")

    # --- weave the relationship web through the REAL production endpoint (in-process, no separate server) ---
    app = create_app(".")
    client = TestClient(app)
    resp = client.post(f"/api/stories/{skey}/weave-bonds", json={})
    bonds = (resp.json() or {}).get("bonds", []) if resp.status_code == 200 else []
    print(f"weave-bonds → HTTP {resp.status_code}, {len(bonds)} bonds proposed")
    for b in bonds:
        print(f"  · {b['source']} → {b['target']}  [{b['nature']}] {b['dynamic']} / {b['target_dynamic']}")
        print(f"      potential:  {b['potential']}")
        print(f"      trajectory: {b['trajectory']}")

    if bonds:
        ctx2 = build_context(root)   # fresh read — sees the story as weave-bonds left it on disk
        ctx2.update_story_fields(skey, {"relationships": bonds})
        print(f"→ accepted {len(bonds)} bonds into story.relationships")

    write_html("configs/last_school_arc.html", setting, history, cast, LOCATIONS, bonds)
    print("→ wrote configs/last_school_arc.html (double-click to view)")
    print(f"→ open http://127.0.0.1:5173/stories/{skey} in the running frontend to inspect live")


if __name__ == "__main__":
    main()
