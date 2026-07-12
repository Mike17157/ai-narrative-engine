"""Throwaway: generate a Rewrite-SPIRIT cast via the nakige character engine.

Copies Rewrite's DNA (a too-green town over a buried verdict, a hidden war whose soldiers are
ordinary-seeming students with impossible talents) WITHOUT reusing a single name or character, then
runs generate_character on function-level role slots so the engine invents its own wounded people.
Run: python -m scripts.gen_rewrite_spirit
"""
import html as _html
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from loom.server import build_context
from loom.stories.character_engine import generate_cast, generate_history, generate_setting

WORLD = (
    "A small town at the end of a train line that keeps getting slower — one train a day now, and soon "
    "none. The young leave for the cities and don't come back; the shops close one by one; the school "
    "loses a class a year. The people who stay have quietly learned not to get too attached, because "
    "everyone leaves eventually, one way or another. The winters are long and grey. Daylight is school, "
    "part-time shifts at the convenience store, the same faces getting older. Nothing dramatic ever "
    "happens here — and that is almost the hardest part."
)

# Function-level slots (NOT Rewrite's characters), each with a war-connection mode — the cast SPANS the
# spectrum: mundane (private tragedy, war is backdrop — Kotone), touched (an ordinary family caught in a
# war event), heir (a combatant's orphan — Chihaya).
ROLES = [
    ("a cheerful first-year who works the late shift at the convenience store", "mundane"),
    ("the third-year who keeps the half-empty club running long after it stopped mattering", "mundane"),
    ("a classmate everyone quietly assumes will be the next to leave for the city", "mundane"),
]


def show(c: dict) -> None:
    if not c:
        print("  (empty gen)\n")
        return
    print(f"── {c['name']} · {c['persona']}  [{c['archetype']} · {c['mode']}]")
    print(f"   READS AS  {c['reads_as']}")
    print(f"   BACKSTORY {c['backstory']}")
    print(f"   TRAUMA    {c['trauma']}")
    print(f"   BOND      {c['bond']}")
    print(f"   SECRET    {c['secret']}")
    print(f"   LIE       {c['lie']}")
    print(f"   NEED      {c['need']}")
    print(f"   GOAL      {c['goal']}")
    print(f"   ENGINE    {c['engine']}")
    print(f"   PLAYS_OFF {c['plays_off']}")
    for i in c["idiosyncrasies"]:
        print(f"   quirk     {i['quirk']}  →  ({i['hidden_source']})")
    print(f"   REVEAL    {c['reveal']}\n")


def write_html(path: str, world: str, setting: dict, history: list[dict], cast: list[dict]) -> None:
    """Dump the whole generation to a standalone HTML file you can just double-click open."""
    e = _html.escape
    css = ("body{font:15px/1.6 -apple-system,Segoe UI,sans-serif;max-width:820px;margin:2rem auto;"
           "padding:0 1rem;color:#1a1a1a;background:#faf9f6}h1{font-size:20px}h2{font-size:16px;margin:.3rem 0}"
           ".fac{display:flex;gap:10px;margin:1rem 0}.fac div{flex:1;background:#eef;border-radius:8px;"
           "padding:.5rem .7rem;font-size:13px}.card{border:1px solid #ddd;border-radius:12px;"
           "padding:1rem 1.2rem;margin:1rem 0;background:#fff}.lbl{font-size:11px;letter-spacing:.04em;"
           "text-transform:uppercase;color:#999;margin-top:.6rem}.rev{background:#eef;border-radius:8px;"
           "padding:.5rem .7rem;margin-top:.5rem}.mode{font-size:11px;color:#888;float:right}"
           ".persona{font-style:italic;color:#444}.src{color:#667}")
    p = [f"<!doctype html><meta charset=utf-8><title>Cast</title><style>{css}</style>",
         f"<h1>{e(setting['question'])}</h1><div class=fac>"
         + "".join(f"<div><b>{e(f['NAME'])}</b><br>{e(f['STANCE'])}</div>" for f in setting["factions"]) + "</div>"]
    if history:
        p.append("<div class=lbl>history</div><ul>"
                 + "".join(f"<li>{e(h['WHEN'])}: <b>{e(h['EVENT'])}</b> — {e(h['WHAT'])}</li>" for h in history) + "</ul>")
    for c in cast:
        p.append(f"<div class=card><span class=mode>{e(c['mode'])} · {e(c.get('event') or 'unrelated')}</span>"
                 f"<h2>{e(c['name'])}</h2><p class=persona>{e(c['persona'])}</p>"
                 f"<div class=lbl>backstory</div>{e(c['backstory'])}<div class=lbl>trauma</div>{e(c['trauma'])}"
                 f"<div class=lbl>reads as</div>{e(c['reads_as'])}")
        for q in c["idiosyncrasies"]:
            p.append(f"<div class=lbl>quirk</div>{e(q['quirk'])} <span class=src>// {e(q['hidden_source'])}</span>")
        p.append(f"<div class=rev><b>reveal:</b> {e(c['reveal'])}</div></div>")
    Path(path).write_text("".join(p), encoding="utf-8")


def main() -> None:
    ctx = build_context(Path("."))
    prov = ctx.text_provider_for("minimax/minimax-m3", {"reasoning_effort": "low"})
    bprov = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "low"})   # prose + cheap large-context
    print("WORLD SEED (Rewrite-spirit, no names):\n" + WORLD + "\n")
    setting = generate_setting(prov, world=WORLD)
    question, factions = setting["question"], setting["factions"]
    print("CENTRAL QUESTION (the verdict the whole world argues):\n  " + question + "\n")
    print("FACTIONS (one-word stances = the two answers):")
    for f in factions:
        print(f"  · {f.get('NAME','')} — {f.get('STANCE','')}")
    print()
    history = None
    if {m for _, m in ROLES} - {"mundane"}:              # only war-connected casts need an event timeline
        history = generate_history(prov, world=WORLD, factions=factions, question=question, n=len(ROLES) + 2)
        print("WORLD HISTORY (each backstory is a sub-story nested in one episode):")
        for e in history:
            print(f"  · {e.get('WHEN','')}: {e.get('EVENT','')} — {e.get('WHAT','')}")
        print()
    t0 = time.time()
    cast = generate_cast(prov, world=WORLD, roles=ROLES, history=history, factions=factions,
                         fantastical=False, question=question, backstory_provider=bprov,
                         ensemble=["childhood friend", "kuudere", "deadpan loner"])
    dt = time.time() - t0
    for c in cast:
        show(c)
    write_html("configs/last_cast.html", WORLD, setting, history, cast)
    print(f"[cast of {len(cast)} generated in {dt:.0f}s wall — characters ran concurrently]")
    print("→ wrote configs/last_cast.html (double-click to view)")


if __name__ == "__main__":
    main()
