"""Regression checks for author-visible text that must never reach a model.

``[[hidden]]...[[/hidden]]`` remains exactly as the author wrote it in storage
and UI payloads, but is stripped at every model-call seam.  This is a real
redaction boundary, not an instruction asking a model to ignore a secret.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loom.config.schema import Story
from loom.config.schema import ModelDef
from loom.providers.registry import build_provider
from loom.stories.authoring.card_payload import model_story_card
from loom.stories.characters.labeled import run_text
from loom.stories.runtime.context import build_turn_context
from loom.stories.runtime.engine import PlayState, step_consequence, step_prose, step_scribe
from loom.stories.visibility import (ModelVisibilityImageProvider, ModelVisibilityProvider, extract_model_hidden, has_model_hidden, model_view,
                                     model_visibility_provider, preserve_model_hidden, strip_model_hidden,
                                     wrap_model_hidden)
from loom.stories.workflows import WorkflowTrace, model_task


_SECRET = "SHURI_IS_THE_ENTITY_AND_MURDERED_THE_FERRYMAN"
_OTHER_SECRET = "THE_RETURNER_WILL_DIE_AT_MIDNIGHT"


class _Context:
    def __init__(self, root: Path):
        self.root = root
        self.base_settings = SimpleNamespace(characters={})

    def portrait_manifest(self, _key: str) -> dict:
        return {}


class _CaptureProvider:
    """Records exactly what the final provider was handed."""
    def __init__(self, *, text: str = "The ferry moves on.", data: dict | None = None):
        self.text = text
        self.data = data
        self.calls: list[dict] = []

    def generate_text(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(text=self.text, data=self.data)


class _CaptureImageProvider:
    def __init__(self):
        self.calls: list[dict] = []

    def generate_image(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(images=[])


def _root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="loom-model-visibility-"))
    shutil.copytree(Path("configs"), root / "configs")
    return root


def _assert_redacted(material: str) -> None:
    low = material.lower()
    assert _SECRET.lower() not in low
    assert _OTHER_SECRET.lower() not in low
    assert "[[hidden" not in low
    assert "[[model-hidden" not in low


def _story() -> Story:
    return Story(
        name="Quiet ferry",
        premise=("A homecoming begins in sunlight. "
                 f"[[hidden]]{_SECRET}[[/hidden]] "
                 "The island waits across the water."),
        tone=f"Tender mystery [[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]",
        start="ferry",
        locations=[{
            "id": "ferry",
            "name": "Electric ferry",
            "description": ("The deck smells of salt. "
                            f"[[hidden]]{_SECRET}[[/hidden]]"),
        }],
    )


def _deps(root: Path, provider: _CaptureProvider):
    return SimpleNamespace(
        ctx=_Context(root),
        st=SimpleNamespace(cast=[]),
        provider=provider,
        consequence_provider=provider,
        scribe_provider=provider,
        fallback=None,
        scenario={},
        on_event=lambda _event: None,
        cancel=lambda: False,
        trace=WorkflowTrace("model-visibility-test"),
    )


def _step_context(state: PlayState, root: Path, provider: _CaptureProvider):
    return SimpleNamespace(state=state, deps=_deps(root, provider))


def test_hidden_wrappers_are_author_visible_but_model_redacted():
    paired = f"The ferry [[hidden]]{_SECRET}[[/hidden]] leaves at dusk."
    explicit = f"The ferry [[model-hidden]]{_OTHER_SECRET}[[/model-hidden]] leaves at dusk."

    assert has_model_hidden(paired)
    assert has_model_hidden(explicit)
    assert strip_model_hidden(paired) == "The ferry  leaves at dusk."
    assert strip_model_hidden(explicit) == "The ferry  leaves at dusk."
    assert extract_model_hidden(paired) == [_SECRET]
    assert wrap_model_hidden(_SECRET) == f"[[hidden]]{_SECRET}[[/hidden]]"
    # The source string is still the author-facing representation; redaction
    # is a projection, never an in-place rewrite.
    assert _SECRET in paired
    assert _OTHER_SECRET in explicit


def test_hidden_parser_handles_nesting_and_fails_closed_for_unterminated_openers():
    nested = (
        "Public start. [[hidden]]outer secret "
        f"[[model-hidden]]{_SECRET}[[/model-hidden]] still secret[[/hidden]] Public end."
    )
    clean = strip_model_hidden(nested) or ""
    assert "Public start." in clean
    assert "Public end." in clean
    assert "outer secret" not in clean
    _assert_redacted(clean)

    unterminated = f"Visible opening. [[hidden]]{_SECRET} and everything after it"
    clean_unterminated = strip_model_hidden(unterminated) or ""
    assert "Visible opening." in clean_unterminated
    _assert_redacted(clean_unterminated)

    # A typoed closer carries no secret by itself; it must not erase author
    # prose before it.  The implementation may remove the harmless closer.
    stray_closer = strip_model_hidden("Visible [[/hidden]] ending.") or ""
    assert "Visible" in stray_closer and "ending" in stray_closer


def test_model_view_recursively_redacts_without_mutating_the_card_data():
    source = {
        "premise": f"Visible [[hidden]]{_SECRET}[[/hidden]] premise",
        "beats": ["Public beat", f"[[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]"],
        "nested": {"note": f"Note [[hidden]]{_SECRET}[[/hidden]]"},
        "count": 3,
    }

    projected = model_view(source)

    assert projected is not source
    assert projected["count"] == 3
    _assert_redacted(repr(projected))
    # UI/storage still owns the original annotated text.
    assert _SECRET in source["premise"]
    assert _OTHER_SECRET in source["beats"][1]


def test_generic_story_agent_projection_omits_director_entity_and_loop_mechanics():
    """A card co-author gets public shape, never the director's answer key."""
    source = {
        "name": "Quiet ferry",
        "world": {
            "genre": "mystery",
            "entity": {
                "description": "A familiar figure appears where it should not.",
                "knowledge": _SECRET,
                "limitations": _OTHER_SECRET,
                "tactic": _SECRET,
                "objective": _OTHER_SECRET,
            },
            "loop": {
                "start": "ferry",
                "reset": "ferry",
                "memory": "only the Returner remembers",
                "returner": "player",
                "end_condition": "survive the night",
                "policy": {"trigger": _SECRET, "preserve": {"runtime": [_OTHER_SECRET]}},
            },
        },
        "fields": {
            "author_notes": [_SECRET],
            "architect_state": {
                "mission": "Draft a quiet ferry mystery.",
                "pending_question": {"id": "arc-design-missing", "text": _OTHER_SECRET},
                "history": [{"role": "architect", "text": _SECRET}],
            },
        },
    }

    projected = model_story_card(_Context(Path(".")), "quiet-ferry", source)

    assert projected["world"]["entity"] == {"description": "A familiar figure appears where it should not."}
    assert projected["world"]["loop"] == {
        "start": "ferry", "reset": "ferry", "memory": "only the Returner remembers",
        "returner": "player", "end_condition": "survive the night",
    }
    assert "author_notes" not in projected["fields"]
    assert "architect_state" not in projected["fields"]
    _assert_redacted(repr(projected))
    assert source["world"]["entity"]["tactic"] == _SECRET
    assert source["world"]["loop"]["policy"]["trigger"] == _SECRET


