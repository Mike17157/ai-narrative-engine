"""Process-level coverage for the local Story Host persistence bridge."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import yaml

from loom.config import load_settings
from loom.server.services import card_store, story_store
from loom.stories.authoring.card_payload import public_story_card


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "story-stdio"
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    for card in (root / "configs" / "characters").glob("*.yaml"):
        card.unlink()
    story_store.save_story(root, "bridge", {
        "name": "Bridge",
        "type": "novel",
        "premise": "A ferry approaches a storm-bound island.",
        "world": {"setting": "A wet island crossing."},
        "locations": [{"id": "ferry", "name": "Electric ferry", "description": "A quiet deck."}],
        "start": "ferry",
        "fields": {"status": "interviewing"},
    })
    return root


def _proposal() -> dict:
    return {
        "message": "I added a tactile crossing atmosphere.",
        "world": {
            "genre": "", "setting": "", "atmosphere": "Salt mist turns the ferry windows pearly.",
            "history": "", "customs": "", "technology": "", "background": "",
            "loop": {"start": "", "reset": "", "memory": "", "returner": "", "end_condition": "", "policy": {
                "trigger": "", "restart": "", "preserve": {"runtime": [], "memories": [], "clear_memories_for_others": False},
                "victims_return_after_end": False,
            }},
            "entity": {"description": "", "knowledge": "", "limitations": "", "tactic": "", "objective": ""},
        },
        "premise": "", "locations": [], "start": "", "time_system": {"slots": [], "entity_periods": []},
        "first_day_plan": {"objective": "", "opening_time": "", "opening_location": "", "opening_present": [], "events": []},
        "characters": [], "character_cores": [], "relationships": [], "open_questions": [],
    }


def _run(root: Path, requests: list[dict]) -> list[dict]:
    payload = "".join(json.dumps(request) + "\n" for request in requests)
    process = subprocess.run(
        [sys.executable, "-m", "loom.lean.stdio", "--root", str(root)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    lines = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    assert len(lines) == len(requests), process.stdout
    return lines


def _author_read(root: Path, key: str) -> dict:
    response = _run(root, [{"id": "read", "op": "story.read", "payload": {"key": key}}])[0]
    assert response["ok"] is True
    return response["result"]


def test_jsonl_bridge_keeps_context_model_safe_and_commits_through_the_validator(tmp_path: Path):
    root = _root(tmp_path)
    first = _run(root, [{"id": "context", "op": "architect.context", "key": "bridge"}])[0]

    assert first["ok"] is True
    context = first["result"]
    assert context["allowed_scopes"] == ["cast", "first_day", "premise", "world"]
    assert "lorebook" not in context["card"]

    prepared = _run(root, [{
        "id": "prepare", "op": "architect.prepare", "key": "bridge", "scope": "world",
        "brief": "Make the crossing tactile.", "revision": context["revision"],
    }])[0]
    assert prepared["ok"] is True
    assert "AUTHORIZED STORY WORK ORDER" in prepared["result"]["system"]
    assert prepared["result"]["work_order"]["worker"] == "task"

    proposal = _proposal()
    validated, committed = _run(root, [
        {
            "id": "validate", "op": "architect.validate", "key": "bridge", "scope": "world",
            "revision": context["revision"], "proposal": proposal,
        },
        {
            "id": "commit", "op": "architect.commit", "key": "bridge", "scope": "world",
            "revision": context["revision"], "proposal": proposal,
        },
    ])

    assert validated["ok"] is True
    assert validated["result"]["review"]["status"] == "approved"
    assert committed["ok"] is True
    assert committed["result"]["story"]["world"]["atmosphere"] == proposal["world"]["atmosphere"]
    assert committed["result"]["revision"] != context["revision"]


def test_jsonl_bridge_rejects_private_scope_and_stale_revisions(tmp_path: Path):
    root = _root(tmp_path)
    context = _run(root, [{"id": "context", "op": "architect.context", "key": "bridge"}])[0]["result"]
    responses = _run(root, [
        {
            "id": "private", "op": "architect.prepare", "key": "bridge", "scope": "time_system",
            "brief": "do it", "revision": context["revision"],
        },
        {
            "id": "stale", "op": "architect.prepare", "key": "bridge", "scope": "world",
            "brief": "do it", "revision": "stale",
        },
    ])

    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "invalid_scope"
    assert responses[1]["ok"] is False
    assert responses[1]["error"]["code"] == "stale_revision"
    assert responses[1]["error"]["retryable"] is True


def test_jsonl_bridge_reads_the_author_card_and_readiness_without_http(tmp_path: Path):
    root = _root(tmp_path)
    listed, read, readiness, graph, forbidden_list, forbidden = _run(root, [
        {"id": "list", "op": "story.list", "payload": {}},
        {"id": "read", "op": "story.read", "payload": {"key": "bridge"}},
        {"id": "readiness", "op": "story.readiness", "payload": {"key": "bridge"}},
        {"id": "graph", "op": "story.control_graph", "payload": {"key": "bridge"}},
        {"id": "forbidden-list", "op": "story.list", "payload": {"path": "raw"}},
        {"id": "forbidden", "op": "story.read", "payload": {"key": "bridge", "path": "raw"}},
    ])

    assert listed["ok"] is True
    assert listed["result"]["stories"] == [{
        "key": "bridge", "name": "Bridge", "premise": "A ferry approaches a storm-bound island.",
        "tone": "", "themes": [], "locations": 1, "start": "ferry", "cast": [],
    }]

    assert read["ok"] is True
    story = read["result"]["story"]
    settings = load_settings(root)
    expected = public_story_card(
        SimpleNamespace(root=root, base_settings=settings),
        "bridge",
        settings.stories["bridge"].model_dump(),
    )
    assert story == expected
    assert story["key"] == "bridge"
    assert story["world"]["setting"] == "A wet island crossing."
    assert "runtime_scenario" not in story.get("fields", {})

    assert readiness["ok"] is True
    assert readiness["result"]["status"] == "interviewing"
    assert "_contract" not in readiness["result"]

    assert graph["ok"] is True
    assert graph["result"]["key"] == "bridge"
    assert graph["result"]["graph"]["version"] == 1

    assert forbidden_list["ok"] is False
    assert forbidden_list["error"]["code"] == "forbidden_capability"
    assert forbidden["ok"] is False
    assert forbidden["error"]["code"] == "forbidden_capability"


def test_jsonl_bridge_mints_only_the_minimal_valid_story_card(tmp_path: Path):
    root = _root(tmp_path)
    created, duplicate, forbidden = _run(root, [
        {"id": "create", "op": "story.create", "payload": {"name": "New Harbour", "type": "vn"}},
        {"id": "duplicate", "op": "story.create", "payload": {"name": "New Harbour", "type": "novel"}},
        {"id": "forbidden", "op": "story.create", "payload": {"name": "Nope", "fields": {"world": "raw"}}},
    ])

    assert created["ok"] is True
    assert created["result"] == {"ok": True, "key": "new_harbour"}
    assert duplicate["ok"] is True
    assert duplicate["result"] == {"ok": True, "key": "new_harbour_2"}
    assert forbidden["ok"] is False
    assert forbidden["error"]["code"] == "forbidden_capability"

    listed, read = _run(root, [
        {"id": "list", "op": "story.list", "payload": {}},
        {"id": "read", "op": "story.read", "payload": {"key": "new_harbour"}},
    ])
    assert listed["ok"] is True
    # newest-first by relational `updated` (matches the app library); the fixture save is oldest
    assert [item["key"] for item in listed["result"]["stories"]] == ["new_harbour_2", "new_harbour", "bridge"]
    assert read["ok"] is True
    assert read["result"]["story"]["name"] == "New Harbour"
    assert read["result"]["story"]["type"] == "vn"
    assert read["result"]["story"]["fields"]["status"] == "interviewing"


def test_jsonl_bridge_inline_text_is_locked_whitelisted_and_invalidates_active_runtime(tmp_path: Path):
    root = _root(tmp_path)
    raw, characters = story_store.load_story(root, "bridge")
    raw["world"] = {"setting": "The old crossing.", "entity": {"knowledge": "Old private knowledge."}}
    raw["time_system"] = {"entity_periods": [{"id": "night", "state": "watching", "constraint": "Only after dark."}]}
    raw["fields"] = {
        "status": "active",
        "runtime_scenario": {"ready": True},
        "character_cores": {"player": "A reluctant passenger."},
        "first_day_plan": {
            "objective": "Reach shore.",
            "events": [{"id": "arrival", "visible": "Rain hits the deck.", "evidence": "A wet ticket."}],
        },
    }
    story_store.save_story(root, "bridge", raw, characters)

    before = _author_read(root, "bridge")
    model_before = _run(root, [{"id": "context-before", "op": "architect.context", "payload": {"key": "bridge"}}])[0]
    assert model_before["ok"] is True

    updated = _run(root, [{
        "id": "edit-evidence",
        "op": "story.inline_text",
        "payload": {
            "key": "bridge",
            "path": ["fields", "first_day_plan", "events", "arrival", "evidence"],
            "value": "  A ticket stamped before the storm.  ",
            "expected_author_revision": before["author_revision"],
        },
    }])[0]
    assert updated["ok"] is True
    assert updated["result"]["story"]["fields"]["first_day_plan"]["events"][0]["evidence"] == "A ticket stamped before the storm."
    assert updated["result"]["author_revision"] != before["author_revision"]

    persisted, _characters = story_store.load_story(root, "bridge")
    assert persisted["fields"]["status"] == "interviewing"
    assert "runtime_scenario" not in persisted["fields"]

    model_after = _run(root, [{"id": "context-after", "op": "architect.context", "payload": {"key": "bridge"}}])[0]
    assert model_after["ok"] is True
    # Evidence is editable author material but deliberately absent from the
    # bounded model card, so only the author revision may authorize its save.
    assert model_after["result"]["revision"] == model_before["result"]["revision"]

    stale, forbidden = _run(root, [
        {
            "id": "stale",
            "op": "story.inline_text",
            "payload": {
                "key": "bridge", "path": ["world", "setting"], "value": "A changed shore.",
                "expected_author_revision": before["author_revision"],
            },
        },
        {
            "id": "forbidden",
            "op": "story.inline_text",
            "payload": {
                "key": "bridge", "path": ["fields", "runtime_scenario"], "value": "nope",
                "expected_author_revision": updated["result"]["author_revision"],
            },
        },
    ])
    assert stale["ok"] is False
    assert stale["error"]["code"] == "stale_revision"
    assert forbidden["ok"] is False
    assert forbidden["error"]["code"] == "invalid_edit"


def test_jsonl_bridge_scopes_cast_text_to_one_story_and_clones_global_source(tmp_path: Path):
    root = _root(tmp_path)
    first_story = {
        "name": "First", "type": "novel", "cast": [{"character": "shared"}],
        "fields": {"status": "interviewing"},
    }
    second_story = {
        "name": "Second", "type": "novel", "cast": [{"character": "shared"}],
        "fields": {"status": "interviewing"},
    }
    story_store.save_story(root, "first", first_story, {
        "shared": {"name": "First local", "system": "", "fields": {"role": "First role"}},
    })
    story_store.save_story(root, "second", second_story, {
        "shared": {"name": "Second local", "system": "", "fields": {"role": "Second role"}},
    })
    (root / "configs" / "characters" / "shared.yaml").write_text(
        yaml.safe_dump({"name": "Library shared", "system": "", "fields": {"role": "Library role"}}),
        encoding="utf-8",
    )
    story_store.save_story(root, "global_source", {
        "name": "Global Source", "type": "novel", "cast": [{"character": "shared"}],
        "fields": {"status": "interviewing"},
    }, {})

    first = _author_read(root, "first")
    second = _author_read(root, "second")
    global_read = _author_read(root, "global_source")
    assert first["story"]["cast_details"][0]["name"] == "First local"
    assert second["story"]["cast_details"][0]["name"] == "Second local"
    assert global_read["story"]["cast_details"][0]["name"] == "Library shared"
    first_context, second_context = _run(root, [
        {"id": "first-context", "op": "architect.context", "payload": {"key": "first"}},
        {"id": "second-context", "op": "architect.context", "payload": {"key": "second"}},
    ])
    assert first_context["result"]["card"]["cast_details"][0]["name"] == "First local"
    assert second_context["result"]["card"]["cast_details"][0]["name"] == "Second local"

    changed = _run(root, [{
        "id": "second-role",
        "op": "story.cast_text",
        "payload": {
            "key": "second", "character": "shared", "field": "role", "value": "Changed locally",
            "expected_author_revision": second["author_revision"],
        },
    }])[0]
    assert changed["ok"] is True
    assert changed["result"]["story"]["cast_details"][0]["role"] == "Changed locally"
    first_after_second_edit = _author_read(root, "first")
    assert first_after_second_edit["story"]["cast_details"][0]["role"] == "First role"
    assert first_after_second_edit["author_revision"] == first["author_revision"]
    first_context_after_second = _run(root, [{
        "id": "first-context-after-second", "op": "architect.context", "payload": {"key": "first"},
    }])[0]
    assert first_context_after_second["result"]["revision"] == first_context["result"]["revision"]

    # An initially global source is copied into this Story on its first local
    # author edit; neither the global source card nor another Story is rewritten.
    cloned = _run(root, [{
        "id": "clone-global",
        "op": "story.cast_text",
        "payload": {
            "key": "global_source", "character": "shared", "field": "appearance", "value": "Raincoat",
            "expected_author_revision": global_read["author_revision"],
        },
    }])[0]
    assert cloned["ok"] is True
    stored_global, embedded_global = story_store.load_story(root, "global_source")
    assert stored_global["name"] == "Global Source"
    assert embedded_global["shared"]["fields"]["appearance"] == "Raincoat"
    source = card_store.load_characters(root)["shared"]   # the global card (was shared.yaml)
    assert source["fields"] == {"role": "Library role"}

    rejected = _run(root, [{
        "id": "rejected-field",
        "op": "story.cast_text",
        "payload": {
            "key": "global_source", "character": "shared", "field": "system", "value": "not allowed",
            "expected_author_revision": cloned["result"]["author_revision"],
        },
    }])[0]
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "forbidden_capability"
