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


# ── The script testing FLOW ───────────────────────────────────────────────────────
# One declarative case per registered script: (doc, params, check). Every script runs
# through stories/scripts.py:invoke — the same seam the model path uses — so a passing
# case proves the real behavior. `test_script_coverage` asserts the table covers the WHOLE
# registry, so a new @script can't ship without a case here. Adding a script => add a row.

def _ids(doc):
    return [n.get("id") for n in doc.get("nodes", [])]


SCRIPT_CASES = {
    # graph (the development graph)
    "add_beat": ({"nodes": [{"id": "b0", "title": "Open", "next": []}]}, {"title": "Crack", "after": "b0"},
                 lambda d: len(d["nodes"]) == 2 and d["nodes"][0]["next"] == [d["nodes"][1]["id"]]
                 and set(d["nodes"][1]) == {"id", "title", "inflection", "start", "end", "what_happened", "next"}),
    "insert_between": ({"nodes": [{"id": "a", "next": ["b"]}, {"id": "b", "next": []}]}, {"a": "a", "b": "b", "title": "mid"},
                       lambda d: d["nodes"][0]["next"] == [d["nodes"][2]["id"]] and d["nodes"][2]["next"] == ["b"]),
    "set_beat_field": ({"nodes": [{"id": "b0", "title": "Open", "next": []}]}, {"id": "b0", "field": "title", "value": "Shut"},
                       lambda d: d["nodes"][0]["title"] == "Shut"),
    "connect": ({"nodes": [{"id": "b0", "next": []}, {"id": "b1", "next": []}]}, {"source": "b0", "target": "b1"},
                lambda d: d["nodes"][0]["next"] == ["b1"]),
    "disconnect": ({"nodes": [{"id": "b0", "next": ["b1"]}]}, {"source": "b0", "target": "b1"},
                   lambda d: d["nodes"][0]["next"] == []),
    "delete_beat": ({"nodes": [{"id": "b0"}, {"id": "b1"}]}, {"id": "b1"},
                    lambda d: _ids(d) == ["b0"]),
    "move_beat": ({"nodes": [{"id": "b0"}, {"id": "b1"}, {"id": "b2"}]}, {"id": "b0", "after": "b1"},
                  lambda d: _ids(d) == ["b1", "b0", "b2"]),
    "reorder_beats": ({"nodes": [{"id": "b0"}, {"id": "b1"}, {"id": "b2"}]}, {"order": ["b2", "b1", "b0"]},
                      lambda d: _ids(d) == ["b2", "b1", "b0"]),
    "set_spine": ({}, {"field": "wound", "value": "abandonment"},
                  lambda d: d.get("wound") == "abandonment"),
    # locations
    "add_location": ({"locations": []}, {"name": "Pier", "description": "a wooden pier"},
                     lambda d: d["locations"][0]["name"] == "Pier"
                     and set(d["locations"][0]) == {"id", "name", "description", "background_prompt", "parent"}),
    "set_location_field": ({"locations": [{"id": "l0", "name": "X"}]}, {"id": "l0", "field": "name", "value": "Dock"},
                           lambda d: d["locations"][0]["name"] == "Dock"),
    "set_location_area": ({"locations": [{"id": "l0"}, {"id": "a0"}]}, {"id": "l0", "area": "a0"},
                          lambda d: d["locations"][0]["parent"] == "a0"),
    "remove_location": ({"locations": [{"id": "l0"}]}, {"id": "l0"},
                        lambda d: d["locations"] == []),
    "set_start": ({"locations": [{"id": "l0"}]}, {"id": "l0"}, lambda d: d.get("start") == "l0"),
    # cast
    "add_character": ({"cast": []}, {"name": "Aria", "role": "rival", "persona": "sharp"},
                      lambda d: d["cast"][0]["name"] == "Aria" and d["cast"][0]["primary"] is False),
    "set_character_field": ({"cast": [{"id": "c0", "role": "rival"}]}, {"id": "c0", "field": "role", "value": "ally"},
                            lambda d: d["cast"][0]["role"] == "ally"),
    "remove_character": ({"cast": [{"id": "c0"}]}, {"id": "c0"}, lambda d: d["cast"] == []),
    # relationships (authored baseline; same shape seeds runtime state)
    "set_relationship": ({}, {"source": "c0", "target": "c1", "nature": "rival",
                              "dynamic": "open rivalry", "stance": "hostile"},
                         lambda d: d["relationships"][0]["nature"] == "rival"
                         and d["relationships"][0]["stance"] == "hostile"
                         and d["relationships"][0]["dynamic"] == "open rivalry"),
    "remove_relationship": ({"relationships": [{"source": "c0", "target": "c1"}]}, {"source": "c0", "target": "c1"},
                            lambda d: d["relationships"] == []),
    # scene connections (the navigable map)
    "connect_scenes": ({"locations": [{"id": "l0"}, {"id": "l1"}]}, {"source": "l0", "target": "l1", "label": "the gate"},
                       lambda d: d["connections"][0]["source"] == "l0" and d["connections"][0]["target"] == "l1"),
    "disconnect_scenes": ({"connections": [{"source": "l0", "target": "l1"}]}, {"source": "l0", "target": "l1"},
                          lambda d: d["connections"] == []),
}


