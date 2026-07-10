"""CAST DYNAMICS — an ensemble as a SYSTEM OF FRICTIONS, generated in STAGED prose passes.

Entertainment is a property of the cast, not the individual: comedy comes from CONTRASTING recognisable
TYPES colliding (the optimist vs the cynic, the zealot vs the gremlin). So build from types, and build
the collisions on purpose. The anti-cliché guard is per-member: a specific WOUND (why this person is
this type) and a TWIST (where they break it). And the nakige knife: the SAME friction that entertains
early is the shape of the absence late — every dynamic carries `friction` (comedy) and `turns_to` (grief).

Why STAGED instead of one nested blob (the blob was the schema that kept emptying):
  • Pass 1 — a light SKELETON: the members as contrasting types (labeled records, no JSON).
  • Pass 2 — FAN OUT: each clashing pair gets its own tiny `friction`/`turns_to` call.
A dozen small prose calls don't choke where one heavy nested json_schema did. The per-pair calls are
independent, so a caller can run them in parallel. Self-check: python -m loom.stories.cast_dynamics
"""
from __future__ import annotations

from .labeled import parse_labeled, parse_records, run_text

_SKELETON_SYS = (
    "Design an ENSEMBLE as a SYSTEM OF FRICTIONS built from CONTRASTING recognisable personality TYPES "
    "(tsundere, genki optimist, deadpan cynic, gremlin, rich-girl, delinquent-with-a-heart, mum-friend, "
    "himbo, try-hard, chuuni, airhead, stoic). Pick types that COLLIDE. For each member give:\n"
    "NAME, ARCHETYPE (the type, one line), TWIST (one way THIS person breaks the type so they're not a "
    "cliché), COMIC_BIT (a funny bit that sparks off the others, with NO visible sadness), WOUND (brief: "
    "why they became this type — the mask over the wound).\n"
    "The type is the on-ramp; the specific wound and twist make it a person. Output ONE RECORD PER MEMBER, "
    "records separated by a line containing only '---'. Each record is labelled lines:\n"
    "NAME: ...\nARCHETYPE: ...\nTWIST: ...\nCOMIC_BIT: ...\nWOUND: ..."
)
_DYNAMIC_SYS = (
    "Two characters collide. FRICTION: the type-vs-type comic clash and the RUNNING BIT it produces "
    "(their double act, rivalry, or odd-couple routine) — the entertainment, opaque comedy. TURNS_TO: how "
    "that SAME bit becomes the shape of grief or absence when one of them is gone — the nakige knife. The "
    "funnier the friction, the harder the fall.\n"
    "Output exactly two labelled lines:\nFRICTION: <...>\nTURNS_TO: <...>"
)


def design_cast(provider, *, world: str, size: int = 4) -> dict:
    """Design an ensemble as a system of frictions: a skeleton pass (contrasting types) topped up until we
    hit `size`, then a fanned pass per clashing pair. Returns {cast:[...], dynamics:[...]} (or {} if empty)."""
    keys = ["NAME", "ARCHETYPE", "TWIST", "COMIC_BIT", "WOUND"]
    records: list[dict] = []
    seen: set[str] = set()
    for _ in range(4):                          # skeleton, then top-up rounds until we reach `size`
        if not records:
            prompt = f"WORLD: {world}\nDesign EXACTLY {size} ensemble members, no more, no fewer.\n"
        else:
            have = "; ".join(f"{m['NAME']} ({m['ARCHETYPE']})" for m in records)
            prompt = (f"WORLD: {world}\nALREADY DESIGNED (do NOT repeat these names or types): {have}\n"
                      f"Add EXACTLY {size - len(records)} MORE contrasting members that collide with them.\n")
        for r in parse_records(run_text(provider, _SKELETON_SYS, prompt), keys):
            nm = (r.get("NAME") or "").strip()
            if nm and nm.lower() not in seen:
                records.append(r)
                seen.add(nm.lower())
        if len(records) >= size:
            break
    records = records[:size]
    if not records:
        return {}
    cast = [{k.lower(): v for k, v in r.items()} for r in records]

    dynamics = []
    for i in range(len(cast)):
        for j in range(i + 1, len(cast)):
            a, b = cast[i], cast[j]
            d = parse_labeled(run_text(
                provider, _DYNAMIC_SYS,
                f"WORLD: {world}\n"
                f"A: {a['name']} — {a['archetype']} — {a['comic_bit']}\n"
                f"B: {b['name']} — {b['archetype']} — {b['comic_bit']}\n"), ["FRICTION", "TURNS_TO"])
            if d.get("FRICTION"):
                dynamics.append({"between": [a["name"], b["name"]],
                                 "friction": d["FRICTION"], "turns_to": d["TURNS_TO"]})
    return {"cast": cast, "dynamics": dynamics}


def demo() -> None:
    class _Stub:
        def __init__(self): self.n = 0
        def generate_text(self, *, system, prompt):
            class _R: pass
            r = _R()
            if "SYSTEM OF FRICTIONS" in system:
                self.n += 1                                  # skeleton returns 2; top-up fills the 3rd
                if self.n == 1:
                    r.text = ("NAME: Mari\nARCHETYPE: genki\nTWIST: t\nCOMIC_BIT: b\nWOUND: w\n---\n"
                              "NAME: Vann\nARCHETYPE: deadpan\nTWIST: t\nCOMIC_BIT: b\nWOUND: w")
                else:
                    r.text = "NAME: Grem\nARCHETYPE: gremlin\nTWIST: t\nCOMIC_BIT: b\nWOUND: w"
            else:
                r.text = "FRICTION: f\nTURNS_TO: she does the bit alone"
            return r

    stub = _Stub()
    out = design_cast(stub, world="a ship", size=3)
    assert [m["name"] for m in out["cast"]] == ["Mari", "Vann", "Grem"]   # top-up filled 2 -> 3
    assert len(out["dynamics"]) == 3                         # C(3,2) = 3 pairs, fanned
    assert out["dynamics"][-1]["turns_to"] == "she does the bit alone"
    assert design_cast(object(), world="x") == {}           # no provider → {}, no crash
    assert "CONTRASTING recognisable personality TYPES" in _SKELETON_SYS and "nakige knife" in _DYNAMIC_SYS
    print("ok — cast_dynamics: skeleton + top-up-to-size + fanned per-pair dynamics (no JSON)")


if __name__ == "__main__":
    demo()
