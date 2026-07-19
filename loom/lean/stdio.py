"""Local JSONL bridge for the Story Host.

This is deliberately a process protocol, not another HTTP API.  It lets the
Bun/OhMyPi host reuse the mature Story aggregate and validation code while the
database contract is being ported.  Every non-blank line on stdin produces one
JSON response on stdout; diagnostics stay on stderr.

The bridge intentionally exposes only narrow Story capabilities: author-card
reads, explicitly allowlisted direct prose/cast edits, readiness, the bounded
public Architect lifecycle, and Story-owned image operations. It has no raw
SQL, generic save, provider configuration, workflow, or filesystem operation.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import sys
from typing import Any, Callable

from ..config import load_settings
from ..config.schema import Character, Story, story_reference_errors
from ..server.context_storage import _self_heal_refs
from ..server.services import card_store, story_store
from ..stories.api.library import _play_readiness, _public_architect_model_card
from ..stories.authoring.card_payload import public_story_card
from ..stories.authoring.inline_text import InlineTextEditError, apply_inline_text_edit, parse_inline_text_edit
from ..stories.authoring.starter_set import (
    DevelopError,
    _assert_public_only_proposal,
    apply_develop_proposal,
    build_develop_prompt,
    develop_schema,
    validate_candidate,
)
from ..stories.authoring.story_architect import card_revision
from ..stories.authoring.story_control_graph import (
    build_story_control_graph,
    resolve_architect_work_order,
    work_order_prompt,
)
from ..stories.visibility import preserve_model_hidden, strip_model_hidden


PUBLIC_SCOPES = frozenset({"world", "premise", "cast", "first_day"})
_IMAGE_ROLES = ("scene", "base", "sprite", "chat")
_FORBIDDEN_WORK_ORDER_FIELDS = frozenset({
    "mode", "worker", "specialist", "protected", "public_only", "work_order",
    "model", "use_fallback",
})
_CAST_TEXT_FIELDS = frozenset({"name", "role", "personality", "appearance", "background", "connection"})


class BridgeError(ValueError):
    """A safe, structured failure that can cross the local process boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        revision: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.revision = revision

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.retryable:
            error["retryable"] = True
        if self.revision:
            error["revision"] = self.revision
        return error


def _context(root: Path, settings: Any) -> SimpleNamespace:
    """The small projection contract required by the existing card helpers."""
    return SimpleNamespace(root=root, base_settings=settings)


def _global_characters(root: Path) -> dict[str, Character]:
    """Load only reusable character cards, never another Story's embedded copy.

    ``load_settings`` intentionally merges every Story-local embedded card into
    ``settings.characters``. That is convenient for broad legacy routes, but
    unsafe for a per-Story author projection when two Stories share a stable
    character key. The desktop bridge instead starts from the global card store
    and overlays the aggregate currently being read or committed.
    """
    characters: dict[str, Character] = {}
    for key, document in card_store.load_characters(root).items():
        try:
            characters[key] = Character(**document)
        except Exception:  # noqa: BLE001 - mirrors the resilient settings loader.
            continue
    return characters


def _story_settings(root: Path, settings: Any, embedded: dict[str, Any]) -> Any:
    """Return Settings with this Story's character records taking precedence."""
    characters = _global_characters(root)
    for key, document in embedded.items():
        try:
            characters[str(key)] = Character(**document)
        except Exception:  # noqa: BLE001 - malformed legacy records stay absent from projections.
            continue
    return settings.model_copy(update={"characters": characters})


def _story_cast_keys(raw: dict[str, Any]) -> list[str]:
    """Return this aggregate's explicit cast keys in stable order."""
    keys: list[str] = []
    for member in raw.get("cast") or []:
        key = member if isinstance(member, str) else (member.get("character") if isinstance(member, dict) else "")
        key = str(key or "").strip()
        if key and key not in keys:
            keys.append(key)
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    source = str(fields.get("source_character") or "").strip()
    if source and source not in keys:
        keys.append(source)
    return keys


