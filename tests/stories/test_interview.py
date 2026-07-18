from loom.stories.authoring.interview import (apply_patch, normalize_interview_messages,
                                              parse_patch, patch_from_recovery, split_response,
                                              split_response_with_focus)
from loom.commands import split_cmd_response


def test_interview_patch_updates_real_card_and_keeps_unrelated_fields():
    card = {"world": {"genre": "fantasy"}, "premise": "", "fields": {"status": "interviewing", "interview_history": []}}
    _, patch = split_response("A rain-soaked start.\n```PATCH\n{\"world\": {\"setting\": \"a drowned port\"}, \"fields\": {\"open_questions\": [\"Who owns the lighthouse?\"]}}\n```")
    updated = apply_patch(card, patch)
    assert updated["world"] == {"genre": "fantasy", "setting": "a drowned port"}
    assert updated["fields"]["open_questions"] == ["Who owns the lighthouse?"]
    assert updated["fields"]["interview_history"] == []
    plan = {"objective": "survive the first day", "events": [{
        "id": "ferry", "when": "morning", "location": "harbor", "participants": ["player"],
        "visible": "The ferry noses toward the harbor while a suitcase rattles in an empty seat.",
        "hook": "The player can inspect the suitcase or ask the ferryman who left it there.",
    }]}
    assert apply_patch(card, {"fields": {"first_day_plan": plan}})["fields"]["first_day_plan"] == plan
    clock = {"entity_periods": [{"id": "night", "slots": ["night"], "state": "hunting"}]}
    assert apply_patch(card, {"time_system": clock})["time_system"]["entity_periods"][0]["id"] == "night"
    abilities = [{"id": "candle-spark", "name": "Candle spark", "aliases": ["spark a wick"],
                  "limits": "Prepared wicks only."}]
    assert apply_patch(card, {"fields": {"player_abilities": abilities}}, "world")["fields"]["player_abilities"] == abilities
    private_fact = "The ferry captain was copied before the story opens."
    updated = apply_patch(card, {"fields": {"author_notes": [private_fact]}}, "world")
    assert updated["fields"]["author_notes"] == [f"[[hidden]]{private_fact}[[/hidden]]"]


def test_patch_rejects_out_of_section_and_invalid_fields():
    try:
        parse_patch("```PATCH\n{\"cast\": []}\n```", {"world"})
        raise AssertionError("out-of-section patch was accepted")
    except ValueError as exc:
        assert "outside" in str(exc)
    try:
        apply_patch({}, {"fields": {"secret": "no"}})
        raise AssertionError("invalid fields patch was accepted")
    except ValueError as exc:
        assert "only change" in str(exc)
    try:
        apply_patch({"themes": ["Legacy"]}, {"themes": ["Impermanence"]}, "overview")
        raise AssertionError("Overview was allowed to rewrite a storyline theme")
    except ValueError as exc:
        assert "outside" in str(exc)


def test_cmd_is_the_primary_interview_transport_and_prose_only_is_safe():
    prose, patch = split_response('A quiet return.\n```CMD\nMERGE world {"genre":"mystery slice of life"}\nSET premise "A graduate returns by ferry to her island home."\n```')
    assert prose == "A quiet return."
    assert patch["world"]["genre"] == "mystery slice of life"
    assert patch["premise"].startswith("A graduate")
    prose, patch = split_response("I hear you — Shuri is already established.")
    assert prose.startswith("I hear") and patch == {}
    prose, commands = split_cmd_response('Note\n```CMD\nSET title "Harbor"\n```')
    assert prose == "Note" and commands == [{"op": "SET", "path": "title", "value": "Harbor"}]
    prose, commands = split_cmd_response('Question\n``` MERGE world {"genre":"mystery"} SET premise "Ferry home" ```')
    assert prose == "Question" and [c["op"] for c in commands] == ["MERGE", "SET"]
    prose, commands = split_cmd_response(
        'Good — I will keep the harbor ordinary until the rupture.\n\n'
        'CMD\n'
        'MERGE world {"genre":"mystery"}\n'
        'SET premise "A ferry returns home."\n'
        'CMD\n\n'
        'What makes Shuri stay after the ferry leaves?'
    )
    assert prose == (
        "Good — I will keep the harbor ordinary until the rupture.\n\n"
        "What makes Shuri stay after the ferry leaves?"
    )
    assert commands == [
        {"op": "MERGE", "path": "world", "value": {"genre": "mystery"}},
        {"op": "SET", "path": "premise", "value": "A ferry returns home."},
    ]


