"""Per-sheet Story-card development.

The monolithic ``/card/develop`` pass asks one structured call to invent world,
locations, cast, bonds, and day-one scenes in a single shot.  Every section is
then co-invented in the same context window, so cross-references (a character's
home, a scene's participants, a bond's target) are guesses the model makes
about its own not-yet-committed output.

This module splits that pass into six small sheet calls in dependency order —
world, locations, cast, bonds, arc, day one.  Each sheet is validated, applied,
and saved before the next call runs, and each later prompt carries a compact
fact-sheet digest of what is already committed canon.  Cross-references name
real established ids instead of co-invented ones, and each call has one job
small enough that its structured output cannot truncate a sibling section.

Sheet application reuses the exact ``apply_develop_proposal`` scope gates, so
a sheet physically cannot write outside its own section, and the same
public-only and canon-preservation invariants hold as in the batch pass.
"""
from __future__ import annotations

from typing import Any, Callable

from ...server.services import story_store
from ..visibility import preserve_model_hidden
from . import card_graph
from .starter_set import (
    DevelopError,
    apply_develop_proposal,
    develop_schema,
    validate_candidate,
)

_STRING = {"type": "string"}
_STRINGS = {"type": "array", "items": _STRING}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    """Strict object schema; every key required, empty means "no proposal"."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def _section(name: str) -> dict[str, Any]:
    return develop_schema()["properties"][name]


PROSE_DISCIPLINE = (
    "PROSE DISCIPLINE, for EVERY text field: tight declarative COMPLETE sentences "
    "with terminal punctuation — one idea per field. Budgets: premise/setting/"
    "atmosphere/description/visible/hook ≤ 40 words; persona/connection/dynamic/"
    "note ≤ 25 words. Never a fragment, never a clause that trails off mid-thought. "
    "If a thought runs long, drop the sentence — never cut one short. The narrator "
    "reads these fields verbatim on every turn; every word must be load-bearing."
)

_SYSTEM = f"""You are the story's co-author, writing ONE sheet of a live Story card in a six-sheet development pass.

The established sheets below are canon. Preserve them exactly: never rewrite, rename, or replace an established fact, place, person, bond, or question. Fill only genuinely missing material. A high-level author brief is direction, never permission to silently retcon canon.

{PROSE_DISCIPLINE}

Use short stable lowercase-kebab ids for new locations and events. Refer to locations by id and to people by their exact established name. Empty strings and empty lists mean "no proposal for that field" — return only this sheet's fields."""


class SheetSpec:
    """One sheet call: its apply-scopes, its strict schema, and its task brief."""

    def __init__(self, name: str, title: str, scopes: frozenset[str],
                 schema: dict[str, Any], task: str):
        self.name = name
        self.title = title
        self.scopes = scopes
        self.schema = schema
        self.task = task


