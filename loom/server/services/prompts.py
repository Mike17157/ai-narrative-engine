"""Prompt-composition helpers — free functions with no app state.

The image pipeline is Anima-only (a Qwen-Image natural-language DiT). Anima reads expressive
natural-language prose far better than a flat Danbooru tag list, so this module no longer does
tag-snapping / literal-trap rewrites / BREAK-regionizing. The `_safe_image_tags`, `_snap_prompt` and
`_regionize_prompt` names are kept as thin whitespace-tidying passthroughs so existing call sites
keep compiling and prose flows to the model intact.

The WD14 tagger + the vision CAPTIONER + the vocabulary INDEX are retained (separately) for the
identity-anchor follow-up (tag a rendered/reference image → weave its canonical tags into later
prompts). They are NOT used to *compose* prompts.

Image-prompts are authored directly as prose by GLM 5.2 (see `_NL_BASE_SYSTEM` / `compose_nl_base`,
and the stage system prompts in configs/story_builder.json + loom/scenario/builder.py).
"""

from __future__ import annotations

import re

# The emotion sprite set is now a FIXED canonical taxonomy (Plutchik-grounded ~32). Re-export the
# keys as DEFAULT_EMOTIONS so any legacy reference keeps working. See services/emotions.py.
from .emotions import EMOTION_KEYS as DEFAULT_EMOTIONS  # noqa: F401

# ---------------------------------------------------------------------------
# Vision / tagger system prompts (image → tags). Kept for the identity-anchor
# follow-up and the alternate-outfit / expression / persona flows. These READ
# images into tags; they do not COMPOSE render prompts.
# ---------------------------------------------------------------------------
_DESCRIBE_SYSTEM = (
    "You are an expert anime character tagger. Given a reference image and the character's "
    "persona, output ONE line of lowercase, comma-separated Danbooru tags describing the "
    "character's CANONICAL APPEARANCE so a model can redraw them consistently. "
    "Include: 1girl/1boy/solo as appropriate; the character's name tag ONLY if they are a "
    "clearly recognizable, well-known character; hair colour/length/style; eye colour; distinctive "
    "body features; and their DEFAULT outfit and accessories. Prefer what you actually see in the "
    "image; use the persona only to disambiguate. Do NOT include expression, pose, background, "
    "camera framing, art-style, medium or quality words. No sentences, no trailing period. Tags only."
)
_OUTFIT_SYSTEM = (
    "You compose a natural-language OUTFIT prompt for an established anime character. "
    "You are given the character's canonical appearance, their persona, and an outfit "
    "instruction. Output ONE line of descriptive prose: KEEP every identity cue "
    "(count, name if any, hair, eyes, face, body), and REPLACE clothing, accessories and (only if "
    "the instruction implies it) the setting to match the instruction, written as flowing "
    "descriptive phrases. Keep a neutral expression. "
    "Do NOT add art-style, medium or quality words. No trailing period."
)
_EXPRESSION_SYSTEM = (
    "You choose facial-expression cues for an anime character reacting with a given EMOTION. "
    "TINGE the base emotion with THIS character's personality — the same base feeling reads "
    "differently per person: a coy character's 'happy' is a coy/bashful happy, a sly one's is a "
    "sly/smug happy, a guarded one's is a restrained half-smile. Pick the personality flavour the "
    "persona implies and let it shape the cues (a stoic shows subtle expressions; an energetic one "
    "is exaggerated). Output ONE line of 3-7 comma-separated EXPRESSION cues — facial expression, "
    "eyes, eyebrows, mouth, and emotion-specific cues (blush, tears, sweatdrop, wavy mouth, smirk, "
    "averted gaze, etc.), written as short descriptive phrases. Do NOT restate hair, clothing, body, "
    "background, framing or the character's name. No trailing period."
)

# Persona (the {{user}} side) generation — given the user's long-form self-description, produce BOTH
# (1) a tight chat-ready SUMMARY (prose, for the system prompt) and (2) an APPEARANCE description a
# full-body portrait renders from. The subject describes THEMSELVES; read it as first-person
# self-portrayal and render them faithfully (sex, age, body, style) without inventing traits the
# description doesn't support. Minors are always depicted clothed and non-sexualised.
_PERSONA_SYSTEM = (
    "You turn a user's free-form SELF-DESCRIPTION (who THEY are in the chat) into two fields:\n\n"
    "1) `summary` — a tight 2-3 sentence persona blurb in THIRD person, written as disciplined prose "
    "for a chat system prompt. Distil the essence (identity, personality, demeanour, distinctive "
    "traits). NOT tags, NOT first person, NOT a full background — just the compact read a character "
    "card would carry. Stay faithful to the description; never invent facts it doesn't state.\n\n"
    "2) `appearance` — a vivid, specific 3-6 sentence natural-language description for a FULL-BODY "
    "portrait of THIS person, so the image model can draw them consistently. Read the sex/age/body/"
    "style HONESTLY from the description. Cover hair (colour/length/style), eyes (colour + shape), "
    "skin tone, build, height, and their DEFAULT outfit/accessories. Flowing descriptive prose — "
    "plain colour words, real garment/feature names. Do NOT include expression, pose, background, "
    "camera framing, art-style, medium or quality words. No trailing period.\n\n"
    "If the description is too thin to infer a trait, OMIT that trait rather than guess. Return ONLY the "
    "two fields."
)