def _author_revision(raw: dict[str, Any], embedded: dict[str, Any], global_characters: dict[str, Character]) -> str:
    """Fingerprint only the Story-local direct-edit domain.

    It is deliberately distinct from the Architect/model-card revision: it
    includes editable hidden scene evidence, entity scheduling, and local
    character copies.  It deliberately excludes unrelated private/runtime
    documents, so returning this opaque optimistic-concurrency token to the
    renderer does not become a change oracle for fields the author card never
    exposes or edits.
    """
    editable_world = ("genre", "setting", "biome", "culture", "atmosphere", "history", "customs", "technology", "background")
    editable_entity = ("name", "role", "description", "tactic", "limitations", "objective", "knowledge")
    editable_location = ("name", "description", "background_prompt")
    editable_event = ("visible", "hook", "theme", "tone", "trigger", "evidence", "hidden")
    editable_cast = ("role", "personality", "appearance", "background", "connection")
    story: dict[str, Any] = {
        name: deepcopy(raw[name])
        for name in ("name", "premise", "tone", "art_style")
        if isinstance(raw.get(name), str)
    }
    world_raw = raw.get("world") if isinstance(raw.get("world"), dict) else {}
    world = {name: deepcopy(world_raw[name]) for name in editable_world if isinstance(world_raw.get(name), str)}
    entity_raw = world_raw.get("entity") if isinstance(world_raw.get("entity"), dict) else {}
    entity = {name: deepcopy(entity_raw[name]) for name in editable_entity if isinstance(entity_raw.get(name), str)}
    if entity:
        world["entity"] = entity
    if world:
        story["world"] = world

    locations: list[dict[str, str]] = []
    for location_raw in raw.get("locations") or []:
        if not isinstance(location_raw, dict) or not isinstance(location_raw.get("id"), str):
            continue
        location = {"id": location_raw["id"]}
        location.update({name: deepcopy(location_raw[name]) for name in editable_location if isinstance(location_raw.get(name), str)})
        locations.append(location)
    if locations:
        story["locations"] = locations

    fields_raw = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    fields: dict[str, Any] = {}
    cores_raw = fields_raw.get("character_cores") if isinstance(fields_raw.get("character_cores"), dict) else {}
    cores = {str(key): deepcopy(value) for key, value in cores_raw.items() if isinstance(key, str) and isinstance(value, str)}
    if cores:
        fields["character_cores"] = cores
    plan_raw = fields_raw.get("first_day_plan") if isinstance(fields_raw.get("first_day_plan"), dict) else {}
    plan: dict[str, Any] = {}
    if isinstance(plan_raw.get("objective"), str):
        plan["objective"] = deepcopy(plan_raw["objective"])
    events: list[dict[str, str]] = []
    for event_raw in plan_raw.get("events") or []:
        if not isinstance(event_raw, dict) or not isinstance(event_raw.get("id"), str):
            continue
        event = {"id": event_raw["id"]}
        event.update({name: deepcopy(event_raw[name]) for name in editable_event if isinstance(event_raw.get(name), str)})
        events.append(event)
    if events:
        plan["events"] = events
    if plan:
        fields["first_day_plan"] = plan
    if fields:
        story["fields"] = fields

    time_raw = raw.get("time_system") if isinstance(raw.get("time_system"), dict) else {}
    periods: list[dict[str, str]] = []
    for period_raw in time_raw.get("entity_periods") or []:
        if not isinstance(period_raw, dict) or not isinstance(period_raw.get("id"), str):
            continue
        period = {"id": period_raw["id"]}
        period.update({name: deepcopy(period_raw[name]) for name in ("state", "constraint") if isinstance(period_raw.get(name), str)})
        periods.append(period)
    if periods:
        story["time_system"] = {"entity_periods": periods}

    characters: dict[str, Any] = {}
    for key in _story_cast_keys(raw):
        document = embedded.get(key)
        if not isinstance(document, dict) and key in global_characters:
            document = global_characters[key].model_dump()
        if not isinstance(document, dict):
            continue
        character = {"name": deepcopy(document["name"])} if isinstance(document.get("name"), str) else {}
        character_fields = document.get("fields") if isinstance(document.get("fields"), dict) else {}
        fields = {name: deepcopy(character_fields[name]) for name in editable_cast if isinstance(character_fields.get(name), str)}
        if fields:
            character["fields"] = fields
        characters[key] = character
    source = {"story": story, "characters": characters}
    try:
        encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        encoded = repr(source)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _author_card(root: Path, key: str, settings: Any, raw: dict[str, Any], embedded: dict[str, Any]) -> dict[str, Any]:
    """Build the normal author card against only this Story's character view."""
    scoped_settings = _story_settings(root, settings, embedded)
    configured = scoped_settings.stories.get(key)
    if configured is None:
        raise BridgeError("not_found", "no such story")
    # Follow the existing GET route's pydantic-default behavior, but use a
    # Story-local character projection rather than the global merged map.
    return public_story_card(_context(root, scoped_settings), key, configured.model_dump())


