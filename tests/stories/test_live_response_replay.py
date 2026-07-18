"""End-to-end replays of ACTUAL captured model completions, not hand-built patches.

test_interview_endpoint.py's cast/relationship tests fake `run_interview_turn` to return an
already-*parsed* `patch` dict — that exercises the minting/key-resolution post-processing, but
never touches `split_response_with_focus` (the CMD-text parser), which is exactly where three of
this session's live bugs actually lived. A test built on a patch dict I hand-wrote can only prove
"if the patch already looks like X, the code does Y" — it can't prove real model text ever
produces X, because I'm the one deciding what "realistic" means.

Every raw string below is the UNEDITED `raw_response` this session actually captured from a live
model call (minimax-m3 / deepseek-chat) via scripts/story_turn.py's --json dump, at the moment
each bug was found. The fake here only replaces the network call; parsing (`split_response_with_
focus`) and the mint/resolve pipeline both run for real, so a regression in the parser is exactly
as visible here as one in the post-processing it feeds.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.authoring.interview import split_response_with_focus
from loom.stories.authoring.interview_graph import InterviewResult
import loom.stories.authoring.interview_graph as interview_graph


def _isolated_client() -> TestClient:
    root = Path(tempfile.mkdtemp())
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    return TestClient(create_app(root))


def _replay(raw_text: str):
    """A fake turn that runs the REAL parser on captured raw text instead of returning
    a hand-built patch — the only thing faked is the network call to the model."""
    async def fake_turn(**_kwargs):
        reply, patch, next_focus = split_response_with_focus(raw_text)
        return InterviewResult(reply=reply, patch=patch, next_focus=next_focus, raw_response=raw_text)
    return fake_turn


# Captured live 2026-07-16 (quality-test workflow, minimax-m3, focus=cast): a bare ``` fence
# with a literal CMD label — the ORIGINAL, always-supported shape. What this call actually
# exposed live wasn't a parsing failure: character_cores landed keyed "riya_kavadi" (slug)
# while character_wounds landed keyed "Riya Kavadi" (the model's literal display name) —
# nothing downstream that looks wounds up by the resolved cast key could ever find it.
RIYA_KAVADI_RAW = '''Meet Riya Kavadi — the woman the dock can't get rid of and the boats can't quite do without.

She used to be the senior engineer on the night ferry until an engine-room fire she didn't cause ended with her license suspended and the man who actually modified the locked hatch still piloting the same boat. Three years on, she stocks hooks and re-riggs lines for sport fishermen, with burn scars across her palms she never quite manages to hide.

```
CMD
SET cast [{"name":"Riya Kavadi","role":"disgraced ferry engineer who now runs the bait-and-tackle shop at the dock","appearance":"Late forties, lean and sun-darkened, with shiny burn scars across both palms that she never quite manages to hide; salt-grey hair cropped short because long hair and engine work don't mix; usually in a canvas apron stained with fish-gut and machine oil","personality":"Blunt, precise, impatient with anyone who wastes her time or her rope, but oddly gentle with people who are too scared to move on the water","background":"Former senior engineer on the local ferry, lost her license after an engine-room fire she didn't cause; knows the channel, the boats, and exactly who to blame","connection":"The face at the dock — anyone heading out by water has to deal with her first"}]
SET fields.character_cores {"Riya Kavadi":"Runs the bait-and-tackle shop at the dock; knows every current in the channel and is not shy about telling you what's wrong with your knots."}
SET fields.character_wounds {"Riya Kavadi":"Riya was the senior engineer on the night ferry three years ago when a fuel-line rupture turned the engine room into an inferno. She dragged two passengers and the helmsman out through a hatch that should have been locked from the outside; the burn scars on her hands came from that night. The company never acknowledged the near-miss because reporting it would have triggered a coast-guard audit that would have shut down their insurance. Instead they blamed Riya for the locked hatch — a modification the previous chief engineer had made without her knowledge — and had her license suspended. She has spent the years since stocking hooks and re-rigging lines for sport fishermen while the man who actually ordered that hatch modified still pilots the ferry she used to engineer."}
NEXT_FOCUS: world
```

What just happened out on the water — or is about to — that pulls Riya back into something she swore she'd stopped caring about?'''


def test_replay_riya_kavadi_resolves_cores_and_wounds_to_the_same_key():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Riya replay"}).json()["key"]
    original, original_provider = interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider
    interview_graph.run_interview_turn = _replay(RIYA_KAVADI_RAW)
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Invent an interesting new character."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider = original, original_provider

    story = client.get(f"/api/stories/{key}").json()
    cast_key = story["cast"][0]["character"]
    assert story["fields"]["character_cores"][cast_key].startswith("Runs the bait-and-tackle")
    assert story["fields"]["character_wounds"][cast_key].startswith("Riya was the senior engineer")
    assert "Riya Kavadi" not in story["fields"]["character_wounds"]


# Captured live 2026-07-16 (quality-test workflow, minimax-m3, focus=cast): a ```json fence
# with NO "CMD" label at all — the exact shape that made PARSED PATCH come back {} despite a
# fully well-formed cast/cores/wounds block, silently discarding the whole turn.
EUNICE_MARROW_RAW = '''A university town still carrying the loss of its library needs someone whose life has been bent around that loss, and the most useful such person is one the story can interrogate but only on her terms. Eunice Marrow, last surviving clerk of the old library, is that character — the only person in town who can answer questions about what was actually there, and the only one who will make the protagonist earn each answer.

Who is the protagonist — a student, a faculty child, someone returning to town — and do they already know Eunice when the story opens, or do they have to find her?

```json
SET cast [{"name":"Eunice Marrow","role":"Last surviving clerk of the university library that burned two generations ago; keeper of a private reconstruction of the lost collection.","appearance":"Late sixties, thin, gray hair cropped short, the posture of a woman who has stood at catalog drawers for fifty years. Dark cardigans, a satchel of manila folders she takes everywhere.","personality":"Measured, exacting, faintly amused by anyone who has not yet earned her attention. Will not volunteer information; tests a question before she answers it. Patient in a way that looks like suspicion.","background":"Filing clerk at the university library for two years before the fire; sent home with a fever the afternoon it started. Has spent the decades since piecing together what she can of the lost catalog from salvaged fragments, secondhand recollection, and her own memory.","connection":"She is the only person in town who can answer questions about what the library actually held, and the only obstacle between the university and the collection she is keeping."}]
SET fields.character_cores {"eunice-marrow":"Eunice Marrow, last of the old library's staff, guards her private reconstruction of the lost collection and only spends her words on questions she judges worth her time."}
SET fields.character_wounds {"eunice-marrow":"Eunice was nineteen and a filing clerk when her parents — both of whom worked the university grounds — had pulled strings to get her the post. She spent two years learning the catalog the way another girl her age might learn a hymnal, and she was sent home with a fever the afternoon the library caught. She came back to cinders and three colleagues she never saw again, and for fifty years she has been the only person willing to insist the official story is missing pieces. Her late husband was a university press compositor who never read any of her notes. Her daughter stopped speaking to her when Eunice refused to donate the collection. The town considers her half-mad with grief; the university wants what she is keeping; she has outlived everyone who could contradict her."}
```

NEXT_FOCUS: cast'''


def test_replay_eunice_marrow_json_fenced_cmd_block_is_no_longer_dropped():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Eunice replay"}).json()["key"]
    original, original_provider = interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider
    interview_graph.run_interview_turn = _replay(EUNICE_MARROW_RAW)
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Invent an interesting new character."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider = original, original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert len(story["cast"]) == 1
    cast_key = story["cast"][0]["character"]
    assert story["fields"]["character_wounds"][cast_key].startswith("Eunice was nineteen")


# Captured live 2026-07-16 (hargrove_sons build, deepseek-chat, focus=cast): the model, shown
# its own synthetic "player" context entry, echoed it back as a literal cast item AND proposed
# a short self-chosen key ("walt") for the new character that diverges from the name-derived
# mint key — two independent bugs in one real response.
WALT_HARGROVE_RAW = '''Walt Hargrove grew up in the shadow of the Millbrook mill's silicosis deaths, watching his father Eldon handle the funerals and the hushed settlements that kept the families from suing. He learned early that the Hargrove name meant discretion—keeping the town's grief quiet, even when it meant burying the truth. When he took over the funeral home in 1979, he inherited not just the business but the weight of those ledgers in the basement, every silicosis death and every family his father had sat with. Walt's wound is his belief that the Hargroves' role is to protect the town's secrets, even at the cost of their own. He's spent his life upholding that silence, and when Dana got pregnant, he saw her choice as another necessary sacrifice to keep the family's reputation intact—never questioning whether it was right, only whether it was what the Hargroves had always done.

```cmd
SET cast [{"character": "player", "name": "player", "role": "", "appearance": "", "personality": "", "background": "", "connection": "", "core": "", "wound": "At nineteen, Dana got pregnant, and Walt's silent insistence on protecting the family name in a town that brings its grief to their door forced her into a closed adoption. She gave the baby up without ever feeling like she truly decided it herself, and has spent the ten years since deliberately avoiding any knowledge of the child, blaming Walt for making the choice feel inevitable even though he never said the words out loud."}, {"character": "walt", "name": "Walt Hargrove", "role": "Patriarch of the Hargrove family, owner of the funeral home", "appearance": "", "personality": "", "background": "", "connection": "Father of Dana", "core": "", "wound": "Walt grew up in the shadow of the Millbrook mill's silicosis deaths, watching his father Eldon handle the funerals and the hushed settlements that kept the families from suing."}]
SET fields.character_wounds {"player": "At nineteen, Dana got pregnant, and Walt's silent insistence on protecting the family name in a town that brings its grief to their door forced her into a closed adoption.", "walt": "Walt grew up in the shadow of the Millbrook mill's silicosis deaths, watching his father Eldon handle the funerals and the hushed settlements that kept the families from suing."}
```
NEXT_FOCUS: cast'''


def test_replay_walt_hargrove_drops_the_echoed_player_item_and_resolves_the_proposed_key():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Walt replay"}).json()["key"]
    original, original_provider = interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider
    interview_graph.run_interview_turn = _replay(WALT_HARGROVE_RAW)
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Add Walt."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider = original, original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert len(story["cast"]) == 1  # the echoed "player" item never became a cast member
    cast_key = story["cast"][0]["character"]
    assert cast_key != "player"
    assert story["fields"]["character_wounds"]["player"].startswith("At nineteen, Dana got pregnant")
    assert story["fields"]["character_wounds"][cast_key].startswith("Walt grew up in the shadow")
    assert "walt" not in story["fields"]["character_wounds"]  # the model's short proposed key, resolved away


# Captured live 2026-07-16 (hargrove_sons build, deepseek-chat, focus=cast): four relationships
# naming "player" as source, exactly the identifier the system prompt tells the model to use —
# all four were silently dropped by the old validity check.
RELATIONSHIPS_RAW = '''```cmd
SET relationships [{"source": "player", "target": "walt_hargrove", "relationship": "Strained, ten years of unspoken blame"}, {"source": "player", "target": "emmett_vale", "relationship": "Immediate territorial tension\\u2014he's been the son Walt never had while she was gone"}, {"source": "walt_hargrove", "target": "emmett_vale", "relationship": "Real father-figure bond, Walt's proudest achievement he won't admit to Dana"}, {"source": "player", "target": "greg_tillman", "relationship": "Professional rivalry that's about to get personal"}]
```
NEXT_FOCUS: arcs'''


def test_replay_relationships_naming_the_protagonist_all_persist():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Relationships replay"}).json()["key"]
    original, original_provider = interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        interview_graph.run_interview_turn = _replay(
            '```cmd\nSET cast [{"name": "Walt Hargrove"}, {"name": "Emmett Vale"}, {"name": "Greg Tillman"}]\n```'
        )
        seed = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Add the cast."}],
        })
        assert seed.status_code == 200, seed.text
        by_name = {m["character"]: m["character"] for m in client.get(f"/api/stories/{key}").json()["cast"]}
        assert {"walt_hargrove", "emmett_vale", "greg_tillman"} <= set(by_name)

        interview_graph.run_interview_turn = _replay(RELATIONSHIPS_RAW)
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "cast", "messages": [{"role": "user", "text": "Establish relationships."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider = original, original_provider

    relationships = client.get(f"/api/stories/{key}").json()["relationships"]
    assert len(relationships) == 4
    assert all(r["source"] == "player" for r in relationships[:2] + relationships[3:])
    assert relationships[2]["source"] == "walt_hargrove" and relationships[2]["target"] == "emmett_vale"


# Captured live 2026-07-16 (hargrove_sons build, deepseek-chat, focus=premise): a single
# backtick-wrapped command with no fence at all, alongside a similarly-wrapped NEXT_FOCUS —
# invisible to every parsing strategy until tonight, silently dropped as ordinary prose.
OPEN_QUESTIONS_RAW = ('`SET fields.open_questions ["Does Comfort Cairn know what\'s in the Hargrove ledgers?", '
                       '"Will Dana tell Walt she knows about the adoption agency letter?"]`  \n`NEXT_FOCUS: world`')


def test_replay_single_backtick_open_questions_command_is_no_longer_dropped():
    client = _isolated_client()
    key = client.post("/api/stories/new", json={"name": "Open questions replay"}).json()["key"]
    original, original_provider = interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider
    interview_graph.run_interview_turn = _replay(OPEN_QUESTIONS_RAW)
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (object(), {"available": True})
    try:
        turn = client.post(f"/api/stories/{key}/interview", json={
            "focus": "premise", "messages": [{"role": "user", "text": "Record the open questions."}],
        })
        assert turn.status_code == 200, turn.text
    finally:
        interview_graph.run_interview_turn, ProviderContextMixin.story_agent_provider = original, original_provider

    story = client.get(f"/api/stories/{key}").json()
    assert story["fields"]["open_questions"] == [
        "Does Comfort Cairn know what's in the Hargrove ledgers?",
        "Will Dana tell Walt she knows about the adoption agency letter?",
    ]
    # premise was untouched — the fallback-echo path this used to fall through to never fires
    assert story["premise"] == ""


if __name__ == "__main__":
    test_replay_riya_kavadi_resolves_cores_and_wounds_to_the_same_key()
    test_replay_eunice_marrow_json_fenced_cmd_block_is_no_longer_dropped()
    test_replay_walt_hargrove_drops_the_echoed_player_item_and_resolves_the_proposed_key()
    test_replay_relationships_naming_the_protagonist_all_persist()
    test_replay_single_backtick_open_questions_command_is_no_longer_dropped()
    print("ok — live-captured model responses replay correctly end to end")