# Full-body framing appended to every persona portrait render (mirrors the character base image). The
# portrait is a full-body identity reference (not a bust), so the whole look reads.
_PERSONA_FRAMING = ("solo, full body, standing, facing viewer, looking at viewer, simple background, "
                    "grey background, full body shot, head to toe, feet visible")

# JSON schema for the single persona-describe call (structured output): summary prose + appearance description.
PERSONA_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "appearance"],
    "properties": {
        "summary": {"type": "string",
                    "description": "2-3 sentence third-person persona blurb for the chat system prompt."},
        "appearance": {"type": "string",
                       "description": "a vivid 3-6 sentence natural-language full-body appearance "
                                      "description for a portrait of this person."},
    },
}

# Tier-A of the valence translation layer: authors a character's personality-rooted EMOTIONAL
# EXPRESSION RANGE — a curated SUBSET of the canonical emotion taxonomy, each placed on the
# (valence, arousal) circumplex and NUDGED per persona. The runtime director then emits {v,a} per
# turn and snaps to the nearest key in this range. NOT invention-gated (a deterministic read of the
# persona, like the base-image features). See AppContext.compose_affect_range.
_AFFECT_SYSTEM = (
    "You are a casting director building a character's EMOTIONAL EXPRESSION RANGE — the specific "
    "set of emotions this character can display as portrait sprites. You are given a vocabulary of "
    "emotion keys and a character persona. Your job is to curate a subset that fits the character.\n\n"
    "Rules:\n"
    "(1) SELECT 8-14 keys the character genuinely expresses. Root the selection in personality: a "
    "cheerful optimist centers on happy/amused/excited; a volatile character gets angry/frustrated/"
    "rage; a shy romantic leads with blushed/shy/longing/hopeful. Do NOT return the entire "
    "vocabulary — a range is expressive BECAUSE it is curated and personal.\n"
    "(2) Cover emotional breadth: include at least 2 positive, 2 negative, and 1-2 neutral/ambiguous "
    "keys unless the persona genuinely never goes there.\n"
    "(3) For adult/NSFW characters the intimacy keys (desire, arousal, teasing, submission, ecstasy, "
    "etc.) are available — include them when they fit the persona.\n"
    "(4) Always include 'neutral' — every character needs a resting face.\n\n"
    "Return ONLY the selected emotion keys as a JSON list of strings."
)
# Appended to every portrait render so sprites are consistent, chat-friendly busts. Front-facing,
# straight-on anchors keep the camera steady.
_PORTRAIT_FRAMING = "upper body, front view, facing viewer, straight-on, looking at viewer, simple background"
# Outfit images are FULL BODY (whole-look reference), unlike the face-focused emotion sprites. This is
# CAMERA only — front-view / straight-on anchors lock the angle; stance + limbs come from the per-emotion
# pose library (ctx.pose_tags), so pose and framing never fight over arm position.
_FULLBODY_FRAMING = ("solo, full body, front view, facing viewer, straight-on, looking at viewer, "
                     "full body shot, head to toe, feet visible, simple background, grey background")


def _persona_text(c) -> str:
    """A compact persona blob for the image-prompt model: name + system +
    the descriptive card fields most relevant to look and demeanour."""
    parts = [f"Name: {c.name}" if c.name else ""]
    for fld in ("appearance", "description", "personality", "scenario"):
        v = (c.fields or {}).get(fld)
        if v:
            parts.append(f"{fld.capitalize()}: {v}")
    if c.system:
        parts.append(c.system)
    blob = "\n".join(p for p in parts if p).strip()
    return blob[:4000]  # keep the prompt bounded


def _gen_text(provider, system: str, prompt: str, images: list[str] | None = None) -> str:
    res = provider.generate_text(system=system, prompt=prompt, images=images or [])
    return (res.text or "").strip().replace("\n", " ").strip(" ,.")


