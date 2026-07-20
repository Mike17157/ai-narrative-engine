from loom.stories.manga_agent import (bootstrap_turn, normalize_agent_turn,
                                      structure_digest)


FIELDS = {"prologue": {"sections": [{"title": "Arrival", "text": "..."},
                                    {"title": "", "text": "An untitled opening passage that keeps going "
                                     "well past sixty characters to exercise the truncation path."}]}}

SCENES = [
    {"loc": "Platform 9", "pages": [{"text": "a", "beat": "the train arrives"},
                                    {"text": "b", "beat": ""}]},
    {"loc": "Night bus", "pages": [{"text": "c"}]},
]

CHAPTERS = [
    {"title": "Platform 9", "text": "one two three", "source_scene": 0},
    {"title": "Night bus", "text": "four five", "source_scene": 1},
]

PANELS = [
    {"id": "chapter-1", "chapter": 0, "title": "Platform 9", "shot": "wide two-shot",
     "characters": ["mina", "rae"], "moment": "Mina catches Rae's sleeve.",
     "image": "/api/stories/x/bg/manga_chapter_1.png"},
    {"id": "chapter-2", "chapter": 1, "title": "Night bus", "shot": "medium two-shot",
     "characters": ["vera", "mina"], "moment": "Vera folds the letter."},
]


def test_digest_empty_world_points_at_bake_but_lacks_scenes():
    digest = structure_digest({}, {})
    assert digest["prologue"] == []
    assert digest["scenes"] == [] and digest["baked_chapters"] == [] and digest["panels"] == []
    assert digest["status"] == {"has_scenes": False, "has_baked": False, "has_plan": False,
                                "rendered_count": 0, "panel_count": 0, "next_step": "discuss"}


def test_digest_full_world_counts_and_rendered_flags():
    digest = structure_digest(FIELDS, {"manuscript": SCENES, "baked_story": CHAPTERS,
                                       "manga_plan": {"panels": PANELS}})
    assert digest["prologue"] == ["Arrival",
                                  "An untitled opening passage that keeps going well past sixty"]
    assert digest["scenes"] == [
        {"index": 0, "location": "Platform 9", "pages": 2, "beats": ["the train arrives"]},
        {"index": 1, "location": "Night bus", "pages": 1, "beats": []},
    ]
    assert digest["baked_chapters"] == [
        {"index": 0, "title": "Platform 9", "words": 3},
        {"index": 1, "title": "Night bus", "words": 2},
    ]
    assert digest["panels"][1]["rendered"] is False
    assert digest["panels"][0]["characters"] == ["mina", "rae"]
    assert digest["status"]["panel_count"] == 2
    assert digest["status"]["rendered_count"] == 1


def test_next_step_transitions():
    played = {"manuscript": SCENES}
    assert structure_digest({}, played)["status"]["next_step"] == "bake"
    baked = {**played, "baked_story": CHAPTERS}
    assert structure_digest({}, baked)["status"]["next_step"] == "manga-plan"
    planned = {**baked, "manga_plan": {"panels": PANELS}}
    assert structure_digest({}, planned)["status"]["next_step"] == "manga-render"
    done = {**baked, "manga_plan": {"panels": [dict(p, image="/x.png") for p in PANELS]}}
    assert structure_digest({}, done)["status"]["next_step"] == "discuss"


def _digest(**world):
    return structure_digest({}, world)


def test_normalize_keeps_applicable_action():
    digest = _digest(manuscript=SCENES)
    turn = normalize_agent_turn(
        {"reply": "Time to bake.", "suggestions": ["Bake the prose"],
         "action": {"kind": "bake", "note": "Turn the played scenes into chapters."}}, digest)
    assert turn == {"reply": "Time to bake.", "suggestions": ["Bake the prose"],
                    "action": {"kind": "bake", "note": "Turn the played scenes into chapters."}}


def test_normalize_drops_bake_without_scenes_and_explains():
    digest = _digest()
    turn = normalize_agent_turn(
        {"reply": "Let's bake!", "suggestions": [], "action": {"kind": "bake"}}, digest)
    assert turn["action"] is None
    assert "play some scenes first" in turn["reply"]


def test_normalize_drops_manga_plan_without_baked_chapters():
    digest = _digest(manuscript=SCENES)
    turn = normalize_agent_turn(
        {"reply": "Plan the panels.", "suggestions": [], "action": {"kind": "manga-plan"}}, digest)
    assert turn["action"] is None
    assert "bake the prose first" in turn["reply"]


def test_normalize_drops_manga_render_when_all_panels_rendered():
    digest = _digest(manuscript=SCENES, baked_story=CHAPTERS,
                     manga_plan={"panels": [dict(p, image="/x.png") for p in PANELS]})
    turn = normalize_agent_turn(
        {"reply": "Render now.", "suggestions": [], "action": {"kind": "manga-render"}}, digest)
    assert turn["action"] is None
    assert "nothing new to render" in turn["reply"]


def test_normalize_keeps_manga_render_with_unrendered_panels():
    digest = _digest(manuscript=SCENES, baked_story=CHAPTERS, manga_plan={"panels": PANELS})
    turn = normalize_agent_turn(
        {"reply": "Render the plan.", "suggestions": [], "action": {"kind": "manga-render", "note": "go"}},
        digest)
    assert turn["action"] == {"kind": "manga-render", "note": "go"}


def test_normalize_drops_unknown_action_kind():
    digest = _digest(manuscript=SCENES)
    turn = normalize_agent_turn(
        {"reply": "ok", "suggestions": [], "action": {"kind": "illustrate"}}, digest)
    assert turn["action"] is None


def test_normalize_reply_fallback_and_suggestion_trimming():
    digest = _digest()
    turn = normalize_agent_turn(
        {"reply": "  ", "suggestions": ["a", "", "b", "a", "c", "d", "e"], "action": None}, digest)
    assert turn["reply"]
    assert turn["suggestions"] == ["a", "b", "c", "d"]
    assert normalize_agent_turn(None, digest)["reply"]
    assert normalize_agent_turn({"reply": "hi"}, digest)["suggestions"] == []


def test_bootstrap_shape_and_next_step_variants():
    turn = bootstrap_turn(_digest())
    assert set(turn) == {"reply", "suggestions", "action"}
    assert turn["action"] is None
    assert 2 <= len(turn["suggestions"]) <= 4
    planned = bootstrap_turn(_digest(manuscript=SCENES, baked_story=CHAPTERS,
                                     manga_plan={"panels": PANELS}))
    assert "render" in planned["reply"].lower()
    assert all(isinstance(s, str) and s for s in planned["suggestions"])
