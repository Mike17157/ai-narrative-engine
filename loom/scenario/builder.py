"""Story Builder — a storyboard-first, procedural authoring process.

A full story is unbounded (millions of tokens at play-time), so we never
generate it. Instead we author a small, controlled **storyboard** — a plausible
plot outline (logline + ordered beats, each noting what happens · where · who) —
and then EXTRACT the scenes (neutral locations) and the cast from it. Grounding
both in one coherent plot keeps them consistent (no independent-stage drift).

Three discrete stages the UI drives one at a time (review/approve between each):
  1. storyboard(...)         → { logline, premise, tone, themes, beats[] }
  2. extract_locations(...)  → { start, locations[] }   (neutral places, PURE bg)
  3. extract_characters(...) → { npcs[] }                (the cast, minus primary)

Each is a single structured-output LLM call. Prompts are defaults here; the
server may override any stage's `system` from configs/story_builder.json.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

INVENTION = {
    "faithful": (
        "Stay strictly faithful to the card. Only structure and lightly expand what is "
        "explicitly present; do not invent beyond clear implications of the source material."
    ),
    "balanced": (
        "Preserve everything the card establishes. Invent new plot, places and supporting "
        "characters only where needed to make a coherent, plausible, playable story."
    ),
    "inventive": (
        "Use the card as a creative seed. Freely invent a rich plot, places and supporting "
        "cast, while keeping the core character entirely true to the card."
    ),
}

_OBJ = "object"


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _str_arr() -> dict:
    return _arr({"type": "string"})


# --- per-stage structured-output schemas (strict-mode friendly) ---------------
# (The storyboard stage streams a readable delimited format instead — see
# storyboard_inputs() / parse_storyboard() — so it can be watched live.)
LOCATIONS_SCHEMA = {
    "type": _OBJ, "additionalProperties": False, "required": ["start", "locations"],
    "properties": {
        "start": {"type": "string"},
        "locations": _arr({"type": _OBJ, "additionalProperties": False,
                           "required": ["id", "name", "description", "background_prompt"],
                           "properties": {
                               "id": {"type": "string"}, "name": {"type": "string"},
                               "description": {"type": "string"},
                               "background_prompt": {"type": "string"},
                           }}),
    },
}

# One character card (used for the protagonist base card AND each NPC item).
_CARD_PROPS = {
    "name": {"type": "string"}, "persona": {"type": "string"},
    "appearance": {"type": "string"}, "role": {"type": "string"},
}

CHARACTERS_SCHEMA = {
    "type": _OBJ, "additionalProperties": False, "required": ["npcs"],
    "properties": {
        "npcs": _arr({"type": _OBJ, "additionalProperties": False,
                      "required": ["name", "persona", "appearance", "role"],
                      "properties": _CARD_PROPS}),
    },
}

# Phase-1 cast roster: distinct full names + a heritage hint, decided across the WHOLE cast in one
# pass so surnames don't collide (no two 'Chen's) and the cast isn't all one ethnicity.
ROSTER_SCHEMA = {
    "type": _OBJ, "additionalProperties": False, "required": ["cast"],
    "properties": {
        "cast": _arr({"type": _OBJ, "additionalProperties": False,
                      "required": ["name", "heritage"],
                      "properties": {"name": {"type": "string"}, "heritage": {"type": "string"}}}),
    },
}

# Step 1 of cast-building: the single BASE CHARACTER CARD distilled from the source card.
PROTAGONIST_SCHEMA = {
    "type": _OBJ, "additionalProperties": False,
    "required": ["name", "persona", "appearance", "role"],
    "properties": _CARD_PROPS,
}

# Wardrobe planning emits OUTFITS only — the emotion sprite set is now a FIXED canonical taxonomy
# (composed per-character separately; see loom/server/services/emotions.py), no longer planned here.
WARDROBE_SCHEMA = {
    "type": _OBJ, "additionalProperties": False, "required": ["outfits"],
    "properties": {
        "outfits": _arr({"type": _OBJ, "additionalProperties": False,
                         "required": ["name", "attire_prompt"],
                         "properties": {"name": {"type": "string"},
                                        "attire_prompt": {"type": "string"}}}),
    },
}

# Shared, NON-NEGOTIABLE format rule for any field that becomes an image-model
# prompt. Illustrious / SDXL anime checkpoints are trained on Danbooru tags and
# choke on prose — natural-language sentences come out as garbled text or get
# ignored. So every image prompt MUST be a comma-separated tag list.
_TAG_RULE = (
    "OUTPUT FORMAT — CRITICAL, NON-NEGOTIABLE: this text goes straight into an "
    "Illustrious / SDXL anime image model that is trained on DANBOORU TAGS, not prose. "
    "Write it as a flat, comma-separated list of short lowercase booru tags. Think in SLOTS "
    "(structure, NOT fixed values — fill each with a real canonical booru tag that fits THIS "
    "character, and NEVER output the angle brackets): an outfit reads `<count>, <main garment>, "
    "<layers>, <accessories>, <footwear>`; an expression reads `<count>, <eyes>, <eyebrows>, "
    "<mouth>, <emotion>`. RULES: tags only; NO full sentences; NO articles (a/an/the); NO "
    "connecting words (with/and/wearing/while/as); NO narration or commentary; prefer canonical "
    "booru tags. Begin a character with a count tag by SEX — 1girl (female) / 1boy (male). "
    "A sentence anywhere in this field is a failure."
)

# The `appearance` field is the character's PERSISTENT physical identity, reused as the
# base for every sprite (sprites compose appearance + outfit + expression). So it must
# be structured, non-overlapping, and contain NO clothing — clothing lives in wardrobe
# outfits; the base image itself is rendered in a neutral swimwear template (full body).
_APPEARANCE_RULE = (
    "Write `appearance` as a STRUCTURED, NON-OVERLAPPING physical description in this exact "
    "order, each attribute stated ONCE and never contradicted: "
    "(1) count tag by SEX — 1girl (female) / 1boy (male); adulthood is conveyed by 'mature female' "
    "/ 'mature male', NOT by the count tag (1woman/1man are not real tags); (2) apparent age — "
    "HONEST (a number, or child / teenager / young adult / adult / middle-aged / elderly); do NOT "
    "force everyone adult. (3) hair color; (4) hair length; (5) ONE primary hairstyle (don't stack "
    "ponytail+bun+twintails); (6) eye color; (7) eye shape if notable (tareme/tsurime); (8) skin "
    "tone; (9) body build (vary — not always slim) and height; (10) face & distinguishing features "
    "(freckles, mole under eye, scar, glasses, fang, makeup, etc.).\n"
    "Describe ONLY the body and face — the persistent identity. Do NOT include clothing, "
    "outfits, accessories, pose, expression, background, lighting or scene; those are handled "
    "separately. The base image is rendered in plain swimwear, so any clothing here would "
    "corrupt it.\n"
    "PLAIN, LITERAL TAGS ONLY — translate any artistic or literary phrasing into plain booru "
    "colours, because the image model paints words literally. Skin tone is a BRIGHTNESS tag "
    "(pale skin / light skin / tan / dark skin / very dark skin) and must NEVER be a colour-name "
    "like 'olive'/'fair'/'porcelain' (dead or wrong — 'olive skin' renders GREEN). Hair and eyes use a plain colour word "
    "('black hair', 'brown eyes'), not metaphor / gem / material names (raven, auburn, chestnut, "
    "emerald, sapphire) and no 'highlights' or 'sheen'.\n\n" + _TAG_RULE
)

# Clothing counterpart of `_APPEARANCE_RULE` — an `attire_prompt` must be authored with the SAME
# care as the base image, but for the OUTFIT (clothing) only. Structured slots, real canonical
# Danbooru clothing tags, literal (the model paints words literally), full range of outfit types
# INCLUDING swimwear, and strictly NO physical identity / expression / pose / background (those are
# the base image + the expression sprite).
_OUTFIT_RULE = (
    "Write `attire_prompt` as a COMPLETE outfit in CANONICAL Danbooru CLOTHING tags, with the SAME "
    "rigor the base image gets — think in SLOTS, fill each with a REAL booru tag that fits THIS "
    "outfit (skip a slot that doesn't apply; NEVER output the angle brackets): start with the SEX "
    "count tag (1girl / 1boy), then (1) MAIN garment(s) — <colour> + <garment> ('white blouse', "
    "'pleated skirt', 'black dress', 'hoodie', 'serafuku', 'one-piece swimsuit'); (2) LAYERS over/"
    "under (jacket, cardigan, coat, vest, apron, turtleneck); (3) LEGWEAR (thighhighs, pantyhose, "
    "socks, kneehighs — with colour); (4) FOOTWEAR (boots, sneakers, high heels, sandals, mary "
    "janes); (5) HEADWEAR + worn ACCESSORIES (hat, beret, gloves, scarf, necktie, belt, glasses, "
    "earrings, hair ornament). Keep ONE coherent colour palette.\n"
    "THE FULL RANGE OF OUTFITS IS WELCOME — casual, school, formal/evening, work/uniform, fantasy or "
    "armour, sleepwear, and SWIMWEAR (bikini, one-piece swimsuit, school swimsuit). Choose what "
    "genuinely fits the character and the scene; do NOT shy away from swimwear when it fits.\n"
    "LITERAL TAGS ONLY — plain colour words ('red', 'navy blue', 'black'), real garment names, NO "
    "brand names, NO metaphor / material poetry ('gossamer', 'liquid silk', 'flowing gown'). "
    "CLOTHING + worn ACCESSORIES ONLY: NO body / hair / eye / skin / face tags (those are the base "
    "identity), NO facial expression, NO pose, NO background or scene.\n\n" + _TAG_RULE
)

DEFAULT_SYSTEMS = {
    "storyboard": (
        "You are a story architect outlining a story as the CHAPTERS OF A BOOK — a deliberate, "
        "tactical plan, NOT flowing prose. Think structurally: lay out a clear dramatic arc and give "
        "EACH chapter a definite function in it — setup, inciting incident, rising complication, "
        "midpoint turn, crisis, climax, resolution (adapt to the story). Typically 6-12 chapters.\n\n"
        "CHAPTERS MUST BE DISTINCT — this is the most important rule. Each chapter is a self-contained "
        "episode with its OWN situation, location, and dramatic question — a discrete unit you could "
        "title and summarise as 'this chapter accomplishes X'. They must NOT bleed into one continuous "
        "scene; expect deliberate jumps in time, place and focus between chapters. If two chapters "
        "happen in the same place at the same time with the same beat, merge them.\n\n"
        "WRITING STYLE: study the source material above (PERSONA, EXAMPLE DIALOGUE, OPENING MESSAGE) "
        "and MIRROR its voice — tone, vocabulary, register. But each chapter's summary is a PLANNING "
        "note (what happens, what changes, the stakes, and the hook into the next chapter), 2-4 "
        "concrete sentences — NOT a passage of the actual prose.\n\n"
        "LOCATIONS: give EACH chapter a SPECIFIC, evocative setting with its own identity. Avoid two "
        "opposite failures: (1) trivial near-duplicates of one spot ('beach near the water' vs 'beach "
        "further inland'); and (2) lazily reusing ONE GENERIC place for many chapters (four chapters "
        "all at 'Ocean Beach'). When the story revisits a broad area, make each visit a genuinely "
        "different specific spot or condition — e.g. 'the crowded public beach at noon', 'a secluded "
        "rocky cove', 'a bonfire on the dark dunes', 'the weathered fishing pier'. Reuse an exact "
        "location name only when two chapters truly share the same spot AND moment.\n\n"
        "Write EXACTLY these lines and nothing else (no markdown, no commentary):\n"
        "LOGLINE: <one evocative sentence>\n"
        "PREMISE: <a rich paragraph of 3-5 sentences, in the source's voice>\n"
        "TONE: <a few words>\n"
        "THEMES: <comma-separated>\n"
        "CHAPTERS:\n"
        "1. <short chapter title> | <what this chapter accomplishes: events, the turn, stakes, hook> | <single location name> | <characters present, comma-separated>\n"
        "2. <...> | <...> | <...> | <...>\n"
        "Each chapter is ONE line with FOUR fields separated by ' | '. NEVER put a line break or a "
        "'|' inside a field. Continue numbering through all chapters."
    ),
    "locations": (
        "You design the LOCATIONS for a story from the set of places its beats occur in. A location "
        "is an objective, NEUTRAL place — just a backdrop, with no events or characters baked in. "
        "For each distinct place give a stable id (lowercase-hyphen), a name, a short objective "
        "description, and a background_prompt.\n\n"
        "KEENLY DISTINCT & SPECIFIC: each location is a genuinely different, SPECIFIC setting a viewer "
        "would instantly tell apart. Consolidate ONLY trivial duplicates (the very same spot from a "
        "different angle or moment). But do NOT collapse a broad area into one generic place: if the "
        "story spends several chapters 'at the beach', yield the distinct SPECIFIC spots it actually "
        "uses — 'the crowded public beach', 'a hidden rocky cove', 'a bonfire on the night dunes', "
        "'the weathered pier' — each named and specific, NEVER a single generic 'beach' reused, and "
        "never trivial variants like 'beach near the water' vs 'beach further inland'. Aim for a tight "
        "set (about 4-7) of strongly varied, specific locations.\n\n"
        "The background_prompt must depict ONLY the empty environment as a pure background plate "
        "(setting, architecture, scenery, time of day, weather) with ABSOLUTELY NO people, "
        "characters, figures, silhouettes or crowds — a clean, unpopulated backdrop sprites "
        "composite onto.\n\n"
        "Make each one POETICALLY BEAUTIFUL, not a literal snapshot. Lean into mood and light — "
        "dramatic or tender lighting, colour palette, atmosphere (mist, dust motes, bloom, "
        "reflections, falling petals), depth and a sense of stillness or wonder. Favour an "
        "illustrative / painterly look over photorealism.\n\n"
        "FRAME THE PLACE ITSELF, NOT ITS CONTEXT: the background_prompt depicts the IMMEDIATE, "
        "defining subject of THIS spot in close-to-medium framing — its own surfaces, structures, "
        "textures and props — not the wide geography around it. If the location is 'the old sea "
        "wall', describe the WALL (weathered stone blocks, clinging moss, barnacles, a worn rail, a "
        "thin strip of sky) — NOT a panoramic beach. Lead with the spot's defining feature and "
        "include only the immediate surroundings that physically touch it, so every location reads "
        "as its own distinct place instead of the same broad vista.\n\n"
        "FORMAT — CRITICAL: the `background_prompt` goes straight into an Illustrious / SDXL anime "
        "image model trained on DANBOORU TAGS — prose comes out garbled. Write it as a flat, "
        "comma-separated list of short lowercase booru tags, NOT sentences and NO articles/connecting "
        "words. Start with `no humans, scenery`, then the place, then the mood/light/atmosphere tags — "
        "as a SLOT pattern (structure, NOT fixed values; fill each with real tags for THIS place, "
        "never output the brackets): `no humans, scenery, <place/landform>, <2-3 features that "
        "touch it>, <time of day>, <weather/atmosphere>, <lighting>, <mood + quality tags>`. "
        "The `description` field stays plain prose (it is for humans, not the image model).\n\n"
        "Pick the starting location (where the story opens)."
    ),
    # Step 1: distill the imported source card into ONE clean base character card — the
    # canonical format the supporting cast then mimics. Reuses the `characters` stage model.
    "protagonist": (
        "You normalize an imported character card into a clean BASE CHARACTER CARD — the canonical "
        "reference every other character in this story will mimic. Read ALL the source material "
        "(persona, description, personality, scenario, example dialogue) and produce ONE card for "
        "the MAIN CHARACTER.\n\n"
        "Write `persona` as a clean character card in EXACTLY these markdown sections, in order:\n"
        "  >**<Name>: <age> years old**\n"
        "  ### **<Name>'s Appearance** — physical look in prose, with '*   **Hair & Eyes:**' and "
        "'*   **Attire:**' detail lines.\n"
        "  ### **<Name>'s Personality** — traits, virtues, flaws, contradictions, how they come across.\n"
        "  ### **<Name>'s Background** — backstory, role in the world, key relationships.\n"
        "DISTILL the source faithfully: keep every established FACT, but normalize to JUST these "
        "sections at a MODERATE, consistent depth — the SAME format and length the supporting cast "
        "uses. Do NOT preserve the source's extra sections verbatim (quirk lists, communication-style "
        "guides, long example-dialogue dumps) — fold their essence into Personality/Background. The "
        "protagonist's card must NOT be longer or differently shaped than the rest of the cast.\n\n"
        "Set `name` to the character's ACTUAL NAME — a person's name taken from the persona "
        "(e.g. 'Kaia Nakumura'). NEVER use the card's title, a quote/greeting, or the story name; "
        "if the source gives only a first name, keep that (don't invent a surname for the protagonist).\n"
        "Give a one-phrase `role` (e.g. 'protagonist'). The `appearance` field (and ONLY that field "
        "— the persona stays prose) must follow this:\n"
        + _APPEARANCE_RULE
    ),
    "characters": (
        "You are a character writer. For each supporting character from the storyboard, write a "
        "THOROUGH background usable as a chat character — rich and specific, not a one-liner.\n\n"
        "MATCH THE BASE CHARACTER CARD: a BASE CHARACTER CARD for the main character is provided "
        "below. Write each supporting character's `persona` in the IDENTICAL structure, the SAME "
        "section headings, and the SAME level of depth as that base card — so the whole cast shares "
        "one consistent card format. (If no base card is provided, use clear labelled sections: "
        "Appearance, Personality, Background, Relationships, Voice.)\n\n"
        "Also give an `appearance` field and a one-phrase `role`. Stay consistent with the card's "
        "world and tone, and mirror its voice. Do NOT include the main character — only the "
        "supporting cast.\n\n"
        "The `appearance` field (and ONLY that field — the persona stays prose) must follow this:\n"
        + _APPEARANCE_RULE
    ),
    "wardrobe": (
        "You are a character art director planning the OUTFITS for a visual-novel character. "
        "Given the story and one character, produce the outfit list.\n\n"
        "OUTFITS — the FIRST outfit is always a SWIMSUIT look: name it 'Base (swimwear)' with a "
        "swimsuit concept (e.g. a coloured bikini for female characters, swim trunks for male). THEN "
        "add the distinct outfits this character genuinely needs ACROSS THIS STORY: usually just one "
        "main outfit; add more only when the plot clearly changes their attire (a transformation, a "
        "time-skip, a formal event, a disguise, ruined/bloodied clothing).\n"
        "For EACH outfit, write a SHORT CONCEPT in `attire_prompt` — just the handful of defining "
        "garment tags (e.g. 'navy blue sailor uniform, pleated skirt' or 'red bikini'). Do NOT try to "
        "fully detail every garment, accessory and colour here — a DEDICATED SECOND PASS expands each "
        "outfit on its own into the complete, detailed look (full colours, legwear, footwear, "
        "accessories, makeup, piercings). Keep this pass light so you don't get overwhelmed.\n\n"
        "(The emotional EXPRESSION range is a fixed set handled separately — do not plan it here.)\n\n"
        "Each `attire_prompt` is CLOTHING booru tags (NO facial expression, NO pose, NO background), "
        "following these OUTFIT rules:\n" + _OUTFIT_RULE
    ),
    # Base-image generator: fills the fixed physical-feature schema (assembled into tags
    # in code). Invention does NOT apply here — a base image is a deterministic identity
    # capture. Editable in the Config pane like the other stages.
    "base_image": (
        "You are a character art director. Read the character and fill EVERY field of the "
        "physical-feature schema for their base reference image. Draw specific detail from the "
        "persona/appearance notes; where sparse, INFER tasteful detail that fits their world, age "
        "and role — but never contradict anything stated. These are PERSISTENT physical traits "
        "only — NO clothing, pose, expression or background (added later). Give the `appearance` "
        "field as a LIST of short, EXPLICIT, ATOMIC descriptors — one attribute per item ('silver "
        "hair', 'long hair', 'wavy hair', 'violet eyes', 'pale skin', 'mole under eye'), NOT prose "
        "and never compound ('long silver hair' -> split). The system grounds each to a real Danbooru "
        "tag an Illustrious model understands. Use the enum values for the other fields. Stay literal "
        "and internally consistent — never describe contradictory traits."
    ),
}

STAGES = ["storyboard", "scenes", "characters"]


def _card_context(name: str, persona: str, extras: dict) -> str:
    parts = [f"CHARACTER NAME: {name}", "", "PERSONA:", persona or "(none)"]
    label = {"scenario_text": "SCENARIO (from card)", "first_mes": "OPENING MESSAGE (from card)",
             "mes_example": "EXAMPLE DIALOGUE", "appearance": "APPEARANCE"}
    for k, lbl in label.items():
        v = (extras or {}).get(k)
        if v:
            parts += ["", f"{lbl}:", str(v)[:2500]]
    return "\n".join(parts)


# Stages whose output is a deterministic identity capture — the invention directive
# (how freely to invent beyond the card) does NOT apply to them.
NO_INVENTION = {"base_image"}

# Stages that read an image — they need a VISION-capable model, so the model picker filters
# to those. base_image is NO LONGER here: its prompt is generated from the character's written
# description (text), so a strong text model does it better than a hallucination-prone vision one.
NEEDS_IMAGE: set[str] = set()


def _sys(systems: dict, stage: str, invention: str) -> str:
    base = (systems or {}).get(stage) or DEFAULT_SYSTEMS[stage]
    # 'none'/'off' disables the directive for this stage; base_image never gets one.
    if stage in NO_INVENTION or invention in (None, "none", "off"):
        return base
    return f"{base}\n\n{INVENTION.get(invention, INVENTION['balanced'])}"


def _slug(s: str, fallback: str = "x") -> str:
    out = re.sub(r"[^\w\-]+", "-", (s or "").strip().lower()).strip("-")
    return out or fallback


def _call(provider, system: str, prompt: str, schema: dict, stage: str, on_delta=None) -> dict:
    # on_delta (optional): a token callback — when given, the structured output streams so a UI
    # can watch the JSON form live (provider falls back to a blocking call if the stream mangles).
    res = provider.generate_text(system=system, prompt=prompt, emits=schema, on_delta=on_delta)
    data = res.data or {}
    if not data:
        raise ValueError(f"stage '{stage}' returned no structured data "
                         f"(the author model may not support structured output)")
    return data


# Stage 1 — streamed as readable text, then parsed --------------------------- #
def storyboard_inputs(*, name: str, persona: str, extras: dict | None = None,
                      invention: str = "balanced", systems: dict | None = None) -> tuple[str, str]:
    """(system, prompt) for the storyboard stage. Plain text (no structured
    output) so it can be streamed token-by-token and watched live."""
    card = _card_context(name, persona, extras or {})
    return (_sys(systems or {}, "storyboard", invention),
            f"{card}\n\nStoryboard a plausible story for this character.")


_BEAT_RE = re.compile(r"^\s*\d+[.)]\s*(.*\S)\s*$")


def parse_storyboard(text: str) -> dict:
    """Parse the streamed LOGLINE/PREMISE/TONE/THEMES/BEATS format into a board."""
    logline = premise = tone = ""
    themes: list[str] = []
    beats: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("logline:"):
            logline = line.split(":", 1)[1].strip()
        elif low.startswith("premise:"):
            premise = line.split(":", 1)[1].strip()
        elif low.startswith("tone:"):
            tone = line.split(":", 1)[1].strip()
        elif low.startswith("themes:"):
            themes = [t.strip() for t in line.split(":", 1)[1].split(",") if t.strip()]
        elif low.startswith("chapters:") or low.startswith("beats:"):
            continue
        else:
            m = _BEAT_RE.match(line)
            if not m:
                continue
            parts = [p.strip() for p in m.group(1).split("|")]
            # Preferred 4-field chapter: title | summary | location | characters.
            # Tolerate the old 3-field (summary | location | characters).
            if len(parts) >= 4:
                title, summary, location = parts[0], parts[1], parts[2]
                chars = [c.strip() for c in parts[3].split(",")]
            else:
                title = ""
                summary = parts[0] if parts else ""
                location = parts[1] if len(parts) > 1 else ""
                chars = [c.strip() for c in parts[2].split(",")] if len(parts) > 2 else []
            beats.append({"title": title, "summary": summary, "location": location,
                          "characters": [c for c in chars if c]})
    return {"logline": logline, "premise": premise, "tone": tone, "themes": themes, "beats": beats}


# Stage 2 ------------------------------------------------------------------- #
def extract_locations(provider, *, board: dict, invention: str = "balanced",
                      systems: dict | None = None) -> dict:
    # Distinct place names from the beats (order-preserving), in the prompt.
    seen, places = set(), []
    for b in board.get("beats", []):
        loc = (b.get("location") or "").strip()
        if loc and loc.lower() not in seen:
            seen.add(loc.lower()); places.append(loc)
    place_lines = "\n".join(f"- {p}" for p in places) or "(infer from the logline)"
    out = _call(provider, _sys(systems or {}, "locations", invention),
                f"LOGLINE: {board.get('logline','')}\nTONE: {board.get('tone','')}\n"
                f"PLACES THE STORY VISITS:\n{place_lines}\n\n"
                f"Consolidate these into a tight set of KEENLY DISTINCT neutral locations.",
                LOCATIONS_SCHEMA, "locations")

    locations, id_map = [], {}
    for l in out.get("locations", []):
        lid = _slug(l.get("id") or l.get("name"), f"place-{len(id_map)+1}")
        base, n = lid, 2
        while lid in id_map.values():
            lid, n = f"{base}-{n}", n + 1
        id_map[(l.get("name") or lid).lower()] = lid
        locations.append({"id": lid, "name": l.get("name", lid),
                          "description": l.get("description", ""),
                          "background_prompt": l.get("background_prompt", "")})
    start = None
    raw_start = (out.get("start") or "").lower()
    start = id_map.get(raw_start) or next((l["id"] for l in locations
                                           if l["id"] == out.get("start")), None) \
        or (locations[0]["id"] if locations else None)
    return {"start": start, "locations": locations}


# Stage 3 ------------------------------------------------------------------- #
def _name_tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]


def extract_protagonist(provider, *, name: str, persona: str, extras: dict | None = None,
                        invention: str = "faithful", systems: dict | None = None, on_event=None) -> dict:
    """Step 1 of cast-building: distill the imported source card into ONE clean BASE
    CHARACTER CARD — a structured persona (the source's own section format), the appearance
    booru tags, and a role. This card is the canonical format every supporting character
    then mimics. Faithful by default (the main character must stay true to the source).
    `on_event` (optional): stream {type:phase|delta} so a UI can watch it. Returns
    { name, persona, appearance, role }."""
    if on_event:
        on_event({"type": "phase", "label": f"Distilling the base card — {name}"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    card = _card_context(name, persona, extras or {})
    out = _call(provider, _sys(systems or {}, "protagonist", invention),
                f"{card}\n\nNormalize this into ONE clean base character card for the main character.",
                PROTAGONIST_SCHEMA, "protagonist", on_delta=dl)
    return {"name": out.get("name") or name, "persona": out.get("persona") or (persona or ""),
            "appearance": out.get("appearance", ""), "role": out.get("role") or "protagonist"}


def revise_character(provider, *, name: str, persona: str, role: str = "", appearance: str = "",
                     instruction: str = "", board: dict | None = None, invention: str = "balanced",
                     systems: dict | None = None, on_event=None) -> dict:
    """Re-derive ONE existing cast member's card with a user CHANGE applied — rewriting persona,
    role and appearance together so the story overview AND the base image stay in sync. `instruction`
    is the user's free-text steer ('make her older, a rival not a friend'); empty = a faithful
    refresh. Keeps the same NAME unless the change explicitly renames them. Streams phase+delta via
    `on_event`. Returns { name, persona, appearance, role }."""
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
    out = _call(provider, _sys(systems or {}, "protagonist", invention), prompt,
                PROTAGONIST_SCHEMA, "characters", on_delta=dl)
    return {"name": out.get("name") or name, "persona": out.get("persona") or persona,
            "appearance": out.get("appearance", ""), "role": out.get("role") or role or "supporting"}


def extract_characters(provider, *, name: str, persona: str, board: dict,
                       extras: dict | None = None, invention: str = "balanced",
                       systems: dict | None = None, reference_card: str = "", on_event=None) -> dict:
    # The protagonist (the source character) is added separately as the primary cast
    # member — exclude her from the supporting NPCs even when beats use a short/variant
    # name ('Kaia' vs the card's 'Kaia Nakumura'), so she isn't duplicated as an NPC.
    pl = (name or "").lower().strip()
    ptoks = _name_tokens(pl)
    pfirst = ptoks[0] if ptoks else ""

    def is_protagonist(c: str) -> bool:
        cl = (c or "").lower().strip()
        if not cl:
            return True
        if pl and (pl in cl or cl in pl):
            return True
        return bool(pfirst) and pfirst in _name_tokens(cl)  # shares the protagonist's first name

    # The user/player and narrator are NOT cast members — beats routinely list them, including the
    # literal '{{user}}' template token. Never generate a character card for them.
    _PLACEHOLDERS = {"user", "you", "player", "narrator", "protagonist", "mc", "main character",
                     "me", "self", "reader", "viewer"}
    # collective / group references ('art school peers', 'the students', 'crowd') name no ONE
    # person — the model otherwise invents a random (often duplicate) character for the slot.
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
            ftok = toks[0] if toks else c.lower()   # dedup by FIRST name → never two 'Maya's
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
    sys_p = _sys(systems or {}, "characters", invention)
    logline = board.get("logline", "")

    # PHASE 1 — CAST ROSTER in ONE pass: assign each character a DISTINCT full name + a heritage,
    # deciding the whole cast together so surnames don't collide (no two 'Chen's) and they aren't
    # all one ethnicity. Per-character calls alone can't see each other, so they cluster (every
    # invented surname echoes the protagonist's). This pass coordinates that up front.
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
    except Exception:  # noqa: BLE001 — fall back to the bare first names if the roster pass fails
        roster = []
    entries = []
    for i, nm in enumerate(names):
        r = roster[i] if i < len(roster) else {}
        toks = (r.get("name") or "").strip().split()
        surname = toks[-1] if len(toks) >= 2 else ""
        # Keep the BEAT first name (already deduped → distinct) and take ONLY the surname from the
        # roster, so the model can't rename two different characters to the same first name.
        entries.append({"first": nm, "name": (f"{nm} {surname}".strip()),
                        "heritage": (r.get("heritage") or "").strip()})

    # PHASE 2 — one focused full card per character (parallel), using the assigned name + heritage.
    def _prompt(e: dict) -> str:
        her = f" — heritage: {e['heritage']}" if e["heritage"] else ""
        return (f"{card}\n\nLOGLINE: {logline}\n"
                f"Write the FULL character card for THIS ONE supporting character: {e['name']}{her}." + ref +
                "\n\n- Use EXACTLY this name and heritage — do NOT rename or change the surname.\n"
                "- DEPTH: concise but vivid — a few tight, specific sentences per section in the base "
                "card's structure. Do NOT pad or pile on detail; keep it readable, not bulky.\n"
                "- Reflect the HERITAGE through SKIN TONE + natural hair/eye COLOUR + a characteristic "
                "HAIRSTYLE (e.g. East-Asian = PALE skin + black hair; West-African = dark skin + "
                "braids/afro). Derive BUILD naturally — don't default to slim, don't force a body type.\n"
                "Write ONLY this character — not the protagonist, not anyone else.")

    def _one(e: dict):
        # Parallel, so we DON'T stream per-token (interleaved tokens are unreadable) — emit a
        # 'phase' when a character starts and an 'item' with its result when it finishes.
        if on_event:
            on_event({"type": "phase", "label": f"Writing {e['name']}"})
        try:
            out = _call(provider, sys_p, _prompt(e), PROTAGONIST_SCHEMA, "characters")
        except Exception:  # noqa: BLE001 — one character failing must not sink the whole cast
            return None
        if on_event and out:
            on_event({"type": "item", "name": out.get("name") or e["name"],
                      "text": out.get("appearance", "")})
        return out

    # Phase-2 calls are independent → run CONCURRENTLY (OpenRouter allows it). Order preserved.
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
        # drop the protagonist, user/group placeholders, same-name collisions, AND duplicate FIRST
        # names (two 'Maya's) — the model occasionally reuses one when it renames a slot.
        if (not nkey or is_protagonist(nm_out) or is_placeholder(nm_out)
                or nkey in seen_out or fkey in seen_first):
            continue
        seen_out.add(nkey); seen_first.add(fkey)
        npcs.append({"name": nm_out, "persona": out.get("persona", ""),
                     "appearance": out.get("appearance", ""), "role": out.get("role", "")})
    return {"npcs": npcs}


# Stage 4 — wardrobe planning for one character (OUTFITS only; emotions are a fixed taxonomy) #
def plan_wardrobe(provider, *, char_name: str, persona: str, appearance: str, story: dict,
                  invention: str = "balanced", systems: dict | None = None, on_event=None) -> dict:
    """Guess the outfits this character needs across the story (short attire concepts; a dedicated
    second pass details each). The emotion sprite set is a FIXED canonical taxonomy composed
    separately, so it is NOT planned here. `on_event` (optional): stream {type:phase|delta} so a UI
    can watch it. Returns { outfits:[{name, attire_prompt}] }."""
    beats = story.get("storyboard", {}).get("beats") or story.get("beats") or []
    # Only the beats this character appears in (by name) — their arc.
    nl = (char_name or "").lower()
    arc = [b for b in beats if any(nl in (c or "").lower() for c in b.get("characters", []))] or beats
    beat_lines = "\n".join(f"- {b.get('summary','')}" for b in arc[:24])
    ctx = (f"STORY: {story.get('premise','')}\nTONE: {story.get('tone','')}\n\n"
           f"CHARACTER: {char_name}\nPERSONA:\n{persona or '(none)'}\n"
           f"APPEARANCE: {appearance or '(infer)'}\n\n"
           f"THIS CHARACTER'S BEATS:\n{beat_lines or '(use the story overall)'}\n\n"
           f"Plan {char_name}'s outfits across the story.")
    if on_event:
        on_event({"type": "phase", "label": f"Planning {char_name}'s wardrobe"})
    dl = (lambda t: on_event({"type": "delta", "text": t})) if on_event else None
    out = _call(provider, _sys(systems or {}, "wardrobe", invention), ctx, WARDROBE_SCHEMA, "wardrobe", on_delta=dl)
    outfits = [{"name": o.get("name", ""), "attire_prompt": o.get("attire_prompt", "")}
               for o in out.get("outfits", []) if o.get("attire_prompt")]
    return {"outfits": outfits}