# Unified outfit author: one dedicated call per outfit that generates BOTH the character's
# appearance AND their outfit as a single coherent prose prompt. All outfits run in parallel.
# The result replaces the old two-piece (appearance + attire) concatenation at render time.
_UNIFIED_OUTFIT_SYSTEM = (
    "You write a COMPLETE image-generation prompt for ONE character in ONE outfit. "
    "You receive the character's persona and appearance notes, plus the outfit name and a "
    "brief visual concept.\n\n"
    "Write 60-130 words of prose — a single paragraph — covering:\n"
    "  • Physical features: hair (colour, length, style), eyes (colour), skin tone, body type, "
    "any distinctive features — stated plainly and specifically\n"
    "  • The complete outfit: every garment (with colour and material), layers, legwear, footwear, "
    "headwear, accessories, jewellery, piercings — one coherent palette, listed in logical order\n"
    "  • A single characterful pose that reflects their personality — this is the ONE place for "
    "flair: posture, weight, hands, tilt of the head (e.g. 'leaning back with arms loosely crossed, "
    "a faint smirk', 'hands clasped behind her back, heels together, chin slightly down'). "
    "Let the pose set the mood for the whole image.\n\n"
    "Style rules:\n"
    "  • Outfit and features: LITERAL and CONCRETE — plain real colour words, real garment names\n"
    "  • NOT a tag list, NOT comma-dumped descriptors; write connected sentences\n"
    "  • Outfit: NO mood, NO metaphor, NO flowery language\n"
    "  • Accessory fit/drape may use a short descriptive phrase "
    "(e.g. 'scarf loosely knotted at the throat', 'glasses perched near the tip of her nose')\n"
    "  • Do NOT include: expression, background, camera framing, art-style, or quality words\n\n"
    "The text goes directly to a natural-language anime image model — specific prose with one "
    "characterful pose reads far better than either a flat tag list or vague evocative language."
)

UNIFIED_OUTFIT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["prompt"],
    "properties": {
        "prompt": {
            "type": "string",
            "description": (
                "60-130 word prose paragraph: physical appearance (hair, eyes, skin, body) stated "
                "plainly, then the complete outfit (garments, colours, accessories) listed factually, "
                "then ONE characterful pose reflecting personality (posture, hands, tilt). "
                "Outfit is literal; pose may carry flair. No background, camera framing, or quality words."
            ),
        },
    },
}

# The OUTFIT counterpart of the structured base — one complete, detailed outfit. (Emotions are a
# fixed canonical taxonomy; see services/emotions.py.)
OUTFIT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["outfit"],
    "properties": {
        "outfit": {"type": "array", "items": {"type": "string"},
                   "description":
                       "A LIST of ~8-18 short, EXPLICIT garment/accessory descriptors for ONE complete, "
                       "DETAILED outfit — generous, never a lazy sketch. NOT prose, NOT sentences. "
                       "RULES:\n"
                       "(1) ONE item per garment or accessory, each a SINGLE concept of the FORM "
                       "'<colour> <garment>', '<material/pattern> <garment>', or '<accessory>'. Fill "
                       "the placeholders from THIS outfit; do NOT copy them literally. NEVER cram "
                       "several attributes into one item — '<material> <colour> <garment>' is WRONG; "
                       "split into '<colour> <garment>' + '<material>'.\n"
                       "(2) EXPLICIT, vivid words; no metaphor/brand poetry. Plain real colour words; "
                       "descriptive wording is fine (each item becomes part of the rendered prompt).\n"
                       "(3) No 'she wears', no connectives, no sentences.\n"
                       "COVER: main garment(s); layers (jacket/cardigan/coat/vest); LEGWEAR; FOOTWEAR; "
                       "HEADWEAR; ACCESSORIES (jewellery, bag, gloves, belt — note placement: 'single "
                       "bracelet', 'pendant necklace'); PIERCINGS and MAKEUP that fit. ONE coherent "
                       "palette. CLOTHING / ACCESSORIES / PIERCINGS / MAKEUP only — NO body, hair, "
                       "eyes, skin, expression, pose or background."},
    },
}


def _dedupe_outfit_tags(tags: list) -> list:
    """Drop exact dupes AND a tag whose words are a strict subset of another tag with the SAME
    head noun — collapses 'jeans' vs 'black jeans', 'shirt' vs 'white shirt', 'bracelet' vs
    'single bracelet' (keep the more specific). Order-preserving."""
    cleaned, seen = [], set()
    for t in tags:
        t = " ".join(str(t).split())
        tl = t.lower()
        # drop ABSENCE tags ('no jacket', 'no eyewear', 'without …') — they don't belong in a
        # positive prompt — and exact dupes.
        if not t or tl in seen or tl.startswith("no ") or tl.startswith("without "):
            continue
        cleaned.append(t)
        seen.add(tl)
    words = [(t, set(t.lower().split()), t.lower().split()[-1] if t.split() else "") for t in cleaned]
    keep = []
    for t, tw, head in words:
        subsumed = any(o is not t and ohead == head and tw < ow for o, ow, ohead in words)
        if not subsumed:
            keep.append(t)
    return keep


