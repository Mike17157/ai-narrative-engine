"""Storyboard: generate and parse the chapter outline (streaming, no structured output)."""

from __future__ import annotations

import re

from ._helpers import _card_context, _sys


def storyboard_inputs(*, name: str, persona: str, extras: dict | None = None,
                      systems: dict | None = None, premise: str = "") -> tuple[str, str]:
    """(system, prompt) for the storyboard stage. Streamed token-by-token."""
    card = _card_context(name, persona, extras or {})
    if premise:
        user_prompt = (
            f"{card}\n\n"
            f"USER'S STORY PREMISE: {premise}\n\n"
            "Storyboard a story for this character, using the premise above as your starting point."
        )
    else:
        user_prompt = f"{card}\n\nStoryboard a plausible story for this character."
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
