"""End-to-end privacy checks for the thematic arc authoring boundary."""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.authoring.interview_graph import InterviewResult
import loom.stories.authoring.interview_graph as interview_graph
import loom.stories.authoring.card_graph as card_graph


_PRIVATE_MARKERS = (
    "PRIVATE_TRUTH__SHURI_LOVES_THE_RETURNER",
    "PRIVATE_NEED__LET_HERSELF_BE_CHOSEN",
    "PRIVATE_RECOGNITION__SHE_HAS_ALWAYS_LOVED_HIM",
    "PRIVATE_PRESSURE__THE_FERRY_WILL_TAKE_HER_AWAY",
    "PRIVATE_REVELATION__SHE_UNDERSTANDS_TOO_LATE",
)


def _arc_design() -> dict:
    return {
        "version": 1,
        "themes": [{
            "id": "impermanence",
            "label": "Impermanence",
            "question": "Can love still matter when time runs out?",
        }],
        "arcs": [{
            "id": "shuri-last-ferry",
            "title": "The last ferry",
            "theme_id": "impermanence",
            "owner": "shuri",
            "dramatic_question": "Will Shuri let the return crossing become another goodbye?",
            "starting_belief": "Being useful is safer than asking to be chosen.",
            "truth": _PRIVATE_MARKERS[0],
            "turning_points": [{
                "id": "ferry-bell",
                "kind": "pressure",
                "scene_id": "crossing",
                "when": "morning",
                "public_surface": "The ferry bell cuts off a conversation before either can finish it.",
                "private_pressure": _PRIVATE_MARKERS[3],
                "revelation": _PRIVATE_MARKERS[4],
            }],
            "character_threads": [{
                "character": "shuri",
                "protective_strategy": "She turns tenderness into a practical task.",
                "blind_spot": "Care means asking for nothing.",
                "unacknowledged_need": _PRIVATE_MARKERS[1],
                "limitation": "She cannot ask someone to stay.",
                "visible_tell": "She rearranges cups when cornered.",
                "recognition": _PRIVATE_MARKERS[2],
            }],
        }],
    }


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-arc-api-boundary-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _create_story(client: TestClient) -> str:
    response = client.post("/api/stories/new", json={"name": "Arc boundary"})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def _assert_not_public(payload: object) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    for marker in _PRIVATE_MARKERS:
        assert marker not in serialized


def test_interview_persists_private_arc_for_the_director_but_returns_a_redacted_story_card():
    """An arc interview owns the private document; the Story-card response never does."""
    patch = {"themes": ["Impermanence"], "fields": {"arc_design": _arc_design()}}
    original_turn = interview_graph.run_interview_turn
    original_provider = ProviderContextMixin.story_agent_provider

    async def fake_turn(**_kwargs):
        return InterviewResult(reply="Shuri's avoidance is now a pressure, not a promised confession.",
                               patch=patch, next_focus="first_day")

    interview_graph.run_interview_turn = fake_turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        with _isolated_client() as client:
            key = _create_story(client)
            seeded = client.put(f"/api/stories/{key}", json={"cast": [{"character": "shuri"}]})
            assert seeded.status_code == 200, seeded.text

            interview = client.post(f"/api/stories/{key}/interview", json={
                "focus": "arcs",
                "messages": [{"role": "user", "text": _PRIVATE_MARKERS[0]}],
            })
            assert interview.status_code == 200, interview.text
            body = interview.json()

            # The authoring mutation is durable, but every returned Story-card
            # projection follows the public redaction boundary.
            assert body["story"]["themes"] == ["Impermanence"]
            assert "arc_design" not in body["story"].get("fields", {})
            assert body["story"]["fields"]["arc_outline"] == {
                "themes": [{"id": "impermanence", "label": "Impermanence"}],
                "arcs": [{"owner": "shuri", "theme_id": "impermanence", "theme": "Impermanence"}],
            }
            assert body["story"]["fields"]["author_arc_outline"] == {
                "arcs": [{
                    "id": "shuri-last-ferry",
                    "title": "The last ferry",
                    "owner": "shuri",
                    "theme": {
                        "id": "impermanence",
                        "label": "Impermanence",
                        "question": "Can love still matter when time runs out?",
                    },
                    "dramatic_question": "Will Shuri let the return crossing become another goodbye?",
                }],
            }
            _assert_not_public(body["story"])

            public = client.get(f"/api/stories/{key}")
            assert public.status_code == 200, public.text
            assert "arc_design" not in public.json().get("fields", {})
            _assert_not_public(public.json())

            # The raw author conversation remains resumable, but lives on an
            # explicit author-only endpoint instead of being injected into
            # the Story-card payload that generic model operations receive.
            history = client.get(f"/api/stories/{key}/interview-history")
            assert history.status_code == 200, history.text
            assert _PRIVATE_MARKERS[0] in json.dumps(history.json())

            director = client.get(f"/api/stories/{key}/director-preview")
            assert director.status_code == 200, director.text
            preview = director.json()
            assert preview["author_only"] is True
            assert preview["arc_design"]["configured"] is True
            assert preview["arc_design"]["valid"] is True
            assert preview["arc_design"]["arcs"][0]["truth"] == _PRIVATE_MARKERS[0]
            assert preview["arc_design"]["character_threads"][0]["unacknowledged_need"] == _PRIVATE_MARKERS[1]
            assert preview["arc_design"]["scene_beats"][0]["private_pressure"] == _PRIVATE_MARKERS[3]
    finally:
        interview_graph.run_interview_turn = original_turn
        ProviderContextMixin.story_agent_provider = original_provider


