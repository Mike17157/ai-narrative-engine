"""Throwaway: turn a character card into a MATURE SEED (a story foundation to roleplay from).
Run: python -m scripts.seed_from_card
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from loom.server import build_context
from loom.stories.world.creation import build_seed

# A Janitor-AI-style card (thin, scenario-led) — the kind of seed a user would import.
CARD = """Name: Rin Amasawa
Description: A 24-year-old night-shift nurse at a small failing hospital in a shrinking coastal town.
Composed, precise, a little cold with new people. Wears her hair up, never talks about herself, lives
alone above a closed pharmacy.
Personality: reserved, dutiful, dry humour, quietly kind, avoids attachment.
Scenario: You are a new hire on her ward. The hospital is being wound down and most patients have been
transferred out. Tonight it's just the two of you on the late shift."""


def main() -> None:
    ctx = build_context(Path("."))
    prov = ctx.text_provider_for("minimax/minimax-m3", {"reasoning_effort": "low"})
    bprov = ctx.text_provider_for("deepseek/deepseek-v4-pro", {"reasoning_effort": "low"})   # prose + cheap large-context
    print("CARD (imported):\n" + CARD + "\n")
    t0 = time.time()
    s = build_seed(prov, card=CARD, backstory_provider=bprov)
    dt = time.time() - t0
    if not s:
        print("(empty seed)")
        return
    print(f"── {s['name']}  [{s['archetype']}]")
    print(f"   WORLD     {s['world']}")
    print(f"   QUESTION  {s['question']}")
    print(f"   BACKSTORY {s['backstory']}")
    print(f"   TRAUMA    {s['trauma']}")
    print(f"   BOND      {s['bond']}")
    print(f"   SECRET    {s['secret']}")
    print(f"   LIE       {s['lie']}")
    print(f"   NEED      {s['need']}")
    print(f"   GOAL      {s['goal']}")
    print(f"   ENGINE    {s['engine']}")
    print(f"\n   OPENING   {s['opening']}")
    print(f"\n[seed built in {dt:.0f}s]")


if __name__ == "__main__":
    main()