PLAY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "location", "present", "pov", "emotions", "movement"],
    "properties": {
        "reply": {"type": "string"},
        "location": {"type": "string"},
        "present": {"type": "array", "items": {"type": "string"}},
        # The character the narration currently FOLLOWS (close-third viewpoint), or the player's
        # name for their own view. Sticky: it carries between turns and shifts only on an explicit
        # trigger (the POV character leaves, the player moves, a scene break) — see the play prompt.
        "pov": {"type": "string",
                "description": "name of the character whose perspective the narration follows this "
                               "turn (or the player's name for their own viewpoint)"},
        # Per present character: pick ONE emotion key from that character's listed range.
        # The key is used directly for sprite lookup — no coordinate translation.
        "emotions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["character", "emotion"],
            "properties": {
                "character": {"type": "string"},
                "emotion": {"type": "string",
                            "description": "exact emotion key from this character's available range "
                                           "(listed in the system prompt under their name)"},
            }}},
        "movement": {"type": "boolean"},
        # Player lifecycle: the only thing that triggers memory consolidation. "sleeping" → the
        # cast consolidates the scenes they witnessed (a dream/rest pass); "dead" → the storymaster
        # sends the player back to a significant moment. Report "active" on every normal turn.
        "player_status": {"type": "string", "enum": ["active", "sleeping", "dead"],
                          "description": "set to 'sleeping' when the player character sleeps/rests, "
                                         "'dead' when they die, otherwise 'active'"},
    },
}


# ---------------------------------------------------------------------------
# Composition helpers — now natural-language passthroughs.
# Anima reads prose intact, so these are whitespace-tidying no-ops kept under their old names so
# existing call sites compile. (Historically they snapped/regionized/rewrote booru tags for
# Illustrious; that path is gone.)
# ---------------------------------------------------------------------------
def _safe_image_tags(text: str, family: str | None = None) -> str:
    """Tidy whitespace/punctuation in a natural-language prompt. Preserves all words — Anima
    reads expressive prose, so we never rewrite 'literal traps' or strip mood cues."""
    out = text or ""
    return re.sub(r"\s+", " ", out).strip(" ,")


