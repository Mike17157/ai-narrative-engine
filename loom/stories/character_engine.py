"""The NAKIGE character engine — build a character from the WOUND up, in STAGED prose passes.

Key-style (Clannad, Little Busters): the surface is a lovable, recognisable TYPE made of funny bits,
and every bit is secretly a scar. Build from the wound up so the charm and the grief are one material;
the reveal turns the laugh into the cry.

Why STAGED (four focused calls, not one blob):
  1. different aspects are different cognitive MODES — trauma-writing, then psychology, then comedy,
     then the turn. One call makes the model context-switch mid-answer and dilutes all of them; one
     call per mode goes deeper on each.
  2. each call is light and PROSE (labeled text, no json_schema), so nothing chokes the reasoning
     model into an empty — the reliability comes from the shape of the work, not a weaker model.
Each pass is fed the prior ones, so the chain stays coherent. Self-check: python -m loom.stories.character_engine
"""
from __future__ import annotations

from .labeled import parse_labeled, run_text

# ── Four focused passes, one cognitive mode each; each asks for labeled prose, never JSON ──────────
_WOUND_SYS = (
    "You give a story character ONE specific WOUND to build them from: a concrete traumatic EVENT, "
    "plainly named — a thing that happened, not a diagnosis. Not 'abandonment issues' but 'the winter "
    "her mother went to work and the house burned down before she got home.' Also give a fitting NAME.\n"
    "Output exactly two labelled lines, nothing else:\n"
    "NAME: <name>\n"
    "WOUND: <one concrete event, 1-3 plain sentences>"
)
_PSYCH_SYS = (
    "From a character's WOUND, derive their psychology. LIE: the false belief the wound installed, in "
    "their own words (first person). NEED: what would actually heal them — the thing the lie forbids. "
    "GOAL: their conscious want, a displaced substitute for the need that is safe to chase in daylight. "
    "ENGINE: the coping they run EVERY day to keep the wound sealed.\n"
    "Output exactly these labelled lines, nothing else:\n"
    "LIE: <...>\nNEED: <...>\nGOAL: <...>\nENGINE: <...>"
)
_SURFACE_SYS = (
    "Give a character a COMIC surface built out of their wound. ARCHETYPE: a recognisable personality "
    "type in one line (tsundere, genki optimist, deadpan, gremlin, mum-friend, delinquent-with-a-heart, "
    "try-hard, chuuni). TWIST: one specific way THIS person breaks the type, so they are not a cliché. "
    "PLAYS_OFF: the temperament they clash or click with, and the running bit it produces. "
    "IDIOSYNCRASIES: 2-3 FUNNY, SOCIAL bits — opaque comedy the reader enjoys with NO visible sadness, "
    "that rope in or spark off other people. Write each as '<the funny bit> // <the hidden piece of the "
    "wound it is secretly made of>'.\n"
    "The laugh is load-bearing for the later cry: if the sadness shows on the surface you have spoiled "
    "the punchline. Output exactly these labelled lines, nothing else:\n"
    "ARCHETYPE: <type>\nTWIST: <...>\nPLAYS_OFF: <...>\nIDIOSYNCRASIES:\n"
    "<funny bit> // <hidden source>\n<funny bit> // <hidden source>"
)
_REVEAL_SYS = (
    "Given a character's WOUND and their funny surface bits, write the REVEAL: how, at the story's turn, "
    "ONE bit re-reads as the wound — the thing the reader laughed at becomes the thing they weep at. "
    "Name the exact re-contextualisation.\n"
    "Output one labelled line:\nREVEAL: <...>"
)


def generate_character(provider, *, world: str, role: str) -> dict:
    """Build one nakige character through four chained prose passes. Returns the full dict, or {} if the
    first pass yields no wound (nothing to build on)."""
    ctx = f"WORLD: {world}\nROLE IN THE STORY: {role}\n"

    ident = parse_labeled(run_text(provider, _WOUND_SYS, ctx), ["NAME", "WOUND"])
    if not ident.get("WOUND"):
        return {}
    ctx += f"NAME: {ident['NAME']}\nWOUND: {ident['WOUND']}\n"

    psych = parse_labeled(run_text(provider, _PSYCH_SYS, ctx), ["LIE", "NEED", "GOAL", "ENGINE"])

    surf = parse_labeled(
        run_text(provider, _SURFACE_SYS, ctx + f"LIE: {psych['LIE']}\nGOAL: {psych['GOAL']}\n"),
        ["ARCHETYPE", "TWIST", "PLAYS_OFF", "IDIOSYNCRASIES"])
    idios = []
    for line in (surf.get("IDIOSYNCRASIES") or "").splitlines():
        if "//" in line:
            quirk, _, hidden = line.partition("//")
            quirk = quirk.strip(" -*•\t")
            if quirk:
                idios.append({"quirk": quirk, "hidden_source": hidden.strip()})

    reveal = parse_labeled(
        run_text(provider, _REVEAL_SYS, ctx + "SURFACE BITS:\n" + "\n".join(i["quirk"] for i in idios)),
        ["REVEAL"])

    return {"name": ident["NAME"], "archetype": surf["ARCHETYPE"], "twist": surf["TWIST"],
            "wound": ident["WOUND"], "lie": psych["LIE"], "need": psych["NEED"], "goal": psych["GOAL"],
            "engine": psych["ENGINE"], "idiosyncrasies": idios, "plays_off": surf["PLAYS_OFF"],
            "reveal": reveal["REVEAL"]}


def demo() -> None:
    # Stub returns the right labeled block per pass (dispatched on a distinctive phrase in the system).
    class _Stub:
        def generate_text(self, *, system, prompt):
            class _R: pass
            r = _R()
            if "ONE specific WOUND" in system:
                r.text = "NAME: Mira\nWOUND: the winter the boat did not come back"
            elif "derive their psychology" in system:
                r.text = "LIE: nobody stays\nNEED: to be kept without earning it\nGOAL: be indispensable\nENGINE: over-helping"
            elif "COMIC surface" in system:
                r.text = ("ARCHETYPE: gremlin\nTWIST: secretly meticulous\nPLAYS_OFF: the deadpan\n"
                          "IDIOSYNCRASIES:\nsteals everyone's spoons // to feel she holds the household together\n"
                          "narrates people's feelings for them // because no one narrated hers")
            else:
                r.text = "REVEAL: the spoon hoard is every meal she was not there for"
            return r

    c = generate_character(_Stub(), world="a coast", role="a girl at the harbour")
    assert c["name"] == "Mira" and c["archetype"] == "gremlin"
    assert len(c["idiosyncrasies"]) == 2
    assert c["idiosyncrasies"][0]["hidden_source"] == "to feel she holds the household together"
    assert c["goal"] == "be indispensable" and c["reveal"].startswith("the spoon hoard")
    assert generate_character(object(), world="w", role="r") == {}   # no provider → {}, no crash
    # theory lives in the focused passes, one mode each
    assert "ONE specific WOUND" in _WOUND_SYS and "load-bearing" in _SURFACE_SYS and "re-reads" in _REVEAL_SYS
    print("ok — character_engine: 4 staged prose passes (no JSON) chained + tolerant parse")


if __name__ == "__main__":
    demo()