def test_next_focus_marker_is_private_and_does_not_change_the_patch():
    prose, patch, next_focus = split_response_with_focus(
        'Who is waiting at the dock?\n```CMD\nMERGE world {"setting":"an island"}\n```\n'
        'NEXT_FOCUS: cast'
    )
    assert prose == "Who is waiting at the dock?"
    assert patch == {"world": {"setting": "an island"}}
    assert next_focus == "cast"


def test_backtick_wrapped_next_focus_marker_is_still_recognized():
    # Measured live (deepseek-chat): a model sometimes wraps NEXT_FOCUS in inline code
    # alongside a similarly-wrapped SET command — "`NEXT_FOCUS: world`" — instead of the
    # plain marker line the prompt asks for. Previously invisible to _NEXT_FOCUS, so the
    # turn silently fell back to some other next section.
    prose, patch, next_focus = split_response_with_focus(
        'Noted the open questions.\n`SET fields.open_questions ["x"]`  \n`NEXT_FOCUS: world`'
    )
    assert next_focus == "world"
    assert patch == {"fields": {"open_questions": ["x"]}}
    assert "NEXT_FOCUS" not in prose


def test_focused_day_plan_and_schedule_are_not_overwritten_or_mistargeted():
    card = {"world": {"setting": "island"}, "time_system": {"entity_periods": []},
            "fields": {"first_day_plan": {"objective": "old", "events": []}}}
    plan = {"objective": "Reach the dock before the copy hunts.", "events": [
        {"id": "arrival", "when": "morning", "location": "ferry", "participants": ["player"],
         "visible": "The ferry arrives while a familiar whistle sounds from below deck.",
         "hook": "The player can search below deck or step outside to find the whistle's source.",
         "hidden": "The entity watches.", "trigger": "Leave the ferry.",
         "evidence": "A familiar whistle.", "knowledge": {"protagonist": "uneasy"}}
    ]}
    updated = apply_patch(card, {"fields": {"first_day_plan": plan}}, "first_day")
    assert updated["fields"]["first_day_plan"] == plan
    try:
        apply_patch(card, {"world": {"setting": "somewhere else"}}, "first_day")
        raise AssertionError("focused Day One edit was allowed to change world canon")
    except ValueError as exc:
        assert "outside" in str(exc)

    schedule = {"entity_periods": [{"id": "night", "slots": ["night"], "state": "hunting"}]}
    updated = apply_patch(card, {"time_system": schedule}, "time_system")
    assert updated["time_system"]["entity_periods"] == schedule["entity_periods"]


def test_focused_day_plan_uses_the_same_playable_scene_contract_as_the_architect():
    card = {"fields": {"first_day_plan": {"events": []}}}
    abstract = {"objective": "Learn the island's past.", "events": [{
        "id": "subtle-hints", "when": "evening", "location": "village-square", "participants": ["shuri"],
        "visible": "Subtle hints of the island's history surface through conversation or environment.",
        "hook": "The player can investigate the hints.",
    }]}
    try:
        apply_patch(card, {"fields": {"first_day_plan": abstract}}, "first_day")
        raise AssertionError("an abstract lore placeholder was accepted as a scene")
    except ValueError as exc:
        assert "concrete clue" in str(exc)

    concrete = {"objective": "Learn the island's past.", "events": [{
        "id": "bell-offering", "when": "evening", "location": "village-square", "participants": ["shuri"],
        "visible": "A child leaves wet flowers beneath the old ferry bell, then runs when Shuri recognizes the knot.",
        "hook": "The player can examine the knot or ask Shuri why it frightens her.",
    }]}
    updated = apply_patch(card, {"fields": {"first_day_plan": concrete}}, "first_day")
    assert updated["fields"]["first_day_plan"]["events"][0]["participants"] == ["player", "shuri"]


def test_legacy_bootstrap_messages_do_not_enter_author_history():
    history = normalize_interview_messages([
        {"role": "user", "text": "This is an existing story card. Read what is already established and identify the single most important thing it still needs before it can drive playable scenes."},
        {"role": "assistant", "text": "What does Shuri want?"},
        {"role": "user", "text": "She wants to go home."},
    ])
    assert history == [
        {"role": "assistant", "text": "What does Shuri want?"},
        {"role": "user", "text": "She wants to go home."},
    ]


def test_structured_recovery_only_changes_the_selected_card_paths():
    patch = patch_from_recovery("world", {"changes": [
        {"path": "world.loop.memory", "value": "Only the Returner remembers."},
        {"path": "world.loop.reset", "value": "Everything external resets."},
        {"path": "world.loop.end_condition", "value": "Merge with the entity and win a battle of will; victims remain dead."},
        {"path": "premise", "value": "must be ignored"},
    ]})
    assert patch == {"world": {"loop": {
        "memory": "Only the Returner remembers.", "reset": "Everything external resets.",
        "end_condition": "Merge with the entity and win a battle of will; victims remain dead.",
    }}}


