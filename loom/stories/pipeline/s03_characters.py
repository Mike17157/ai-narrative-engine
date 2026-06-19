"""Step 03 — Characters: distill protagonist card, revise a character, extract supporting cast."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from ._helpers import (
    PROTAGONIST_SCHEMA, ROSTER_SCHEMA, _APPEARANCE_RULE,
    _call, _card_context, _sys,
)


def _name_tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def extract_protagonist(provider, *, name: str, persona: str, extras: dict | None = None,
                        systems: dict | None = None, on_event=None) -> dict:
    """Distill the imported source card into ONE clean base character card.

    Returns { name, persona, appearance, role }.
    `on_event` (optional): stream {type:phase|delta} so a UI can watch it."""
    if on_event:
        on_event({"type": "phase", "label": f"Distilling the base card — {name}"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    card = _card_context(name, persona, extras or {})
    out = _call(provider, _sys(systems or {}, "protagonist"),
                f"{card}\n\nNormalize this into ONE clean base character card for the main character.",
                PROTAGONIST_SCHEMA, "protagonist", on_delta=dl)
    return {"name": out.get("name") or name, "persona": out.get("persona") or (persona or ""),
            "appearance": out.get("appearance", ""), "role": out.get("role") or "protagonist"}


def revise_character(provider, *, name: str, persona: str, role: str = "", appearance: str = "",
                     instruction: str = "", board: dict | None = None,
                     systems: dict | None = None, on_event=None) -> dict:
    """Re-derive ONE existing cast member's card with a user CHANGE applied.

    `instruction` is the user's free-text steer; empty = faithful refresh.
    Returns { name, persona, appearance, role }."""
    if on_event:
        on_event({"type": "phase", "label": f"Rewriting {name}"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    logline = (board or {}).get("logline", "")
    current = "\n\n".join(p for p in [
        f"CHARACTER NAME: {name}",
        f"CURRENT ROLE: {role}" if role else "",
        f"CURRENT PERSONA:\n{persona}" if persona else "",
        f"CURRENT APPEARANCE: {appearance}" if appearance else "",
        f"STORY LOGLINE: {logline}" if logline else "",
    ] if p)
    change = (f"APPLY THIS CHANGE: {instruction.strip()}" if instruction and instruction.strip()
              else "Refresh and sharpen this character while staying faithful to who they already are.")
    prompt = (f"{current}\n\n{change}\n\n"
              "Rewrite this ONE character's card with the change applied. Keep everything the change "
              "does NOT touch consistent with who they already are and with the story. Keep the SAME "
              "`name` unless the change explicitly renames them. Give an updated one-phrase `role`. "
              "The `appearance` field (and ONLY that field — the persona stays prose) must follow "
              "this:\n" + _APPEARANCE_RULE)
    out = _call(provider, _sys(systems or {}, "protagonist"), prompt,
                PROTAGONIST_SCHEMA, "characters", on_delta=dl)
    return {"name": out.get("name") or name, "persona": out.get("persona") or persona,
            "appearance": out.get("appearance", ""), "role": out.get("role") or role or "supporting"}


def extract_characters(provider, *, name: str, persona: str, board: dict,
                       extras: dict | None = None,
                       systems: dict | None = None, reference_card: str = "", on_event=None) -> dict:
    """Extract and generate supporting cast cards from the storyboard.

    Returns { npcs: [{name, persona, appearance, role}] }."""
    pl = (name or "").lower().strip()
    ptoks = _name_tokens(pl)
    pfirst = ptoks[0] if ptoks else ""

    def is_protagonist(c: str) -> bool:
        cl = (c or "").lower().strip()
        if not cl:
            return True
        if pl and (pl in cl or cl in pl):
            return True
        return bool(pfirst) and pfirst in _name_tokens(cl)

    _PLACEHOLDERS = {"user", "you", "player", "narrator", "protagonist", "mc", "main character",
                     "me", "self", "reader", "viewer"}
    _GROUP_WORDS = {"everyone", "group", "groups", "crowd", "crowds", "people", "peers", "friends",
                    "students", "classmates", "others", "regulars", "patrons", "staff", "customers",
                    "kids", "guys", "girls", "boys", "men", "women", "family", "team", "gang", "crew",
                    "locals", "strangers", "audience", "villagers", "townsfolk", "onlookers",
                    "bystanders", "mob", "cast", "everybody", "nobody", "someone", "anyone"}

    def is_placeholder(c: str) -> bool:
        cl = (c or "").lower().strip()
        if not cl or "{{" in cl or "}}" in cl:
            return True
        toks = _name_tokens(cl)
        return (cl in _PLACEHOLDERS or "user" in toks
                or any(t in _GROUP_WORDS for t in toks))

    seen, names = set(), []
    for b in board.get("beats", []):
        for c in b.get("characters", []):
            c = (c or "").strip()
            if not c or is_protagonist(c) or is_placeholder(c):
                continue
            toks = _name_tokens(c)
            ftok = toks[0] if toks else c.lower()
            if ftok in seen:
                continue
            seen.add(ftok); names.append(c)
    if not names:
        return {"npcs": []}
    card = _card_context(name, persona, extras or {})
    ref = ""
    if reference_card:
        ref = ("\n\nBASE CHARACTER CARD (the MAIN CHARACTER) — write THIS supporting character "
               "in the EXACT same structure, section headings, and depth:\n" + reference_card)
    sys_p = _sys(systems or {}, "characters")
    logline = board.get("logline", "")

    # PHASE 1 — CAST ROSTER: distinct full names + heritage, decided for the whole cast together.
    roster: list[dict] = []
    try:
        rsys = ("You are casting a story's SUPPORTING characters. Give EACH a distinct full "
                "'First Last' name (keep their given first name, invent a surname) and a short "
                "HERITAGE. The cast must feel like a real, VARIED friend group: a natural MIX of "
                "ethnic backgrounds — NOT all the same, and NOT all matching the protagonist — and "
                "NO two may share a surname. Keep it natural and plausible, not a forced quota.")
        rprompt = (f"PROTAGONIST (already cast — exclude her; the others should NOT all share her "
                   f"background): {name}\nLOGLINE: {logline}\n"
                   "SUPPORTING CHARACTERS (first names):\n" + "\n".join(f"- {n}" for n in names) +
                   "\n\nReturn EACH with a full name + a short heritage (e.g. 'Japanese-American', "
                   "'Nigerian-British', 'white / Irish', 'Mexican', 'Korean', 'Italian-American').")
        if on_event:
            on_event({"type": "phase", "label": "Casting the supporting roster"})
        rdl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
        roster = (_call(provider, rsys, rprompt, ROSTER_SCHEMA, "characters", on_delta=rdl) or {}).get("cast", [])
    except Exception:  # noqa: BLE001
        roster = []
    entries = []
    for i, nm in enumerate(names):
        r = roster[i] if i < len(roster) else {}
        toks = (r.get("name") or "").strip().split()
        surname = toks[-1] if len(toks) >= 2 else ""
        entries.append({"first": nm, "name": (f"{nm} {surname}".strip()),
                        "heritage": (r.get("heritage") or "").strip()})

    # PHASE 2 — one focused full card per character (parallel).
    def _prompt(e: dict) -> str:
        her = f" — heritage: {e['heritage']}" if e["heritage"] else ""
        return (f"{card}\n\nLOGLINE: {logline}\n"
                f"Write the FULL character card for THIS ONE supporting character: {e['name']}{her}." + ref +
                "\n\n- Use EXACTLY this name and heritage — do NOT rename or change the surname.\n"
                "- DEPTH: concise but vivid — a few tight, specific sentences per section in the base "
                "card's structure. Do NOT pad or pile on detail; keep it readable, not bulky.\n"
                "- Reflect the HERITAGE through SKIN TONE + natural hair/eye COLOUR + a characteristic "
                "HAIRSTYLE appropriate to their background. Derive BUILD naturally — don't default to "
                "slim, don't force a body type.\n"
                "Write ONLY this character — not the protagonist, not anyone else.")

    def _one(e: dict):
        if on_event:
            on_event({"type": "phase", "label": f"Writing {e['name']}"})
        try:
            out = _call(provider, sys_p, _prompt(e), PROTAGONIST_SCHEMA, "characters")
        except Exception:  # noqa: BLE001
            return None
        if on_event and out:
            on_event({"type": "item", "name": out.get("name") or e["name"],
                      "text": out.get("appearance", "")})
        return out

    with ThreadPoolExecutor(max_workers=min(len(entries), 6)) as ex:
        results = list(ex.map(_one, entries))

    npcs: list[dict] = []
    seen_out, seen_first = set(), set()
    for e, out in zip(entries, results):
        if not out:
            continue
        nm_out = (out.get("name") or e["name"]).strip()
        nkey = re.sub(r"[^a-z0-9]+", "", nm_out.lower())
        ftoks = _name_tokens(nm_out)
        fkey = ftoks[0] if ftoks else nkey
        if (not nkey or is_protagonist(nm_out) or is_placeholder(nm_out)
                or nkey in seen_out or fkey in seen_first):
            continue
        seen_out.add(nkey); seen_first.add(fkey)
        npcs.append({"name": nm_out, "persona": out.get("persona", ""),
                     "appearance": out.get("appearance", ""), "role": out.get("role", "")})
    return {"npcs": npcs}
