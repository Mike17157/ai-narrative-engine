"""Print the EXACT system+user every character-engine pass sends — offline, no model, no network.

Proves what actually reaches the provider: a capturing fake records every generate_text(system, prompt),
returns a minimal valid stub so the chain proceeds, and we print each payload verbatim. If a lorebook or
any hidden preamble were leaking in, it would appear here. Run: python -m scripts.dump_payloads
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from loom.stories.character_engine import generate_cast, generate_history


class Capture:
    def __init__(self):
        self.calls = []

    def generate_text(self, *, system, prompt):
        self.calls.append((system, prompt))
        class _R: pass
        r = _R()
        s = system
        if "timeline of a WAR" in s:
            r.text = "WHEN: 20 years ago\nEVENT: The Raid\nWHAT: they burned the ledgers"
        elif "emotional formation that made them" in s:
            r.text = ("NAME: Test\nBACKSTORY: a friendship that ended badly one summer\n"
                      "TRAUMA: age 9, the friend stopped answering\nBOND: the shop where they met\nSECRET: keeps a map")
        elif "maladaptive core belief" in s:
            r.text = "LIE: none stay\nNEED: to be kept\nGOAL: be useful\nENGINE: control"
        elif "defining WAY OF BEING" in s:
            r.text = "PERSONA: a chuuni\nREADS_AS: the weird kid\nPLAYS_OFF: y\nQUIRKS:\na // \nc // "
        else:
            r.text = "REVEAL: z"
        return r


def main():
    cap = Capture()
    WORLD = "a green town with two hidden orders that take people"
    hist = generate_history(cap, world=WORLD, n=2)
    generate_cast(cap, world=WORLD,
                  roles=[("a first-year", "mundane"), ("a captain", "heir")], history=hist)

    for i, (system, prompt) in enumerate(cap.calls):
        print("=" * 90)
        print(f"CALL {i}")
        print("--- SYSTEM " + "-" * 79)
        print(system)
        print("--- USER (prompt) " + "-" * 72)
        print(prompt)
        print()


if __name__ == "__main__":
    main()
