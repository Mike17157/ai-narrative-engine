"""Characters: extract cast, compose appearance, generate emotions — all character data in one place.

Functions:
  extract_protagonist    — distill source card into clean base card
  revise_character       — re-derive one cast member with a user instruction applied
  extract_characters     — extract supporting NPCs from the storyboard
  compose_base_prompt    — author base-image appearance prompt from persona
  compose_expressions    — face-expression booru tags for all canonical emotions
  compose_affect_range   — curate which emotions this character displays (SFW/NSFW)
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor

from ._helpers import (
    PROTAGONIST_SCHEMA, ROSTER_SCHEMA, _APPEARANCE_RULE,
    _call, _card_context, _sys,
)


# ---------------------------------------------------------------------------
# Cast extraction
# ---------------------------------------------------------------------------

def _name_tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def extract_protagonist(provider, *, name: str, persona: str, extras: dict | None = None,
                        systems: dict | None = None, on_event=None, craft: str = "") -> dict:
    """Distill the imported source card into one clean base character card.
    Returns {name, persona, appearance, role}."""
    if on_event:
        on_event({"type": "phase", "label": f"Distilling the base card — {name}"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    card = _card_context(name, persona, extras or {})
    out = _call(provider, _sys(systems or {}, "protagonist") + (("\n\n" + craft) if craft else ""),
                f"{card}\n\nNormalize this into ONE clean base character card for the main character.",
                PROTAGONIST_SCHEMA, "protagonist", on_delta=dl)
    return {"name": out.get("name") or name, "persona": out.get("persona") or (persona or ""),
            "appearance": out.get("appearance", ""), "role": out.get("role") or "protagonist"}


def revise_character(provider, *, name: str, persona: str, role: str = "", appearance: str = "",
                     instruction: str = "", board: dict | None = None,
                     systems: dict | None = None, on_event=None) -> dict:
    """Re-derive one existing cast member's card with a user change applied.
    Returns {name, persona, appearance, role}."""
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
                       extras: dict | None = None, systems: dict | None = None,
                       reference_card: str = "", on_event=None, craft: str = "") -> dict:
    """Extract and generate supporting cast cards from the storyboard.
    Returns {npcs: [{name, persona, appearance, role}]}."""
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
    sys_p = _sys(systems or {}, "characters") + (("\n\n" + craft) if craft else "")
    logline = board.get("logline", "")

    # Phase 1 — cast roster: distinct full names + heritage decided for the whole cast together.
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

    # Phase 2 — one focused full card per character (parallel).
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


# ---------------------------------------------------------------------------
# Appearance prompt
# ---------------------------------------------------------------------------

def compose_base_prompt(provider, name: str, persona: str, appearance_notes: str = "",
                        role: str = "", systems: dict | None = None) -> dict:
    """Author the base-image appearance prompt for a character.
    Returns {prompt, features, companions} or {error}."""
    from loom.server.services.prompts import (
        compose_nl_base, _assemble_base_prompt, FEATURES_SCHEMA,
    )
    from ._helpers import DEFAULT_SYSTEMS
    if provider is None:
        return {"error": "no author model configured"}

    try:
        nl = (compose_nl_base(provider, name, persona, appearance_notes, role)
              or compose_nl_base(provider, name, persona, appearance_notes, role))
    except Exception:  # noqa: BLE001
        nl = None
    if nl:
        feats = {k: v for k, v in nl.items() if k != "prompt"}
        return {"prompt": nl["prompt"], "features": feats, "companions": []}

    system = (systems or {}).get("base_image") or DEFAULT_SYSTEMS["base_image"]
    context = "\n\n".join(p for p in [
        f"NAME: {name}",
        f"PERSONA:\n{persona}" if persona else "",
        f"APPEARANCE NOTES: {appearance_notes}" if appearance_notes else "",
        f"ROLE: {role}" if role else "",
    ] if p)
    context = ("Fill the feature schema from this character's WRITTEN DESCRIPTION below "
               "(persona + appearance). Give `appearance` as a LIST of short, explicit, ATOMIC "
               "descriptors (one attribute each) — the system weaves each into the image prompt.\n\n"
               + context)

    def _gen_feats():
        return (provider.generate_text(system=system, prompt=context, emits=FEATURES_SCHEMA).data) or {}

    try:
        feats = _gen_feats() or _gen_feats()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"appearance generation failed: {exc}"}
    if not feats:
        return {"error": "model returned no structured features "
                         "(author model may not support structured output)"}
    return {"prompt": _assemble_base_prompt(feats), "features": feats, "companions": []}


# ---------------------------------------------------------------------------
# Emotions — generated at character-extraction time, before wardrobe
# ---------------------------------------------------------------------------

def compose_expressions(provider, persona: str) -> dict:
    """Face-expression booru tags for the full canonical emotion taxonomy.
    One structured call. Returns {emotion_key: face_tags}."""
    from loom.server.services.emotions import EMOTIONS, EMOTION_KEYS, EMOTION_HINTS
    from loom.server.services.prompts import _EXPRESSION_SYSTEM
    fallback = {k: EMOTION_HINTS[k] for k in EMOTION_KEYS}
    if provider is None:
        return fallback
    schema = {"type": "object", "additionalProperties": False, "required": EMOTION_KEYS,
              "properties": {k: {"type": "string"} for k in EMOTION_KEYS}}
    listing = "\n".join(f"- {e['key']} ({e['label']}): cues — {e['hint']}" for e in EMOTIONS)
    system = _EXPRESSION_SYSTEM + (
        "\n\nYou are given a FIXED list of base emotions. For EVERY emotion key, output how THIS "
        "character's face shows it as 3-7 booru expression tags (face/eyes/eyebrows/mouth + emotion "
        "tags) — the base emotion TINGED by their personality (e.g. coy happy, sly happy, guarded "
        "sad). Return exactly one field per emotion key.")
    prompt = f"CHARACTER PERSONA:\n{persona}\n\nEMOTIONS (give a face prompt for each):\n{listing}"
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        data = {}
    return {k: (str(data.get(k) or "").strip() or fallback[k]) for k in EMOTION_KEYS}


def compose_affect_range(provider, persona: str, nsfw: bool = False,
                         systems: dict | None = None, attire: str = "") -> dict:
    """Curate which emotions this character displays — SFW only or SFW+NSFW.

    One structured call. Returns {range: [key1, key2, ...]} sorted by circumplex angle
    so the carousel X-axis has stable left-to-right order. Falls back to NORMAL_KEYS.

    ``attire`` makes the range OUTFIT-SPECIFIC: the emotions someone shows shift with the
    social register of what they're wearing — reserved/composed in formal or work attire,
    candid and warm in casual wear, playful/flushed/inviting in swimwear or lingerie, and the
    most intimate ones in bedroom wear. Same taxonomy, a register-appropriate SUBSET.
    """
    from loom.server.services.emotions import (
        EMOTIONS, EMOTION_KEYS, NORMAL_KEYS, EMOTION_COORDS,
    )
    from loom.server.services.prompts import _AFFECT_SYSTEM
    pool = EMOTION_KEYS if nsfw else NORMAL_KEYS
    fallback = {"range": NORMAL_KEYS[:]}
    if provider is None:
        return fallback
    system = (systems or {}).get("emotion") or _AFFECT_SYSTEM
    schema = {
        "type": "object", "additionalProperties": False, "required": ["range"],
        "properties": {"range": {
            "type": "array", "minItems": 6, "maxItems": len(pool),
            "items": {"type": "string", "enum": pool},
        }},
    }
    pool_emotions = [e for e in EMOTIONS if e["key"] in set(pool)]
    listing = "\n".join(f"- {e['key']}: {e['hint']}" for e in pool_emotions)
    register = (
        f"OUTFIT THEY ARE WEARING:\n{attire}\n\n"
        "Curate the emotions this character would naturally SHOW while wearing THIS outfit, in "
        "its social register. The set should visibly DIFFER by register: reserved and composed "
        "for formal or work attire; relaxed, warm and candid for casual wear; playful, teasing, "
        "flushed and inviting for swimwear or lingerie; the most intimate and vulnerable ones for "
        "bedroom / intimate wear.\n\n"
    ) if attire.strip() else ""
    prompt = (f"CHARACTER PERSONA:\n{persona}\n\n"
              f"{register}"
              f"AVAILABLE EMOTION KEYS:\n{listing}\n\n"
              f"Return the emotion keys for this character's range.")
    try:
        data = provider.generate_text(system=system, prompt=prompt, emits=schema).data or {}
    except Exception:  # noqa: BLE001
        return fallback
    raw = data.get("range") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return fallback
    seen: set[str] = set()
    pool_set = set(pool)
    cleaned = [k for k in raw if isinstance(k, str) and k in pool_set and not seen.add(k)]  # type: ignore[func-returns-value]
    if not cleaned:
        return fallback
    if "neutral" in pool_set and "neutral" not in seen:
        cleaned.append("neutral")
    cleaned.sort(key=lambda k: math.atan2(EMOTION_COORDS[k][1], EMOTION_COORDS[k][0]))
    return {"range": cleaned}
