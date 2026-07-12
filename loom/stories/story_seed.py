"""Card -> MATURE SEED: turn an imported character card into a story foundation you can roleplay from.

Chains the existing depth engine (loom.stories.character_engine), CARD-FIRST: read the card -> infer the
one canonical archetype that fits -> derive the grounded WORLD + the quiet CENTRAL QUESTION the character
anchors -> deepen them into a continuous backstory (the archetype's characteristic wound instantiated in
that world, on a strong model) -> psych -> an OPENING scene. The result is enough to start an ongoing
roleplay, and later (the transcript->story converter) to bake into an illustrated story.

The heavy passes (`character_engine`) already exist; this only orchestrates them from a card instead of a
world seed. Self-check: python -m loom.stories.story_seed
"""
from __future__ import annotations

from .character_engine import _ARCHETYPES, _CLINICAL, _PSYCH_SYS, _backstory_sys, _pass
from .labeled import parse_labeled, run_text


def _clin(body: str) -> str:
    return _CLINICAL + "\n\n" + body


_ARCHETYPE_SYS = (
    "Read the character card. Choose the ONE canonical archetype that best fits this character from this "
    "list: " + ", ".join(_ARCHETYPES) + ".\nOutput one line, nothing else:\nARCHETYPE: <one of the list>"
)

_SEED_SYS = (
    "From the character card, establish a STORY FOUNDATION to roleplay from. Give a grounded WORLD (the "
    "situation and stage this character lives in, 2-3 plain sentences, true to the card's scenario) and "
    "the CENTRAL QUESTION the story quietly asks — a lived human feeling a reader recognises (loneliness, "
    "wanting to belong, being afraid to be happy, loving something you know you'll lose). It must NOT be a "
    "dramatic abstraction or a debate prompt; a plain feeling, answered by people, understated.\n"
    "Output exactly, nothing else:\nWORLD: <...>\nQUESTION: <...>"
)

_OPENING_SYS = (
    "Write the OPENING scene of an ongoing story to roleplay from — a specific first moment that puts "
    "THIS character in motion in their world, faithful to the card's scenario, in a quiet grounded "
    "register. 3-5 sentences, ending on a beat that invites the other person (the player) to respond. "
    "Output one line:\nOPENING: <...>"
)


def build_seed(provider, *, card: str, backstory_provider=None) -> dict:
    """Turn a character card (name + description + scenario, as text) into a MATURE SEED. `provider` runs
    the light passes; `backstory_provider` (a stronger model, optional) writes the continuous backstory.
    Returns the seed dict, or {} if the card yields no usable trauma."""
    cctx = f"CHARACTER CARD:\n{card.strip()}\n"

    # 1. infer the canonical archetype (fall back to a safe default off-list)
    a = parse_labeled(run_text(provider, _clin(_ARCHETYPE_SYS), cctx), ["ARCHETYPE"]).get("ARCHETYPE", "")
    name = a.strip().lower()
    name = name if name in _ARCHETYPES else "childhood friend"
    arche = _ARCHETYPES[name]

    # 2. world + the quiet central question, seeded by the card
    ws = parse_labeled(run_text(provider, _clin(_SEED_SYS), cctx), ["WORLD", "QUESTION"])
    world, question = ws.get("WORLD", ""), ws.get("QUESTION", "")

    # 3. deepen: reverse-derive the archetype's characteristic wound through THIS world, card as the face
    bctx = (f"WORLD: {world}\nCENTRAL QUESTION: {question}\n{cctx}"
            f"ARCHETYPE: {name}\nCHARACTERISTIC WOUND (instantiate THIS in the world): {arche['wound']}\n"
            "Keep the character's NAME and surface faithful to the card; the backstory is what's underneath.\n")
    ident = _pass(backstory_provider or provider, _clin(_backstory_sys("mundane")), bctx,
                  ["NAME", "BACKSTORY", "TRAUMA", "BOND", "SECRET"], ["NAME", "BACKSTORY", "TRAUMA"])
    if not ident.get("TRAUMA"):
        return {}

    # 4. psych, built around the archetype's coping
    pctx = (f"WORLD: {world}\nNAME: {ident['NAME']}\nBACKSTORY: {ident['BACKSTORY']}\n"
            f"TRAUMA: {ident['TRAUMA']}\nSECRET: {ident['SECRET']}\n"
            f"ASSIGNED COPING (build ENGINE around this, do NOT default to control): {arche['coping']}\n")
    psych = parse_labeled(run_text(provider, _clin(_PSYCH_SYS), pctx), ["LIE", "NEED", "GOAL", "ENGINE"])

    # 5. an opening scene to roleplay from
    octx = f"WORLD: {world}\n{cctx}BACKSTORY: {ident['BACKSTORY']}\n"
    opening = parse_labeled(run_text(provider, _clin(_OPENING_SYS), octx), ["OPENING"]).get("OPENING", "")

    return {"name": ident["NAME"], "archetype": name, "world": world, "question": question,
            "backstory": ident["BACKSTORY"], "trauma": ident["TRAUMA"], "bond": ident["BOND"],
            "secret": ident["SECRET"], "lie": psych["LIE"], "need": psych["NEED"], "goal": psych["GOAL"],
            "engine": psych["ENGINE"], "opening": opening}


def demo() -> None:
    class _Stub:
        def generate_text(self, *, system, prompt):
            class _R: pass
            r = _R()
            assert "DOSSIER" in system
            if "canonical archetype" in system:
                r.text = "ARCHETYPE: kuudere"
            elif "STORY FOUNDATION" in system:
                r.text = "WORLD: a winding-down hospital in a shrinking town\nQUESTION: is it worth caring for a place that's leaving anyway"
            elif "emotional formation that made them" in system:
                r.text = ("NAME: Rin\nBACKSTORY: she trained here when the ward was full and warm\n"
                          "TRAUMA: the night the last of her mentors was transferred out without a goodbye\n"
                          "BOND: the empty night ward she keeps immaculate\nSECRET: she rehearses handovers to no one")
            elif "maladaptive core belief" in system:
                r.text = "LIE: caring only makes the leaving worse\nNEED: to be stayed with\nGOAL: keep the ward perfect\nENGINE: withdrawal"
            else:
                r.text = "OPENING: The ward is dark except for the nurses' station. She looks up as you come in, already knowing your name."
            return r

    seed = build_seed(_Stub(), card="Name: Rin\nDescription: a cold night-shift nurse")
    assert seed["name"] == "Rin" and seed["archetype"] == "kuudere"
    assert seed["question"].startswith("is it worth") and seed["engine"] == "withdrawal"
    assert seed["backstory"].startswith("she trained") and seed["opening"].startswith("The ward is dark")
    assert build_seed(object(), card="x") == {}                     # no provider -> {}, no crash
    print("ok — story_seed: card -> archetype + world + question + backstory + psych + opening")


if __name__ == "__main__":
    demo()
