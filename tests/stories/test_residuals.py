from loom.providers.base import TextResult
from loom.stories.records.residuals import apply_residual_calls, consolidate, render_dossier
from loom.stories.runtime.state import record_raw_turn


def test_raw_turn_recording_is_mechanical_and_idempotent():
    world = {}
    record_raw_turn(world, step=4, present=["mara", "theo"], location="attic", text="Theo gives Mara a key.", player_input="I take it.")
    record_raw_turn(world, step=4, present=["mara"], location="elsewhere", text="duplicate")
    assert world["turns"] == [{"step": 4, "present": ["mara", "theo"], "location": "attic", "input": "I take it.", "text": "Theo gives Mara a key."}]


def test_residual_calls_require_supplied_source_evidence():
    world = {}
    good = {"fn": "assert_residual", "params": {"subject": "mara", "predicate": "carries", "object": "brass key", "evidence": [4], "status": "observed", "requires_source": False}}
    bad = {"fn": "assert_residual", "params": {"subject": "mara", "predicate": "hates", "object": "Theo", "evidence": [99], "status": "inferred", "requires_source": True}}
    result = apply_residual_calls(world, [good, bad], allowed_steps={4})
    assert result["applied"] == [{"fn": "assert_residual", "id": "res-1"}]
    assert result["rejected"] and world["residuals"][0]["evidence"] == [4]


def test_dossier_renders_verbatim_raw_text_not_json():
    story = {"premise": "A return home.", "locations": [{"id": "attic", "name": "The Attic", "description": "dusty rafters"}],
             "arcs": [{"id": "return", "name": "The Return", "premise": "Mara must come home."}],
             "cast": [{"character": "mara", "primary": True}]}
    world = {"turns": [{"step": 4, "present": ["mara"], "location": "attic", "input": "I take it.", "text": "Theo gives Mara a key."}]}
    dossier, turns = render_dossier(story, world)
    assert "[TURN 4]" in dossier and "Theo gives Mara a key." in dossier
    assert "LOCATIONS (part of the story card)" in dossier and "The Attic [attic]" in dossier
    assert "ARCS (part of the story card)" in dossier and "The Return [return]" in dossier
    assert "\"text\"" not in dossier and turns[0]["step"] == 4


def test_consolidation_uses_small_tools_and_keeps_raw_turns():
    class Provider:
        def generate_text(self, **kwargs):
            assert kwargs["tools"] and "Theo gives Mara a key." in kwargs["prompt"]
            return TextResult(text="", tool_calls=[{"fn": "assert_residual", "params": {
                "subject": "mara", "predicate": "carries", "object": "key", "evidence": [4],
                "status": "observed", "requires_source": False}}])
    world = {"turns": [{"step": 4, "present": ["mara"], "location": "attic", "input": "", "text": "Theo gives Mara a key."}]}
    result = consolidate(Provider(), {"premise": "A return home.", "cast": [{"character": "mara"}]}, world)
    assert result["applied"] and world["turns"][0]["text"] == "Theo gives Mara a key."
    assert world["residual_consolidated_through"] == 4
