"""The prose discipline layer (loom/prose.py): sentence-safe budgeting at the DATA level
and the fragment lint that guards ingest. The bug class under test: destructive word
slices (`split()[:6]`) that stored mid-clause fragments like "deflects Rinka's pitches
with a new" and fed them to the narrator every turn."""
from loom.prose import lint_story_texts, lint_text, looks_like_fragment, tighten


class TestTighten:
    def test_never_cuts_mid_clause(self):
        text = "Deflects Rinka's pitches with a new fake haunting every time. Laughs at her own jokes."
        out = tighten(text, 8)
        assert out == "Deflects Rinka's pitches with a new fake haunting every time."
        # the old cap produced exactly this fragment:
        assert out != "deflects Rinka's pitches with a new"

    def test_first_sentence_kept_whole_when_overlong(self):
        out = tighten("A complete sentence well past budget lives here whole.", 3)
        assert out.endswith("whole.")

    def test_empty(self):
        assert tighten(None, 5) == "" and tighten("   ", 5) == ""


class TestFragmentHeuristic:
    def test_complete_sentence_passes(self):
        assert not looks_like_fragment("She leaves a canned coffee on the wall every night.")

    def test_census_label_passes(self):
        assert not looks_like_fragment("rival herbalist")
        assert not looks_like_fragment("mom's kitchen")

    def test_dangling_function_word_is_fragment(self):
        assert looks_like_fragment("deflects Rinka's pitches with a new")
        assert looks_like_fragment("pins grainy photographs of the dusk")

    def test_unpunctuated_long_text_is_fragment(self):
        assert looks_like_fragment("she waits by the gate every single evening without fail")

    def test_quoted_terminal_passes(self):
        assert not looks_like_fragment('“forgets” her juice box on the roof.')


class TestLintText:
    def test_fragment_warns(self):
        issues = lint_text("deflects Rinka's pitches with a new", path="r.x.dynamic")
        assert [i["code"] for i in issues] == ["prose.fragment"]
        assert issues[0]["severity"] == "warning"

    def test_over_budget_informs(self):
        issues = lint_text("word " * 50 + ".", path="premise", max_words=40)
        assert [i["code"] for i in issues] == ["prose.over_budget"]
        assert issues[0]["severity"] == "info"

    def test_clean_is_silent(self):
        assert lint_text("Tight and complete.", path="x") == []
        assert lint_text("", path="x") == []


class TestLintStoryTexts:
    def _story(self, dynamic: str) -> dict:
        return {
            "premise": "A whole premise.",
            "relationships": [{"id": "r-a-b", "source": "a", "target": "b",
                               "dynamic": dynamic, "potential": "", "trajectory": ""}],
            "locations": [{"id": "gate", "description": "Iron gates chained at dusk.",
                           "scenes": [{"id": "s1", "backstory": "Kotori waits here."}]}],
            "fields": {"first_day_plan": {"events": [
                {"id": "e1", "visible": "Kotori waits with two coffees.",
                 "hook": "Ask about the plaque or keep walking."}]}},
        }

    def test_catches_relationship_fragment(self):
        issues = lint_story_texts(self._story("deflects Rinka's pitches with a new"))
        assert any(i["code"] == "prose.fragment" and "dynamic" in i["path"] for i in issues)

    def test_clean_card_is_silent(self):
        assert lint_story_texts(self._story("Deflects every pitch with a new fake haunting.")) == []

    def test_malformed_rows_skipped(self):
        story = self._story("ok.")
        story["relationships"].append("not-a-dict")
        story["locations"].append(None)
        assert lint_story_texts(story) == []