def _load_raw_story(root: Path, key: str, settings: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the current aggregate without ever exposing the raw result to Bun."""
    configured = settings.stories.get(key)
    if configured is None:
        # Match every existing Story route: a stale/orphaned SQLite row is not
        # independently addressable once it is no longer part of Settings.
        raise BridgeError("not_found", "no such story")
    loaded = story_store.load_story(root, key)
    if loaded is not None:
        return loaded
    return configured.model_dump(), {}


def _safe_card(root: Path, key: str, raw: dict[str, Any], settings: Any) -> dict[str, Any]:
    # This intentionally reuses the exact narrow projection used by the
    # existing public Architect endpoint.  Porting persistence must not widen
    # the model/browser visibility boundary by accident.
    return _public_architect_model_card(_context(root, settings), key, raw)


def _context_result(root: Path, key: str, settings: Any) -> tuple[dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    raw, embedded = _load_raw_story(root, key, settings)
    scoped_settings = _story_settings(root, settings, embedded)
    card = _safe_card(root, key, raw, scoped_settings)
    return raw, embedded, scoped_settings, {
        "revision": card_revision(card),
        "card": card,
        "model_card": card,
        "control_graph": build_story_control_graph(card),
        "allowed_scopes": sorted(PUBLIC_SCOPES),
    }


def _scope_and_order(payload: dict[str, Any]) -> tuple[set[str], Any]:
    forbidden = sorted(name for name in _FORBIDDEN_WORK_ORDER_FIELDS if name in payload)
    if forbidden:
        raise BridgeError(
            "forbidden_capability",
            "public Architect requests cannot select a worker, visibility mode, or model route",
        )
    scope = payload.get("scope")
    if not isinstance(scope, str) or scope not in PUBLIC_SCOPES:
        raise BridgeError(
            "invalid_scope",
            "public Architect scopes may include only world, premise, cast, and first_day",
        )
    try:
        scopes = {scope}
        return scopes, resolve_architect_work_order(scopes, mode="develop", public_only=True)
    except ValueError as exc:
        raise BridgeError("invalid_scope", str(exc)) from exc


def _require_current_revision(expected: Any, current: str) -> None:
    if not isinstance(expected, str) or not expected:
        raise BridgeError("missing_revision", "a public Architect request requires a revision")
    if expected != current:
        raise BridgeError(
            "stale_revision",
            "the Story card changed; refresh the public Architect context and try again",
            retryable=True,
            revision=current,
        )


def _proposal(payload: dict[str, Any]) -> dict[str, Any]:
    proposal = payload.get("proposal")
    if not isinstance(proposal, dict):
        raise BridgeError("invalid_proposal", "a public Architect request requires a structured proposal")
    # `open_questions` is a global legacy working field and is deliberately
    # excluded from a one-scope browser/agent capability.
    return {name: value for name, value in deepcopy(proposal).items() if name != "open_questions"}


def architect_context(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    key = _key(payload)
    _raw, _characters, _scoped_settings, result = _context_result(root, key, load_settings(root))
    return result


def _story_read_payload(payload: dict[str, Any], operation: str) -> str:
    unknown = sorted(set(payload) - {"key"})
    if unknown:
        raise BridgeError("forbidden_capability", f"{operation} accepts only a Story key")
    return _key(payload)


def _empty_payload(payload: dict[str, Any], operation: str) -> None:
    if payload:
        raise BridgeError("forbidden_capability", f"{operation} does not accept arguments")


def story_list(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the existing lightweight Story library projection over local IPC."""
    _empty_payload(payload, "story.list")
    settings = load_settings(root)
    entries: list[dict[str, Any]] = []
    from ..server.services import story_store as _SS
    updated = _SS.story_updated_map(root)
    for key, story in settings.stories.items():
        mtime = updated.get(key, 0.0)
        entries.append({
            "key": key,
            "name": story.name,
            "premise": story.premise,
            "tone": story.tone,
            "themes": story.themes,
            "locations": len(story.locations),
            "start": story.start,
            "cast": [member.character for member in story.cast],
            "_mtime": mtime,
        })
    entries.sort(key=lambda entry: entry["_mtime"], reverse=True)
    for entry in entries:
        entry.pop("_mtime", None)
    return {"stories": entries}


def _story_create_payload(payload: dict[str, Any]) -> tuple[str, str]:
    unknown = sorted(set(payload) - {"name", "type"})
    if unknown:
        raise BridgeError("forbidden_capability", "story.create accepts only a name and Story type")
    name = payload.get("name", "Untitled story")
    if not isinstance(name, str):
        raise BridgeError("invalid_story", "Story name must be text")
    name = name.strip() or "Untitled story"
    if len(name) > 200:
        raise BridgeError("invalid_story", "Story name is too long")
    type_ = payload.get("type", "novel")
    if not isinstance(type_, str):
        raise BridgeError("invalid_story", "Story type must be text")
    return name, type_ if type_ in {"novel", "vn"} else "novel"


def story_create(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Mint the same empty, interviewing Story card as the existing library route."""
    from ..config.schema import Story

    name, type_ = _story_create_payload(payload)
    settings = load_settings(root)
    existing_names = {story.name for story in settings.stories.values()}
    resolved_name, counter = name, 2
    while resolved_name in existing_names:
        resolved_name, counter = f"{name} ({counter})", counter + 1

    base = re.sub(r"[^\w\-]+", "_", resolved_name.lower()).strip("_") or "story"
    key, counter = base, 2
    while story_store.story_exists(root, key):
        key, counter = f"{base}_{counter}", counter + 1

    # Creation is intentionally not a generic save capability: it starts with
    # the exact minimal, validated aggregate used by POST /api/stories/new.
    story = {"name": resolved_name, "type": type_, "fields": {"status": "interviewing"}}
    Story(**story)
    (root / "configs" / "stories").mkdir(parents=True, exist_ok=True)
    story_store.save_story(root, key, story, {})
    return {"ok": True, "key": key}


def story_read(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the existing author-facing card projection over local IPC.

    This is intentionally broader than the OMP/model card but is still the
    same redacted author-card projection served by the normal Story endpoint.
    The host never forwards this result to a model; Architect preparation uses
    ``architect.context`` and its separate model-safe projection instead.
    """
    key = _story_read_payload(payload, "story.read")
    settings = load_settings(root)
    raw, embedded = _load_raw_story(root, key, settings)
    global_characters = _global_characters(root)
    return {
        "story": _author_card(root, key, settings, raw, embedded),
        "author_revision": _author_revision(raw, embedded, global_characters),
    }


def _expected_author_revision(payload: dict[str, Any], operation: str) -> str:
    revision = payload.get("expected_author_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise BridgeError("missing_revision", f"{operation} requires the current author revision")
    return revision.strip()


def _inline_text_payload(payload: dict[str, Any]) -> tuple[str, list[str], str, str]:
    unknown = sorted(set(payload) - {"key", "path", "value", "expected_author_revision"})
    if unknown:
        raise BridgeError("forbidden_capability", "story.inline_text accepts only a key, path, text, and author revision")
    key = _key(payload)
    try:
        path, value = parse_inline_text_edit(payload.get("path"), payload.get("value"))
    except InlineTextEditError as exc:
        raise BridgeError(exc.code, str(exc)) from exc
    return key, path, value, _expected_author_revision(payload, "story.inline_text")


def _validate_story_edit(story: dict[str, Any], embedded: dict[str, Any], known_characters: set[str]) -> None:
    """Mirror the existing Story field-write validation inside a DB lock."""
    _self_heal_refs(story, known_characters)
    try:
        Story(**story)
    except Exception as exc:  # noqa: BLE001 - pydantic's internals are not a renderer API.
        raise BridgeError("invalid_story", "the edited Story card is not valid") from exc
    errors = story_reference_errors(story, known_characters)
    if errors:
        raise BridgeError("invalid_story", "the edited Story card has an invalid cast reference")


def _demote_active_runtime(story: dict[str, Any]) -> None:
    """Prevent an old compiled play snapshot from surviving an author edit."""
    fields = story.setdefault("fields", {})
    if fields.get("status") == "active":
        fields["status"] = "interviewing"
        fields.pop("runtime_scenario", None)


def _stale_author_revision(revision: str) -> BridgeError:
    return BridgeError(
        "stale_revision",
        "the Story card changed; reload it before saving this edit",
        retryable=True,
        revision=revision,
    )


def _updated_author_result(root: Path, key: str) -> dict[str, Any]:
    settings = load_settings(root)
    raw, embedded = _load_raw_story(root, key, settings)
    global_characters = _global_characters(root)
    return {
        "ok": True,
        "story": _author_card(root, key, settings, raw, embedded),
        "author_revision": _author_revision(raw, embedded, global_characters),
    }


def story_inline_text(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one explicit author prose leaf with optimistic concurrency."""
    key, path, value, expected_revision = _inline_text_payload(payload)
    settings = load_settings(root)
    raw, embedded = _load_raw_story(root, key, settings)
    global_characters = _global_characters(root)
    current_revision = _author_revision(raw, embedded, global_characters)
    if expected_revision != current_revision:
        raise _stale_author_revision(current_revision)

    outcome_state: dict[str, Any] = {}

    def update_latest(live_raw: dict[str, Any], live_embedded: dict[str, Any]):
        live_global_characters = _global_characters(root)
        live_revision = _author_revision(live_raw, live_embedded, live_global_characters)
        if expected_revision != live_revision:
            outcome_state["stale_revision"] = live_revision
            return None
        try:
            apply_inline_text_edit(live_raw, path, value)
            _validate_story_edit(live_raw, live_embedded, set(live_embedded) | set(live_global_characters))
        except InlineTextEditError as exc:
            outcome_state["error"] = BridgeError(exc.code, str(exc))
            return None
        except BridgeError as exc:
            outcome_state["error"] = exc
            return None
        return live_raw, live_embedded, None

    try:
        outcome = story_store.update_story_atomically(root, key, update_latest)
    except Exception as exc:  # noqa: BLE001 - do not surface DB details through local IPC.
        raise BridgeError("storage_error", "could not save the Story text", retryable=True) from exc
    if not outcome.found:
        raise BridgeError("not_found", "no such story")
    if not outcome.committed:
        stale = outcome_state.get("stale_revision")
        if isinstance(stale, str):
            raise _stale_author_revision(stale)
        error = outcome_state.get("error")
        if isinstance(error, BridgeError):
            raise error
        raise BridgeError("invalid_edit", "could not save the Story text")
    return _updated_author_result(root, key)


def _cast_text_payload(payload: dict[str, Any]) -> tuple[str, str, str, str, str]:
    unknown = sorted(set(payload) - {"key", "character", "field", "value", "expected_author_revision"})
    if unknown:
        raise BridgeError("forbidden_capability", "story.cast_text accepts only a Story key, cast field, text, and author revision")
    key = _key(payload)
    character = payload.get("character")
    field = payload.get("field")
    value = payload.get("value")
    if not isinstance(character, str) or not character.strip():
        raise BridgeError("invalid_character", "a Story cast member is required")
    if not isinstance(field, str) or field not in _CAST_TEXT_FIELDS:
        raise BridgeError("forbidden_capability", "that cast field is not directly editable")
    if not isinstance(value, str):
        raise BridgeError("invalid_character", "cast edits must be text")
    return key, character.strip(), field, value, _expected_author_revision(payload, "story.cast_text")


def story_cast_text(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Edit one current Story's cast copy without touching shared source cards."""
    key, character, field, value, expected_revision = _cast_text_payload(payload)
    settings = load_settings(root)
    raw, embedded = _load_raw_story(root, key, settings)
    global_characters = _global_characters(root)
    current_revision = _author_revision(raw, embedded, global_characters)
    if expected_revision != current_revision:
        raise _stale_author_revision(current_revision)

    outcome_state: dict[str, Any] = {}

    def update_latest(live_raw: dict[str, Any], live_embedded: dict[str, Any]):
        live_global_characters = _global_characters(root)
        live_revision = _author_revision(live_raw, live_embedded, live_global_characters)
        if expected_revision != live_revision:
            outcome_state["stale_revision"] = live_revision
            return None
        if character not in _story_cast_keys(live_raw):
            outcome_state["error"] = BridgeError("not_found", "no such story cast member")
            return None
        source = live_embedded.get(character)
        if not isinstance(source, dict):
            global_source = live_global_characters.get(character)
            if global_source is None:
                outcome_state["error"] = BridgeError("not_found", "no such character")
                return None
            source = global_source.model_dump()
        updated_character = deepcopy(source)
        if field == "name":
            updated_character["name"] = value.strip() or updated_character.get("name") or character
        else:
            fields = dict(updated_character.get("fields") or {})
            fields[field] = value
            updated_character["fields"] = fields
        try:
            Character(**updated_character)
            updated_embedded = dict(live_embedded)
            updated_embedded[character] = updated_character
            # A compiled scenario can include character behavior. Unlike the
            # legacy cast endpoint, the local capability fails closed and
            # returns the card to authoring before it can be played again.
            _demote_active_runtime(live_raw)
            _validate_story_edit(live_raw, updated_embedded, set(updated_embedded) | set(live_global_characters))
        except BridgeError as exc:
            outcome_state["error"] = exc
            return None
        except Exception as exc:  # noqa: BLE001 - keep invalid card detail local to the validator.
            outcome_state["error"] = BridgeError("invalid_character", "the edited character card is not valid")
            return None
        return live_raw, updated_embedded, None

    try:
        outcome = story_store.update_story_atomically(root, key, update_latest)
    except Exception as exc:  # noqa: BLE001
        raise BridgeError("storage_error", "could not save the Story character", retryable=True) from exc
    if not outcome.found:
        raise BridgeError("not_found", "no such story")
    if not outcome.committed:
        stale = outcome_state.get("stale_revision")
        if isinstance(stale, str):
            raise _stale_author_revision(stale)
        error = outcome_state.get("error")
        if isinstance(error, BridgeError):
            raise error
        raise BridgeError("invalid_character", "could not save the Story character")
    return _updated_author_result(root, key)


def story_readiness(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Run the existing deterministic readiness compiler without HTTP."""
    key = _story_read_payload(payload, "story.readiness")
    settings = load_settings(root)
    raw, _embedded = _load_raw_story(root, key, settings)
    readiness = _play_readiness(raw)
    readiness.pop("_contract", None)
    return readiness


def story_control_graph(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the safe Story navigation graph without a web request."""
    key = _story_read_payload(payload, "story.control_graph")
    settings = load_settings(root)
    raw, _embedded = _load_raw_story(root, key, settings)
    return {"key": key, "graph": build_story_control_graph(raw)}


def architect_prepare(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    key = _key(payload)
    scopes, work_order = _scope_and_order(payload)
    brief = payload.get("brief", "")
    if not isinstance(brief, str):
        raise BridgeError("invalid_brief", "brief must be text")
    if len(brief) > 8000:
        raise BridgeError("invalid_brief", "brief is too long")
    settings = load_settings(root)
    _raw, _characters, _scoped_settings, context = _context_result(root, key, settings)
    _require_current_revision(payload.get("revision"), context["revision"])
    clean_brief = (strip_model_hidden(brief) or "").strip()
    return {
        "revision": context["revision"],
        "system": work_order_prompt(work_order),
        "prompt": build_develop_prompt(context["card"], brief=clean_brief, scopes=scopes, public_only=True),
        "schema": develop_schema(),
        "work_order": work_order.as_dict(),
    }


def _validate_candidate(
    root: Path,
    key: str,
    raw: dict[str, Any],
    embedded_characters: dict[str, Any],
    settings: Any,
    scopes: set[str],
    proposal: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Apply the existing validator without committing a database mutation."""
    try:
        _assert_public_only_proposal(proposal)
        candidate = apply_develop_proposal(
            raw,
            proposal,
            story_key=key,
            embedded_characters=embedded_characters,
            known_characters=settings.characters,
            scopes=scopes,
            public_only=True,
        )
        candidate.story = preserve_model_hidden(raw, candidate.story)
        return candidate, validate_candidate(candidate, settings.characters)
    except DevelopError as exc:
        raise BridgeError("invalid_proposal", f"invalid public Architect proposal: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - translate storage/model-shape details at the capability boundary.
        raise BridgeError("invalid_proposal", f"could not validate public Architect proposal: {exc}") from exc


def _review_result(candidate: Any, story: dict[str, Any], work_order: Any) -> dict[str, Any]:
    readiness = _play_readiness(story)
    readiness.pop("_contract", None)
    return {
        "message": candidate.message,
        "patch": candidate.patch,
        "created": {"characters": candidate.created},
        "updated_sections": candidate.updated_sections,
        "readiness": readiness,
        "work_order": work_order.as_dict(),
        "review": {
            "agent": work_order.reviewer,
            "status": "approved",
            "gates": list(work_order.reviewer_gates()),
        },
    }


def architect_validate(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    key = _key(payload)
    scopes, work_order = _scope_and_order(payload)
    proposal = _proposal(payload)
    settings = load_settings(root)
    raw, embedded, scoped_settings, context = _context_result(root, key, settings)
    _require_current_revision(payload.get("revision"), context["revision"])
    candidate, validated = _validate_candidate(root, key, raw, embedded, scoped_settings, scopes, proposal)
    result = _review_result(candidate, validated["story"], work_order)
    result.update({"revision": context["revision"], "card": context["card"]})
    return result


def architect_commit(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Commit one validated proposal through the same locked aggregate path as HTTP."""
    key = _key(payload)
    scopes, work_order = _scope_and_order(payload)
    expected_revision = payload.get("revision")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise BridgeError("missing_revision", "a public Architect commit requires a revision")
    proposal = _proposal(payload)
    settings = load_settings(root)
    outcome_state: dict[str, Any] = {}

    def commit_latest(live_raw: dict[str, Any], embedded_characters: dict[str, Any]):
        scoped_settings = _story_settings(root, settings, embedded_characters)
        live_card = _safe_card(root, key, live_raw, scoped_settings)
        live_revision = card_revision(live_card)
        if expected_revision != live_revision:
            outcome_state["stale_revision"] = live_revision
            return None
        try:
            candidate, validated = _validate_candidate(
                root,
                key,
                live_raw,
                embedded_characters,
                scoped_settings,
                scopes,
                proposal,
            )
        except BridgeError as exc:
            outcome_state["error"] = exc
            return None
        return validated["story"], validated["characters"], (candidate, validated["story"], validated["characters"])

    try:
        outcome = story_store.update_story_atomically(root, key, commit_latest)
    except Exception as exc:  # noqa: BLE001 - keep database exceptions out of the renderer.
        raise BridgeError("storage_error", "could not save public Architect proposal") from exc
    if not outcome.found:
        raise BridgeError("not_found", "no such story")
    if not outcome.committed:
        stale = outcome_state.get("stale_revision")
        if isinstance(stale, str):
            raise BridgeError(
                "stale_revision",
                "the Story card changed; refresh the public Architect context and try again",
                retryable=True,
                revision=stale,
            )
        error = outcome_state.get("error")
        if isinstance(error, BridgeError):
            raise error
        raise BridgeError("invalid_proposal", "could not validate public Architect proposal")

    candidate, updated, updated_characters = outcome.result
    # Refresh only after persistence.  This lets newly embedded cast records
    # participate in the safe display projection without holding a DB lock.
    refreshed_settings = load_settings(root)
    updated_card = _safe_card(root, key, updated, _story_settings(root, refreshed_settings, updated_characters))
    result = _review_result(candidate, updated, work_order)
    result.update({
        "revision": card_revision(updated_card),
        "story": updated_card,
        "card": updated_card,
    })
    return result


def _image_enabled() -> bool:
    """Keep Comfy opt-in for the desktop host without starting it at boot."""
    raw = os.environ.get("LOOM_STORY_HOST_COMFY", os.environ.get("LOOM_LEAN_COMFY", "0"))
    return str(raw).strip().lower() not in {"", "0", "false", "no", "off"}


def _image_context(root: Path):
    """Lazily build the lean context only for an image operation.

    Constructing it registers a managed Comfy runner but never starts it. The
    existing renderer calls ``ensure_up`` only after a valid image request.
    """
    from .app import build_lean_context

    return build_lean_context(root, comfy_enabled=_image_enabled())


def _image_status(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if payload:
        raise BridgeError("invalid_image_request", "image.status does not accept a payload")
    from . import images as image_capability

    context = _image_context(root)
    models = image_capability.available_krea_models(context)
    enabled = _image_enabled()
    return {
        "enabled": enabled,
        "configured": bool(models),
        "models": models,
        "roles": list(_IMAGE_ROLES),
        "running": False,
        "message": (
            "Krea2/Comfy rendering is disabled for this Story Host. Set LOOM_STORY_HOST_COMFY=1 to enable it."
            if not enabled else "Krea2/Comfy starts only after a valid image request."
        ),
    }


def _image_key(root: Path, key: str) -> None:
    # Image candidates must belong to a real Story, but a candidate image never
    # changes that Story aggregate or its revision. Do not trust the narrower
    # image-model context for ownership; reload the authoritative Story index.
    _load_raw_story(root, key, load_settings(root))


def _reject_unknown_image_fields(payload: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise BridgeError(
            "forbidden_capability",
            "Story image requests may not choose raw workflows, providers, output paths, or generation flags",
        )


def image_list(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_image_fields(payload, {"key"})
    key = _key(payload)
    from . import images as image_capability

    context = _image_context(root)
    _image_key(root, key)
    images = []
    for item in image_capability.list_story_images(root, key):
        image_id = str(item["id"])
        images.append({
            "id": image_id,
            "role": item.get("role"),
            "asset": {"story_key": key, "image_name": f"{image_id}.png", "media_type": "image/png"},
        })
    return {"images": images}


def image_request(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown_image_fields(payload, {"key", "prompt", "role", "model"})
    if not _image_enabled():
        raise BridgeError("image_disabled", "Krea2/Comfy rendering is disabled for this Story Host")
    key = _key(payload)
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise BridgeError("invalid_image_request", "image prompt is required")
    prompt = (strip_model_hidden(prompt) or "").strip()
    if not prompt:
        raise BridgeError("invalid_image_request", "image prompt is required")
    if len(prompt) > 12_000:
        raise BridgeError("invalid_image_request", "image prompt is too long")
    role = payload.get("role", "scene")
    model = payload.get("model")
    if not isinstance(role, str) or (model is not None and not isinstance(model, str)):
        raise BridgeError("invalid_image_request", "image role and model must be text")

    from . import images as image_capability

    context = _image_context(root)
    _image_key(root, key)
    if not image_capability.available_krea_models(context):
        raise BridgeError("image_unconfigured", "no registered Krea2/Comfy workflow is available")
    try:
        image = asyncio.run(image_capability.render_story_image(
            context,
            story_key=key,
            prompt=prompt,
            role=role,
            model=model,
        ))
    except ValueError as exc:
        raise BridgeError("invalid_image_request", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - hide provider and filesystem details from the renderer.
        raise BridgeError("render_failed", "could not render the Story image", retryable=True) from exc
    image_id = str(image["id"])
    return {
        "image": {
            "id": image_id,
            "role": image.get("role"),
            "model": image.get("model"),
            "asset": {"story_key": key, "image_name": f"{image_id}.png", "media_type": "image/png"},
        },
    }


def _key(payload: dict[str, Any]) -> str:
    key = payload.get("key")
    if not isinstance(key, str) or not key.strip():
        raise BridgeError("invalid_key", "a Story key is required")
    return key.strip()


_HANDLERS: dict[str, Callable[[Path, dict[str, Any]], dict[str, Any]]] = {
    "story.list": story_list,
    "story.create": story_create,
    "story.read": story_read,
    "story.inline_text": story_inline_text,
    "story.cast_text": story_cast_text,
    "story.readiness": story_readiness,
    "story.control_graph": story_control_graph,
    "architect.context": architect_context,
    "architect.prepare": architect_prepare,
    "architect.validate": architect_validate,
    "architect.commit": architect_commit,
    "image.status": _image_status,
    "image.list": image_list,
    "image.request": image_request,
}


def handle_request(root: Path, request: Any) -> dict[str, Any]:
    """Handle one decoded JSONL request. Kept public for process-free tests."""
    if not isinstance(request, dict):
        raise BridgeError("invalid_request", "request must be an object")
    op = request.get("op")
    if not isinstance(op, str) or op not in _HANDLERS:
        raise BridgeError("unknown_operation", "unsupported Story Host operation")
    payload = request.get("payload")
    if payload is None:
        payload = {name: value for name, value in request.items() if name not in {"id", "op"}}
    if not isinstance(payload, dict):
        raise BridgeError("invalid_request", "request payload must be an object")
    return _HANDLERS[op](root, payload)


def _respond(root: Path, raw_line: str) -> dict[str, Any]:
    request_id: Any = None
    try:
        request = json.loads(raw_line)
        if isinstance(request, dict):
            request_id = request.get("id")
        result = handle_request(root, request)
        return {"id": request_id, "ok": True, "result": result}
    except BridgeError as exc:
        return {"id": request_id, "ok": False, "error": exc.as_dict()}
    except json.JSONDecodeError:
        return {
            "id": request_id,
            "ok": False,
            "error": {"code": "invalid_json", "message": "request must be valid JSON"},
        }
    except Exception as exc:  # noqa: BLE001 - no tracebacks or data cross the renderer boundary.
        print(f"story-stdio internal error: {type(exc).__name__}", file=sys.stderr, flush=True)
        return {
            "id": request_id,
            "ok": False,
            "error": {"code": "internal_error", "message": "Story Host operation failed"},
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Story Host JSONL persistence bridge")
    parser.add_argument("--root", type=Path, default=Path("."), help="Project root containing configs/")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    # JSONL is a wire protocol. Windows' inherited console code page is often
    # cp1252, which cannot encode ordinary Story prose (for example an arrow
    # from an existing prompt). Pin the streams to UTF-8 before writing a
    # single response so the Bun sidecar always receives valid JSON bytes.
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:  # pragma: no cover - Python 3.11 supplies reconfigure.
        pass
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        response = _respond(root, raw_line)
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the subprocess test.
    raise SystemExit(main())
