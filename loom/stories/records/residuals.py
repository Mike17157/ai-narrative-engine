"""Evidence-preserving residual consolidation for roleplay turns.

Raw turns are immutable source records. A residual is a small, revisable claim
over that evidence, never a replacement summary of it. The model reads a
plain-text dossier and may make only narrow tool calls; code validates every
source turn before changing the graph overlay.
"""

from __future__ import annotations

from typing import Any


RESIDUAL_TOOLS = [
    {"name": "assert_residual", "description": "Assert one durable story claim supported by source turns.",
     "parameters": {"type": "object", "additionalProperties": False,
        "required": ["subject", "predicate", "object", "evidence", "status", "requires_source"],
        "properties": {"subject": {"type": "string", "description": "exact character key, player, or world"},
                       "predicate": {"type": "string", "description": "short present-tense relation, e.g. carries, avoids, suspects"},
                       "object": {"type": "string", "description": "the thing/state claimed"},
                       "evidence": {"type": "array", "items": {"type": "integer"}},
                       "status": {"type": "string", "enum": ["observed", "inferred", "contested"]},
                       "requires_source": {"type": "boolean", "description": "true when nuance in the raw turns is needed before relying on it"}}}},
    {"name": "retire_residual", "description": "Retire a residual contradicted or resolved by source turns.",
     "parameters": {"type": "object", "additionalProperties": False,
        "required": ["id", "evidence", "reason"],
        "properties": {"id": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "integer"}},
                       "reason": {"type": "string"}}}},
]

SYSTEM = """You consolidate an interactive story's evidence graph. Read the RAW TURNS as prose;
the ledger is context, not prose to rewrite. Create a residual only when a fact or pattern will
matter in a later scene. Every assertion must cite turn numbers that support it. Use observed only
for explicit facts; use inferred for a defensible pattern; use contested for unresolved conflict.
Do not update character identity cards. Do not invent events. It is correct to make no tool calls."""


def _one_line(value: Any, limit: int = 420) -> str:
    return " ".join(str(value or "").split())[:limit]


def render_dossier(story: Any, world: dict, *, characters: dict[str, Any] | None = None,
                   after_step: int = -1, max_chars: int = 48000) -> tuple[str, list[dict]]:
    """Render the unified story card + character subcards + verbatim source turns.

    Arc and location are sections of the Story card, not detached context lanes.
    Character identity remains on its portable card, which is embedded here by
    reference only for this model read.
    """
    data = story.model_dump() if hasattr(story, "model_dump") else dict(story or {})
    turns = [t for t in (world.get("turns") or []) if isinstance(t, dict) and int(t.get("step", -1)) > after_step]
    # New records are authoritative; legacy sessions can still be consolidated once.
    if not turns:
        turns = [{"step": i, "present": (world.get("scene_cast") or [[]])[i] if i < len(world.get("scene_cast") or []) else [],
                  "location": "", "input": "", "text": text}
                 for i, text in enumerate(world.get("transcript") or []) if i > after_step and str(text).strip()]
    characters = characters or {}
    cast, cast_keys = [], []
    for member in data.get("cast") or []:
        key = member.get("character") or ""
        if key:
            cast_keys.append(key)
            char = characters.get(key)
            name = getattr(char, "name", "") or key
            foundation = _one_line(getattr(char, "system", ""), 1100)
            overlay = _one_line((world.get("cards") or {}).get(name) or (world.get("cards") or {}).get(key), 600)
            cast.append(f"- {name} [{key}]" + (" (primary)" if member.get("primary") else "")
                        + (f"\n  CARD: {foundation}" if foundation else "")
                        + (f"\n  PLAY OVERLAY: {overlay}" if overlay else ""))
    locations = [f"- {loc.get('name') or loc.get('id')} [{loc.get('id')}]"
                 + (f": {_one_line(loc.get('description'), 280)}" if loc.get('description') else "")
                 for loc in data.get("locations") or [] if loc.get("id")]
    arcs = [f"- {arc.get('name') or arc.get('id')} [{arc.get('id')}]"
            + (f": {_one_line(arc.get('premise') or arc.get('mini_ending'), 360)}" if (arc.get('premise') or arc.get('mini_ending')) else "")
            + (f"; cast: {', '.join(arc.get('cast') or [])}" if arc.get('cast') else "")
            for arc in data.get("arcs") or [] if arc.get("id")]
    active_arc = world.get("arc") if isinstance(world.get("arc"), dict) else {}
    residuals = [r for r in (world.get("residuals") or []) if r.get("active", True)]
    lines = ["STORY CARD", f"Premise: {_one_line(data.get('premise'), 900)}",
             f"Tone: {_one_line(data.get('tone'), 240)}", f"World pressure: {_one_line((data.get('world') or {}).get('pressure'), 500)}",
             f"Current arc: {_one_line(active_arc.get('name'), 180)}" + (f" — stage {active_arc.get('stage')}" if active_arc else ""),
             "", "LOCATIONS (part of the story card)", "\n".join(locations) or "(none)",
             "", "ARCS (part of the story card)", "\n".join(arcs) or "(none)",
             "", "CHARACTER CARDS (referenced by the story cast)", "\n".join(cast) or "(none)",
             "", "ACTIVE RESIDUALS"]
    lines.extend(f"- {r.get('id')}: {r.get('subject')} {r.get('predicate')} {r.get('object')} "
                 f"[{r.get('status')}; evidence: {', '.join(map(str, r.get('evidence') or []))}]"
                 for r in residuals[-30:])
    lines.append("(none)" if not residuals else "")
    lines += ["", "RAW TURNS — verbatim evidence"]
    used: list[dict] = []
    size = len("\n".join(lines))
    for turn in turns:
        block = (f"\n[TURN {turn.get('step')}] location: {turn.get('location') or 'unknown'}; "
                 f"present: {', '.join(turn.get('present') or []) or 'unknown'}\n"
                 + (f"PLAYER: {turn.get('input')}\n" if turn.get('input') else "")
                 + f"STORY: {turn.get('text') or ''}\n")
        if used and size + len(block) > max_chars:
            break
        lines.append(block.rstrip())
        used.append(turn)
        size += len(block)
    return "\n".join(line for line in lines if line is not None), used


