"""Per-scene bench readout — the scene (not the turn) as the unit of measurement.

Reads a bench report (bench_m3_inproc format: turns[] with lanes/scores/reply), groups
turns into SCENES by the scene-context signature the loader stamped into lanes["scene_ctx"],
and reports per scene: turns, how much context was carried verbatim vs recomputed, the
carry rate, and mean critic scores. Older reports (no scene_ctx lanes) get a byte-growth
summary only.

Run: python scripts/bench_scene_report.py configs/bench_glm52_firstday_nolore.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

AXES = ["voice", "legibility", "plainness", "withholding", "pulse",
        "pov_discipline", "continuity", "causality"]


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "configs/bench_glm52_firstday_nolore.json")
    d = json.loads(path.read_text(encoding="utf-8"))
    turns = d.get("turns") or []
    if not turns:
        raise SystemExit(f"no turns in {path}")

    have_sc = any(isinstance((t.get("lanes") or {}).get("scene_ctx"), dict) for t in turns)
    if not have_sc:
        sizes = [int((t.get("lanes") or {}).get("system") or 0) for t in turns]
        print(f"{path.name}: pre-scene-loader report (no lanes.scene_ctx).")
        if sizes:
            print(f"  system bytes/turn: first={sizes[0]} last={sizes[-1]} "
                  f"mean={sum(sizes)//len(sizes)} (rebuilt in full every turn)")
        raise SystemExit(0)

    # group consecutive turns by scene signature
    scenes: list[dict] = []
    for t in turns:
        sc = (t.get("lanes") or {}).get("scene_ctx") or {}
        sig = sc.get("sig") or "?"
        if not scenes or scenes[-1]["sig"] != sig:
            scenes.append({"sig": sig, "turns": []})
        scenes[-1]["turns"].append(t)

    print(f"{path.name}: {len(turns)} turns across {len(scenes)} scenes\n")
    tot = {"cb": 0, "rb": 0, "cn": 0, "rn": 0}
    for i, scn in enumerate(scenes):
        ts = scn["turns"]
        carried = sum(int((t.get("lanes") or {}).get("scene_ctx", {}).get("carried_bytes") or 0) for t in ts)
        recomputed = sum(int((t.get("lanes") or {}).get("scene_ctx", {}).get("recomputed_bytes") or 0) for t in ts)
        carried_n = sum(int((t.get("lanes") or {}).get("scene_ctx", {}).get("carried") or 0) for t in ts)
        recomputed_n = sum(int((t.get("lanes") or {}).get("scene_ctx", {}).get("recomputed") or 0) for t in ts)
        tot["cb"] += carried; tot["rb"] += recomputed; tot["cn"] += carried_n; tot["rn"] += recomputed_n
        scored = [t["scores"] for t in ts if isinstance(t.get("scores"), dict)
                  and all(k in t["scores"] for k in AXES)]
        means = {a: round(sum(s[a] for s in scored) / len(scored), 1) for a in AXES} if scored else {}
        rate = carried / (carried + recomputed) if (carried + recomputed) else 0.0
        tids = [t["turn"] for t in ts]
        print(f"scene {i}  sig={scn['sig']}  turns={tids}")
        print(f"  carried={carried_n} blk/{carried}B recomputed={recomputed_n} blk/{recomputed}B"
              f"  carry-rate={rate:.0%}")
        if means:
            print("  scores: " + " ".join(f"{a[:4]}={v}" for a, v in means.items()))
    rate = tot["cb"] / (tot["cb"] + tot["rb"]) if (tot["cb"] + tot["rb"]) else 0.0
    print(f"\nTOTAL: carried={tot['cn']} blk/{tot['cb']}B recomputed={tot['rn']} blk/{tot['rb']}B"
          f"  carry-rate={rate:.0%}")


if __name__ == "__main__":
    main()
