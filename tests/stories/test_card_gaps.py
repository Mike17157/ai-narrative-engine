"""Read-only structural action-plan coverage for Story cards."""
from __future__ import annotations

from copy import deepcopy
import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app
from loom.stories.authoring.gap_analysis import analyze_story_card


_PRIVATE_EVENT = "ENTITY SECRET: the drowned copy already knows the player died."
_PRIVATE_ARC = "PRIVATE ARC TRUTH: Shuri has loved the player for years."


def _partial_card() -> dict:
    return {
        "name": "Incomplete ferry",
        "cast": [{"character": "shuri"}, {"character": "mio"}],
        "fields": {
            "first_day_plan": {
                "events": [{
                    "id": "crossing",
                    "visible": "Two people share a quiet ferry ride.",
                    "participants": ["shuri", "mio"],
                    "hidden": _PRIVATE_EVENT,
                }],
            },
            "arc_design": {
                "version": 1,
                "themes": [{"id": "impermanence", "label": "Impermanence"}],
                "arcs": [{
                    "id": "shuri-arc", "theme_id": "impermanence", "owner": "shuri",
                    "truth": _PRIVATE_ARC,
                    "character_threads": [],
                }],
            },
        },
    }


def _connected_card() -> dict:
    return {
        "name": "Connected ferry",
        "start": "ferry",
        "themes": ["Impermanence"],
        "locations": [
            {"id": "ferry", "name": "Electric ferry"},
            {"id": "harbor", "name": "Island harbor"},
        ],
        "cast": [{"character": "player"}, {"character": "shuri"}],
        "relationships": [{
            "id": "player-shuri", "source": "player", "target": "shuri",
            "nature": "childhood friends", "dynamic": "bittersweet closeness",
        }],
        "fields": {
            "player_id": "player",
            "first_day_plan": {
                "objective": "Reach the island before night.",
                "opening_time": "morning",
                "opening_location": "ferry",
                "opening_present": ["player", "shuri"],
                "events": [
                    {
                        "id": "crossing", "when": "morning", "location": "ferry",
                        "participants": ["player", "shuri"],
                        "visible": "The ferry approaches the island.",
                        "hook": "The player can ask Shuri why she came to meet them.",
                    },
                    {
                        "id": "dock-warning", "when": "evening", "location": "harbor",
                        "participants": ["player", "shuri"],
                        "visible": "Shuri hesitates at the dock.",
                        "hook": "The player can ask why Shuri is afraid to leave the dock.",
                        "hidden": _PRIVATE_EVENT,
                        "knowledge": {"entity": "The copy is watching the returner."},
                        "evidence": "Saltwater stains a dry coat.",
                    },
                ],
            },
            "arc_design": {
                "version": 1,
                "themes": [{"id": "impermanence", "label": "Impermanence",
                            "question": "What does delay cost?"}],
                "arcs": [{
                    "id": "shuri-arc", "title": "The missed crossing",
                    "theme_id": "impermanence", "owner": "shuri",
                    "dramatic_question": "Can Shuri risk asking to be chosen?",
                    "starting_belief": "Being useful is safer than asking.",
                    "truth": _PRIVATE_ARC,
                    "turning_points": [{
                        "id": "dock-pressure", "kind": "pressure", "scene_id": "dock-warning",
                        "when": "evening", "public_surface": "The ferry bell interrupts her.",
                    }],
                    "character_threads": [{
                        "id": "shuri-thread", "character": "shuri", "want": "Keep the player safe.",
                        "protective_strategy": "Turns tenderness into practical tasks.",
                        "blind_spot": "She mistakes love for duty.",
                        "unacknowledged_need": "To ask before it is too late.",
                        "limitation": "Leaves before she can ask.",
                        "visible_tell": "Straightens the timetable when cornered.",
                        "pressure_points": [{
                            "scene_id": "dock-warning", "when": "evening",
                            "public_pressure": "The player asks why she missed their meeting.",
                        }],
                        "recognition": "She loves the player.",
                    }],
                }],
            },
        },
    }


