"""The StoryMaster ORCHESTRATOR — a smart, infrequent planning pass above the per-scene
director (see [[storymaster-pacing]]: "doctrine + development ledger + adversarial critic",
validated in sim, built here). Runs once per scene boundary — the same cadence director.py's
`_h_scene` already uses — on a STRONGER reasoning model, while every per-turn pass (writer,
scribe, consequence) keeps running on whatever cheap/fast model the story session is
configured with. Two-tier by design: expensive reasoning rarely, cheap execution constantly.

Architecture: PROPOSE -> CRITIQUE -> COMMIT.
  - PROPOSE reconciles the story's scattered central-question sources (premise_parts.question,
    world.pressure, an active arc's question, arc_design's theme questions) into ONE, sets a
    whole-story pacing signal (not scene-local invention), and may nominate exactly one
    already-eligible-but-ungated `arc_design` revelation turning point as earned.
  - CRITIQUE is a second, skeptical call whose ONLY job is to veto or confirm that
    nomination — opening a revelation early is the one irreversible-feeling mistake here;
    nothing else this function decides needs a second opinion. Skipped entirely when nothing
    was nominated (saves the call).
  - COMMIT writes the doctrine onto `world["doctrine"]` (read by `plot_direction` and threaded
    into the scene planner's prompt) + a capped development-ledger note, and — only if the
    critique approved — sets the exact `flags` the nominated turning point's `requires` names,
    the missing "something decides a resolution has been earned" half of arc tracking.

Self-check: python -m loom.stories.runtime.storymaster
"""
from __future__ import annotations

from typing import Any

# Pinned per the user's explicit ask: a stronger reasoning model for the rare orchestration
# pass, decoupled from whatever cheap/fast model the story session's writer/scribe use.
_MASTER_MODEL = "moonshotai/kimi-k3"
_LEDGER_CAP = 5

_DOCTRINE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["central_question", "target_tone", "escalate", "focus", "ledger_note", "nominate_reveal"],
    "properties": {
        "central_question": {"type": "string", "description": "ONE authoritative central "
            "question for the whole story, synthesized from the candidates given — not a new "
            "invention unrelated to them."},
        "target_tone": {"type": "string", "description": "the register the NEXT stretch of "
            "scenes should carry (comedy, tenderness, dread, awkwardness, relief, ...) — a "
            "whole-story pacing call, not just what would suit this one scene."},
        "escalate": {"type": "boolean", "description": "true if tension should actively begin "
            "rising now, given how far into the story this is and what's already been spent"},
        "focus": {"type": "string", "description": "which open thread or character deserves "
            "the story's attention this stretch — a short phrase, not a command"},
        "ledger_note": {"type": "string", "description": "ONE sentence a future planning pass "
            "should remember about this decision — the development ledger's new entry"},
        "nominate_reveal": {
            "anyOf": [
                {"type": "null"},
                {"type": "object", "additionalProperties": False,
                 "required": ["turn_id", "reason"],
                 "properties": {
                     "turn_id": {"type": "string", "description": "the EXACT id of one "
                         "candidate from CANDIDATE REVELATIONS below — never invent one"},
                     "reason": {"type": "string", "description": "why the evidence so far "
                         "earns this specific revelation now"},
                 }},
            ],
            "description": "null unless a candidate below is genuinely earned by what's "
            "happened in play so far — err toward null when uncertain",
        },
    },
}

_CRITIQUE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["approve", "reason"],
                    "properties": {"approve": {"type": "boolean"}, "reason": {"type": "string"}}}

_PROPOSE_SYS = (
    "You are the STORY-MASTER: the one planning pass that sees the whole story, not just this "
    "scene. Reconcile the central question, set the pacing for the NEXT stretch of play, and — "
    "only if the evidence genuinely earns it — nominate one gated revelation as ready to open. "
    "Be conservative: a story that waits too long is fine; one that resolves early is ruined. "
    "JSON only.")

