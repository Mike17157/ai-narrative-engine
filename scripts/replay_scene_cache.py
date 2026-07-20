"""Verify the scene-keyed context loader OFFLINE — NO LLM calls.

Drives the real compiled-story turn pipeline (prepare_turn -> build_turn_context ->
record_observation) over the first-day scenario with recorded narrator replies, threading
the scene-context cache across turns exactly as the engine threads it through the State
doc's `ctx` level. Prints, per turn: the scene signature, location, whether a boundary
fired, and how many cacheable blocks were carried verbatim vs recomputed.

Asserts the loader's invariants:
  1. turn 0 is a boundary (cold cache).
  2. WITHIN a scene (unchanged signature), no already-cached block's input hash may change
     — block contents are a pure function of scene-stable inputs, so a mid-scene mutation
     would mean retrieval re-ran against turn-volatile text. (New block ids MAY appear: a
     character's beat-driven voice budget keys its own variant.)
  3. The lore lane block (when present) follows the same rule.

Run: ./.venv/Scripts/python.exe scripts/replay_scene_cache.py [bench_report]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loom.server.app import build_context
from loom.stories.runtime.compiled import _fresh_runtime, prepare_turn, record_observation
from loom.stories.runtime.context import build_turn_context

bench_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("configs/bench_glm52_firstday_nolore.json")
bench = json.loads(bench_path.read_text(encoding="utf-8"))
scenario = json.loads(Path("configs/bench_first_day.json").read_text(encoding="utf-8"))
user_turns = scenario["turns"]
replies = [t["reply"] for t in bench["turns"]]

ctx = build_context(".")
st = ctx.base_settings.stories["evergreen"]
contract = (st.fields or {})["runtime_scenario"]
world, runtime = _fresh_runtime(contract)
name_to_key = {}
for m in st.cast:
    c = ctx.base_settings.characters.get(m.character)
    name_to_key[((c.name if c else m.character) or "").lower()] = m.character

history: list[dict] = []
cache: dict = {}
failures: list[str] = []
prev_sig = ""
prev_hashes: dict[str, str] = {}
print(f"{'t':>2} {'bnd':>4} {'sig':>18} {'location':<16} {'carried':>7} {'recomp':>7}  blocks")
for i, user in enumerate(user_turns):
    body = {"text": user, "history": list(history) + [{"role": "user", "text": user}]}
    scene = prepare_turn(contract, world, runtime, body)
    tc = build_turn_context(ctx, st, "evergreen", body, world,
                            story_scope="story_evergreen", thread_scope="thread_trace",
                            scenario=contract, runtime_state=runtime, scenario_scene=scene,
                            scene_cache=cache)
    sc = (tc.get("lanes") or {}).get("scene_ctx") or {}
    cache = tc.get("scene_cache") or {}
    blocks = cache.get("blocks") or {}
    hashes = {bid: str(ent.get("hash") or "") for bid, ent in blocks.items()
              if isinstance(ent, dict)}
    sig = str(sc.get("sig") or "")
    blist = ",".join(sorted(hashes)) or "(none)"
    print(f"{i:>2} {str(bool(sc.get('boundary')))[:1]:>4} {sig:>18} {str(tc.get('cur')):<16} "
          f"{sc.get('carried', 0):>3}/{sc.get('carried_bytes', 0):<5}B "
          f"{sc.get('recomputed', 0):>3}/{sc.get('recomputed_bytes', 0):<5}B  {blist}")

    # invariant 1
    if i == 0 and not sc.get("boundary"):
        failures.append("turn 0 must be a boundary (cold cache)")
    # invariant 2: same sig as previous turn -> no existing block hash may mutate
    if sig == prev_sig:
        for bid, h in hashes.items():
            if bid in prev_hashes and prev_hashes[bid] != h:
                failures.append(f"turn {i}: block {bid!r} mutated mid-scene (sig {sig})")
    prev_sig, prev_hashes = sig, hashes

    roster = tc.get("roster") or []
    present_keys = [name_to_key.get((n or "").lower()) for n in roster]
    reply = replies[i] if i < len(replies) else "(replay stop)"
    record_observation(runtime, text=reply, player_input=user,
                       present=[k for k in present_keys if k])
    history.append({"role": "user", "text": user})
    history.append({"role": "narrator", "text": reply})

stats = cache.get("stats") or {}
print(f"\nlifetime: turns={stats.get('turns')} carried={stats.get('carried')} blocks/"
      f"{stats.get('carried_bytes')}B recomputed={stats.get('recomputed')} blocks/"
      f"{stats.get('recomputed_bytes')}B")
if failures:
    print("\nINVARIANT FAILURES:")
    for f in failures:
        print("  -", f)
    raise SystemExit(1)
print("scene-loader invariants hold ✓")
