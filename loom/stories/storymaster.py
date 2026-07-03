"""The StoryMaster — the coordinator that owns the world model and the network of functions that
react to play.

The narrator writes prose; the scribe reports what's in it; the StoryMaster turns that report into
EVENTS, runs the handlers those events trigger (handlers may emit further events — the network),
mutates the ONE world model, and (later) spawns slow work — fleshing characters, rendering sprites
and scenes — as background jobs. It also owns the decision of what the narrator sees next turn
(the context compile). One owner instead of world-mutation scattered across the turn.

Slice 1 handlers (here): the CHARACTER NETWORK, organized by location —
  • `person`    a named person appeared in the narration → upsert a thin record AT a location
                (mention makes them real; they persist, pinned to a place — static for now).
  • `encounter` a person is physically ON STAGE and not yet fleshed → flesh them into a full card
                via the transposition engine (similarity-seeded), one at a time (gradual).
  • `fleshed`   (emitted by encounter) → the seam where a sprite-render job will hang later.

Registering a new capability is just `@StoryMaster.on("event")` — images and plot beats become
handlers on this same bus, not more inline code.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable


class StoryMaster:
    # event type -> [handler(self, data)]. Class-level so handlers register at import.
    HANDLERS: dict[str, list[Callable]] = defaultdict(list)

    @classmethod
    def on(cls, etype: str):
        def deco(fn: Callable) -> Callable:
            cls.HANDLERS[etype].append(fn)
            return fn
        return deco

    def __init__(self, ctx, st, world: dict, *, provider=None, location: str = "", jobs=None):
        self.ctx = ctx                    # AppContext (character lookups, root, providers)
        self.st = st                      # the Story
        self.world = world                # THE world model (mutated in place)
        self.provider = provider          # writer, for fleshing
        self.location = location          # the current scene location (name) — for placing people
        self.jobs = jobs                  # optional: spawn a background job (name, fn) — images later
        self._q: deque = deque()
        self.world.setdefault("people", {})   # {name: {at, note, born, card?}}

    # ── event bus ────────────────────────────────────────────────────────────────
    def emit(self, etype: str, **data) -> None:
        self._q.append((etype, data))

    def _drain(self) -> None:
        steps = 0
        while self._q and steps < 200:    # cascade guard (handlers emit handlers)
            etype, data = self._q.popleft()
            for h in StoryMaster.HANDLERS.get(etype, []):
                try:
                    h(self, data)
                except Exception:  # noqa: BLE001 — a handler must never drop the turn
                    pass
            steps += 1

    # ── the world model — helpers handlers use ─────────────────────────────────────
    def _cast_names(self) -> set[str]:
        out = set()
        for m in self.st.cast:
            c = self.ctx.base_settings.characters.get(m.character)
            out.add(((c.name if c else m.character) or m.character).lower())
        return out

    def known(self) -> set[str]:
        return self._cast_names() | {n.lower() for n in (self.world.get("people") or {})}

    # ── the turn hook ──────────────────────────────────────────────────────────────
    def ingest(self, *, people: list[dict], present: list[str], narration: str) -> list[str]:
        """Process one turn's scribe report into events, then run the network. `people` = every
        named person the scribe saw (name, at, note); `present` = who is physically on stage.
        Returns the names FLESHED this turn (for the response)."""
        self._fleshed_now: list[str] = []
        self._narration = narration
        for p in (people or []):
            nm = (p.get("name") or "").strip()
            if nm:
                self.emit("person", name=nm, at=(p.get("at") or "").strip(), note=(p.get("note") or "").strip())
        known = self.known()
        for nm in (present or []):
            if nm and nm.lower() not in known:
                self.emit("encounter", name=nm)
        self._drain()
        return self._fleshed_now


# ── Slice-1 handlers: the character network, organized by location ──────────────────

@StoryMaster.on("person")
def _h_person(sm: StoryMaster, d: dict) -> None:
    """Mention → a thin record, pinned to a location (static). Persists; never overwrites a
    fleshed card. Absent people keep their place; someone seen in-scene lands at the current loc."""
    nm = d["name"]
    if nm.lower() in sm._cast_names():
        return                                          # already a first-class cast member
    rec = sm.world["people"].get(nm) or {}
    rec["note"] = d.get("note") or rec.get("note", "")
    at = d.get("at") or rec.get("at") or (sm.location if nm.lower() in
                                          (sm._narration or "").lower() else "")
    rec["at"] = at
    rec.setdefault("born", False)
    sm.world["people"][nm] = rec


@StoryMaster.on("encounter")
def _h_encounter(sm: StoryMaster, d: dict) -> None:
    """On stage and not fleshed → flesh into a full card (similarity-seeded transposition), one
    per turn. Emits `fleshed` (the future sprite-render seam)."""
    if sm._fleshed_now:                                 # gradual: one new face fully realized per turn
        return
    nm = d["name"]
    rec = sm.world["people"].get(nm) or {}
    if rec.get("born"):
        return
    if sm.provider is None:
        sm.world["people"].setdefault(nm, rec).update({"at": sm.location, "born": False})
        return
    from .worldgen import birth_character
    story_ctx = f"{sm.st.premise or sm.st.name}. Just now in the story: {(sm._narration or '')[:600]}"
    card = birth_character(sm.provider, name=nm, role=(rec.get("note") or "someone met in this scene"),
                           story=story_ctx, prominence="supporting", root=sm.ctx.root)
    if card.get("card"):
        rec.update({"at": sm.location, "note": rec.get("note", ""), "born": True, "card": card["card"]})
        sm.world["people"][nm] = rec
        sm._fleshed_now.append(nm)
        sm.emit("fleshed", name=nm)


@StoryMaster.on("fleshed")
def _h_fleshed(sm: StoryMaster, d: dict) -> None:
    """Seam for the sprite-render job (Slice 2, needs ComfyUI). For now just note it in the log so
    the world records that a face was born."""
    sm.world.setdefault("log", []).append(f"(a new person entered the story: {d['name']})")


def people_by_location(world: dict, current: str) -> str:
    """The narrator's PEOPLE feed, organized by where they are: who's at the current place up
    front, everyone else indexed under their location. Bounds a growing cast to the current scene
    plus a location index. '' if nobody's been recorded yet."""
    people = world.get("people") or {}
    if not people:
        return ""
    here, elsewhere = [], defaultdict(list)
    cur = (current or "").strip().lower()
    for nm, r in people.items():
        at = (r.get("at") or "").strip()
        line = nm + (f" — {r['note']}" if r.get("note") else "")
        if at and at.lower() == cur:
            here.append(line)
        else:
            elsewhere[at or "whereabouts unknown"].append(nm)
    out = []
    if here:
        out.append("HERE with you:\n" + "\n".join(f"- {l}" for l in here))
    if elsewhere:
        out.append("ELSEWHERE (go to them to bring them on):\n"
                   + "\n".join(f"- at {loc}: {', '.join(ns)}" for loc, ns in elsewhere.items()))
    return "PEOPLE (met in this story):\n" + "\n".join(out) if out else ""
