"""Per-character PERCEPTION — who saw which indexed step.

A story advances in indexed STEPS (one per play turn). A character only knows what they
WITNESSED: their record starts at the step they JOINED (their first present step), so a late
arrival sees only from then on — at the moment they join, just the current (last) step, never
the backlog. This is the substrate for info-isolated cast + scoping each character's memory
(record_scene) and context to what they actually saw.

Stored on the world-state level: `step` (a monotonically increasing counter) and `presence`
({character_key: [step indices the character was present for]}).
"""
from __future__ import annotations


def record_step(world: dict, present: list[str]) -> int:
    """Advance to the next step and record every PRESENT character as having seen it. A
    character appearing for the first time JOINS at this step (their list starts here → no
    backlog), so they only ever see from their arrival on. Returns the new step's index."""
    if not isinstance(world, dict):
        return 0
    if not isinstance(world.get("presence"), dict):
        world["presence"] = {}
    step = int(world.get("step") or 0)
    present = [str(k) for k in (present or []) if k]
    for k in present:
        world["presence"].setdefault(k, []).append(step)
    # The inverse link too: scene_cast[step] = who was in this passage. So a passage links to its
    # characters directly (consolidation reads it; the UI can make passages clickable to cards).
    if not isinstance(world.get("scene_cast"), list):
        world["scene_cast"] = []
    world["scene_cast"].append(present)
    world["step"] = step + 1
    return step


def present_at(world: dict, step: int) -> list[str]:
    """Who was in the scene at this passage/step — the inverse of seen_steps (passage → characters)."""
    sc = (world or {}).get("scene_cast") or []
    return list(sc[step]) if 0 <= step < len(sc) else []


def seen_steps(world: dict, char_key: str) -> list[int]:
    """The step indices this character witnessed (in order; their join step is seen_steps[0])."""
    return list((world.get("presence") or {}).get(str(char_key), []))


def joined_at(world: dict, char_key: str) -> int | None:
    """The step the character first appeared (None if they've never been present)."""
    s = seen_steps(world, char_key)
    return s[0] if s else None


def saw_step(world: dict, char_key: str, step: int) -> bool:
    return step in seen_steps(world, char_key)


def visible(world: dict, char_key: str, steps: list) -> list:
    """Filter an indexed `steps` list (each item is whatever the caller stores per step, indexed
    by position) down to ONLY the ones this character saw — i.e. their view of the transcript. A
    just-joined character gets only their join step (the last one), never the prior backlog."""
    seen = set(seen_steps(world, char_key))
    return [s for i, s in enumerate(steps or []) if i in seen]


def demo() -> None:
    """Self-check: join semantics (no backlog) + accumulation. Run: python -m loom.stories.perception"""
    w: dict = {}
    assert record_step(w, ["a", "b"]) == 0          # step 0: a, b present
    assert record_step(w, ["a"]) == 1               # step 1: only a
    assert record_step(w, ["a", "c"]) == 2          # step 2: a joins-with c (c is new)
    assert w["step"] == 3
    assert seen_steps(w, "a") == [0, 1, 2]          # a saw everything
    assert seen_steps(w, "b") == [0]                # b only the opening
    assert seen_steps(w, "c") == [2]                # c JOINED at 2 → no backlog
    assert joined_at(w, "c") == 2
    assert visible(w, "c", ["m0", "m1", "m2"]) == ["m2"]   # c sees only the last message
    assert not saw_step(w, "c", 0)
    assert present_at(w, 0) == ["a", "b"]            # passage→characters (inverse link)
    assert present_at(w, 2) == ["a", "c"]
    assert present_at(w, 9) == []                    # out of range → empty
    print("perception demo ok")


if __name__ == "__main__":
    demo()