_CRITIQUE_SYS = (
    "You are a SKEPTICAL second opinion. A nomination to open a specific character's gated "
    "revelation was just proposed. Your only job is to check whether the evidence given — the "
    "turning point's own pressure points and what has actually happened in play — genuinely "
    "earns it, or whether this is premature. Default to NOT approving when you are not certain. "
    "JSON only.")


def _candidate_reveals(st: Any, world: dict, present: set[str]) -> list[dict]:
    """Gated `revelation` turning points relevant to who's on stage, not yet open. Author-side
    visibility (private_pressure included) is safe here — this never reaches the narrator."""
    raw = (getattr(st, "fields", None) or {}).get("arc_design")
    if not raw:
        return []
    try:
        from ..authoring.arc_design import _gate_open, normalize_arc_design
        design = normalize_arc_design(raw)
    except Exception:  # noqa: BLE001 — a malformed private draft must never break the scene
        return []
    flags = world.get("flags") or {}
    out = []
    for arc in design.get("arcs", []):
        owner = arc.get("owner", "")
        for tp in arc.get("turning_points", []):
            if tp.get("kind") != "revelation":
                continue
            targets = set(tp.get("characters") or []) or {owner}
            if present and not (targets & present):
                continue
            if _gate_open(tp.get("requires") or {}, flags):
                continue   # already open — nothing to nominate
            out.append({"id": f"{arc['id']}::{tp['id']}", "owner": owner,
                        "public_surface": tp.get("public_surface", ""),
                        "private_pressure": tp.get("private_pressure", ""),
                        "revelation": tp.get("revelation", ""),
                        "requires": tp.get("requires") or {}})
    return out


def _central_question_candidates(st: Any, world: dict) -> list[str]:
    out = []
    parts = getattr(st, "premise_parts", None) or {}
    if parts.get("question"):
        out.append(parts["question"])
    pressure = (getattr(st, "world", None) or {}).get("pressure")
    if pressure:
        out.append(pressure)
    arc = world.get("arc") or {}
    if arc.get("question"):
        out.append(arc["question"])
    raw = (getattr(st, "fields", None) or {}).get("arc_design")
    if raw:
        try:
            from ..authoring.arc_design import normalize_arc_design
            for theme in normalize_arc_design(raw).get("themes", []):
                if theme.get("question"):
                    out.append(theme["question"])
        except Exception:  # noqa: BLE001
            pass
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q); uniq.append(q)
    return uniq