def _snap_prompt(text: str, family: str | None = None) -> str:
    """Natural-language passthrough — returns the prompt tidied, vocabulary-intact."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def _regionize_prompt(text: str, mode: str | None = None, family: str | None = None) -> str:
    """Natural-language passthrough — the author's prose ordering is meaningful, so it is returned
    tidied, not re-sorted into BREAK regions."""
    if not text or not text.strip():
        return text or ""
    return re.sub(r"\s+", " ", text).strip()


def _base_prompt(ch) -> str:
    """The default positive prompt for a character's base image: their own physical `appearance`
    (falls back to the persona/name — never a hard-coded gender) framed as a clean FULL-BODY
    reference in plain swimwear. Minimal clothing on purpose — a complex outfit corrupts the
    identity capture; story outfits are layered on later (wardrobe)."""
    appearance = ((ch.fields or {}).get("appearance")
                  or ch.system or ch.name or "solo").strip()
    male = re.search(r"\b1\s*(boy|man|male)\b", appearance.lower()) is not None
    swim = "swim trunks, bare chest" if male else "bikini"
    return _regionize_prompt(
        f"{appearance}, solo, full body, standing, facing viewer, {swim}, "
        "grey background, simple background, full body shot, head to toe, feet visible")


# ---------------------------------------------------------------------------
# Structured base-appearance feature schema (the deterministic fallback).
# The primary path is `compose_nl_base` (one GLM 5.2 pass that derives these fields AND authors
# prose). This schema is the fallback if the structured-prose call fails — `_assemble_base_prompt`
# then weaves its atomic descriptors into a tidy prose-ish prompt.
# ---------------------------------------------------------------------------
_APPEARANCE_BLOCK = (
    "smil", "grin", "blush", "tear", "cry", "angry", "happy", " sad", "pout", "wink", "laugh",
    "open mouth", "surpris", "scream", "embarrassed",
    "background", "scenery", "indoors", "outdoors",
    "bikini", "swimsuit", "dress", "shirt", "skirt", "jacket", "coat", "uniform", "hoodie",
    "sweater", "blazer", "pants", "shorts", "gloves", "boots", "shoes", "socks",
    "wearing", "clothes", "outfit",
    "sitting", "lying", "kneeling", "jumping", "walking", "running", "from above", "from below",
    "looking",   # gaze is a derived field (see `gaze`) — strip any gaze the prose leaks
)
FEATURES_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["count", "apparent_age", "expression", "skin_tone", "hair_color", "pose", "gaze",
                 "height_cm", "build", "bust", "distinguishing_feature", "appearance"],
    "properties": {
        "count": {"type": "string", "enum": ["1girl", "1boy"],
                  "description": "the character's SEX only: 1girl (female) or 1boy (male). "
                                 "Age is separate — see apparent_age."},
        "apparent_age": {"type": "string", "description":
                         "the character's apparent age, read HONESTLY from the persona — a number "
                         "('24 years old', '10 years old') or a band: 'child' / 'teenager' / "
                         "'young adult' / 'adult' / 'middle-aged' / 'elderly'. Do NOT force every "
                         "character to be an adult; capture their REAL age. (Minors are always "
                         "depicted clothed and non-sexualised.)"},
        "expression": {"type": "string", "description":
                       "ONE persistent RESTING expression by PERSONALITY, and DEFAULT TO WARMTH: "
                       "most people look approachable, so use 'light smile' or 'smile' UNLESS the "
                       "persona is genuinely otherwise. Map: warm/kind/gentle/shy/cheerful -> "
                       "'light smile'/'smile'; playful/mischievous -> 'grin'; confident/cocky -> "
                       "'smirk'; stern/disciplined -> 'serious'; grumpy/cold/cruel -> 'scowl'/"
                       "'glaring' (ONLY if the character is truly hard). Do NOT default everyone "
                       "to smug/serious/cold — that reads cruel. NEVER 'neutral expression'/"
                       "'expressionless' (cold resting-bitch-face) or a big transient emotion."},
        "skin_tone": {"type": "string",
                      "enum": ["pale skin", "light skin", "tan", "dark skin", "very dark skin"],
                      "description": "the character's skin tone — MATCH their heritage (infer from "
                                     "name + persona; don't default non-white characters to pale). "
                                     "TONE only; texture ('shiny skin') and marks ('freckles') go in "
                                     "the appearance list."},
        "hair_color": {"type": "string",
                       "enum": ["black hair", "dark brown hair", "brown hair", "light brown hair",
                                "blonde hair", "platinum blonde", "strawberry blonde", "ginger",
                                "orange hair", "auburn hair", "red hair", "grey hair", "white hair",
                                "blue hair", "pink hair", "purple hair", "green hair"],
                       "description":
                           "the character's HAIR COLOUR. If the persona states one, use it; otherwise "
                           "CHOOSE and VARY across the cast — do NOT default everyone to black/brown, "
                           "and blonde/red/etc. are valid for ANY character. For a natural redhead "
                           "prefer 'auburn hair' over the vivid 'red hair'."},
        "pose": {"type": "string",
                 "enum": ["arms at sides", "hand on hip", "crossed arms", "arms behind back",
                          "hands in pockets", "contrapposto"],
                 "description": "ONE subtle STANDING reference pose that suits the PERSONALITY "
                                "(the base stays standing, full-body, facing viewer)."},
        "gaze": {"type": "string",
                 "enum": ["looking at viewer", "looking to the side", "looking away"],
                 "description":
                     "where the character's EYES point. DEFAULT to 'looking at viewer' — most "
                     "characters MEET the viewer's gaze. 'looking to the side' / 'looking away' "
                     "ONLY for a genuinely shy, timid, aloof or evasive personality."},
        "height_cm": {"type": "integer",
                      "description":
                          "the character's height in CENTIMETRES — a REALISTIC number derived from "
                          "sex, age, build and species, and VARIED across the cast. Rough human "
                          "ranges: adult women ~150-178, adult men ~165-195. Used to SCALE the sprite "
                          "(compositing), NOT as an image tag."},
        "build": {"type": "string",
                  "enum": ["petite", "slim", "slender", "toned", "athletic",
                           "curvy", "voluptuous", "plump", "muscular"],
                  "description":
                      "the character's FIGURE — DERIVE it from CONCRETE persona facts (age, "
                      "profession, training, lifestyle, role); NEVER just default to slim. Aim for "
                      "VARIETY across the cast. 'muscular' is ONLY for male characters or a true "
                      "female bodybuilder."},
        "bust": {"type": "string",
                 "enum": ["flat chest", "small breasts", "medium breasts", "large breasts",
                          "huge breasts"],
                 "description":
                     "chest size, CONSISTENT with the figure + persona (ignored for male/child "
                     "characters in code). Vary it with the build."},
        "distinguishing_feature": {"type": "array", "items": {"type": "string"},
            "description":
                "1-2 DISTINCTIVE facial identity hooks that make THIS face unmistakable and "
                "DIFFERENT from the model's default pretty face. Draw from DIFFERENT categories: a "
                "skin mark (a mole, freckles, a beauty mark, a scar); an eye distinction "
                "(heterochromia, a distinctive eye shape, eyeliner or eyeshadow); eyewear (glasses); "
                "or a feature like a fang. Marks/features only — NOT hair / clothing / expression / pose."},
        "appearance": {"type": "array", "items": {"type": "string"},
                       "description":
                           "A LIST of ~10-20 short, EXPLICIT visual descriptors for THIS character's physical "
                           "look — specific and flattering, what makes them distinct. NOT prose, NOT "
                           "sentences. RULES:\n"
                           "(1) ONE concept per item — a head noun with its qualifier(s) for a SINGLE "
                           "attribute, of the FORM '<length> hair', '<hairstyle>', '<colour> eyes', "
                           "'<eye shape>', '<skin texture>', '<facial mark>'.\n"
                           "(2) EXPLICIT, vivid words; plain real colour words; no metaphor/figurative "
                           "phrasing.\n"
                           "(3) No 'she has', no connectives, no full sentences.\n"
                           "COVER: hair LENGTH + ONE primary style + a detail; eyes (colour + shape, "
                           "eyes OPEN); skin texture + any marks; secondary proportions. NOT hair "
                           "base-colour / height / build / bust (separate fields), NO expression, "
                           "clothing, pose, background or scene."},
    },
}


def _assemble_base_prompt(f: dict, family: str | None = None) -> str:
    """Deterministic FALLBACK base-image prompt from the FEATURES_SCHEMA fields — woven into tidy
    descriptive prose. The primary path is `compose_nl_base` (a single GLM 5.2 pass that authors
    genuinely structured, personality-integrated prose); this runs only when that call fails.

    Preserves the guardrails the structured fields enforce: minor-safety, sex-anchoring, varied
    derived hair/build/bust, committed resting expression/gaze/pose, the full-body/swimwear/grey
    identity-capture framing."""
    count = (f.get("count") or "1girl").strip().lower()
    male = re.search(r"\b1\s*(boy|man|male)\b", count) is not None

    age = (f.get("apparent_age") or "").lower()
    am = re.search(r"\d+", age)
    if am:
        minor = int(am.group()) < 18
    elif any(w in age for w in ("child", "teen", "kid")):
        minor = True
    else:
        minor = False

    if minor:
        attire = "a plain t-shirt and shorts"
    elif male:
        attire = "swim trunks, bare chest"
    else:
        attire = "a simple two-piece swimsuit"

    count_re = re.compile(r"^\d+\s*(boy|girl|man|woman|male|female|other)s?$")

    def _clean(items):
        out = []
        for item in (items or []):
            for atom in str(item).split(","):
                a = atom.strip()
                if a and not count_re.match(a.lower()) and not any(b in a.lower() for b in _APPEARANCE_BLOCK):
                    out.append(a)
        return out

    app = _clean(f.get("appearance"))
    face_hooks = _clean(f.get("distinguishing_feature"))

    _HAIR = ("black hair", "dark brown hair", "brown hair", "light brown hair", "blonde hair",
             "platinum blonde", "strawberry blonde", "ginger", "orange hair", "auburn hair",
             "red hair", "grey hair", "white hair", "blue hair", "pink hair", "purple hair", "green hair")
    hair = (f.get("hair_color") or "").strip().lower()
    if hair not in _HAIR:
        hair = "brown hair"

    _BUILDS = ("petite", "slim", "slender", "toned", "athletic", "muscular",
               "curvy", "voluptuous", "plump")
    build = (f.get("build") or "").strip().lower()
    if build not in _BUILDS:
        build = "athletic"
    if build == "muscular" and not male:
        build = "athletic"
    if minor and build in ("curvy", "voluptuous", "plump", "muscular"):
        build = "slim"

    bust = ""
    if not male and not minor:
        b = (f.get("bust") or "").strip().lower()
        if b in ("flat chest", "small breasts", "medium breasts", "large breasts", "huge breasts"):
            bust = b

    expr = (f.get("expression") or "").strip().lower()
    if not expr or "neutral" in expr or "expressionless" in expr:
        expr = "light smile"
    gaze = (f.get("gaze") or "").strip().lower()
    if gaze not in ("looking at viewer", "looking to the side", "looking away"):
        gaze = "looking at viewer"
    pose = (f.get("pose") or "").strip().lower()
    if pose not in ("arms at sides", "hand on hip", "crossed arms", "arms behind back",
                    "hands in pockets", "contrapposto"):
        pose = "arms at sides"
    skin = (f.get("skin_tone") or "").strip().lower()
    if skin not in ("pale skin", "light skin", "tan", "dark skin", "very dark skin"):
        skin = "light skin"

    pron = "his" if male else "her"
    noun = ("boy" if male else "girl") if minor else ("man" if male else "woman")
    if am and minor:
        subj = f"a {int(am.group())}-year-old {noun}"
    else:
        subj = f"a {noun}"

    look = []
    if hair.lower() not in " ".join(app).lower():
        look.append(hair)
    look.extend(app)
    clauses = [subj]
    if look:
        clauses.append("with " + ", ".join(look))
    if face_hooks:
        clauses.append("distinctive for " + ", ".join(face_hooks))
    if skin:
        clauses.append(skin)
    body_bits = [build] + ([bust] if bust else [])
    clauses.append("a " + ", ".join(body_bits) + " figure")
    main = ", ".join(clauses) + "."

    stance = {"arms at sides": f"arms relaxed at {pron} sides",
              "hand on hip": f"one hand on {pron} hip",
              "crossed arms": "arms crossed",
              "arms behind back": f"hands clasped behind {pron} back",
              "hands in pockets": "hands in pockets",
              "contrapposto": "a relaxed contrapposto"}.get(pose, "standing")
    subj_pron = "He" if male else "She"
    stance_sentence = f"{subj_pron} is standing with {stance}, {expr}, {gaze}."

    framing = f"Dressed in {attire}. Full body, head to toe, against a plain grey background."
    out = " ".join(s.strip() for s in (main, stance_sentence, framing) if s and s.strip())
    return re.sub(r"\s+", " ", out).strip()


# ---------------------------------------------------------------------------
# Anima / natural-language structured-prose author (single pass).
# ---------------------------------------------------------------------------
_NL_BASE_SYSTEM = (
    "You are a character art director. From the character's WRITTEN DESCRIPTION (name, persona, "
    "appearance notes, role), you do TWO things in ONE response:\n"
    "  (A) fill the structured fields — the sex count, apparent age, skin tone, hair colour, build, "
    "bust, height, resting expression, gaze and pose, derived honestly from the description (with "
    "VARIETY across a cast — do not default everyone to brown hair / slim / medium);\n"
    "  (B) WRITE the `prompt` field as a STRUCTURED, richly detailed natural-language appearance "
    "description for a natural-language anime image model (Anima / Qwen-Image), which reads flowing "
    "prose far better than a tag list. This prose is the whole positive prompt the image model gets "
    "(the workflow carries its own quality/style tags), so it must be vivid, specific and complete.\n\n"
    "WRITING THE `prompt` — seven sections in order, each 1-3 sentences, woven into CONNECTED prose "
    "(you may use the section order as a guide but it must read as descriptive writing, not a "
    "bulleted list):\n"
    "  1. OVERVIEW — who they are: sex, apparent age, an overall first impression and the kind of "
    "beauty they have (elegant, cute, sultry, wholesome, striking — VARY it across a cast, never the "
    "same default pretty).\n"
    "  2. HAIR — colour, length, the primary style AND how it falls, plus a telling detail. Let the "
    "PERSONALITY shape it: a disciplined character's hair is controlled and precise; a free spirit's "
    "is loose and unruly; a noble's is formal. Describe how it frames the face.\n"
    "  3. FACE & EYES — the shape of the face, the eyes (colour + shape), the brows, and crucially how "
    "the EYE SHAPE + RESTING EXPRESSION read as personality: soft tareme for warmth/gentleness, sharp "
    "tsurime for fierce/stern/cocky. Eyes open and alert. Note any distinguishing mark (a mole, "
    "freckles, a scar, heterochromia, glasses, makeup).\n"
    "  4. SKIN — tone and texture (a healthy glow, a matte softness, freckles).\n"
    "  5. BODY — the build/figure and proportions, derived from their life (a dancer is toned and "
    "lithe; a scholar slim and slight; a hearty cook plump and warm). Be specific and flattering; do "
    "not default to slim. For adult women note the bust in keeping with the frame. Height where notable.\n"
    "  6. PRESENCE — a subtle standing pose that suits the personality, the resting gaze (most meet "
    "the viewer's eyes; a shy or aloof character looks aside), and the air they carry.\n"
    "  7. ATTIRE + FRAMING — the minimal base attire the reference uses (a simple swimsuit for an "
    "adult, a plain t-shirt and shorts for a child), then: full body, head to toe, against a plain "
    "grey background.\n\n"
    "PERSONALITY IS THE THROUGH-LINE: the same personality should be visible in the hairstyle, the "
    "eyes, the expression, the pose and the build — they must COHERE. Do not describe a shy character "
    "with sharp angry eyes and a cocky smirk. Keep the structured fields CONSISTENT with the prose "
    "(same hair colour, build, expression, etc.).\n\n"
    "HONESTY: read the real age from the persona and reflect it physically; do not force every "
    "character to be an adult. Minors are ALWAYS depicted clothed and non-sexualised — give a child a "
    "youthful face and slighter build, and never an adult/sexualised frame.\n\n"
    "The `prompt` is roughly 120-200 words. NO art-style or quality buzzwords (the workflow carries "
    "those), NO scene/background beyond the plain grey reference backdrop."
)
_NL_BASE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["count", "apparent_age", "skin_tone", "hair_color", "build", "bust", "height_cm",
                 "expression", "gaze", "pose", "prompt"],
    "properties": {
        "count": {"type": "string", "enum": ["1girl", "1boy"],
                  "description": "the character's SEX only: 1girl (female) or 1boy (male)."},
        "apparent_age": {"type": "string", "description":
                         "the character's apparent age, read HONESTLY from the persona — a number "
                         "('24 years old', '10 years old') or a band: child / teenager / young adult / "
                         "adult / middle-aged / elderly. Do NOT force every character to be an adult."},
        "skin_tone": {"type": "string",
                      "enum": ["pale skin", "light skin", "tan", "dark skin", "very dark skin"],
                      "description": "skin tone, MATCHED to the character's heritage."},
        "hair_color": {"type": "string",
                       "enum": ["black hair", "dark brown hair", "brown hair", "light brown hair",
                                "blonde hair", "platinum blonde", "strawberry blonde", "ginger",
                                "orange hair", "auburn hair", "red hair", "grey hair", "white hair",
                                "blue hair", "pink hair", "purple hair", "green hair"],
                       "description": "hair colour — VARY across the cast; do NOT default everyone to "
                                      "black/brown. Blonde/red/etc. are valid for any character."},
        "build": {"type": "string",
                  "enum": ["petite", "slim", "slender", "toned", "athletic", "muscular",
                           "curvy", "voluptuous", "plump"],
                  "description": "figure DERIVED from the character's life; do NOT default to slim. "
                                 "'muscular' is for male characters or a true female bodybuilder."},
        "bust": {"type": "string",
                 "enum": ["flat chest", "small breasts", "medium breasts", "large breasts", "huge breasts"],
                 "description": "chest size consistent with the frame + persona (adult women only)."},
        "height_cm": {"type": "integer", "description":
                      "height in centimetres (used to scale the sprite). Adult women ~150-178, men "
                      "~165-195; vary across the cast."},
        "expression": {"type": "string", "description":
                       "the RESTING expression in the prose — default warm ('a soft smile') unless the "
                       "persona is genuinely otherwise; never a blank 'neutral' face."},
        "gaze": {"type": "string", "description":
                 "where the eyes rest — 'looking at viewer' by default; aside for shy/aloof."},
        "pose": {"type": "string", "description":
                 "a subtle standing reference pose that suits the personality."},
        "prompt": {"type": "string", "description":
                   "The finished structured-prose appearance prompt — 120-200 words, the seven sections "
                   "woven into connected descriptive writing, personality visible throughout. This text "
                   "goes STRAIGHT to the image model; it is the whole positive prompt (minus the "
                   "workflow's own quality tags). Keep it CONSISTENT with the structured fields above."},
    },
}


def compose_nl_base(provider, name: str, persona: str, appearance_notes: str = "",
                    role: str = "") -> dict | None:
    """The Anima/natural-language base-image author in ONE pass: reads the character's description
    directly and returns a dict that BOTH derives the structured fields (count/age/skin/hair/build/
    bust/height/expression/gaze/pose — the consistency/variety guardrails + metadata) AND authors the
    `prompt` as genuinely structured, personality-integrated prose (body part by part; personality
    driving hairstyle / eye shape / expression / pose).

    The returned dict is shaped like FEATURES_SCHEMA fields plus a `prompt` key, so the caller can
    store it as `features` and use `prompt` directly as the image prompt. Returns None on any failure
    (no provider, empty result, provider error) — the caller then falls back to the deterministic
    `_assemble_base_prompt`. Never raises; generation never hard-depends on it."""
    if provider is None or not hasattr(provider, "generate_text"):
        return None
    context = "\n\n".join(p for p in [
        (f"NAME: {name}" if name else ""),
        (f"PERSONA:\n{persona}" if persona else ""),
        (f"APPEARANCE NOTES: {appearance_notes}" if appearance_notes else ""),
        (f"ROLE: {role}" if role else ""),
    ] if p)
    if not context.strip():
        return None
    try:
        data = provider.generate_text(system=_NL_BASE_SYSTEM, prompt=context,
                                      emits=_NL_BASE_SCHEMA).data or {}
    except Exception:  # noqa: BLE001 — never block generation on the structured pass
        return None
    if not data or not (data.get("prompt") or "").strip():
        return None
    data = dict(data)
    data["prompt"] = re.sub(r"\s+", " ", str(data["prompt"]).strip())
    return data