def test_structured_recovery_unwraps_a_double_encoded_plain_text_value():
    """Measured live: a recovery pass sometimes wraps its own plain-text answer in an
    extra pair of JSON quotes (world.history arrived as the literal string
    '"A university town."', quote marks included), despite the schema declaring that
    path a plain string, not JSON-encoded. Confirm the extra layer gets stripped."""
    patch = patch_from_recovery("premise", {"changes": [
        {"path": "premise", "value": "\"A university town whose library burned down.\""},
    ]})
    assert patch == {"premise": "A university town whose library burned down."}


def test_structured_recovery_leaves_ordinary_prose_with_quotes_untouched():
    # A quote character inside ordinary prose does not make the whole value valid JSON
    # on its own, so this must never fire on real (non-double-encoded) text.
    patch = patch_from_recovery("premise", {"changes": [
        {"path": "premise", "value": 'He said "hello" and left.'},
    ]})
    assert patch == {"premise": 'He said "hello" and left.'}


def test_world_aliases_are_normalized_into_loop_rules():
    updated = apply_patch({"world": {"loop": {"start": "ferry"}}}, {
        "world": {
            "loop_end_condition": "Merge and win.",
            "reset_persistence": "Only memory persists.",
            "victims_return_after_end": False,
        },
    })
    assert updated["world"]["loop"] == {
        "start": "ferry", "end_condition": "Merge and win.",
        "persistence": "Only memory persists.", "victims_return_after_end": False,
    }
    assert "loop_end_condition" not in updated["world"]


def test_world_focus_can_add_a_real_map_without_erasing_loop_rules():
    card = {"world": {"loop": {"start": "ferry", "memory": "only the Returner remembers"}},
            "locations": [{"id": "ferry", "name": "Old ferry", "description": ""}]}
    updated = apply_patch(card, {
        "locations": [{"id": "ferry", "name": "Electric ferry", "description": "quiet water"},
                      {"id": "harbor", "name": "Island harbor", "description": "the dock"}],
        "start": "ferry",
        "world": {"loop": {"policy": {"trigger": "death"}}},
    }, "world")
    assert updated["start"] == "ferry"
    assert [place["id"] for place in updated["locations"]] == ["ferry", "harbor"]
    assert updated["locations"][0]["name"] == "Electric ferry"
    assert updated["world"]["loop"] == {
        "start": "ferry", "memory": "only the Returner remembers",
        "policy": {"trigger": "death"},
    }


def test_cast_focus_persists_a_concrete_backstory_distinct_from_the_public_core():
    card = {"cast": [{"character": "shuri"}], "fields": {"status": "interviewing"}}
    wound = ("Shuri's parents ran a failing restaurant and pressured her to work unpaid every "
             "night; she yearned to do well in school for a way out.")
    updated = apply_patch(card, {"fields": {
        "character_wounds": {"shuri": wound},
        "character_cores": {"shuri": "Quietly reliable — always the one who covers the shift."},
    }}, "cast")
    assert updated["fields"]["character_wounds"]["shuri"] == wound
    assert updated["fields"]["character_cores"]["shuri"] == "Quietly reliable — always the one who covers the shift."

    # A later turn merges in a new character's wound without disturbing shuri's.
    again = apply_patch(updated, {"fields": {"character_wounds": {"player": "A different backstory."}}}, "cast")
    assert again["fields"]["character_wounds"]["shuri"] == wound
    assert again["fields"]["character_wounds"]["player"] == "A different backstory."

    try:
        apply_patch(card, {"fields": {"character_wounds": {"shuri": "[[hidden]]smuggled[[/hidden]]"}}}, "cast")
        raise AssertionError("a character_wounds value should not be able to smuggle a hidden marker")
    except ValueError:
        pass


def test_wound_and_core_written_inline_on_a_cast_item_still_land_in_their_own_fields():
    """Measured live: models reliably write `core`/`wound` inline on the cast item
    instead of the separate fields.character_cores/character_wounds commands the
    prompt asks for. apply_patch must not depend on the model getting this right —
    it extracts them itself, and the cast list they came from stays clean."""
    card = {"cast": [], "fields": {}}
    updated = apply_patch(card, {"cast": [
        {"character": "curtis", "name": "Curtis", "role": "delivery driver",
         "core": "Keeps the route running because stopping means facing the rent notice.",
         "wound": "Curtis took in his brother's daughter three years ago and the rent notice came last week."},
    ]}, "cast")
    assert updated["fields"]["character_cores"]["curtis"] == (
        "Keeps the route running because stopping means facing the rent notice.")
    assert updated["fields"]["character_wounds"]["curtis"] == (
        "Curtis took in his brother's daughter three years ago and the rent notice came last week.")
    # The persisted cast list itself never carries core/wound — only the identity.
    cast_entry = updated["cast"][0]
    assert "core" not in cast_entry
    assert "wound" not in cast_entry
    assert cast_entry["character"] == "curtis"

    # An explicit, separate fields.character_wounds command in the same patch wins
    # over whatever the model also stuffed inline on the cast item.
    updated2 = apply_patch(card, {
        "cast": [{"character": "curtis", "wound": "inline version, should lose"}],
        "fields": {"character_wounds": {"curtis": "explicit field command, should win"}},
    }, "cast")
    assert updated2["fields"]["character_wounds"]["curtis"] == "explicit field command, should win"