def direct_scene(sm) -> dict:
    """Refresh `sm.world["doctrine"]` for the scene that's about to open. No-op (returns {})
    if the master model has no active connection — the per-scene director + locally-derived
    `plot_direction` fallback still steer the turn, just without whole-story awareness."""
    provider = sm.ctx.text_provider_for(_MASTER_MODEL, {"reasoning_effort": "high"})
    if provider is None or not hasattr(provider, "generate_text"):
        return {}
    world = sm.world
    present = {n.lower() for n in sm._names_at(sm.location)} | sm._cast_names()
    candidates = _candidate_reveals(sm.st, world, present)
    questions = _central_question_candidates(sm.st, world)
    ledger = world.get("doctrine_log") or []
    promises = [p.get("setup", "") for p in (world.get("promises") or []) if p.get("status") == "open"]
    lines = [
        f"CENTRAL QUESTION CANDIDATES (pick one, or synthesize the shared thread between them):\n"
        + "\n".join(f"- {q}" for q in questions) if questions else "",
        f"DEVELOPMENT LEDGER (your own past notes, most recent last):\n"
        + "\n".join(f"- {n}" for n in ledger) if ledger else "",
        f"OPEN THREADS (unresolved promises the cast is carrying):\n"
        + "\n".join(f"- {p}" for p in promises) if promises else "",
        f"CANDIDATE REVELATIONS (gated, not yet open — nominate by id ONLY if genuinely earned):\n"
        + "\n".join(f"- id={c['id']} owner={c['owner']} surface=\"{c['public_surface']}\" "
                    f"pressure=\"{c['private_pressure']}\"" for c in candidates) if candidates else
        "CANDIDATE REVELATIONS: none pending.",
        f"A new scene is opening at: {sm.location}.",
    ]
    prompt = "\n\n".join(b for b in lines if b)
    out = provider.generate_text(system=_PROPOSE_SYS, prompt=prompt, emits=_DOCTRINE_SCHEMA)
    doctrine = getattr(out, "data", None) or {}
    if not doctrine:
        return {}
    nomination = doctrine.get("nominate_reveal")
    approved = False
    if nomination and nomination.get("turn_id"):
        cand = next((c for c in candidates if c["id"] == nomination["turn_id"]), None)
        if cand is not None:
            critique_prompt = (
                f"TURNING POINT: owner={cand['owner']} surface=\"{cand['public_surface']}\"\n"
                f"PRIVATE PRESSURE: {cand['private_pressure']}\nREVELATION: {cand['revelation']}\n"
                f"PROPOSER'S REASON: {nomination['reason']}\n\nApprove opening it now?")
            v = provider.generate_text(system=_CRITIQUE_SYS, prompt=critique_prompt, emits=_CRITIQUE_SCHEMA)
            verdict = getattr(v, "data", None) or {}
            approved = bool(verdict.get("approve"))
            if approved:
                flags = world.setdefault("flags", {})
                for key, desired in (cand["requires"].get("flags") or {}).items():
                    flags[str(key)] = desired[0] if isinstance(desired, (list, tuple)) else desired
    world["doctrine"] = {"central_question": doctrine.get("central_question", ""),
                         "target_tone": doctrine.get("target_tone", ""),
                         "escalate": bool(doctrine.get("escalate")),
                         "focus": doctrine.get("focus", "")}
    if doctrine.get("ledger_note"):
        log = world.setdefault("doctrine_log", [])
        log.append(doctrine["ledger_note"])
        world["doctrine_log"] = log[-_LEDGER_CAP:]
    return world["doctrine"]


def doctrine_block(world: dict) -> str:
    """The doctrine as a compact context block for the local scene planner's prompt."""
    d = world.get("doctrine") or {}
    if not d.get("central_question") and not d.get("target_tone"):
        return ""
    out = ["STORY-MASTER'S DOCTRINE (the whole-story plan — inform this scene with it, don't "
           "force it to announce any of this):"]
    if d.get("central_question"):
        out.append(f"- the story's central question: {d['central_question']}")
    if d.get("target_tone"):
        out.append(f"- pacing calls for this stretch to play in: {d['target_tone']}"
                   + (" (tension should be rising)" if d.get("escalate") else ""))
    if d.get("focus"):
        out.append(f"- give attention to: {d['focus']}")
    return "\n".join(out)


def demo() -> None:
    class _FakeProvider:
        def generate_text(self, *, system: str, prompt: str, emits: dict):
            class R:
                data = ({"central_question": "does staying cost less than leaving", "target_tone": "dread",
                        "escalate": True, "focus": "aoi", "ledger_note": "aoi still hasn't spoken since the reveal",
                        "nominate_reveal": None} if emits is _DOCTRINE_SCHEMA else {"approve": False, "reason": "not yet"})
            return R()

    class _FakeCtx:
        def text_provider_for(self, *_a, **_k): return _FakeProvider()

    class _FakeSt:
        premise_parts = {"question": "does staying cost less than leaving"}
        world = {"pressure": ""}
        fields = {}

    class _FakeSM:
        ctx = _FakeCtx(); st = _FakeSt(); world = {"flags": {}, "promises": []}
        location = "grove"
        def _names_at(self, _l): return []
        def _cast_names(self): return set()

    sm = _FakeSM()
    d = direct_scene(sm)
    assert d.get("central_question") and d.get("target_tone") == "dread"
    assert "STORY-MASTER" in doctrine_block(sm.world)
    print("ok — storymaster: propose→commit, doctrine block renders")


if __name__ == "__main__":
    demo()