def _build_specs() -> tuple[SheetSpec, ...]:
    return (
        SheetSpec(
            "world", "WORLD + PREMISE", frozenset({"world", "premise"}),
            _obj({
                "message": _STRING,
                "world": _section("world"),
                "premise": _STRING,
                "open_questions": _STRINGS,
            }),
            """Write the premise and the world sheet.
- `premise`: ONE sentence (≤ 40 words) naming who the player is and what presses on them.
- World fields by role: `genre`; `setting` = the present-day place; `atmosphere` = its lived texture; `history` = established past; `customs` = recurring ordinary practice; `technology` = the material era; `background` = any remaining current public context. Each ≤ 40 words.
- If this is a loop story, make `loop` executable (trigger, restart, memory rules, what resets). If not, leave loop fields empty. If the story has a hidden entity, describe it in `entity`; otherwise leave entity fields empty.
- Add 2-4 `open_questions` only for genuine unknowns the first arc could answer. Skip generic mystery filler.""",
        ),
        SheetSpec(
            "locations", "LOCATIONS", frozenset({"world"}),
            _obj({
                "message": _STRING,
                "locations": _section("locations"),
                "start": _STRING,
            }),
            """Propose 2-4 named playable sites where scenes can physically happen.
- Each location: kebab `id`, short `name`, and a `description` (≤ 40 words) of observable present-day detail the narrator can stage — sights, sounds, what presses on a visitor. No lore dumps; deep history belongs to the world sheet.
- `start`: the id of the location where play opens. It must be one of the proposed or established ids.""",
        ),
        SheetSpec(
            "cast", "CAST", frozenset({"cast"}),
            _obj({
                "message": _STRING,
                "characters": _section("characters"),
                "character_cores": _section("character_cores"),
            }),
            """Propose at most four genuinely useful people for the established premise and map.
- Each person needs: `name`; `role`; `appearance` (concrete, stageable); `persona` (≤ 25 words — the surface the narrator plays, complete sentence); `personality`; `background`; `connection` (≤ 25 words — their playable tie to the player or premise, complete sentence). `home` must be an established location id or empty. Set `primary` on at most one person — the stable anchor.
- Use a name only for a distinct person; never a placeholder, never a duplicate or re-surnamed version of an established name.
- Give a person a `character_cores` entry only when there is a public, immediately playable tension or driving posture; it is a compact surface, never a hidden wound, secret, diagnosis, or arc outcome. Key it by the person's exact name.
- `want`/`wound`/`lie`/`secret` stay empty unless the author's brief asks for director-only depth.""",
        ),
        SheetSpec(
            "bonds", "RELATIONSHIP WEB", frozenset({"cast"}),
            _obj({
                "message": _STRING,
                "relationships": _section("relationships"),
            }),
            """Weave the bonds between the established cast. Only meaningful pairs; not every pair needs one. `source`/`target` are exact established character names — never `player` (a player tie lives in that character's `connection`).
Field discipline per bond:
- `nature`: a census label (2-5 words, e.g. "childhood friends", "estranged siblings").
- `dynamic`: ONE observable, filmable habit between them — a complete sentence < 25 words. What a bystander could watch them do.
- `stance` / `target_stance`: one word each (warm, wary, indebted, distant...).
- `target_dynamic`: the mirror habit from the other side, same discipline, or empty.
- `note`: one sentence of shared history that explains the habit, or empty.
- `potential`: ONE present-tense hidden fact this bond could cash in later, or empty.
- `trajectory`: from → to in a few words (e.g. "devotion → quiet resentment"), or empty.""",
        ),
        SheetSpec(
            "arc", "DIRECTION (OPEN QUESTIONS)", frozenset({"first_day"}),
            _obj({
                "message": _STRING,
                "open_questions": _STRINGS,
            }),
            """State the story's direction as 3-6 open questions. They are the author's compass, not answered yet.
- Each is ONE interrogative sentence ≤ 25 words, grounded in the established premise, a cast core, or a bond's potential — never generic ("what is the secret of the island?").
- Together they should cover: the central pressure on the player, at least one bond that could turn, and at least one question a Day One scene could begin to probe.
- Do not duplicate a question already on the card; refine past it instead.""",
        ),
        SheetSpec(
            "day_one", "DAY ONE SCENES", frozenset({"first_day", "time_system"}),
            _obj({
                "message": _STRING,
                "time_system": _section("time_system"),
                "first_day_plan": _section("first_day_plan"),
            }),
            """Plan the first day as a handful of possible scene offers, not a scene-by-scene railroad and not a lore list.
- `time_system`: name the day's time slots if the story needs them; entity periods stay empty unless the world sheet established an entity.
- `first_day_plan`: `objective` (one sentence), `opening_time`, `opening_location` (an established location id), `opening_present` (established names, may include `player`).
- 4-6 events. Every NEW event is a concrete encounter the player can enter right now: kebab `id`; `when` (a time slot); `location` (an established id); `participants` (established names, always including `player`); `visible` (≤ 40 words — what is happening and what presses on the player); `hook` (≤ 40 words — the immediate question, choice, or action open to the player); terse public `theme` and `tone`; `roles` (each on-page participant's immediate function, keyed by that participant).
- A history fact by itself is never an event. History enters a scene only embodied as a clue, person, object, rumor, or place the player can inspect, question, follow, or respond to.
- `hidden` is director-only context (a private character fact or secret clue). Set `entity_action` true only when an established entity actively does something in that scene; otherwise false. Set `trigger`/`evidence`/`knowledge` only when the scene genuinely needs them.
- Anchor at least one event to an established open question.""",
        ),
    )


SHEETS: tuple[SheetSpec, ...] = _build_specs()

SHEET_NAMES: tuple[str, ...] = tuple(spec.name for spec in SHEETS)