def apply_residual_calls(world: dict, calls: list[dict], *, allowed_steps: set[int]) -> dict:
    """Validate and apply model tool calls. Invalid/evidence-free changes are rejected."""
    residuals = world.setdefault("residuals", [])
    applied, rejected = [], []
    for call in calls or []:
        name, p = call.get("fn"), call.get("params") or {}
        evidence = {int(x) for x in (p.get("evidence") or []) if isinstance(x, int) or str(x).isdigit()}
        if not evidence or not evidence <= allowed_steps:
            rejected.append({"fn": name, "reason": "evidence must cite supplied raw turns"})
            continue
        if name == "assert_residual":
            subject, predicate, obj = (str(p.get(k) or "").strip() for k in ("subject", "predicate", "object"))
            if not all((subject, predicate, obj)):
                rejected.append({"fn": name, "reason": "subject, predicate, and object are required"}); continue
            status = p.get("status") if p.get("status") in {"observed", "inferred", "contested"} else "inferred"
            existing = next((r for r in residuals if r.get("active", True) and (r.get("subject"), r.get("predicate"), r.get("object")) == (subject, predicate, obj)), None)
            if existing is None:
                existing = {"id": f"res-{len(residuals) + 1}", "subject": subject, "predicate": predicate,
                            "object": obj, "evidence": [], "status": status, "requires_source": bool(p.get("requires_source")), "active": True}
                residuals.append(existing)
            existing["evidence"] = sorted(set(existing.get("evidence") or []) | evidence)
            existing["status"], existing["requires_source"] = status, bool(p.get("requires_source"))
            applied.append({"fn": name, "id": existing["id"]})
        elif name == "retire_residual":
            target = next((r for r in residuals if r.get("id") == p.get("id") and r.get("active", True)), None)
            if target is None:
                rejected.append({"fn": name, "reason": "active residual not found"}); continue
            target.update({"active": False, "retired_by": sorted(evidence), "retire_reason": str(p.get("reason") or "")})
            applied.append({"fn": name, "id": target["id"]})
    return {"applied": applied, "rejected": rejected}


def consolidate(provider, story: Any, world: dict, *, characters: dict[str, Any] | None = None) -> dict:
    """Run one M3-compatible tool-call consolidation over new raw evidence."""
    after = int(world.get("residual_consolidated_through") or -1)
    dossier, turns = render_dossier(story, world, characters=characters, after_step=after)
    if not turns:
        return {"applied": [], "rejected": [], "turns": []}
    result = provider.generate_text(system=SYSTEM, prompt=dossier + "\n\nUse tools only when a durable residual is warranted.", tools=RESIDUAL_TOOLS)
    outcome = apply_residual_calls(world, result.tool_calls, allowed_steps={int(t["step"]) for t in turns})
    world["residual_consolidated_through"] = max(int(t["step"]) for t in turns)
    return {**outcome, "turns": [int(t["step"]) for t in turns]}