def test_script_coverage() -> None:
    section("script coverage (every registered script has a case)")
    from loom.stories import scripts as S
    missing = set(S.REGISTRY) - set(SCRIPT_CASES)
    extra = set(SCRIPT_CASES) - set(S.REGISTRY)
    check(not missing, f"scripts missing a test case: {sorted(missing)}")
    check(not extra, f"cases for unknown scripts: {sorted(extra)}")


def test_scripts_via_invoke() -> None:
    section("scripts (run each case through scripts.invoke)")
    import copy
    from loom.stories import scripts as S
    for name, (doc, params, ok) in SCRIPT_CASES.items():
        out = S.invoke(name, copy.deepcopy(doc), dict(params))
        check(ok(out), f"{name}: behaves as specified")
    # invoke filters stray params and raises on an unknown name
    out = S.invoke("set_spine", {}, {"field": "lie", "value": "I'm fine", "bogus": "x"})
    check(out.get("lie") == "I'm fine", "invoke ignores undeclared params")
    try:
        S.invoke("nope", {}, {})
        check(False, "invoke raises on unknown script")
    except KeyError:
        check(True, "invoke raises on unknown script")


def test_graph_ops_integration() -> None:
    section("graph_ops (registry resolution + apply_ops via the model path)")
    from loom.stories import graph_ops as GO

    class E:
        def __init__(s, content, keywords=(), title="", enabled=True):
            s.content, s.keywords, s.title, s.enabled = content, list(keywords), title, enabled

    def trig(fn, kws=()):
        return E(json.dumps({"fn": fn}), kws, fn)

    fns = GO.parse_functions([trig("add_beat", ["beat"]), trig("set_spine")])
    check(len(fns) == 2 and all(f.impl is not None for f in fns), "entries resolve to code")
    check(fns[0].describe.startswith("Add a new beat") and "title" in fns[0].params, "describe/params canonical from code")
    check(fns[0].keywords == ["beat"], "lorebook entry contributes trigger keywords")
    # the bare-graph endpoint path still wires correctly through apply_ops -> invoke
    g, _ = GO.apply_ops({"nodes": [{"id": "b0", "next": []}]},
                        [{"fn": "add_beat", "params": json.dumps({"title": "Crack", "after": "b0"})}], fns)
    check(len(g["nodes"]) == 2 and g["nodes"][0]["next"] == [g["nodes"][1]["id"]], "apply_ops wires via invoke")
    # native tool-calling path: tools_spec → per-tool schema; calls arrive as dict params
    specs = GO.tools_spec(fns)
    add = next(s for s in specs if s["name"] == "add_beat")
    check("title" in add["parameters"]["properties"] and "after" not in add["parameters"]["required"],
          "tools_spec gives per-tool schema; optional param not required")
    g2, _ = GO.apply_ops({"nodes": [{"id": "b0", "next": []}]},
                         [{"fn": "add_beat", "params": {"title": "Crack", "after": "b0"}}], fns)
    check(len(g2["nodes"]) == 2 and g2["nodes"][0]["next"] == [g2["nodes"][1]["id"]],
          "native tool call (dict params) applies via invoke")
    check(GO.parse_functions([trig("nonesuch")]) == [], "unregistered fn ignored")
    check(GO.is_function_entry(E("Plain lore.", [], "Harbor")) is False, "data entry is not a function")
    _, log = GO.apply_ops({}, [{"fn": "ghost", "params": "{}"}], fns)
    check(any(not x["ok"] for x in log), "unknown call logged as failure")
    # best-practice schema: enum-constrained param; bad target ids surface as failed calls
    efns = GO.parse_functions([trig("set_beat_field"), trig("connect")])
    sbf = next(s for s in GO.tools_spec(efns) if s["name"] == "set_beat_field")
    check("title" in (sbf["parameters"]["properties"]["field"].get("enum") or []),
          "tools_spec exposes enum for a fixed-choice param")
    _, log2 = GO.apply_ops({"nodes": []},
                           [{"fn": "set_beat_field", "params": {"id": "zzz", "field": "title", "value": "x"}}], efns)
    check(log2 and not log2[0]["ok"] and "zzz" in (log2[0].get("error") or ""),
          "bad target id raises -> logged as a failed call (not a silent no-op)")


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

    # coverage: every registered world op was exercised above (move/mood/entity/rel/set_flag/
    # item_add/item_remove/log in the batch, fact here) — a new op can't ship untested.
    exercised = {"move", "mood", "entity", "rel", "set_flag", "item_add", "item_remove", "log", "fact"}
    check(exercised == set(SE.WORLD_OPS), f"every world op tested (untested: {sorted(set(SE.WORLD_OPS) - exercised)})")


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
    check({"learn", "set_emotion"} == set(SIM.SIM_OPS), "every sim op tested")


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


