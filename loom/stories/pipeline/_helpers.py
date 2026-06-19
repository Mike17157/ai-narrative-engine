"""Shared schemas, constants, and low-level helpers used across all pipeline steps."""

from __future__ import annotations

import re

_OBJ = "object"


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _str_arr() -> dict:
    return _arr({"type": "string"})


# ---------------------------------------------------------------------------
# Per-stage structured-output schemas
# ---------------------------------------------------------------------------

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

ROSTER_SCHEMA = {
    "type": _OBJ, "additionalProperties": False, "required": ["cast"],
    "properties": {
        "cast": _arr({"type": _OBJ, "additionalProperties": False,
                      "required": ["name", "heritage"],
                      "properties": {"name": {"type": "string"}, "heritage": {"type": "string"}}}),
    },
}

PROTAGONIST_SCHEMA = {
    "type": _OBJ, "additionalProperties": False,
    "required": ["name", "persona", "appearance", "role"],
    "properties": _CARD_PROPS,
}

WARDROBE_SCHEMA = {
    "type": _OBJ, "additionalProperties": False, "required": ["outfits"],
    "properties": {
        "outfits": _arr({"type": _OBJ, "additionalProperties": False,
                         "required": ["name", "concept"],
                         "properties": {
                             "name": {"type": "string"},
                             "concept": {"type": "string",
                                        "description": "A SHORT visual concept — 3-10 words naming the key garment(s) and palette."},
                         }}),
    },
}


# ---------------------------------------------------------------------------
# Image-prompt format rules
# ---------------------------------------------------------------------------

_TAG_RULE = (
    "OUTPUT FORMAT — CRITICAL, NON-NEGOTIABLE: this text goes straight into a natural-language "
    "anime image model (Anima / Qwen-Image), which reads expressive descriptive prose — NOT a "
    "flat booru tag list. Write it as one or a few vivid, well-composed sentences (you may use "
    "connectives and short clauses). Concrete sensory words — colours, materials, light — and a "
    "touch of mood read strongly; generic filler does not. Think about THIS character's actual "
    "look: an outfit reads 'a <colour> <main garment>, with <layers / accessories / footwear> in "
    "one coherent palette'; an expression reads '<a facial expression>, <eyes>, <brows>, <mouth>, "
    "carrying <the emotion>'. RULES: descriptive prose only; plain real colour words; real garment "
    "/ feature names; NO art-style or quality buzzwords (the workflow carries those); prefer vivid, "
    "specific phrasing. A flat comma-only tag list in this field is a failure."
)

_APPEARANCE_RULE = (
    "Write `appearance` as a vivid, specific, FLATTERING prose description (about 3-6 sentences) "
    "of THIS character's persistent physical identity. Cover, in a natural reading order: who they "
    "are (sex + HONEST age — 'a young woman', 'a man in his thirties', 'a teenage girl'; do NOT "
    "force everyone adult); hair (colour + length + ONE primary style + a detail); eyes (colour + "
    "shape) and where their gaze rests; skin tone and any marks/texture; build and figure (VARY it "
    "across the cast — not always slim); height; the face and 1-2 distinguishing hooks that make "
    "THIS face unmistakable (freckles, a mole, a scar, glasses, heterochromia…). Each attribute "
    "stated ONCE, never contradicted.\n"
    "Describe ONLY the body and face — the persistent identity. Do NOT include clothing, outfits, "
    "accessories, pose, expression, background, lighting or scene; those are handled separately. "
    "The base image is rendered in plain swimwear, so any clothing here would corrupt it.\n"
    "Convey sex and age through the description itself ('a young woman', 'a grown man', 'a "
    "ten-year-old girl') rather than count tags. Use plain, evocative colour words; you may use "
    "connectives and short clauses — this is descriptive prose, not a tag list.\n\n" + _TAG_RULE
)

_OUTFIT_RULE = (
    "Write `attire_prompt` as a LITERAL, FACTUAL outfit inventory — colour + garment name for each "
    "piece, in logical order: MAIN garment(s) first (dress, blouse, hoodie, uniform, swimsuit…); "
    "LAYERS over/under (jacket, cardigan, coat, vest, apron, turtleneck); LEGWEAR (thighhighs, "
    "pantyhose, socks, kneehighs — with colour); FOOTWEAR (boots, sneakers, high heels, sandals, "
    "mary janes); HEADWEAR + worn ACCESSORIES (hat, beret, gloves, scarf, necktie, belt, glasses, "
    "earrings, hair ornament). One coherent colour palette throughout.\n"
    "THE FULL RANGE OF OUTFITS IS WELCOME — casual, school, formal/evening, work/uniform, fantasy or "
    "armour, sleepwear, and SWIMWEAR (bikini, one-piece swimsuit, school swimsuit). Choose what "
    "genuinely fits the character and the scene; do NOT shy away from swimwear when it fits.\n"
    "LITERAL ONLY: plain real colour words, real garment names, concrete materials and silhouettes. "
    "No mood, no metaphor, no flowery language. The ONLY exception: a short phrase describing HOW "
    "an accessory is worn is allowed (e.g. 'scarf loosely knotted at the throat', 'glasses perched "
    "near the tip of her nose'). "
    "CLOTHING + worn ACCESSORIES ONLY: NO body / hair / eye / skin / face tags (those are the base "
    "identity), NO facial expression, NO pose, NO background or scene.\n"
    "Write it as one or two plain sentences — a factual inventory covering every garment and "
    "worn accessory without prose embellishment."
)


