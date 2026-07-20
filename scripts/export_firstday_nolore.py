"""Export the no-lorebook first-day bench report into a readable markdown transcript."""
import json

AXES = ["voice", "legibility", "plainness", "withholding",
        "pulse", "pov_discipline", "continuity", "causality"]

with open("configs/bench_glm52_firstday_nolore.json", encoding="utf-8") as fh:
    d = json.load(fh)
agg, chk = d["aggregate"], d["checks"]
means = {k: v["mean"] for k, v in agg.items() if isinstance(v, dict) and "mean" in v}

out = [
    "# Evergreen — Day One (Rewrite-shaped opening): no-lorebook reproduction",
    "",
    "Narrator/director/scribe: `z-ai/glm-5.2` (all roles, the `text_roles.json` max-quality config). "
    "Critic: `anthropic/claude-sonnet-4-5`, axes 0–5.",
    "**All lorebooks deactivated for this run** (46 books / 464 entries disabled; the production "
    "retrieval path was verified to serve 0 hits; only the hardcoded refusal floor remained). "
    "Lorebooks were restored from snapshot afterwards.",
    "",
    "- Scenario: `configs/bench_first_day.json`",
    "- Raw report: `configs/bench_glm52_firstday_nolore.json`",
    f"- Aggregate means: `{json.dumps(means)}`",
    f"- Checks: `{json.dumps(chk, ensure_ascii=False)}`",
    "",
]
for t in d["turns"]:
    sc = t["scores"]
    sc_txt = " ".join(f"{a[:4]}={sc.get(a, 0)}" for a in AXES)
    out += [f"### Turn {t['turn']} — present: `{t['present']}` — {t['secs']}s", "",
            f"**You:** {t['user']}", "", t["reply"], "", f"`{sc_txt}`", ""]

with open("configs/bench_firstday_nolore_transcript.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
print("written", len(out), "blocks")
