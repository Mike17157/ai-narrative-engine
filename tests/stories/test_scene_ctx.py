"""Scene-keyed context loader: signature, premise, block carry-forward, persistence, and
the build_turn_context integration (boundary detection + mid-scene reuse)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.stories.runtime import state as _state
from loom.stories.runtime.compiled import ensure_runtime, persist_runtime, prepare_turn
from loom.stories.runtime.context import build_turn_context
from loom.stories.runtime.scene_ctx import SceneCache, scene_premise, scene_signature
from loom.stories.runtime.scenario_compiler import compile_authored_scenario


# ── signature ────────────────────────────────────────────────────────────────────

def test_scene_signature_stable_and_boundary_dimensions():
    base = scene_signature(cur="ferry", members=["a", "b"], pov="a", conds=[], scene_id="s1")
    same = scene_signature(cur="ferry", members=["b", "a"], pov="a", conds=[], scene_id="s1")
    assert base == same                      # member order is not a boundary
    assert base != scene_signature(cur="harbor", members=["a", "b"], pov="a", conds=[], scene_id="s1")
    assert base != scene_signature(cur="ferry", members=["a"], pov="a", conds=[], scene_id="s1")
    assert base != scene_signature(cur="ferry", members=["a", "b"], pov="b", conds=[], scene_id="s1")
    assert base != scene_signature(cur="ferry", members=["a", "b"], pov="a", conds=["storm"], scene_id="s1")
    assert base != scene_signature(cur="ferry", members=["a", "b"], pov="a", conds=[], scene_id="s2")


def test_scene_premise_priority():
    assert "quiet ferry" in scene_premise(
        {"title": "Crossing", "visible": "The quiet ferry approaches."}, {}, fallback="beat")
    assert scene_premise(None, {"scene_plan": {"goal": "find the bell", "pressure": "dusk"}}) \
        == "find the bell dusk"
    assert scene_premise(None, {}, fallback="the current beat") == "the current beat"


# ── block cache ──────────────────────────────────────────────────────────────────

def test_scene_cache_hit_miss_and_cross_scene_carry():
    sc = SceneCache({})
    sc.begin_scene("sig-1", 0)
    inputs = {"k": "mara", "n": 3, "rank": "the grove"}
    assert sc.block("embodiment.mara.n3", inputs) is None     # cold miss
    sc.store("embodiment.mara.n3", inputs, "MARA BLOCK")
    assert sc.block("embodiment.mara.n3", inputs) == "MARA BLOCK"
    assert sc.stats()["carried"] == 1 and sc.stats()["recomputed"] == 1
    # A scene boundary keeps blocks: same inputs still hit (carry-forward).
    sc.begin_scene("sig-2", 5)
    assert sc.block("embodiment.mara.n3", inputs) == "MARA BLOCK"
    # Changed inputs miss.
    assert sc.block("embodiment.mara.n3", {**inputs, "rank": "the jetty"}) is None


def test_scene_cache_doc_round_trip_and_lifetime_stats():
    sc = SceneCache({})
    sc.begin_scene("sig-1", 3)
    sc.store("lore", {"scopes": ["s"], "rank": "x"}, "LORE TEXT")
    sc.block("lore", {"scopes": ["s"], "rank": "x"})
    doc = sc.to_doc()
    sc2 = SceneCache(doc)                       # simulate the next turn loading the level
    assert sc2.signature == "sig-1"
    assert sc2.opened_step == 3
    assert sc2.block("lore", {"scopes": ["s"], "rank": "x"}) == "LORE TEXT"
    doc2 = sc2.to_doc()
    assert doc2["stats"]["turns"] == 2
    # Lifetime counters accumulate across turns: one hit before the round trip, one after.
    assert doc2["stats"]["carried"] == 2 and doc2["stats"]["recomputed"] == 1
    assert doc2["stats"]["carried_bytes"] == 2 * len("LORE TEXT")


# ── persistence: the `ctx` level survives the two real save paths ────────────────

def test_ctx_level_survives_with_world_and_persist_runtime():
    cache_doc = {"signature": "sig-1", "blocks": {"lore": {"hash": "h", "text": "t"}}}
    doc = _state.with_world({}, {"entities": {}, "flags": {}, "location": "ferry"})
    doc = _state.set_level(doc, "ctx", cache_doc)
    # free-play save path
    saved = _state.with_world(doc, {"entities": {}, "flags": {"x": 1}, "location": "ferry"})
    assert _state.get_level(saved, "ctx", {}) == cache_doc
    # compiled save path
    saved2 = persist_runtime(doc, {"entities": {}}, {"fingerprint": "f", "scenario_state": {}})
    assert _state.get_level(saved2, "ctx", {}) == cache_doc


# ── build_turn_context integration ───────────────────────────────────────────────

class _Context:
    def __init__(self, root: Path):
        self.root = root
        self.base_settings = SimpleNamespace(characters={})

    def portrait_manifest(self, _key: str) -> dict:
        return {}


def _card() -> dict:
    return {
        "name": "Gated ferry",
        "start": "ferry",
        "locations": [{"id": "ferry", "name": "Electric ferry"},
                      {"id": "harbor", "name": "Island harbor"}],
        "cast": [{"character": "player"}, {"character": "npc_a"}],
        "fields": {"player_id": "player", "first_day_plan": {"events": [
            {"id": "crossing", "when": "morning", "location": "ferry",
             "participants": ["npc_a"], "visible": "The quiet ferry approaches the island."},
            {"id": "night-attack", "when": "night", "location": "harbor",
             "participants": ["npc_a"], "visible": "A familiar figure waits by the water."},
        ]}},
    }


def _build(root, card, contract, world, runtime, scene, history, cache):
    return build_turn_context(_Context(root), Story(**card), "gated-ferry",
                              {"history": history}, world,
                              story_scope="story-gated-ferry", thread_scope="thread-gated",
                              scenario=contract, runtime_state=runtime, scenario_scene=scene,
                              scene_cache=cache)


def test_turn_context_marks_boundary_then_carries_mid_scene():
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    card = _card()
    contract = compile_authored_scenario(card)
    assert contract["ready"], contract["issues"]
    _doc, world, runtime, _fresh = ensure_runtime({}, contract)
    scene = prepare_turn(contract, world, runtime, {})
    history = [{"role": "user", "text": "I look over the railing."}]

    tc1 = _build(root, card, contract, world, runtime, scene, history, cache={})
    sc1 = tc1["lanes"]["scene_ctx"]
    assert sc1["boundary"] is True                        # cold cache opens scene one
    assert tc1["scene_cache"]["signature"] == sc1["sig"]

    # Same scene, same inputs, cache threaded (the engine's State-doc round trip):
    tc2 = _build(root, card, contract, world, runtime, scene, history,
                 cache=tc1["scene_cache"])
    sc2 = tc2["lanes"]["scene_ctx"]
    assert sc2["boundary"] is False
    assert sc2["recomputed"] == 0                          # nothing re-derived mid-scene
    # The cache is the only difference; block contents are byte-identical.
    assert tc1["system"] == tc2["system"]

    # A scene transition is a boundary again.
    world["day"] = {"n": 1, "slot": "night"}
    night_scene = prepare_turn(contract, world, runtime, {"scene_seed": {"id": "night-attack"}})
    tc3 = _build(root, card, contract, world, runtime, night_scene, history,
                 cache=tc2["scene_cache"])
    assert tc3["lanes"]["scene_ctx"]["boundary"] is True
    assert tc3["lanes"]["scene_ctx"]["sig"] != sc1["sig"]


if __name__ == "__main__":
    test_scene_signature_stable_and_boundary_dimensions()
    test_scene_premise_priority()
    test_scene_cache_hit_miss_and_cross_scene_carry()
    test_scene_cache_doc_round_trip_and_lifetime_stats()
    test_ctx_level_survives_with_world_and_persist_runtime()
    test_turn_context_marks_boundary_then_carries_mid_scene()
    print("ok — scene-keyed context loader")
