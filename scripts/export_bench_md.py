"""Export any bench report JSON to a readable markdown transcript (JSON is for machines).

Run: python scripts/export_bench_md.py configs/bench_glm52_firstday_sceneload.json [title]
Writes <report_stem>.md beside the report.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

AXES = ["voice", "legibility", "plainness", "withholding",
        "pulse", "pov_discipline", "continuity", "causality"]


def main() -> None:
    path = Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else path.stem
    d = json.loads(path.read_text(encoding="utf-8"))
    agg = d.get("aggregate") or {}
    means = {k: v["mean"] for k, v in agg.items() if isinstance(v, dict) and "mean" in v}
    out = [
        f"# {title}",
        "",
        f"- Writer/config: `{d.get('writer')}`  ·  critic: `{d.get('critic')}`",
        f"- Scenario: `{d.get('scenario')}`  ·  sid `{d.get('sid')}`",
        f"- Aggregate means: `{json.dumps(means)}`" if means else "",
        f"- Checks: `{json.dumps(d.get('checks'), ensure_ascii=False)}`",
        "",
    ]
    for t in d.get("turns") or []:
        sc = t.get("scores") or {}
        if isinstance(sc, str):
            sc = ast.literal_eval(sc)
        sc_txt = " ".join(f"{a[:4]}={sc.get(a, 0)}" for a in AXES) if sc else "(unscored)"
        lanes = t.get("lanes") or {}
        scx = lanes.get("scene_ctx") or {}
        scene_txt = (f" · scene `{scx.get('sig')}`{' boundary' if scx.get('boundary') else ''}"
                     if scx else "")
        out += [f"### Turn {t.get('turn')} — present: `{t.get('present')}` — {t.get('secs')}s{scene_txt}",
                "", f"**You:** {t.get('user')}", "", t.get("reply", ""), "", f"`{sc_txt}`", ""]
    md = path.with_suffix(".md")
    md.write_text("\n".join(out), encoding="utf-8")
    print(f"→ {md}")


if __name__ == "__main__":
    main()