def test_gap_analysis_reports_missing_connections_without_mutating_or_leaking_private_text():
    card = _partial_card()
    before = deepcopy(card)
    report = analyze_story_card(card)

    assert card == before
    assert report["read_only"] is True
    ids = {gap["id"] for gap in report["gaps"]}
    assert "relationships-missing" in ids
    assert "knowledge-gate-crossing" in ids
    assert "evidence-link-crossing" in ids
    assert "arc-character-thread-missing" in ids
    assert any(gap["section"] == "first_day" and gap["severity"] in {"high", "medium"}
               for gap in report["gaps"])
    serialized = str(report)
    assert _PRIVATE_EVENT not in serialized
    assert _PRIVATE_ARC not in serialized


def test_gap_analysis_recognizes_connected_scene_bond_knowledge_and_arc_links():
    report = analyze_story_card(_connected_card())
    ids = {gap["id"] for gap in report["gaps"]}

    assert report["readiness"]["ready"] is True
    assert "relationships-missing" not in ids
    assert "scene-relationship-link-missing" not in ids
    assert "knowledge-gate-dock-warning" not in ids
    assert "evidence-link-dock-warning" not in ids
    assert "arc-design-missing" not in ids
    assert not any(gap["id"].startswith("arc-scene-link-") for gap in report["gaps"])
    assert not any(gap["id"].startswith("scene-offer-") for gap in report["gaps"])
    # The diagnostic counts structure, not private content.
    assert report["coverage"]["arcs"] == {"configured": True, "themes": 1, "threads": 1}
    assert _PRIVATE_EVENT not in str(report)
    assert _PRIVATE_ARC not in str(report)


def test_gap_analysis_routes_an_abstract_legacy_scene_back_to_a_playable_offer_without_leaking_private_data():
    private = "PRIVATE: the copy leaves a victim's memories in the tide pools."
    card = {
        "name": "Abstract island scene",
        "locations": [{"id": "village-square", "name": "Village square"}],
        "fields": {"first_day_plan": {"events": [{
            "id": "subtle-hints", "when": "evening", "location": "village-square",
            "participants": ["player"],
            "visible": "Subtle hints of the island's history and recent vanishings surface through conversation or environment.",
            "hidden": private,
        }]}},
    }

    report = analyze_story_card(card)
    gap = next(gap for gap in report["gaps"] if gap["id"] == "scene-offer-subtle-hints")

    assert gap["section"] == "first_day"
    assert gap["related"] == ["subtle-hints"]
    assert gap["paths"] == ["fields.first_day_plan.events[0]"]
    assert private not in str(report)


def test_card_gaps_endpoint_is_read_only_and_has_agent_observation_shape():
    root = Path(tempfile.mkdtemp(prefix="loom-card-gaps-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    client = TestClient(create_app(root))
    try:
        created = client.post("/api/stories/new", json={"name": "Gap endpoint"})
        assert created.status_code == 200, created.text
        key = created.json()["key"]
        before = client.get(f"/api/stories/{key}")
        response = client.get(f"/api/stories/{key}/card/gaps")
        after = client.get(f"/api/stories/{key}")
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["key"] == key
    assert payload["read_only"] is True
    assert set(payload) >= {"summary", "gaps", "readiness", "coverage"}
    assert all(set(gap) >= {"id", "section", "title", "detail", "severity", "suggested_scope"}
               for gap in payload["gaps"])
    assert after.json() == before.json()


if __name__ == "__main__":
    test_gap_analysis_reports_missing_connections_without_mutating_or_leaking_private_text()
    test_gap_analysis_recognizes_connected_scene_bond_knowledge_and_arc_links()
    test_gap_analysis_routes_an_abstract_legacy_scene_back_to_a_playable_offer_without_leaking_private_data()
    test_card_gaps_endpoint_is_read_only_and_has_agent_observation_shape()
    print("ok — deterministic story card gaps")