# ---------------------------------------------------------------------------
# Per-stage default system prompts
# ---------------------------------------------------------------------------

DEFAULT_SYSTEMS = {
    "storyboard": (
        "You are a story architect — part structural engineer, part emotional cartographer. You "
        "outline a story as the CHAPTERS OF A BOOK: a deliberate, tactical plan rooted in genuine "
        "human truth, NOT flowing prose.\n\n"
        "START WITH THE HEART. Before anything else, find the real human truth at the center of "
        "this story — the thing it is ACTUALLY about beneath the plot. Not a theme word, not a "
        "summary: a resonant sentence that could open a book. Ask yourself: what does a person "
        "discover, lose, choose, or become here? Write that as the HEART line.\n\n"
        "DRAMATIC STRUCTURE: lay out 6-12 chapters covering the full arc — setup, inciting "
        "incident, rising complication, midpoint turn, crisis, climax, resolution. Adapt as needed, "
        "but every chapter must have a DEFINITE FUNCTION in the arc.\n\n"
        "CHAPTERS MUST BE DISTINCT — this is the most critical rule. Each chapter is a "
        "self-contained episode with its OWN situation, location, and dramatic question. They must "
        "NOT bleed into one continuous scene; expect deliberate jumps in time, place and focus. "
        "If two chapters happen in the same place at the same time with the same beat, merge them.\n\n"
        "FOR EACH CHAPTER, craft two things with equal care:\n"
        "  • NARRATIVE: what concretely happens — events, decisions, turns, stakes. Specific and "
        "    propulsive. 2-3 sentences.\n"
        "  • EMOTIONAL CORE: what shifts INTERNALLY for the protagonist. This is the heart of the "
        "    chapter — the quiet revelation, the wound reopened, the decision that costs something. "
        "    Don't summarise the plot; name the inner change. 1-2 sentences.\n\n"
        "HOOKS: each chapter's hook is a planted seed, a revealed secret, an impossible choice, a "
        "question that CANNOT be left unanswered — not just 'and then...'. Make it genuinely "
        "compelling.\n\n"
        "SCENE PROMPTS: write each chapter's scene prompt as an empty anime background — no people, "
        "no characters. It should MATCH the chapter's emotional tone: a crisis chapter gets stormy "
        "skies and harsh shadows; a tender chapter gets soft warm afternoon light; a revelation "
        "scene gets an eerie stillness. 1-2 vivid painterly sentences.\n\n"
        "WRITING STYLE: study the source material (PERSONA, EXAMPLE DIALOGUE, OPENING MESSAGE) "
        "and MIRROR its voice — tone, vocabulary, register. These are planning notes, not prose "
        "passages.\n\n"
        "LOCATIONS: give EACH chapter a SPECIFIC, evocative setting with its own identity. Avoid "
        "trivial near-duplicates AND generic reuse of one broad place across many chapters. When "
        "revisiting a broad area, use a genuinely different specific spot each time — e.g. 'the "
        "crowded public beach at noon', 'a secluded rocky cove', 'a bonfire on the dark dunes'. "
        "Reuse an exact location name ONLY when two chapters truly share the same spot AND moment.\n\n"
        "Write EXACTLY these lines and nothing else (no markdown, no extra commentary):\n"
        "HEART: <the human truth this story is really about — one resonant sentence>\n"
        "LOGLINE: <one evocative hook sentence>\n"
        "PREMISE: <rich 3-5 sentence paragraph in the character's voice and world>\n"
        "TONE: <a few evocative words>\n"
        "THEMES: <comma-separated, 3-6 themes>\n"
        "CHAPTERS:\n"
        "1. <Chapter Title> | Narrative: <what concretely happens> | Emotional: <what shifts "
        "internally> | Hook: <the tension/question pulling into next chapter> | <Location Name> | "
        "<Characters, comma-separated> | Scene: <1-2 sentence visual background, empty environment, "
        "no people, painterly/evocative, matching this chapter's emotional tone>\n"
        "2. ...\n"
        "Each chapter is ONE line with SEVEN fields separated by ' | '. NEVER put a line break or "
        "a '|' inside a field. The field labels (Narrative:, Emotional:, Hook:, Scene:) must "
        "appear literally. Continue numbering through all chapters."
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
        "FORMAT — CRITICAL: the `background_prompt` goes straight into a natural-language anime "
        "model (Anima / Qwen-Image), which reads expressive descriptive sentences far better than "
        "a tag dump. Write it as one or two vivid, well-composed sentences of prose (you may use "
        "connectives and short clauses) that paint THIS place: lead with the defining subject and "
        "its surfaces/textures, then the time of day, weather and light, and a touch of mood. Begin "
        "it with 'An empty, unpopulated' (or 'no people, no figures,') so it reads as a clean "
        "backdrop. The `description` field stays plain prose (it is for humans, not the image model).\n\n"
        "Pick the starting location (where the story opens)."
    ),
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
        "You are a character art director selecting the OUTFITS for a visual-novel character. "
        "Your only job here is to decide WHICH outfits this character needs — a dedicated per-outfit "
        "pass will write the full detailed prompt for each one.\n\n"
        "OUTFITS — the FIRST outfit is always a SWIMSUIT look: name it 'Base (swimwear)' and give "
        "the brief concept (e.g. 'red string bikini' or 'navy swim trunks'). THEN add the distinct "
        "outfits this character genuinely needs ACROSS THIS STORY: usually just one main outfit; add "
        "more only when the plot clearly changes their attire (a transformation, a time-skip, a formal "
        "event, a disguise, ruined/bloodied clothing).\n\n"
        "For EACH outfit, give:\n"
        "  • `name` — a short descriptive label (e.g. 'Casual', 'School uniform', 'Evening gown')\n"
        "  • `concept` — a 3-10 word visual sketch of the key garment(s) and palette ONLY. "
        "Do NOT write a full description — that happens in the next pass.\n\n"
        "(Emotional expressions are a fixed canonical taxonomy handled separately — do not plan them here.)\n"
        "Keep this pass FAST AND LIGHT — resist the urge to detail accessories or colours beyond "
        "what names the outfit."
    ),
    "base_image": (
        "You are a character art director. Read the character and fill EVERY field of the "
        "physical-feature schema for their base reference image. Draw specific detail from the "
        "persona/appearance notes; where sparse, INFER tasteful detail that fits their world, age "
        "and role — but never contradict anything stated. These are PERSISTENT physical traits "
        "only — NO clothing, pose, expression or background (added later). Give the `appearance` "
        "field as a LIST of short, EXPLICIT, ATOMIC visual descriptors — one attribute per item "
        "('silver hair', 'long hair', 'wavy hair', 'violet eyes', 'pale skin', 'mole under eye'), "
        "NOT compound phrases ('long silver hair' -> split into 'long hair' + 'silver hair'). The "
        "system weaves these descriptors into a coherent image prompt, so keep each one a single "
        "clear visual attribute. Use the enum values for the other fields. Stay vivid, specific and "
        "internally consistent — never describe contradictory traits."
    ),
}

