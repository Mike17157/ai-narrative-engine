"""Contract checks for the server-owned Story Control Graph."""
import pytest

from loom.stories.authoring.story_control_graph import (
    SPECIALISTS,
    architect_hierarchy,
    build_story_control_graph,
    matches_architect_work_order,
    resolve_architect_work_order,
    specialist_for_section,
    specialist_prompt,
    work_order_prompt,
)


def test_control_graph_exposes_safe_story_nodes_and_optional_director_layer():
    hidden = "PRIVATE_ENTITY_MECHANIC_MUST_NOT_APPEAR"
    card = {
        "world": {"setting": "An island", "entity": {"objective": hidden}},
        "premise": "A returner arrives by ferry.",
        "locations": [{"id": "ferry", "name": "Electric ferry"}],
        "cast": [{"character": "shuri"}],
        "fields": {"first_day_plan": {"opening_time": "morning", "events": [{
            "id": "arrival", "when": "morning", "location": "ferry", "participants": ["player"],
            "visible": "A bell rings beneath the deck.", "hook": "The player can inspect the bell.",
            "hidden": hidden,
        }]}, "arc_outline": {"arcs": [{"id": "shuri-arc"}]}},
    }
    graph = build_story_control_graph(card)
    nodes = {node["id"]: node for node in graph["nodes"]}

    assert {"world", "opening", "cast", "scenes", "arcs", "clock", "director"} <= set(nodes)
    assert nodes["clock"]["infrastructure"] is True
    assert nodes["clock"]["specialist"] is None
    assert nodes["director"]["protected"] is True
    assert hidden not in str(graph)
    assert {edge["kind"] for edge in graph["edges"]} >= {"grounds", "orders", "pressures"}


def test_control_graph_redacts_hidden_opening_text_and_hashes_only_its_safe_projection():
    secret_a = "PRIVATE_OPENING_A"
    secret_b = "PRIVATE_OPENING_B"
    base = {
        "world": {"setting": "A small harbor town."},
        "fields": {"first_day_plan": {"events": [{
            "id": "arrival", "when": "morning", "visible": "A ferry arrives.",
            "hidden": secret_a,
        }]}},
    }
    first = build_story_control_graph({
        **base,
        "premise": f"A ferry approaches. [[hidden]]{secret_a}[[/hidden]]",
    })
    second = build_story_control_graph({
        **base,
        "premise": f"A ferry approaches. [[model-hidden]]{secret_b}[[/model-hidden]]",
        "fields": {"first_day_plan": {"events": [{
            "id": "arrival", "when": "morning", "visible": "A ferry arrives.",
            "hidden": secret_b,
        }]}},
    })
    hidden_only = build_story_control_graph({
        "premise": f"[[hidden]]{secret_a}",
    })

    opening = next(node for node in first["nodes"] if node["id"] == "opening")
    hidden_opening = next(node for node in hidden_only["nodes"] if node["id"] == "opening")
    assert opening["summary"] == "A ferry approaches."
    # The analyzer can still surface the missing opening as an author gap, but
    # hidden-only prose must never make the opening look established/ready.
    assert hidden_opening["status"] in {"empty", "needs_author"}
    assert secret_a not in str(first)
    assert secret_b not in str(second)
    assert secret_a not in str(hidden_only)
    # Different private text has no public graph/cache effect.
    assert first["revision"] == second["revision"]


def test_specialist_registry_is_short_affirmative_and_section_bound():
    assert specialist_for_section("world").id == "world"
    assert specialist_for_section("first_day").id == "scenes"
    assert specialist_for_section("time_system").id == "director"
    for specialist in SPECIALISTS:
        prompt = specialist_prompt(specialist.id)
        assert len(prompt.split()) < 500
        assert "do not" not in prompt.lower()
        assert "don't" not in prompt.lower()
        assert "structured proposal" in prompt


def test_oh_my_pi_style_work_order_is_planner_scout_worker_reviewer_and_scope_bound():
    order = resolve_architect_work_order(["world"], mode="develop")

    assert order.planner == "architect"
    assert order.scout == "explore"
    assert order.worker == "task"
    assert order.specialist == "world"
    assert order.reviewer == "reviewer"
    assert order.scopes == ("world",)
    assert order.maximum_model_calls == 1
    assert "AUTHORIZED STORY WORK ORDER" in work_order_prompt(order)
    assert "scope_boundary" in order.reviewer_gates()

    hierarchy = architect_hierarchy()
    roles = {role["id"]: role for role in hierarchy["roles"]}
    assert hierarchy["root"] == "architect"
    assert roles["architect"]["source_role"] == "plan"
    assert roles["architect"]["spawns"] == ["explore"]
    assert roles["task"]["read_only"] is False
    assert roles["reviewer"]["read_only"] is True


def test_work_order_never_accepts_a_client_selected_or_unknown_worker():
    public_order = resolve_architect_work_order(["world", "cast"], public_only=True)
    assert public_order.worker == "task"
    assert public_order.specialist == "task"
    assert public_order.scopes == ("world", "cast")
    assert "public_visibility_boundary" in public_order.reviewer_gates()

    forged = {**public_order.as_dict(), "worker": "oracle"}
    assert matches_architect_work_order(forged, public_order) is False

    protected = resolve_architect_work_order(
        section="first_day", mode="director_knowledge",
    )
    assert protected.worker == "oracle"
    assert protected.specialist == "director"
    assert protected.protected is True
    assert "protected_authorization_boundary" in protected.reviewer_gates()

    with pytest.raises(ValueError, match="unknown Story Architect scope"):
        resolve_architect_work_order(["private_entity_logic"])
    with pytest.raises(ValueError, match="director knowledge"):
        resolve_architect_work_order(["world"], mode="director_knowledge")
    with pytest.raises(ValueError, match="mechanical work orders"):
        resolve_architect_work_order(["arcs"], mode="mechanical")


if __name__ == "__main__":
    test_control_graph_exposes_safe_story_nodes_and_optional_director_layer()
    test_specialist_registry_is_short_affirmative_and_section_bound()
    print("ok — Story Control Graph")
