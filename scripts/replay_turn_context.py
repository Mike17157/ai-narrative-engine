"""Replay the first-day bench deterministically — NO LLM calls.

Drives the real compiled-story turn pipeline (prepare_turn -> build_turn_context
-> record_observation) using the user turns from configs/bench_first_day.json and
the recorded narrator replies from an existing bench report, then dumps the exact
(system, prompt) the narrator receives for each turn so we can see precisely which
context lanes collapse at the night window.

Run: ./.venv/Scripts/python.exe scripts/replay_turn_context.py [bench_report] [out_dir]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loom.server.app import build_context
from loom.stories.runtime.compiled import _fresh_runtime, prepare_turn, record_observation
from loom.stories.runtime.context import build_turn_context

bench_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("configs/bench_glm_allroles.json")
out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("configs/trace_night_window")
out_dir.mkdir(parents=True, exist_ok=True)

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
summary = []
for i, user in enumerate(user_turns):
    body = {"text": user, "history": list(history) + [{"role": "user", "text": user}]}
    scene = prepare_turn(contract, world, runtime, body)
    tc = build_turn_context(ctx, st, "evergreen", body, world,
                            story_scope="story_evergreen", thread_scope="thread_trace",
                            scenario=contract, runtime_state=runtime, scenario_scene=scene)
    ss = runtime["scenario_state"]
    roster = tc.get("roster") or []
    present_keys = [name_to_key.get((n or "").lower()) for n in roster]
    row = {
        "turn": i,
        "user": user,
        "active_scene": ss.get("active_scene"),
        "time": ss.get("time"),
        "location": ss.get("location"),
        "scenario_present": list(ss.get("present") or []),
        "roster": roster,
        "scene_just_opened": bool(runtime.get("scene_just_opened")),
        "lanes": tc.get("lanes"),
        "system_chars": len(tc["system"]),
        "prompt_chars": len(tc["prompt"]),
    }
    summary.append(row)
    (out_dir / f"turn_{i:02d}.system.txt").write_text(tc["system"], encoding="utf-8")
    (out_dir / f"turn_{i:02d}.prompt.txt").write_text(tc["prompt"], encoding="utf-8")

    reply = replies[i] if i < len(replies) else "(replay stop)"
    record_observation(runtime, text=reply, player_input=user,
                       present=[k for k in present_keys if k])
    history.append({"role": "user", "text": user})
    history.append({"role": "narrator", "text": reply})

(out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
for row in summary:
    print(f"t{row['turn']:>2} {row['time']:<8} {str(row['active_scene']):<24} "
          f"present={row['scenario_present']!s:<28} roster={row['roster']} "
          f"opened={row['scene_just_opened']}")
print(f"\ndumps → {out_dir}")