def test_model_rewrites_keep_source_only_hidden_blocks():
    source = {
        "world": {
            "background": f"Old public wording. [[hidden]]{_SECRET}[[/hidden]]",
            "entity": {"knowledge": f"[[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]"},
        },
    }
    rewrite = {
        "world": {
            "background": "Fresh, concise public wording.",
            # Simulate a whole-object rewrite that omitted a key the model was
            # not entitled to inspect or remove.
            "entity": {},
        },
    }

    preserved = preserve_model_hidden(source, rewrite)

    assert "Fresh, concise public wording." in preserved["world"]["background"]
    assert _SECRET in preserved["world"]["background"]
    assert _OTHER_SECRET in preserved["world"]["entity"]["knowledge"]
    _assert_redacted(repr(model_view(preserved)))
    # Neither source nor model result is mutated by the preservation merge.
    assert _SECRET in source["world"]["background"]
    assert "knowledge" not in rewrite["world"]["entity"]


def test_provider_adapter_redacts_both_prompt_lanes():
    raw = _CaptureProvider()
    provider = model_visibility_provider(raw)

    provider.generate_text(
        system=f"Visible system [[hidden]]{_SECRET}[[/hidden]]",
        prompt=f"Visible prompt [[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]",
        emits=None,
    )

    assert len(raw.calls) == 1
    _assert_redacted(str(raw.calls[0]["system"]))
    _assert_redacted(str(raw.calls[0]["prompt"]))
    assert "Visible system" in raw.calls[0]["system"]
    assert "Visible prompt" in raw.calls[0]["prompt"]


def test_registry_makes_visibility_the_default_for_normal_text_providers():
    provider = build_provider(ModelDef(provider="openai", kind="text", options={}))
    assert isinstance(provider, ModelVisibilityProvider)


def test_image_prompts_use_the_same_visibility_boundary():
    raw = _CaptureImageProvider()
    provider = model_visibility_provider(raw)

    provider.generate_image(
        prompt=f"Foggy harbor [[hidden]]{_SECRET}[[/hidden]]",
        negative_prompt=f"No people [[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]",
        latent=b"not-text",
    )

    assert isinstance(provider, ModelVisibilityImageProvider)
    assert len(raw.calls) == 1
    _assert_redacted(str(raw.calls[0]["prompt"]))
    _assert_redacted(str(raw.calls[0]["negative_prompt"]))
    assert raw.calls[0]["latent"] == b"not-text"


