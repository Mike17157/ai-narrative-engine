"""StoryMaster scene lifecycle — the coordinator fires PER SCENE, not per turn.

Pure checks (no LLM, no backend): boundary detection, the `scene` event through the bus,
plan persistence, old-scene close into the log, and the consequence-context block.
"""
from types import SimpleNamespace

from loom.stories.storymaster import (StoryMaster, advance_arc, arc_milestone, plot_direction,
                                      record_page, scene_block, state_card)


def _sm(world, location="The Grove"):
    ctx = SimpleNamespace(base_settings=SimpleNamespace(characters={}), root=None)
    st = SimpleNamespace(cast=[], premise="a woodcutter owes a fairy a debt", name="t")
    return StoryMaster(ctx, st, world, location=location)   # provider=None → no LLM


def test_boundary_and_open():
    w = {}
    sm = _sm(w)
    assert sm.scene_boundary("grove")                    # opening = a boundary
    plan = sm.open_scene(loc_id="grove")
    assert plan["space"] == "grove" and plan["loc"] == "The Grove"
    assert not sm.scene_boundary("grove")                # mid-scene: no re-fire
    assert sm.scene_boundary("village")                  # location change = a boundary


def test_failed_plan_still_marks_boundary():
    # provider=None leaves goal empty — the boundary must still be marked (no re-fire loop)
    w = {}
    _sm(w).open_scene(loc_id="grove")
    assert w["scene_plan"]["space"] == "grove" and w["scene_plan"]["goal"] == ""
    assert not _sm(w).scene_boundary("grove")


def test_old_scene_closes_into_log():
    w = {}
    _sm(w).open_scene(loc_id="grove")
    w["scene_plan"]["goal"] = "learn what the fairy's debt costs"
    _sm(w, "The Village").open_scene(loc_id="village")
    assert w["scene_plan"]["space"] == "village" and w["scene_plan"]["goal"] == ""
    assert any("scene closes" in line and "debt" in line for line in w["log"])


def test_scene_block():
    assert scene_block({}) == ""
    assert scene_block({"goal": ""}) == ""               # unplanned scene → no context block
    b = scene_block({"goal": "G", "pressure": "P", "exit": "E"})
    assert "G" in b and "P" in b and "E" in b and "never announce" in b


def test_state_card_is_a_free_view():
    st = SimpleNamespace(cast=[], premise="a debt to a fairy", name="T")
    w = {"step": 3, "location": "the grove",
         "scene_plan": {"space": "g", "loc": "The Grove", "opened": 2,
                        "goal": "learn the debt", "pressure": "", "exit": ""},
         "people": {"Old Bren": {"at": "The Grove", "note": "charcoal burner"}},
         "details": [{"text": "the cracked brass compass"}],
         "promises": [{"setup": "the fairy will return at dusk", "status": "open"}]}
    c = state_card(w, st)
    for frag in ("STATE CARD", "step 3", "learn the debt", "a debt to a fairy",
                 "Old Bren", "cracked brass compass", "return at dusk"):
        assert frag in c, f"card missing: {frag}"
    w["step"] = 4
    w["details"].append({"text": "a fresh scar on the mule's flank"})
    c2 = state_card(w, st)                     # updating = mutate the model, re-derive: free
    assert "step 4" in c2 and "mule's flank" in c2


def _arc_world():
    return {"arc": {"name": "The Asking", "question": "will Eli ask her?", "stage": 0,
                    "stages": [
                        {"title": "the war with himself", "purpose": "P1",
                         "milestone": "Eli says the words out loud to Lila", "events": ["e1"]},
                        {"title": "dusk walks", "purpose": "P2",
                         "milestone": "they hold hands", "events": ["e2"]}]}}


def test_arc_direction_and_advance():
    st = SimpleNamespace(cast=[], premise="p", name="T")
    w = _arc_world()
    d = plot_direction(w, st)
    assert "STORY ARC" in d and "war with himself" in d and "says the words" in d \
        and "after that: dusk walks" in d
    assert arc_milestone(w) == "Eli says the words out loud to Lila"
    assert advance_arc(w) == "dusk walks" and w["arc"]["stage"] == 1
    assert "dusk walks" in plot_direction(w, st) and arc_milestone(w) == "they hold hands"
    assert advance_arc(w) == "complete"
    assert advance_arc(w) == ""                      # past the end: no-op
    assert arc_milestone(w) == ""                    # no active stage → scribe watch off
    assert "STORY ARC" not in plot_direction(w, st)  # completed arc → derived plot again
    assert any("arc:" in line for line in w["log"])


def test_card_shows_arc():
    st = SimpleNamespace(cast=[], premise="p", name="T")
    w = {**_arc_world(), "step": 1}
    assert "war with himself" in state_card(w, st)


def test_manuscript_groups_by_scene():
    w = {"transcript": []}
    for i, (loc, txt) in enumerate([("Grove", "a"), ("Grove", "b"), ("Village", "c")]):
        w["transcript"].append(txt)
        record_page(w, loc=loc, text=txt, beat=f"beat{i}", step=i)
    ms = w["manuscript"]
    assert [m["loc"] for m in ms] == ["Grove", "Village"]
    assert [p["text"] for p in ms[0]["pages"]] == ["a", "b"]
    assert ms[1]["pages"][0]["ti"] == 2                  # tied to its transcript entry
    record_page(w, loc="Village", text="  ", beat="", step=3)   # empty text → no page
    assert len(ms[1]["pages"]) == 1


if __name__ == "__main__":
    test_boundary_and_open()
    test_failed_plan_still_marks_boundary()
    test_old_scene_closes_into_log()
    test_scene_block()
    test_state_card_is_a_free_view()
    test_arc_direction_and_advance()
    test_card_shows_arc()
    test_manuscript_groups_by_scene()
    print("ok")
