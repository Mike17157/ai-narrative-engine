"""The description-unit layer (runtime/units.py): the ONE way information reaches the
narrator. The guarantees under test are the ones the old fragment lanes violated:
no mid-word cuts, one unit per thing, dedup + stable ordering at composition."""
from loom.stories.runtime.units import (Unit, arc_unit, character_unit, compose,
                                        location_unit, map_unit, particulars_unit,
                                        player_unit, rules_unit, scene_unit, tighten,
                                        transcript_unit, world_unit)


class TestTighten:
    def test_short_text_passes_through(self):
        assert tighten("One two three.", 5) == "One two three."

    def test_cuts_at_sentence_boundary_never_mid_word(self):
        text = "Alpha beta gamma. Delta epsilon zeta eta theta. Iota kappa."
        out = tighten(text, 6)
        assert out == "Alpha beta gamma."
        assert not out.endswith("De")

    def test_first_overlong_sentence_kept_whole(self):
        text = "A very long opening sentence that exceeds the budget entirely. Second."
        out = tighten(text, 3)
        assert out == "A very long opening sentence that exceeds the budget entirely."

    def test_whitespace_normalized(self):
        assert tighten("  a   b\n\nc  ", 10) == "a b c"

    def test_empty(self):
        assert tighten("", 10) == ""
        assert tighten(None, 10) == ""


class TestUnitRender:
    def test_one_liner_emdash(self):
        assert Unit(id="w", kind="world", title="THE WORLD", text="A place.").render() \
            == "THE WORLD — A place."

    def test_multiline_gets_headed_section(self):
        out = Unit(id="p", kind="state", title="KEEP TRUE", text="- a\n- b").render()
        assert out == "KEEP TRUE:\n- a\n- b"

    def test_empty_body_drops(self):
        assert Unit(id="x", kind="world", title="T", text="  ").render() == ""

    def test_no_title_is_bare_body(self):
        assert Unit(id="x", kind="state", title="", text="body").render() == "body"


class TestCompose:
    def test_dedups_by_id_keeping_first(self):
        a = Unit(id="world", kind="world", title="W", text="first.")
        b = Unit(id="world", kind="world", title="W", text="second.")
        assert compose([a, b]) == "W — first."

    def test_orders_by_kind_not_insertion(self):
        tr = Unit(id="transcript", kind="transcript", title="T", text="the play.")
        w = Unit(id="world", kind="world", title="W", text="the world.")
        c = Unit(id="char.a", kind="character", title="A", text="a person.")
        out = compose([tr, c, w])
        assert out.index("the world.") < out.index("a person.") < out.index("the play.")

    def test_same_kind_keeps_insertion_order(self):
        a = Unit(id="char.a", kind="character", title="A", text="first.")
        b = Unit(id="char.b", kind="character", title="B", text="second.")
        out = compose([b, a])
        assert out.index("second.") < out.index("first.")

    def test_empty_units_drop_without_holes(self):
        out = compose([Unit(id="w", kind="world", title="W", text="here."),
                       Unit(id="s", kind="scene", title="S", text="")])
        assert out == "W — here."

    def test_blocks_blank_line_separated(self):
        out = compose([Unit(id="w", kind="world", title="W", text="a."),
                       Unit(id="p", kind="player", title="P", text="b.")])
        assert out == "W — a.\n\nP — b."


class TestFactories:
    def test_character_folds_dynamics_as_sentences(self):
        u = character_unit("kotori_hase", "Kotori Hase", surface="Already waiting at the corner.",
                           dynamics=["With Towa (watcher): pins grainy photographs."])
        assert "Already waiting at the corner." in u.text
        assert "With Towa (watcher): pins grainy photographs." in u.text
        assert u.id == "char.kotori_hase" and u.kind == "character"

    def test_character_dynamics_tightened_at_sentence_boundary(self):
        long_dynamic = ("With Y: a complete thought that goes on for quite a while. "
                        "And then another sentence that pushes the whole thing well past "
                        "the thirty-word budget for a single dynamic line.")
        u = character_unit("x", "X", surface="S.", dynamics=[long_dynamic])
        assert "a complete thought" in u.text
        assert "thirty-word budget" not in u.text

    def test_player_generic_name(self):
        u = player_unit("Player", surface="")
        assert u.title == "THE PLAYER"
        assert 'You narrate the player as "you"' in u.text

    def test_player_named(self):
        u = player_unit("Kotaro", surface="A transfer student.")
        assert u.title == "Kotaro (THE PLAYER)"
        assert "A transfer student." in u.text

    def test_map_current_and_index_no_double_period(self):
        u = map_unit(("The Gate", "Iron gates chained at dusk."),
                     ["The Arcade: crepes and charms.", "The Roof: planter boxes."])
        assert "You are at The Gate: Iron gates chained at dusk." in u.text
        assert ".." not in u.text
        assert "The Arcade: crepes and charms;" in u.text or "charms." in u.text

    def test_location_current_flag(self):
        u = location_unit("gate", "The Gate", "Iron gates.", current=True)
        assert "(the scene is here now.)" in u.text

    def test_world_tone_merged(self):
        u = world_unit("A premise.", "quiet dread")
        assert "A premise." in u.text and "Tone: quiet dread." in u.text

    def test_scene_tightened(self):
        u = scene_unit("Visible truth. " + "More detail. " * 60, max_words=20)
        assert u.text.startswith("Visible truth.")
        assert len(u.text.split()) <= 25

    def test_particulars_lines(self):
        u = particulars_unit(["the brass watch", "open thread: the hum"])
        assert "- the brass watch" in u.text and "- open thread: the hum" in u.text

    def test_arc_settling_and_retired(self):
        u = arc_unit(["find the source"], settling="Kotori warned you",
                     retired="the plaque joke")
        assert "- find the source" in u.text
        assert "Settled earlier: Kotori warned you" in u.text
        assert "Retired threads stay retired: the plaque joke" in u.text

    def test_rules_singular(self):
        assert rules_unit("Obey.").render() == "RULES — Obey."

    def test_transcript_beats_then_verbatim(self):
        u = transcript_unit(["Player: hello", "Narrator: hi"], ["earlier beat"])
        body = u.render()
        assert body.index("Earlier, in brief:") < body.index("This scene so far:")
        assert "- earlier beat" in body and "Player: hello" in body

    def test_transcript_empty_renders_nothing(self):
        assert transcript_unit([], []).render() == ""
