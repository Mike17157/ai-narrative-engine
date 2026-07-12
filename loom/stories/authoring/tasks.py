"""Explicit task dispatch for story-structure work.

Each task is a DIRECT call — the caller names the task ("set_spine", "add_beat",
"rebuild_storyboard") and the system runs exactly that, grounded in canon, in one model call.
No keyword inference, no agent-switching, no graph loop for one-call work.

The task runner:
1. Loads the story's canon (premise + world + current spine/beats) as authoritative context
2. Builds a task-specific prompt that puts the canon FRONT AND CENTER
3. Makes ONE model call with a structured schema for the task's output
4. Applies the result directly (no reflect loop — the model sees full context at once)
5. Persists through the validated update_story_fields path

The graph loop (story_agent_graph.py) stays available for genuine multi-step restructuring
(where intermediate state changes the plan) — but it's invoked by a DIFFERENT task
("restructure"), not by keyword inference. Most work is one-call tasks dispatched here.
"""
from __future__ import annotations

import asyncio
from typing import Any


# ── Canon grounding ──────────────────────────────────────────────────────────── #

def _canon_block(story: dict) -> str:
    """The authoritative world context — premise + world + current spine — rendered as a block
    the model MUST read before writing anything. This is what prevents canon contradictions:
    the model sees the full established truth and is told it's authoritative."""
    premise = story.get("premise") or ""
    world = story.get("world") or {}
    sb = story.get("storyboard") or {}
    parts = ["ESTABLISHED CANON (authoritative — do NOT contradict any of this):"]
    if premise:
        parts.append(f"PREMISE: {premise}")
    if world.get("genre"):
        parts.append(f"GENRE: {world['genre']}")
    if world.get("tone"):
        parts.append(f"TONE: {world['tone']}")
    if world.get("place"):
        parts.append(f"PLACE: {world['place']}")
    if world.get("pressure"):
        parts.append(f"THE PRESSURE: {world['pressure']}")
    forces = world.get("forces") or []
    if forces:
        parts.append("FORCES: " + "; ".join(
            f"{f.get('name','?')} — {f.get('stance','')}" for f in forces if isinstance(f, dict)))
    traditions = world.get("traditions") or []
    if traditions:
        parts.append("TRADITIONS: " + "; ".join(
            f"{t.get('name','?')} — {t.get('logic','')}" for t in traditions if isinstance(t, dict)))
    # Current spine (so the model sees what exists)
    spine_bits = []
    for f in ("logline", "wound", "lie", "truth"):
        v = story.get(f) or sb.get(f) or ""
        if v:
            spine_bits.append(f"{f}: {v}")
    if spine_bits:
        parts.append("CURRENT SPINE:\n" + "\n".join(f"  {b}" for b in spine_bits))
    return "\n".join(parts)


def _thread(fn, *a, **kw):
    return asyncio.to_thread(lambda: fn(*a, **kw))


# ── Task: SET_SPINE ──────────────────────────────────────────────────────────── #

SPINE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["logline", "wound", "lie", "truth"],
    "properties": {
        "logline": {"type": "string", "description": "WHO + WANT + OBSTACLE, one causal sentence. "
                   "Not a theme — a dramatic engine."},
        "wound": {"type": "string", "description": "The formative hurt that shaped the protagonist. "
                  "A pattern, not a single event."},
        "lie": {"type": "string", "description": "The false belief the wound bred — the lens that "
                "distorts everything. One sentence."},
        "truth": {"type": "string", "description": "The realization that breaks the lie. Must "
                  "directly contradict the lie AND be consistent with the established canon above."},
    },
}


