from loom.stories.runtime.scenario import (character_context, close_scene, eligible_scenes,
                                           empty_scenario_state, record_turn, start_scene)


def test_ferry_scene_and_presence_gated_memory():
    story = {"fields": {"scene_catalog": [
        {"id": "ferry", "slots": ["morning"], "locations": ["ferry"], "participants": ["player", "shuri"]},
        {"id": "night-hunt", "slots": ["night"], "locations": ["harbor"], "requires": {"flags": {"clue": True}}},
    ]}}
    state = empty_scenario_state(); state.update({"time": "morning", "location": "ferry"})
    assert [s["id"] for s in eligible_scenes(story, state)] == ["ferry"]
    start_scene(story, state, "ferry")
    record_turn(state, "Shuri, did you hear about the shop owner?", addressed="shuri")
    close_scene(state, summary="Shuri meets the Returner on the ferry.",
                public_facts=["The shop owner is missing."], private={"shuri": ["The Returner seems unsettled."]})
    assert character_context(state, "shuri")["memories"][0]["private"]
    assert not character_context(state, "haru")["memories"]
    assert character_context(state, "haru")["public_facts"] == ["The shop owner is missing."]


if __name__ == "__main__":
    test_ferry_scene_and_presence_gated_memory()
    print("ok — scenario foundation")
