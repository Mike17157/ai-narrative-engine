"""Scene-keyed context cache — the SCENE is the information-loading unit, not the turn.

The play graph already PLANS per scene (step_scene fires once per boundary; the standing
scene_plan is read free every mid-scene turn). This module gives the context LOADER the
same cadence:

  • A scene signature (location + presence set + POV + active setting-stage flags +
    authored scene id) identifies the scene. It is computed every turn; when it changes,
    a boundary has occurred.
  • Cacheable context blocks (cast embodiment exemplars, the lore lane) are ranked/retrieved
    ONCE at scene open against the scene's premise — not re-ranked against each turn's beat.
  • Each block carries a content hash of its inputs. Crossing a boundary recomputes only
    the blocks whose inputs actually changed (information barely varies between scenes —
    unchanged characters carry forward untouched), and identical text byte-for-byte is what
    makes the stable prompt prefix provider-cacheable.

The doc persists as the State document's ``ctx`` level (extensible level set; sibling to
``world``/``runtime``), so it survives across turns with the existing save paths. It is
engine bookkeeping — never rendered into a prompt.

Per-turn stats (carried vs recomputed blocks/bytes, boundary flag) flow through
``lanes["scene_ctx"]`` in the play response so the bench measures information handling
per scene instead of per turn.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

LEVEL = "ctx"                       # the State-doc level this cache persists under


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def hash_inputs(inputs: dict) -> str:
    return hashlib.sha1(_canon(inputs).encode("utf-8")).hexdigest()[:16]


def scene_signature(*, cur: str, members, pov: str, conds, scene_id: str = "") -> str:
    """One id per information-relevant scene configuration. Any change here is a
    scene boundary for loading purposes (a beat change inside the scene is NOT)."""
    return hash_inputs({
        "cur": cur or "",
        "members": sorted(str(m) for m in (members or []) if m),
        "pov": pov or "",
        "conds": sorted(str(c) for c in (conds or []) if c),
        "scene": scene_id or "",
    })


def scene_premise(scenario_scene: dict | None, world_state: dict, fallback: str = "") -> str:
    """The text exemplar/lore retrieval ranks against — the SCENE's premise, stable for
    the scene's whole duration. Compiled play: the authored scene's public surface. Free
    play: the standing scene plan's goal/pressure. Fallback: the current beat (legacy
    per-turn behavior when nothing authored a premise)."""
    sc = scenario_scene if isinstance(scenario_scene, dict) else {}
    parts = [str(sc.get(k) or "") for k in ("title", "visible", "hook")]
    text = " ".join(p for p in parts if p.strip())
    if text.strip():
        return text
    plan = world_state.get("scene_plan") if isinstance(world_state.get("scene_plan"), dict) else {}
    parts = [str(plan.get(k) or "") for k in ("space", "goal", "pressure")]
    text = " ".join(p for p in parts if p.strip())
    return text if text.strip() else (fallback or "")


class SceneCache:
    """Block store keyed by input hash. `signature` identifies the open scene; `blocks`
    outlive scene boundaries so a block whose inputs are unchanged carries forward."""

    def __init__(self, doc: dict | None = None):
        doc = doc if isinstance(doc, dict) else {}
        self._sig = str(doc.get("signature") or "")
        self.opened_step = int(doc.get("opened_step") or 0)
        blocks = doc.get("blocks")
        self._blocks: dict[str, dict] = blocks if isinstance(blocks, dict) else {}
        stats = doc.get("stats")
        self._stats = stats if isinstance(stats, dict) else {}
        # per-turn accumulators (read into lanes after the turn's assembly)
        self.carried = 0
        self.recomputed = 0
        self.carried_bytes = 0
        self.recomputed_bytes = 0
        self.boundary = False

    @property
    def signature(self) -> str:
        return self._sig

    def begin_scene(self, sig: str, step: int) -> None:
        self.boundary = True
        self._sig = sig
        self.opened_step = int(step)

    def block(self, bid: str, inputs: dict) -> str | None:
        """Cached text for `bid` if its inputs are unchanged (None = recompute)."""
        ent = self._blocks.get(bid)
        if isinstance(ent, dict) and ent.get("hash") == hash_inputs(inputs):
            text = str(ent.get("text") or "")
            self.carried += 1
            self.carried_bytes += len(text)
            return text
        return None

    def store(self, bid: str, inputs: dict, text: str) -> str:
        self._blocks[bid] = {"hash": hash_inputs(inputs), "text": text or ""}
        self.recomputed += 1
        self.recomputed_bytes += len(text or "")
        return text or ""

    def stats(self) -> dict:
        return {"carried": self.carried, "recomputed": self.recomputed,
                "carried_bytes": self.carried_bytes,
                "recomputed_bytes": self.recomputed_bytes}

    def to_doc(self) -> dict:
        # Lifetime counters (the bench's carry-rate gauge across a whole play).
        lt = dict(self._stats)
        lt["turns"] = int(lt.get("turns") or 0) + 1
        lt["carried"] = int(lt.get("carried") or 0) + self.carried
        lt["recomputed"] = int(lt.get("recomputed") or 0) + self.recomputed
        lt["carried_bytes"] = int(lt.get("carried_bytes") or 0) + self.carried_bytes
        lt["recomputed_bytes"] = int(lt.get("recomputed_bytes") or 0) + self.recomputed_bytes
        self._stats = lt
        return {"signature": self._sig, "opened_step": self.opened_step,
                "blocks": self._blocks, "stats": lt}