def _clip(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def sheet_digest(raw: dict[str, Any], embedded_characters: dict[str, Any] | None = None) -> str:
    """Compact fact-sheet digest of committed canon for the next sheet's prompt.

    Plain labeled lines, never JSON: one line per fact, ids and exact names in
    the open so a later sheet can cross-reference them verbatim.
    """
    embedded_characters = embedded_characters or {}
    lines: list[str] = []

    premise = _clip(raw.get("premise"), 300)
    if premise:
        lines.append(f"premise: {premise}")

    world = raw.get("world") or {}
    if isinstance(world, dict):
        for field in ("genre", "setting", "atmosphere", "history", "customs", "technology", "background"):
            value = _clip(world.get(field))
            if value:
                lines.append(f"world.{field}: {value}")

    locations = [loc for loc in (raw.get("locations") or []) if isinstance(loc, dict)]
    if locations:
        lines.append("locations:")
        for loc in locations:
            desc = _clip(loc.get("description"), 160)
            suffix = f" — {desc}" if desc else ""
            lines.append(f"  {loc.get('id')}: {loc.get('name') or loc.get('id')}{suffix}")
    start = str(raw.get("start") or "").strip()
    if start:
        lines.append(f"start: {start}")

    cast = [m for m in (raw.get("cast") or []) if isinstance(m, dict)]
    if cast:
        lines.append("cast:")
        for member in cast:
            key = str(member.get("character") or "")
            record = embedded_characters.get(key) or {}
            name = str(record.get("name") or key)
            fields = record.get("fields") or {}
            role = _clip(fields.get("role"), 60)
            surface = _clip(record.get("system") or fields.get("connection"), 160)
            bits = f" ({role})" if role else ""
            suffix = f" — {surface}" if surface else ""
            lines.append(f"  {name}{bits}{suffix}")

    bonds = [r for r in (raw.get("relationships") or []) if isinstance(r, dict)]
    if bonds:
        lines.append("bonds:")
        # Resolve display names from every embedded record, not just cast rows,
        # so a bond to a non-cast participant still reads as a name.
        names = {
            str(k): str((record or {}).get("name") or k)
            for k, record in embedded_characters.items()
        }
        for rel in bonds:
            source = names.get(str(rel.get("source")), str(rel.get("source")))
            target = names.get(str(rel.get("target")), str(rel.get("target")))
            nature = _clip(rel.get("nature"), 60)
            dynamic = _clip(rel.get("dynamic"), 160)
            suffix = f" — {dynamic}" if dynamic else ""
            lines.append(f"  {source} → {target}: {nature}{suffix}")

    fields = raw.get("fields") or {}
    questions = [str(q).strip() for q in (fields.get("open_questions") or []) if str(q).strip()]
    if questions:
        lines.append("open questions:")
        for question in questions:
            lines.append(f"  - {_clip(question, 160)}")

    plan = fields.get("first_day_plan") or {}
    events = [e for e in (plan.get("events") or []) if isinstance(e, dict)]
    if events:
        lines.append("day one scenes:")
        for event in events:
            lines.append(f"  {event.get('id')} ({event.get('when') or '?'}, {event.get('location') or '?'})")

    return "\n".join(lines)


def _sheet_prompt(spec: SheetSpec, index: int, *, brief: str, digest: str,
                  public_only: bool) -> str:
    public_only_rule = """
PUBLIC-ONLY COMPLETION MODE:
- Do not propose entity or loop rules, time/entity periods, hidden scene material, triggers, evidence, knowledge gates, character wounds/lies/secrets, or relationships.
- A scene proposal may contain only its public id, time, location, participants, visible situation, player-facing hook, compact theme/tone, and participant roles.
- The application will reject private mechanics even if you return them, so stop and leave them for an author question.
""" if public_only else ""
    return f"""SHEET {index + 1}/6 — {spec.title}

AUTHOR'S BRIEF: {brief or '(Use the established sheets; do not invent a new premise.)'}

ESTABLISHED SHEETS (canon — cross-reference these ids and names exactly):
{digest or '(none yet — this is the first sheet)'}

TASK:
{spec.task}
{public_only_rule}"""


async def develop_story_in_sheets(
    ctx: Any,
    key: str,
    *,
    brief: str,
    public_only: bool,
    provider: Any,
    on_event: Callable[[dict], None] | None = None,
) -> dict[str, Any]:
    """Run the six-sheet development pass, saving each sheet before the next.

    Returns ``{"sheets": [...], "updated": <live story dict>}``.  A DevelopError
    aborts the pass naming the sheet that failed; every sheet before it stays
    committed (each was a complete, valid save), so the story is never left
    half-valid — only partially built, which the readiness report shows.
    """
    emit = on_event or (lambda _event: None)
    reports: list[dict[str, Any]] = []
    for index, spec in enumerate(SHEETS):
        emit({"type": "sheet", "sheet": spec.name, "status": "start", "index": index})
        loaded = story_store.load_story(ctx.root, key)
        if loaded is None:
            raise DevelopError("no such story")
        live_raw, embedded_characters = loaded
        prompt = _sheet_prompt(
            spec, index, brief=brief,
            digest=sheet_digest(live_raw, embedded_characters),
            public_only=public_only,
        )
        try:
            proposal = await card_graph.run_card_operation(
                operation=f"develop_sheet_{spec.name}", provider=provider,
                system=_SYSTEM, prompt=prompt, schema=spec.schema,
                on_event=on_event,
            )
            candidate = apply_develop_proposal(
                live_raw, proposal or {}, story_key=key,
                embedded_characters=embedded_characters,
                known_characters=ctx.base_settings.characters,
                scopes=set(spec.scopes), public_only=public_only,
            )
            # The sheet model sees a public projection; adding sections must
            # never erase an author-only wrapper already on the card.
            candidate.story = preserve_model_hidden(live_raw, candidate.story)
            validated = validate_candidate(candidate, ctx.base_settings.characters)
        except DevelopError as exc:
            emit({"type": "sheet", "sheet": spec.name, "status": "error"})
            raise DevelopError(f"{spec.name} sheet: {exc}") from exc
        story_store.save_story(ctx.root, key, validated["story"], validated["characters"])
        ctx.reload_settings()
        reports.append({
            "sheet": spec.name,
            "message": candidate.message,
            "updated_sections": candidate.updated_sections,
            "created": candidate.created,
            "patch": candidate.patch,
        })
        emit({"type": "sheet", "sheet": spec.name, "status": "done",
              "updated_sections": candidate.updated_sections})
    return {"sheets": reports, "updated": ctx._read_story_data(key)}