def test_world_graph() -> None:
    section("world graph (global cross-story doc persisted in libSQL)")
    from loom.stories import world_graph as WG

    root = Path(tempfile.mkdtemp())
    check(WG.load(root) == {"locations": [], "relationships": [], "connections": []}, "empty default")

    doc, log = WG.apply(root, [
        {"fn": "add_location", "params": {"name": "Harbor", "description": "a stone harbor"}},
        {"fn": "set_relationship", "params": {"source": "Aria", "target": "Daniel",
                                              "nature": "rival", "dynamic": "old grudge over a stolen ship",
                                              "stance": "hostile"}},
    ])
    check(all(x["ok"] for x in log), "calls applied via the same scripts engine")
    check(len(doc["locations"]) == 1 and doc["relationships"][0]["stance"] == "hostile",
          "location + relationship edge land in the global doc")

    # persisted: a fresh load reads it back from libSQL (not in-memory)
    reloaded = WG.load(root)
    check(reloaded["relationships"][0]["nature"] == "rival", "round-trips through the docs table")

    # containment works in the global graph too (add a second location, nest it)
    harbor = reloaded["locations"][0]["id"]
    doc2, _ = WG.apply(root, [{"fn": "add_location", "params": {"name": "Pier"}}])
    pier = next(l for l in doc2["locations"] if l["name"] == "Pier")["id"]
    _, log3 = WG.apply(root, [{"fn": "set_location_area", "params": {"id": pier, "area": harbor}}])
    check(any(x["ok"] for x in log3) and WG.load(root)["locations"][-1]["parent"] == harbor,
          "set_location_area nests + persists in the global graph")


def test_stage_resolution() -> None:
    section("stage resolution (stage -> preset by convention, no book middleman)")
    from loom.server.services import presets as P
    root = Path(tempfile.mkdtemp())
    P.seed_stage_presets(root)
    p = P.stage_preset(root, "characters")
    check(p is not None and p["id"] == "stage_characters", "stage resolves to its stage_<stage> preset")
    check(P.stage_preset(root, "nope") is None, "unknown stage -> None (caller falls back)")
    check(P.stage_preset(root, "") is None, "blank stage -> None")
    n = len(P.load_presets(root)["presets"])
    P.seed_stage_presets(root)
    check(len(P.load_presets(root)["presets"]) == n, "seed_stage_presets is idempotent")


def test_perception() -> None:
    section("perception (indexed steps + per-character seen / join-no-backlog)")
    from loom.stories import perception as PC
    w: dict = {}
    PC.record_step(w, ["a", "b"]); PC.record_step(w, ["a"]); PC.record_step(w, ["a", "c"])
    check(w["step"] == 3, "step counter advances per turn")
    check(PC.seen_steps(w, "a") == [0, 1, 2], "a saw every step")
    check(PC.seen_steps(w, "b") == [0], "b only saw the opening step")
    check(PC.seen_steps(w, "c") == [2] and PC.joined_at(w, "c") == 2, "c joined at 2 — no backlog")
    check(PC.visible(w, "c", ["m0", "m1", "m2"]) == ["m2"], "a joiner sees only the last message")


def main() -> int:
    for t in (test_script_coverage, test_scripts_via_invoke, test_graph_ops_integration,
              test_world_ops, test_state_doc, test_session_roundtrip, test_sim_ops,
              test_world_graph, test_stage_resolution, test_perception,
              test_real_db_books_resolve_to_code):
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
