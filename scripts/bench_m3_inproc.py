"""Resumable in-process variant of bench_story_play.py. Plays the scenario against the legacy
app via TestClient, checkpointing after every turn, stopping when a time budget runs out so it
can be re-run to continue. Scores with the critic once all turns are played.
Run: python scripts/bench_m3_inproc.py [scenario] [out.json] [budget_secs]"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from loom.server.app import create_app, build_context
from scripts import bench_story_play as bench

scenario = sys.argv[1] if len(sys.argv) > 1 else "school"
out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"configs/bench_m3_{scenario}.json")
budget = float(sys.argv[3]) if len(sys.argv) > 3 else 240.0

if scenario.endswith(".json"):                       # file-driven scenario
    sc = json.loads(Path(scenario).read_text(encoding="utf-8"))
    scenario = Path(scenario).stem
else:
    sc = bench.SCENARIOS[scenario]
ckpt: dict = {}
if out_path.exists():
    try:
        ckpt = json.loads(out_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        out_path.replace(out_path.with_suffix(".corrupt.json"))
        ckpt = {}

sid = ckpt.get("sid") or f"bench-{scenario}-{int(time.time())}"
history: list[dict] = ckpt.get("history") or []
turns: list[dict] = ckpt.get("turns") or []
timings: list[float] = ckpt.get("timings") or []

app = create_app(".")
client = TestClient(app)

start = time.time()
done = len(turns) >= len(sc["turns"])
while not done:
    i = len(turns)
    user = sc["turns"][i]
    history.append({"role": "user", "text": user})
    t0 = time.time()
    r = client.post(f"/api/stories/{sc['story']}/play", json={"sid": sid, "history": list(history)})
    secs = round(time.time() - t0, 1)
    if r.status_code != 200:
        print(f"turn {i} FAILED {r.status_code}: {r.text[:200]}", flush=True)
        history.pop()
        break
    body = r.json()
    reply = body.get("reply", "")
    history.append({"role": "narrator", "text": reply})
    turns.append({"turn": i, "user": user, "reply": reply, "pov": body.get("pov", ""),
                  "present": body.get("present", []), "lanes": body.get("lanes"), "secs": secs})
    timings.append(secs)
    done = len(turns) >= len(sc["turns"])
    print(f"turn {i}: {secs}s ({len(reply)} chars) done={done}", flush=True)
    ckpt = {"scenario": scenario, "sid": sid, "history": history, "turns": turns, "timings": timings}
    out_path.write_text(json.dumps(ckpt, ensure_ascii=False, indent=1), encoding="utf-8")
    if not done and (time.time() - start) > budget:
        print(f"budget exhausted after turn {i}; re-run to continue", flush=True)
        break

if done:
    print("all turns played — scoring…", flush=True)
    ctx = build_context(".")
    roles = json.loads((Path("configs") / "text_roles.json").read_text(encoding="utf-8"))
    critic = ctx.text_provider_for("anthropic/claude-sonnet-4-5", {})
    bench.score_turns(critic, turns)
    report = {"writer": f"narrator={roles.get('narrator')} director={roles.get('director')} "
                        f"scribe={roles.get('scribe')}",
              "critic": "anthropic/claude-sonnet-4-5", "ts": int(time.time()), "scenario": scenario,
              "sid": sid, "turns": turns,
              "checks": bench.deterministic_checks(sc, turns),
              "aggregate": bench.aggregate(turns),
              "turn_secs": timings}
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    agg, chk = report["aggregate"], report["checks"]
    print("== SUMMARY ==")
    print("axes:", " ".join(f"{k[:4]}={agg[k]['mean']}(min {agg[k]['min']})" for k in bench.AXES if k in agg))
    print(f"pov_stable={chk['pov_stable']} bleed={chk['cast_bleed']} recalls={chk['recalls']}")
    if timings:
        print(f"latency: mean={sum(timings)/len(timings):.1f}s min={min(timings)}s max={max(timings)}s "
              f"total={sum(timings):.0f}s")
    print(f"report → {out_path}")
