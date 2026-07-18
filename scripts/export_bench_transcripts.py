"""Export the three narrator bench reports into one readable markdown transcript."""
import ast
import json

AXES = ["voice", "legibility", "plainness", "withholding",
        "pulse", "pov_discipline", "continuity", "causality"]
MODELS = [
    ("GLM-5.2 (`z-ai/glm-5.2`)", "configs/bench_glm_firstday.json"),
    ("Kimi-K3 (`moonshotai/kimi-k3`)", "configs/bench_kimi_firstday.json"),
    ("Sonnet-4.5 (`anthropic/claude-sonnet-4-5`)", "configs/bench_sonnet_firstday.json"),
]

out = [
    "# Evergreen — Day One narrator shootout: full transcripts",
    "",
    "Three narrators walked the same 11-turn first day. Critic: `anthropic/claude-sonnet-4-5`, axes 0–5.",
    "",
    "- Scenario: `configs/bench_first_day.json`",
    "- Raw reports: `configs/bench_glm_firstday.json`, `configs/bench_kimi_firstday.json`, `configs/bench_sonnet_firstday.json`",
    "",
]

for name, path in MODELS:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    secs = [t["secs"] for t in d["turns"]]
    out.append("---")
    out.append("")
    out.append(f"## {name}")
    out.append("")
    out.append(f"Latency: mean {sum(secs)/len(secs):.1f}s/turn, total {sum(secs):.0f}s")
    out.append("")
    out.append(f"Checks: `{json.dumps(d.get('checks'), ensure_ascii=False)}`")
    out.append("")
    for t in d["turns"]:
        sc = ast.literal_eval(t["scores"]) if isinstance(t["scores"], str) else t["scores"]
        present = ast.literal_eval(t["present"]) if isinstance(t["present"], str) else t["present"]
        sc_txt = " ".join(f"{a[:4]}={sc.get(a, 0)}" for a in AXES)
        out.append(f"### Turn {t['turn']} — present: `{present}` — {t['secs']}s")
        out.append("")
        out.append(f"**You:** {t['user']}")
        out.append("")
        out.append(t["reply"])
        out.append("")
        out.append(f"`{sc_txt}`")
        out.append("")

with open("configs/bench_transcripts.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
print("written", len(out), "blocks")