async def task_set_spine(ctx, story_key: str, provider, *, focus: str = "") -> dict:
    """Set all four spine fields (logline, wound, lie, truth) in ONE call, grounded in canon.
    `focus` is an optional steer from the writer ('focus on Eli's relationship with his father')."""
    story = ctx._read_story_data(story_key)
    canon = _canon_block(story)
    system = (
        "You are setting the story's SPINE — the four fields that drive the arc. "
        "Read the ESTABLISHED CANON below FIRST. Every field you write MUST be consistent with it. "
        "If the canon says the father is alive and chained, the truth cannot say he's dead.\n\n"
        + canon +
        "\n\nRULES:\n"
        "• logline = WHO + WANT + OBSTACLE in one causal sentence (not a theme statement)\n"
        "• wound = the formative hurt (a recurring pattern, not one incident)\n"
        "• lie = the false belief it bred (one sentence, the lens that distorts everything)\n"
        "• truth = the realization that BREAKS the lie (must contradict it directly)\n"
        "• The truth MUST be consistent with the canon — it reveals what's really true in THIS world\n"
        f"{'WRITER FOCUS: ' + focus if focus else ''}"
    )
    prompt = "Set the four spine fields for this story."
    res = await _thread(provider.generate_text, system=system, prompt=prompt, emits=SPINE_SCHEMA)
    data = (res.data if hasattr(res, "data") else res) or {}
    # Apply + persist
    fields = {k: data.get(k, "") for k in ("logline", "wound", "lie", "truth") if data.get(k)}
    if fields:
        ctx.update_story_fields(story_key, fields)
    return {"ok": True, "task": "set_spine", "fields": fields,
            "reply": f"Set {len(fields)} spine field(s): {', '.join(fields)}."}


# ── Task: ADD_BEAT ───────────────────────────────────────────────────────────── #

BEAT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "emotional_core", "hook"],
    "properties": {
        "title": {"type": "string", "description": "The dramatic ACTION (what changes), not a "
                  "chapter label."},
        "summary": {"type": "string", "description": "What concretely happens — events, decisions, "
                    "turns. 2-3 sentences."},
        "emotional_core": {"type": "string", "description": "What shifts INTERNALLY for the "
                           "protagonist. The inner change. 1-2 sentences."},
        "hook": {"type": "string", "description": "The tension/question pulling into the next beat."},
        "after": {"type": "string", "description": "The id of the beat this follows (causal chain). "
                  "Omit or null for the first beat."},
    },
}


async def task_add_beat(ctx, story_key: str, provider, *, brief: str = "") -> dict:
    """Add ONE beat to the storyboard — with a real title, summary, emotional core, and hook.
    Grounded in the current spine + canon so the beat actually serves the arc."""
    story = ctx._read_story_data(story_key)
    canon = _canon_block(story)
    sb = story.get("storyboard") or {}
    beats = sb.get("beats") or []
    last_beat = beats[-1] if beats else None
    chain = "\n".join(f"  {b.get('id','?')}: {b.get('title','?')}" for b in beats[-5:]) if beats else "  (none yet)"
    system = (
        "You are adding ONE beat to the storyboard. It must COST something and CAUSE the next. "
        "Read the canon and the current beat chain below FIRST.\n\n"
        + canon +
        f"\n\nCURRENT BEAT CHAIN (last 5):\n{chain}"
        f"\n\nWRITER BRIEF: {brief or 'Add the next logical beat in the chain.'}"
    )
    prompt = "Design one beat that follows the chain and serves the spine."
    res = await _thread(provider.generate_text, system=system, prompt=prompt, emits=BEAT_SCHEMA)
    data = (res.data if hasattr(res, "data") else res) or {}
    # Apply via scripts.invoke (add_beat mutates the storyboard)
    from . import scripts as _S
    story2 = ctx._read_story_data(story_key)  # fresh read for the mutation
    _S.invoke("add_beat", story2, {"title": data.get("title", "Untitled"),
                                    "after": data.get("after")})
    # Now fill the beat's fields (the new beat is the last one)
    sb2 = story2.get("storyboard") or {}
    beats2 = sb2.get("beats") or []
    if beats2:
        new_beat = beats2[-1]
        for field in ("summary", "emotional_core", "hook"):
            if data.get(field):
                new_beat[field] = data[field]
    ctx.update_story_fields(story_key, {"storyboard": sb2})
    return {"ok": True, "task": "add_beat", "beat": data.get("title", ""),
            "reply": f"Added beat: {data.get('title','?')}."}


# ── Dispatch ─────────────────────────────────────────────────────────────────── #

TASKS = {
    "set_spine": task_set_spine,
    "add_beat": task_add_beat,
    # Future: "rebuild_storyboard" → full pipeline or graph loop
    #          "restructure" → graph_agent_graph.run_story_agent
}


async def run_task(ctx, story_key: str, task: str, provider, **kwargs) -> dict:
    """Run a named story task directly. No inference — the caller names the task and it runs
    exactly that, grounded in canon, one call. Returns {ok, task, reply, ...}."""
    fn = TASKS.get(task)
    if fn is None:
        return {"ok": False, "error": f"unknown task '{task}' (valid: {', '.join(TASKS)})"}
    return await fn(ctx, story_key, provider, **kwargs)
