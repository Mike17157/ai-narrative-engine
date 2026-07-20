"""Description units — the ONE way information reaches the narrator.

Every *thing* the narrator may need (the world, a character, a location, the scene, the
rules) is described by exactly ONE unit: a tight block of complete declarative sentences,
budgeted at AUTHORING time. The per-turn input is then COMPOSED from units instead of
assembled from ad-hoc fragment lanes — no mid-word truncations, no relational algebra, no
six stacked rule voices, no duplicated text.

Rules of the layer:
  • One unit per thing, addressed by id ("char.kotori_hase", "loc.forest_gate", "scene").
  • Complete sentences or nothing. `tighten()` enforces budgets by cutting at SENTENCE
    boundaries — renderers must never truncate (the old `[:140]` bug class).
  • Tight declarative prose, not caveman: register bleeds, and the prose lane writes what
    it reads. Director/scribe lanes are parsers — telegraphic is tolerable there only.
  • Volatility classes match the scene-keyed loader: "constant" units are session-stable,
    "scene" units load at scene open (and ride the SceneCache), "turn" units re-render.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...prose import tighten

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…])$")


@dataclass
class Unit:
    id: str
    kind: str                       # world | character | location | scene | arc | player | rules | state | transcript
    title: str                      # short header ("Kotori Hase"); "" joins the previous run
    text: str                       # tight declarative prose — complete sentences only
    volatility: str = "scene"       # constant | scene | turn

    def render(self) -> str:
        body = self.text.strip()
        if not body:
            return ""
        if not self.title:
            return body
        # Multi-line bodies read as a headed section; one-liners stay "Title — body".
        return f"{self.title}:\n{body}" if "\n" in body else f"{self.title} — {body}"


# Composition order: the brief reads world → people → place → moment → rules. Character
# units carry their pair-dynamics folded in, so the relationships lane ceases to exist.
_ORDER = {"world": 0, "player": 1, "character": 2, "location": 3, "scene": 4,
          "arc": 5, "state": 6, "rules": 7, "transcript": 8}


def compose(units: list[Unit]) -> str:
    """The fact sheet: units deduped by id, ordered by kind, blank lines between blocks.
    Empty units drop out silently (an absent thing leaves no hole)."""
    seen: set[str] = set()
    blocks: list[tuple[int, str]] = []
    for u in units:
        if u.id in seen:
            continue
        seen.add(u.id)
        body = u.render()
        if body:
            blocks.append((_ORDER.get(u.kind, 50), body))
    return "\n\n".join(b for _, b in sorted(blocks, key=lambda t: t[0]))


def character_unit(key: str, name: str, *, surface: str, dynamics: list[str] | None = None,
                   home: str = "", max_words: int = 70) -> Unit:
    """One character, one unit: their surface layer (what an observer reads) plus their
    on-stage pair-dynamics as full sentences — the algebra block dies here."""
    parts = [tighten(surface, max_words)]
    for d in dynamics or []:
        d = tighten(d, 30)
        if d:
            parts.append(d)
    if home:
        parts.append(home if home.endswith((".", "!", "?")) else home + ".")
    return Unit(id=f"char.{key}", kind="character", title=name,
                text=" ".join(p for p in parts if p), volatility="scene")


def location_unit(loc_id: str, name: str, description: str, *, current: bool = False,
                  max_words: int = 45) -> Unit:
    text = tighten(description, max_words)
    if current:
        text = (text + " " if text else "") + "(the scene is here now.)"
    return Unit(id=f"loc.{loc_id}", kind="location", title=name, text=text,
                volatility="constant")


def world_unit(premise: str, tone: str, *, max_words: int = 45) -> Unit:
    text = tighten(premise, max_words)
    if tone:
        text = (text + " " if text else "") + f"Tone: {tone.strip()}."
    return Unit(id="world", kind="world", title="THE WORLD", text=text,
                volatility="constant")


def scene_unit(text: str, *, max_words: int = 90) -> Unit:
    """The moment: what is visibly true on arrival, who is here, what presses now."""
    return Unit(id="scene", kind="scene", title="THIS SCENE",
                text=tighten(text, max_words), volatility="scene")


def particulars_unit(lines: list[str], *, max_lines: int = 8) -> Unit:
    """Established small things the story must keep true — one tight line each."""
    kept = [tighten(t, 25) for t in lines if (t or "").strip()][:max_lines]
    kept = [t for t in kept if t]
    return Unit(id="particulars", kind="state", title="ESTABLISHED, KEEP TRUE",
                text="\n".join(f"- {t}" for t in kept), volatility="turn")


def rules_unit(text: str) -> Unit:
    """THE rules unit. Singular. Everything the narrator must obey, in one voice, once."""
    return Unit(id="rules", kind="rules", title="RULES", text=text.strip(),
                volatility="constant")


def player_unit(name: str, *, surface: str, motivation: str = "",
                max_words: int = 70) -> Unit:
    """The player character: surface + drive, with the second-person convention stated once."""
    generic = name.strip().lower() in ("", "player")
    disp = "the player" if generic else name
    parts = [tighten(surface, max_words)] if (surface or "").strip() else []
    mot = tighten(motivation, 30)
    if mot:
        parts.append(mot)
    parts.append(f'You narrate {disp} as "you" — their body and senses are the camera.')
    return Unit(id="player", kind="player",
                title="THE PLAYER" if generic else f"{name} (THE PLAYER)",
                text=" ".join(p for p in parts if p), volatility="constant")


def map_unit(current: tuple[str, str] | None, elsewhere: list[str],
             *, max_words: int = 80) -> Unit:
    """Geography at a glance: the current location described, the rest as a one-line index."""
    parts: list[str] = []
    if current:
        cname, cdesc = current
        parts.append(f"You are at {cname}: {tighten(cdesc, max_words)}")
    idx = [tighten(e, 16).rstrip(".") for e in elsewhere if (e or "").strip()]
    idx = [e for e in idx if e]
    if idx:
        parts.append("Elsewhere: " + "; ".join(idx) + ".")
    return Unit(id="map", kind="location", title="THE MAP", text="\n".join(parts),
                volatility="scene")


def arc_unit(threads: list[str], *, settling: str = "", retired: str = "",
             max_lines: int = 8) -> Unit:
    """The live arc: open story threads plus what earlier scenes settled — advance, never reset."""
    lines = [f"- {tighten(t, 25)}" for t in (threads or []) if (t or "").strip()][:max_lines]
    if settling:
        lines.append(f"- Settled earlier: {tighten(settling, 30)}")
    body = "\n".join(lines)
    if retired:
        body = (body + "\n" if body else "") + f"Retired threads stay retired: {tighten(retired, 25)}"
    return Unit(id="arc", kind="arc", title="TODAY'S ARC", text=body, volatility="turn")


def transcript_unit(verbatim_lines: list[str], earlier: list[str]) -> Unit:
    """RECENT PLAY: the current scene verbatim (the narrator continues THIS text); earlier
    scenes as one-line beats. The raw multi-thousand-line re-feed dies here."""
    parts: list[str] = []
    ear = [tighten(e, 30) for e in (earlier or []) if (e or "").strip()]
    ear = [e for e in ear if e]
    if ear:
        parts.append("Earlier, in brief:\n" + "\n".join(f"- {e}" for e in ear))
    if verbatim_lines:
        parts.append("This scene so far:\n" + "\n".join(verbatim_lines))
    return Unit(id="transcript", kind="transcript", title="THE STORY SO FAR",
                text="\n\n".join(parts), volatility="turn")
