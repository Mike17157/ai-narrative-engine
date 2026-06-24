"""Storyboard: generate and parse the chapter outline (streaming, no structured output)."""

from __future__ import annotations

import re

from ._helpers import _card_context, _sys


def storyboard_inputs(*, name: str, persona: str, extras: dict | None = None,
                      systems: dict | None = None, premise: str = "",
                      spine: dict | None = None) -> tuple[str, str]:
    """(system, prompt) for the storyboard stage. Streamed token-by-token."""
    card = _card_context(name, persona, extras or {})

    # Build the spine block if an emotional spine was pre-generated.
    spine_block = ""
    if spine and (spine.get("wound") or spine.get("beats")):
        lines = [
            "EMOTIONAL SPINE — psychological skeleton the chapters must hang from:",
        ]
        if spine.get("wound"):
            lines.append(f"  WOUND: {spine['wound']}")
        if spine.get("lie"):
            lines.append(f"  LIE (false belief): {spine['lie']}")
        if spine.get("truth"):
            lines.append(f"  TRUTH (must accept): {spine['truth']}")
        if spine.get("heart"):
            lines.append(f"  HEART: {spine['heart']}")
        beats = spine.get("beats") or []
        if beats:
            lines.append("  EMOTIONAL BEATS to force through external events:")
            for b in beats:
                lines.append(f"    [{b.get('inflection', '')}] {b.get('description', '')}")
        lines.append(
            "\n  CRITICAL: every chapter must be designed to force at least one of these internal "
            "inflections. The storyboard shows WHAT HAPPENS; the spine is WHY IT MATTERS. Do not "
            "invent emotional beats — engineer events that produce the beats above."
        )
        spine_block = "\n".join(lines)

    parts = [card]
    if spine_block:
        parts.append(spine_block)
    if premise:
        parts.append(f"USER'S STORY PREMISE: {premise}")
    if spine_block:
        parts.append(
            "Storyboard a story for this character. The emotional spine above is the FIXED inner "
            "journey — derive the outer events to force each beat in order."
        )
    elif premise:
        parts.append("Storyboard a story for this character, using the premise above as your starting point.")
    else:
        parts.append("Storyboard a plausible story for this character.")

    user_prompt = "\n\n".join(parts)
    return (_sys(systems or {}, "storyboard"), user_prompt)


_BEAT_RE = re.compile(r"^\s*\d+[.)]\s*(.*\S)\s*$")


def _strip_label(value: str, label: str) -> str:
    low = value.lower()
    prefix = label.lower() + ":"
    if low.startswith(prefix):
        return value[len(prefix):].strip()
    return value


def parse_storyboard(text: str) -> dict:
    """Parse HEART/LOGLINE/PREMISE/TONE/THEMES/CHAPTERS text into a board dict.

    Supports the 7-field chapter format:
      Title | Narrative: ... | Emotional: ... | Hook: ... | Location | Chars | Scene: ...
    and the legacy 4-field format for backward compatibility.
    """
    heart = logline = premise = tone = ""
    themes: list[str] = []
    beats: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("heart:"):
            heart = line.split(":", 1)[1].strip()
        elif low.startswith("logline:"):
            logline = line.split(":", 1)[1].strip()
        elif low.startswith("premise:"):
            premise = line.split(":", 1)[1].strip()
        elif low.startswith("tone:"):
            tone = line.split(":", 1)[1].strip()
        elif low.startswith("themes:"):
            themes = [t.strip() for t in line.split(":", 1)[1].split(",") if t.strip()]
        elif low.startswith("chapters:") or low.startswith("beats:"):
            continue
        else:
            m = _BEAT_RE.match(line)
            if not m:
                continue
            parts = [p.strip() for p in m.group(1).split("|")]
            if len(parts) >= 7:
                title = parts[0]
                summary = _strip_label(parts[1], "Narrative")
                emotional_core = _strip_label(parts[2], "Emotional")
                hook = _strip_label(parts[3], "Hook")
                location = parts[4]
                chars = [c.strip() for c in parts[5].split(",")]
                scene_prompt = _strip_label(parts[6], "Scene")
                beats.append({
                    "title": title, "summary": summary, "location": location,
                    "characters": [c for c in chars if c],
                    "emotional_core": emotional_core, "hook": hook,
                    "scene_prompt": scene_prompt,
                })
            elif len(parts) >= 4:
                title, summary, location = parts[0], parts[1], parts[2]
                chars = [c.strip() for c in parts[3].split(",")]
                beats.append({"title": title, "summary": summary, "location": location,
                              "characters": [c for c in chars if c]})
            else:
                title = ""
                summary = parts[0] if parts else ""
                location = parts[1] if len(parts) > 1 else ""
                chars = [c.strip() for c in parts[2].split(",")] if len(parts) > 2 else []
                beats.append({"title": title, "summary": summary, "location": location,
                              "characters": [c for c in chars if c]})
    return {"heart": heart, "logline": logline, "premise": premise,
            "tone": tone, "themes": themes, "beats": beats}


def board_to_graph(board: dict, base: dict | None = None) -> dict:
    """Convert a storyboard BOARD into a development GRAPH the canvas renders.

    The board's beats become graph nodes connected in sequence (each node.next points at the
    next). Beat fields map onto the node schema: summary→what_happened, emotional_core→
    inflection, plus title/location. The spine fields (wound/lie/truth) are NOT in a board, so
    we preserve whatever the working graph already had (don't clobber the writer's spine); the
    board's logline updates the graph's logline.
    """
    base = dict(base or {})
    beats = [b for b in (board.get("beats") or []) if isinstance(b, dict)]
    nodes: list[dict] = []
    for i, b in enumerate(beats):
        nodes.append({
            "id": f"sb{i + 1}",
            "title": (b.get("title") or f"Beat {i + 1}").strip(),
            "inflection": (b.get("emotional_core") or "").strip(),
            "what_happened": (b.get("summary") or "").strip(),
            "location": (b.get("location") or "").strip(),
            "start": "", "end": "", "next": [],
        })
    for i in range(len(nodes) - 1):
        nodes[i]["next"] = [nodes[i + 1]["id"]]

    graph = dict(base)
    graph["nodes"] = nodes
    if board.get("logline"):
        graph["logline"] = board["logline"]
    for f in ("wound", "lie", "truth"):
        graph.setdefault(f, base.get(f, ""))
    return graph