STAGES = ["storyboard", "scenes", "characters"]

# Stages that require a VISION-capable model (currently none — base_image uses text).
NEEDS_IMAGE: set[str] = set()


# ---------------------------------------------------------------------------
# Low-level helpers (shared by multiple stages)
# ---------------------------------------------------------------------------

def _card_context(name: str, persona: str, extras: dict) -> str:
    parts = [f"CHARACTER NAME: {name}", "", "PERSONA:", persona or "(none)"]
    label = {"scenario_text": "SCENARIO (from card)", "first_mes": "OPENING MESSAGE (from card)",
             "mes_example": "EXAMPLE DIALOGUE", "appearance": "APPEARANCE"}
    for k, lbl in label.items():
        v = (extras or {}).get(k)
        if v:
            parts += ["", f"{lbl}:", str(v)[:2500]]
    return "\n".join(parts)


def _sys(systems: dict, stage: str) -> str:
    return (systems or {}).get(stage) or DEFAULT_SYSTEMS[stage]


def _slug(s: str, fallback: str = "x") -> str:
    out = re.sub(r"[^\w\-]+", "-", (s or "").strip().lower()).strip("-")
    return out or fallback


def _call(provider, system: str, prompt: str, schema: dict, stage: str, on_delta=None) -> dict:
    res = provider.generate_text(system=system, prompt=prompt, emits=schema, on_delta=on_delta)
    data = res.data or {}
    if not data:
        raise ValueError(f"stage '{stage}' returned no structured data "
                         f"(the author model may not support structured output)")
    return data