def test_generic_card_editor_prompts_do_not_receive_private_arc_material():
    """Review/organize may point to an arc, but cannot be told its answer."""
    original_provider = ProviderContextMixin.text_provider_for
    original_operation = card_graph.run_card_operation
    seen: dict[str, str] = {}

    async def fake_operation(*, operation: str, prompt: str, **_kwargs):
        seen[operation] = prompt
        if operation == "review_card":
            return {"suggestions": []}
        if operation == "organize_card":
            return {
                "world": {"genre": "", "setting": "", "atmosphere": "", "history": "", "customs": "", "technology": "", "background": "", "loop": {
                    "start": "", "reset": "", "memory": "", "returner": "",
                }, "entity": {"description": "", "knowledge": "", "limitations": "", "tactic": "", "objective": ""}},
                "premise": "",
                "time_system": {"entity_periods": []},
                "fields": {"first_day_plan": {"objective": "", "events": []}, "open_questions": []},
            }
        raise AssertionError(f"unexpected operation {operation}")

    ProviderContextMixin.text_provider_for = lambda _self, *_args, **_kwargs: object()
    card_graph.run_card_operation = fake_operation
    try:
        with _isolated_client() as client:
            key = _create_story(client)
            seeded = client.put(f"/api/stories/{key}", json={
                "cast": [{"character": "shuri"}],
                "time_system": {"slots": ["morning"], "entity_periods": [{
                    "id": "hunt", "slots": ["morning"], "state": "hunting",
                    "capabilities": [_PRIVATE_MARKERS[0]], "constraint": _PRIVATE_MARKERS[1],
                }]},
                "fields": {
                    "arc_design": _arc_design(),
                    "author_notes": [_PRIVATE_MARKERS[2]],
                    "first_day_plan": {"events": [{
                        "id": "crossing", "when": "morning", "visible": "The ferry bell rings.",
                        "hidden": _PRIVATE_MARKERS[3], "trigger": _PRIVATE_MARKERS[4],
                        "evidence": _PRIVATE_MARKERS[0], "knowledge": {"protagonist": _PRIVATE_MARKERS[1]},
                    }]},
                },
            })
            assert seeded.status_code == 200, seeded.text
            assert client.post(f"/api/stories/{key}/card/review", json={}).status_code == 200
            assert client.post(f"/api/stories/{key}/card/organize", json={}).status_code == 200
    finally:
        ProviderContextMixin.text_provider_for = original_provider
        card_graph.run_card_operation = original_operation

    for prompt in seen.values():
        _assert_not_public(prompt)
        assert '"hidden"' not in prompt
        assert '"trigger"' not in prompt
        assert '"evidence"' not in prompt
        assert '"knowledge"' not in prompt
        assert '"entity_periods"' not in prompt
        assert '"author_notes"' not in prompt
    assert "arc_outline" in seen["review_card"]
    assert "arc_outline" in seen["organize_card"]
    assert "author_arc_outline" not in seen["review_card"]
    assert "author_arc_outline" not in seen["organize_card"]


if __name__ == "__main__":
    test_interview_persists_private_arc_for_the_director_but_returns_a_redacted_story_card()
    test_generic_card_editor_prompts_do_not_receive_private_arc_material()
    print("ok — thematic arc API boundary")
