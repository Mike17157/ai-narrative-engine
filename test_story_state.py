"""Regression tests for the unified story-state stack (run: `python test_story_state.py`).

Locks the behavior of the State-doc / registered-script refactor so future changes can't
silently regress it — this is the payoff of moving logic out of a DB DSL into code. Covers:

  • stories/scripts.py     — the registered graph/location/cast scripts (the model's tools)
  • stories/graph_ops.py   — registry resolution + apply_calls / bare apply_ops
  • stories/state_engine.py — the WORLD_OPS registry (9 delta ops) + fact write-back
  • stories/state_doc.py    — the leveled doc: migration, projection, level_summary
  • server/services/story_sessions.py — disk round-trip + lazy migration
  • stories/simulation.py   — the SIM_OPS registry

No pytest dependency (none is installed) — a tiny assert harness, non-zero exit on failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_PASS = 0
_FAIL = 0


def check(cond: bool, msg: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {msg}")


def section(name: str) -> None:
    print(f"\n[{name}]")


# ── stories/scripts.py + graph_ops.py ────────────────────────────────────────────

def test_graph_scripts() -> None:
    section("graph scripts (registry + apply_ops)")
    from loom.stories import graph_ops as GO

    class E:
        def __init__(s, content, keywords=(), title="", enabled=True):
            s.content, s.keywords, s.title, s.enabled = content, list(keywords), title, enabled

    def trig(fn, kws=()):
        return E(json.dumps({"fn": fn}), kws, fn)

    fns = GO.parse_functions([trig(n) for n in
        ["add_beat", "insert_between", "set_beat_field", "connect", "disconnect",
         "delete_beat", "move_beat", "reorder_beats", "set_spine"]])
    check(len(fns) == 9 and all(f.impl is not None for f in fns), "all 9 graph fns resolve to code")
    check(fns[0].describe.startswith("Add a new beat"), "describe comes from code")
    check("title" in fns[0].params, "params come from code")

    # add_beat appends a full node AND wires the parent's `next`
    g, _ = GO.apply_ops({"logline": "x", "nodes": [{"id": "b0", "title": "Open", "next": []}]},
                        [{"fn": "add_beat", "params": json.dumps({"title": "Crack", "after": "b0"})}], fns)
    nid = g["nodes"][1]["id"]
    check(len(g["nodes"]) == 2 and g["nodes"][0]["next"] == [nid], "add_beat wires parent.next")
    check(set(g["nodes"][1]) == {"id", "title", "inflection", "start", "end", "what_happened", "next"},
          "add_beat produces the full node shape")

    # insert_between rewires the arrow through the new node
    g, _ = GO.apply_ops({"nodes": [{"id": "a", "next": ["b"]}, {"id": "b", "next": []}]},
                        [{"fn": "insert_between", "params": json.dumps({"a": "a", "b": "b", "title": "mid"})}], fns)
    mid = next(n for n in g["nodes"] if n.get("title") == "mid")
    check(g["nodes"][0]["next"] == [mid["id"]] and mid["next"] == ["b"], "insert_between rewires arrow")

    # spine / reorder / move / delete / connect / disconnect
    g = {"logline": "", "nodes": [{"id": "b0", "next": []}, {"id": "b1", "next": []}, {"id": "b2", "next": []}]}
    g, _ = GO.apply_ops(g, [{"fn": "set_spine", "params": json.dumps({"field": "wound", "value": "abandonment"})}], fns)
    check(g.get("wound") == "abandonment", "set_spine sets a top-level field")
    g, _ = GO.apply_ops(g, [{"fn": "reorder_beats", "params": {"order": ["b2", "b0", "b1"]}}], fns)
    check([n["id"] for n in g["nodes"]] == ["b2", "b0", "b1"], "reorder_beats")
    g, _ = GO.apply_ops(g, [{"fn": "move_beat", "params": json.dumps({"id": "b2", "after": "b1"})}], fns)
    check([n["id"] for n in g["nodes"]] == ["b0", "b1", "b2"], "move_beat")
    g, _ = GO.apply_ops(g, [{"fn": "connect", "params": json.dumps({"source": "b0", "target": "b2"})}], fns)
    check(g["nodes"][0]["next"] == ["b2"], "connect")
    g, _ = GO.apply_ops(g, [{"fn": "disconnect", "params": json.dumps({"source": "b0", "target": "b2"})}], fns)
    check(g["nodes"][0]["next"] == [], "disconnect")
    g, _ = GO.apply_ops(g, [{"fn": "delete_beat", "params": json.dumps({"id": "b1"})}], fns)
    check([n["id"] for n in g["nodes"]] == ["b0", "b2"], "delete_beat")

    # an unknown / non-registered fn is skipped (no inline-code path anymore)
    check(GO.parse_functions([trig("nonesuch")]) == [], "unknown fn ignored")
    check(GO.is_function_entry(E("Plain world lore.", [], "Harbor")) is False, "data entry is not a function")
    check(GO.is_function_entry(trig("set_spine")) is True, "registered {fn} entry is a function")
    _, log = GO.apply_ops({}, [{"fn": "ghost", "params": "{}"}], fns)
    check(any(not x["ok"] for x in log), "unknown call logged as failure")


def test_location_and_cast_scripts() -> None:
    section("location + cast scripts (artifact-agnostic)")
    from loom.stories import graph_ops as GO

    class E:
        def __init__(s, content, keywords=(), title="", enabled=True):
            s.content, s.keywords, s.title, s.enabled = content, list(keywords), title, enabled

    loc = GO.parse_functions([E(json.dumps({"fn": n}), [], n)
                              for n in ["add_location", "set_location_field", "remove_location", "set_start"]])
    d = {"locations": [], "start": None}
    d, _ = GO.apply_ops(d, [{"fn": "add_location", "params": json.dumps({"name": "Pier", "description": "a wooden pier"})}], loc)
    lid = d["locations"][0]["id"]
    check(d["locations"][0] == {"id": lid, "name": "Pier", "description": "a wooden pier", "background_prompt": ""},
          "add_location shape")
    d, _ = GO.apply_ops(d, [{"fn": "set_start", "params": json.dumps({"id": lid})}], loc)
    check(d["start"] == lid, "set_start")
    d, _ = GO.apply_ops(d, [{"fn": "remove_location", "params": json.dumps({"id": lid})}], loc)
    check(d["locations"] == [], "remove_location")

    cast = GO.parse_functions([E(json.dumps({"fn": n}), [], n)
                               for n in ["add_character", "set_character_field", "remove_character"]])
    c = {"cast": []}
    c, _ = GO.apply_ops(c, [{"fn": "add_character", "params": json.dumps({"name": "Aria", "role": "rival", "persona": "sharp"})}], cast)
    check(c["cast"][0]["name"] == "Aria" and c["cast"][0]["primary"] is False, "add_character shape")
    cid = c["cast"][0]["id"]
    c, _ = GO.apply_ops(c, [{"fn": "set_character_field", "params": json.dumps({"id": cid, "field": "role", "value": "ally"})}], cast)
    check(c["cast"][0]["role"] == "ally", "set_character_field")


# ── stories/state_engine.py (WORLD_OPS) ──────────────────────────────────────────

def test_world_ops() -> None:
    section("world ops (state_engine WORLD_OPS)")
    from loom.stories import state_engine as SE
    from loom.server.services import lorebook_store as LS

    check(set(SE.WORLD_OPS) == set(SE._OPS), "registry covers exactly the declared vocabulary")

    def d(op, **kw):
        return {"op": op, "name": kw.get("name", ""), "key": kw.get("key", ""),
                "value": kw.get("value", ""), "title": kw.get("title", ""), "keywords": kw.get("keywords", [])}

    ws = SE.apply_deltas(SE.empty_state(), [
        d("move", name="Aria", value="the pier"),
        d("mood", name="Aria", value="guarded"),
        d("entity", name="Aria", value="sketching"),
        d("rel", name="Aria", key="you", value="-3"),
        d("rel", name="Aria", key="you", value="-9"),   # clamps at -10
        d("set_flag", key="met_aria", value="true"),
        d("item_add", value="brass key"),
        d("item_add", value="brass key"),               # dedup
        d("item_remove", value="BRASS KEY"),            # case-insensitive
        d("log", value="Daniel arrived."),
        d("set_flag", name="set_flag", key="flags", value="x"),  # echoed -> ignored
    ], root=None, scope=None)
    a = ws["entities"]["Aria"]
    check(a["location"] == "the pier" and a["mood"] == "guarded" and a["status"] == "sketching", "move/mood/entity")
    check(a["relationships"]["you"] == -10, "rel accumulates and clamps")
    check(ws["flags"] == {"met_aria": True}, "set_flag coerces bool; echoed op ignored")
    check(ws["inventory"] == [], "item_add dedup + case-insensitive remove")
    check(ws["log"] == ["Daniel arrived."], "log append")
    check(ws["revision"] == 1, "revision bumped once per apply")

    root = Path(tempfile.mkdtemp())
    ws2 = SE.apply_deltas(SE.empty_state(),
                          [d("fact", value="Aria distrusts strangers.", title="Aria distrust", keywords=["Aria", "trust"])],
                          root=root, scope="thread-test")
    entries = LS.load_lorebook(root, "thread-test")
    check(any("distrusts" in (e.content or "") for e in entries), "fact written back to the thread scope")
    check(ws2["log"] and ws2["log"][0].startswith("(established:"), "fact logs an episodic note")


# ── stories/state_doc.py ──────────────────────────────────────────────────────────

def test_state_doc() -> None:
    section("state doc (shape / migration / summary)")
    from loom.stories import state_doc as SD

    check(SD.empty_state() == {"levels": {}, "revision": 0}, "empty_state")
    check(SD.normalize({"levels": "bad", "revision": "3"}) == {"levels": {}, "revision": 3}, "normalize coerces")

    legacy = {"graph": {"nodes": [{"id": "b0"}]}, "draft": {"chapters": 3},
              "world_state": {"flags": {"met": True}, "revision": 5}}
    st = SD.from_session(legacy)
    check(st["levels"]["graph"]["nodes"][0]["id"] == "b0", "graph migrates to a level")
    check(st["levels"]["world"]["flags"]["met"] is True, "world_state migrates to the world level")
    check(st["revision"] == 5, "carries the world revision")
    check(SD.from_session({"state": st}) == SD.normalize(st), "idempotent when state already present")

    back = SD.to_session_fields(st)
    check(back["graph"] == legacy["graph"] and back["world_state"] == legacy["world_state"] and "state" in back,
          "projects back to legacy flat fields")

    st = SD.set_level(st, "sim", {"characters": [{"name": "A"}, {"name": "B"}], "scenes": [{}]})
    ls = SD.level_summary(st)
    check(ls["world"]["size"] == 1 and ls["graph"]["size"] == 1 and ls["sim"]["size"] == 2, "level_summary sizes")


# ── server/services/story_sessions.py ─────────────────────────────────────────────

def test_session_roundtrip() -> None:
    section("session store (disk round-trip + lazy migration)")
    from loom.server.services import story_sessions as SS
    from loom.stories import state_engine as SE, state_doc as SD

    root = Path(tempfile.mkdtemp())
    # legacy-style save (flat world_state) -> file gains a `state` doc + keeps the flat field
    SS.save_session(root, "play-aria", {"character": "aria", "graph": {"nodes": [{"id": "b0"}]},
                                         "world_state": {"flags": {"met": True}, "revision": 2}})
    raw = json.loads((root / "configs" / "story_sessions" / "play-aria.json").read_text())
    check("state" in raw and raw["state"]["levels"]["world"]["flags"]["met"] is True, "save folds flat -> state doc")
    check(raw["world_state"]["flags"]["met"] is True, "legacy flat field still projected")

    # a pre-migration file (no state) gets `state` folded in on load
    p = root / "configs" / "story_sessions" / "old.json"
    p.write_text(json.dumps({"id": "old", "world_state": {"clock": "dawn"}}))
    loaded = SS.load_session(root, "old")
    check(loaded["state"]["levels"]["world"]["clock"] == "dawn", "load lazily migrates")

    # state-native save round-trips the world level
    st = SE.with_world(SD.empty_state(), SE.normalize({"flags": {"x": 1}}))
    SS.save_session(root, "play-aria", {"character": "aria", "state": st})
    raw2 = json.loads((root / "configs" / "story_sessions" / "play-aria.json").read_text())
    check(raw2["world_state"]["flags"]["x"] == 1, "state-in -> legacy projected out")


# ── stories/simulation.py (SIM_OPS) ────────────────────────────────────────────────

def test_sim_ops() -> None:
    section("sim ops (simulation SIM_OPS)")
    from loom.stories import simulation as SIM

    check(set(SIM.SIM_OPS) == {"learn", "set_emotion"}, "sim op registry")
    cast = [{"name": "Aria", "knowledge": [], "state": ""}]
    by_name = {c["name"]: c for c in cast}
    for u in [{"name": "Aria", "learned": "Daniel lied.", "emotion": "wary"},
              {"name": "Aria", "learned": "", "emotion": ""},          # blanks -> no-op
              {"name": "Ghost", "learned": "z", "emotion": "z"}]:      # unknown -> skipped
        c = by_name.get(u["name"])
        if not c:
            continue
        SIM.SIM_OPS["learn"](c, u.get("learned"))
        SIM.SIM_OPS["set_emotion"](c, u.get("emotion"))
    check(cast[0]["knowledge"] == ["Daniel lied."] and cast[0]["state"] == "wary", "sim updates apply, blanks no-op")


# ── real DB function books all resolve to code ─────────────────────────────────────

def test_real_db_books_resolve_to_code() -> None:
    section("real DB function books -> code")
    from loom.server.services import lorebook_store as LS
    from loom.stories import graph_ops as GO

    root = Path(".")
    total = 0
    for book in ("_graph_fns", "_location_fns", "_character_fns"):
        fns = GO.parse_functions(LS.load_lorebook(root, book))
        total += len(fns)
        check(fns and all(f.impl is not None for f in fns), f"{book}: all entries resolve to code ({len(fns)})")
    check(total >= 15, "all built-in function books resolved")


def main() -> int:
    for t in (test_graph_scripts, test_location_and_cast_scripts, test_world_ops, test_state_doc,
              test_session_roundtrip, test_sim_ops, test_real_db_books_resolve_to_code):
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            global _FAIL
            _FAIL += 1
            print(f"  ERROR in {t.__name__}: {exc!r}")
    print(f"\n{'='*48}\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