def test_character_wounds_gets_a_paragraph_length_cap_not_the_one_line_core_cap():
    """A concrete ordinary backstory needs real room — reusing character_cores' 360-char
    one-liner cap silently truncated every wound mid-sentence. Regression for that bug."""
    card = {"cast": [{"character": "shuri"}], "fields": {}}
    long_wound = "Shuri's failing family restaurant. " * 15  # 540 chars, over the 360 cap
    assert len(long_wound) > 360
    updated = apply_patch(card, {"fields": {"character_wounds": {"shuri": long_wound.strip()}}}, "cast")
    assert updated["fields"]["character_wounds"]["shuri"] == long_wound.strip()

    # character_cores keeps its original short-surface-line cap.
    over_core_limit = "x" * 500
    capped = apply_patch(card, {"fields": {"character_cores": {"shuri": over_core_limit}}}, "cast")
    assert len(capped["fields"]["character_cores"]["shuri"]) == 360


def test_location_history_round_trips_through_a_world_focus_patch():
    updated = apply_patch({}, {
        "locations": [{"id": "diner", "name": "The Diner",
                       "history": "It's been failing since the highway bypass opened."}],
    }, "world")
    assert updated["locations"][0]["history"] == "It's been failing since the highway bypass opened."
    from loom.config.schema import Location
    assert Location(**updated["locations"][0]).history == "It's been failing since the highway bypass opened."
    assert Location(id="x", name="y").history == ""


def test_theme_arc_focus_persists_a_private_design_without_widening_other_sections():
    card = {"cast": [{"character": "shuri"}], "fields": {"status": "interviewing"}}
    design = {
        "version": 1,
        "themes": [{"id": "impermanence", "label": "Impermanence", "question": "What does waiting cost?"}],
        "arcs": [{
            "id": "shuri-arc", "title": "Shuri and impermanence", "theme_id": "impermanence", "owner": "shuri",
            "starting_belief": "Nothing needs to change while she stays useful.",
            "truth": "She is in love with the Returner.",
            "character_threads": [{
                "character": "shuri", "protective_strategy": "She makes herself useful.",
                "blind_spot": "Care means asking for nothing.", "limitation": "She cannot ask directly.",
                "visible_tell": "She rearranges cups when cornered.",
            }],
        }],
    }
    updated = apply_patch(card, {"fields": {"arc_design": design}}, "arcs")
    # The canonical theme lives with the Storyline document. The root list is
    # a derived compatibility index for older runtime and search paths.
    assert updated["themes"] == ["Impermanence"]
    assert updated["fields"]["arc_design"]["arcs"][0]["truth"] == "She is in love with the Returner."
    try:
        apply_patch(card, {"world": {"genre": "must not cross section"}}, "arcs")
        raise AssertionError("arc focus was allowed to rewrite world canon")
    except ValueError as exc:
        assert "outside" in str(exc)


if __name__ == "__main__":
    test_interview_patch_updates_real_card_and_keeps_unrelated_fields()
    test_patch_rejects_out_of_section_and_invalid_fields()
    test_cmd_is_the_primary_interview_transport_and_prose_only_is_safe()
    test_focused_day_plan_and_schedule_are_not_overwritten_or_mistargeted()
    test_legacy_bootstrap_messages_do_not_enter_author_history()
    test_structured_recovery_only_changes_the_selected_card_paths()
    test_world_aliases_are_normalized_into_loop_rules()
    test_world_focus_can_add_a_real_map_without_erasing_loop_rules()
    test_cast_focus_persists_a_concrete_backstory_distinct_from_the_public_core()
    test_wound_and_core_written_inline_on_a_cast_item_still_land_in_their_own_fields()
    test_character_wounds_gets_a_paragraph_length_cap_not_the_one_line_core_cap()
    test_location_history_round_trips_through_a_world_focus_patch()
    test_theme_arc_focus_persists_a_private_design_without_widening_other_sections()
    print("ok — interview patch boundary")