def test_visibility_wrapped_image_provider_can_be_cloned_for_batch_rendering():
    from copy import deepcopy

    raw = _CaptureImageProvider()
    provider = model_visibility_provider(raw)
    clone = deepcopy(provider)

    assert isinstance(clone, ModelVisibilityImageProvider)
    assert clone is not provider
    assert clone._provider is not raw


def test_story_runtime_never_sends_hidden_card_or_player_text_to_any_model_pass():
    root = _root()
    story = _story()
    raw_action = (
        "I wave toward the dock. "
        f"[[hidden]]I know that {_SECRET} and I throw a fireball.[[/hidden]]"
    )
    body = {
        "player": {
            "name": "Rowan",
            "description": f"A returning student [[hidden]]{_OTHER_SECRET}[[/hidden]]",
        },
        "history": [
            {"role": "assistant", "text": f"Earlier [[hidden]]{_SECRET}[[/hidden]]"},
            {"role": "user", "text": raw_action},
        ],
    }
    world = {
        "scene": {"members": []},
        "inventory": [f"brass compass [[hidden]]{_SECRET}[[/hidden]]"],
        "flags": {"author_note": f"[[hidden]]{_OTHER_SECRET}[[/hidden]]"},
        "log": [f"A public arrival. [[hidden]]{_SECRET}[[/hidden]]"],
    }
    tc = build_turn_context(
        _Context(root), story, "visibility-ferry", body, world,
        story_scope="story-visibility-ferry", thread_scope="thread-visibility-ferry",
    )

    # Context stays author-faithful internally.  The final model-call seam,
    # not storage, is where the confidentiality boundary is enforced.
    assert _SECRET in tc["system"] or _SECRET in tc["prompt"]
    assert _SECRET in story.premise
    assert _SECRET in body["history"][1]["text"]

    director = _CaptureProvider(text="1. Rowan waves at the dock.")
    state = PlayState(body=body, tc=tc)
    asyncio.run(step_consequence(_step_context(state, root, director)))
    assert len(director.calls) == 1
    _assert_redacted(str(director.calls[0]["system"]))
    _assert_redacted(str(director.calls[0]["prompt"]))
    assert "I wave toward the dock" in director.calls[0]["prompt"]

    writer = _CaptureProvider(text="The ferry hums toward the island.")
    state = PlayState(body=body, tc=tc)
    asyncio.run(step_prose(_step_context(state, root, writer)))
    assert len(writer.calls) == 1
    _assert_redacted(str(writer.calls[0]["system"]))
    _assert_redacted(str(writer.calls[0]["prompt"]))

    scribe = _CaptureProvider(data={"state_deltas": []})
    state = PlayState(body=body, narration="The ferry hums toward the island.", tc=tc)
    asyncio.run(step_scribe(_step_context(state, root, scribe)))
    assert len(scribe.calls) == 1
    _assert_redacted(str(scribe.calls[0]["system"]))
    _assert_redacted(str(scribe.calls[0]["prompt"]))


def test_authoring_workflow_model_task_has_the_same_redaction_boundary():
    root = _root()
    provider = _CaptureProvider()
    trace = WorkflowTrace("visibility-workflow")

    asyncio.run(model_task(
        trace, "interviewer", provider,
        system=f"Editor brief [[hidden]]{_SECRET}[[/hidden]]",
        prompt=f"User says hello [[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]",
    ))

    assert len(provider.calls) == 1
    _assert_redacted(str(provider.calls[0]["system"]))
    _assert_redacted(str(provider.calls[0]["prompt"]))


def test_character_labeled_text_generation_cannot_bypass_the_visibility_boundary():
    provider = _CaptureProvider(text="WOUND: left behind")

    result = run_text(
        provider,
        f"Character brief [[hidden]]{_SECRET}[[/hidden]]",
        f"Persona note [[model-hidden]]{_OTHER_SECRET}[[/model-hidden]]",
    )

    assert result == "WOUND: left behind"
    assert len(provider.calls) == 1
    _assert_redacted(str(provider.calls[0]["system"]))
    _assert_redacted(str(provider.calls[0]["prompt"]))


if __name__ == "__main__":
    test_hidden_wrappers_are_author_visible_but_model_redacted()
    test_hidden_parser_handles_nesting_and_fails_closed_for_unterminated_openers()
    test_model_view_recursively_redacts_without_mutating_the_card_data()
    test_model_rewrites_keep_source_only_hidden_blocks()
    test_provider_adapter_redacts_both_prompt_lanes()
    test_registry_makes_visibility_the_default_for_normal_text_providers()
    test_image_prompts_use_the_same_visibility_boundary()
    test_story_runtime_never_sends_hidden_card_or_player_text_to_any_model_pass()
    test_authoring_workflow_model_task_has_the_same_redaction_boundary()
    test_character_labeled_text_generation_cannot_bypass_the_visibility_boundary()
    print("ok — model visibility boundary")
