"""Pure prompt-composition helpers — free functions with no app state.

Bodies are copied verbatim from app.py (the former create_app closures). They use
only their arguments, module-level constants, stdlib, and project imports that are
performed INSIDE the functions (see _snap_prompt / co-occurrence enrichment) to
avoid import cycles.
"""

from __future__ import annotations

import re

# The emotion sprite set is now a FIXED canonical taxonomy (Plutchik-grounded ~32). Re-export the
# keys as DEFAULT_EMOTIONS so any legacy reference keeps working. See services/emotions.py.
from .emotions import EMOTION_KEYS as DEFAULT_EMOTIONS  # noqa: F401

_DESCRIBE_SYSTEM = (
    "You are an expert anime character tagger. Given a reference image and the character's "
    "persona, output ONE line of lowercase, comma-separated Danbooru tags describing the "
    "character's CANONICAL APPEARANCE so an Illustrious/SDXL model can redraw them consistently. "
    "Include: 1girl/1boy/solo as appropriate; the character's booru name tag ONLY if they are a "
    "clearly recognizable, well-known character; hair colour/length/style; eye colour; distinctive "
    "body features; and their DEFAULT outfit and accessories. Prefer what you actually see in the "
    "image; use the persona only to disambiguate. Do NOT include expression, pose, background, "
    "camera framing, art-style, medium or quality words. No sentences, no trailing period. Tags only."
)
_OUTFIT_SYSTEM = (
    "You compose a Danbooru-tag prompt for an alternate OUTFIT of an established anime character. "
    "You are given the character's canonical appearance tags, their persona, and an outfit "
    "instruction. Output ONE line of lowercase, comma-separated booru tags: KEEP every identity tag "
    "(count, name if any, hair, eyes, face, body), and REPLACE clothing, accessories and (only if "
    "the instruction implies it) the setting to match the instruction. Keep a neutral expression. "
    "Do NOT add art-style, medium or quality words. No sentences, no trailing period."
)
_EXPRESSION_SYSTEM = (
    "You choose facial-expression tags for an anime character reacting with a given EMOTION, "
    "personalized to their persona (a stoic character shows subtle expressions; an energetic one is "
    "exaggerated). Output ONE line of 3-7 lowercase, comma-separated Danbooru EXPRESSION tags ONLY "
    "— facial expression, eyes, eyebrows, mouth, and emotion-specific tags (blush, tears, sweatdrop, "
    "nose blush, wavy mouth, etc.). Do NOT restate hair, clothing, body, background, framing or the "
    "character's name. No sentences, no trailing period."
)
# Appended to every portrait render so sprites are consistent, chat-friendly busts.
_PORTRAIT_FRAMING = "upper body, looking at viewer, simple background"
# Outfit images are FULL BODY (whole-look reference), unlike the face-focused emotion sprites.
_FULLBODY_FRAMING = ("solo, full body, standing, full body shot, head to toe, feet visible, "
                     "looking at viewer, simple background, grey background")


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


# The OUTFIT counterpart of FEATURES_SCHEMA — one complete, detailed outfit. (Emotions are NO
# longer generated here — the sprite set is a fixed canonical taxonomy; see services/emotions.py.)
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
                       "(2) EXPLICIT, LITERAL words; no metaphor/brand poetry. Descriptive wording is "
                       "fine (the system grounds each item to a real tag).\n"
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
    "required": ["reply", "location", "present", "emotions", "movement"],
    "properties": {
        "reply": {"type": "string"},
        "location": {"type": "string"},
        "present": {"type": "array", "items": {"type": "string"}},
        "emotions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["character", "emotion"],
            "properties": {"character": {"type": "string"}, "emotion": {"type": "string"}}}},
        "movement": {"type": "boolean"},
    },
}


# Colour-NAME words a literal SDXL model paints as the actual colour, not as the trait
# they describe (the classic: "olive skin" -> green skin). Rewrite the worst offenders to
# plain booru tags so existing prompts self-heal at render time (newer prompts avoid them
# via the feature schema). Skin tones only — clothing colours aren't in the base prompt.
_IMAGE_TAG_FIXES = [
    (r"\bolive(?:[ -](?:skin|complexion|skin tone|toned?|colou?red))\b", "tan"),
    (r"\bolive(?=\s+skin)", "tan"),
    (r"\bporcelain(?:[ -]skin)?\b", "pale skin"),
    (r"\bebony[ -]skin\b", "dark skin"),
    # "young" biases Illustrious childlike — strip it from ADULT subjects. "young adult" /
    # "young woman" / "young man" are grown-ups, so drop the misleading "young". (An actual
    # young girl/boy is written as 1girl/1boy + child/teen, which we leave untouched.)
    (r"\byoung\s+adult\b", "adult"),
    (r"\byoung\s+(woman|man|female|male)\b", r"\1"),
    # plain hair colours — common artistic/metaphor words the model still slips in. NOTE: 'auburn
    # hair' IS a real booru tag and renders as a NATURAL warm reddish-brown (much gentler than the
    # vivid anime 'red hair'), so we KEEP it; only bare 'auburn' is pinned to the hair tag.
    (r"\bbrunette\b", "brown"),
    (r"\braven\s+(hair|black)\b", "black hair"),
    (r"\bauburn\b(?!\s+hair)", "auburn hair"),
    # neutral-grey the base backdrop (RMBG-2.0 mattes it). Rewrite any other bg tag to grey.
    (r"\b(?:plain white|plain simple|plain|white|green|magenta)\s+background\b", "grey background"),
    # literal-model traps: figurative/shape-by-analogy phrases render as the literal object.
    # face/chin/nose SHAPE is barely tagged on Danbooru — drop the figurative ones outright
    # (their old "fixes" pointed chin / narrow eyes were ALSO dead tags). Eyes → real shapes.
    (r"\bheart[- ]shaped\s+face\b", ""),
    (r"\balmond[- ]shaped\s+eyes\b", "tsurime"),
    (r"\balmond\s+eyes\b", "tsurime"),
    (r"\bbutton\s+nose\b", ""),
    (r"\bsharp\s+eyes\b", "tsurime"),
    (r"\beyebags?\b", ""),
    (r"\bnarrow\s+eyes\b", "tsurime"),
    (r"\b(?:pointed|v-shaped)\s+(?:chin|jaw)\b", ""),
    (r"\b(?:star|diamond|oval)[- ]shaped\s+face\b", ""),
]

# Tags that just never render attractively on this checkpoint — half-lidded / shut / tired
# eye looks, etc. Stripped from every image prompt regardless of where it came from. Extend
# this list as more bad-result tags surface.
_AESTHETIC_BLOCK = (
    "closed eyes", "half-closed", "half closed", "jitome", "eyebag", "bags under eyes",
    "one eye closed", "rolling eyes", "empty eyes", "drooping eyes",
)


def _safe_image_tags(text: str) -> str:
    """Rewrite literal-model-hostile phrases (olive skin, dead-tag eye shapes) to plain tags,
    then drop blank fragments AND aesthetically-bad tags (sleepy/closed eyes …), order-keeping.
    `BREAK` region separators are preserved (each region cleaned independently)."""
    if text and "BREAK" in text:
        parts = [_safe_image_tags(p) for p in re.split(r"\bBREAK\b", text)]
        return " BREAK ".join(p for p in parts if p.strip())
    out = text or ""
    for pat, repl in _IMAGE_TAG_FIXES:
        out = re.sub(pat, repl, out, flags=re.I)
    kept = []
    for part in out.split(","):
        p = part.strip()
        if p and not any(b in p.lower() for b in _AESTHETIC_BLOCK):
            kept.append(p)
    return ", ".join(kept)


def _snap_prompt(text: str) -> str:
    """Snap a prompt onto the real Danbooru vocabulary (alias/typo/reorder), KEEPING any
    unknown tags verbatim — non-destructive. A missing/unbuilt index is a silent no-op so
    generation never depends on it. The editor surfaces unknowns; this just canonicalizes."""
    if text and "BREAK" in text:   # snap each region independently, keep the separators
        parts = [_snap_prompt(p) for p in re.split(r"\bBREAK\b", text)]
        return " BREAK ".join(p for p in parts if p.strip())
    try:
        from ...tags import get_index
        ix = get_index()
        return ix.snap(text)["prompt"] if ix.ready else (text or "")
    except Exception:  # noqa: BLE001 — vocabulary is a nicety, never a hard dependency
        return text or ""


# Going-forward prompt shape: every GENERATED prompt is grounded (real tags) then split into BREAK
# regions. 'coarse' (subject · appearance · outfit · details) is the SDXL/Illustrious-friendly
# default; the provider honours BREAK (ConditioningConcat) with a comma strip-fallback.
_BREAK_MODE = "coarse"


def _extract_tags(text: str) -> list:
    """Ground a NATURAL-LANGUAGE description into real booru tags (the default generation method:
    let the model write freely, then snap n-grams onto the canonical vocabulary). Returns a tag
    list. Falls back to a plain comma-split when the vocabulary index is unavailable, so generation
    never hard-depends on it."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        from ...tags import get_index
        ix = get_index()
        if ix.ready:
            return list(ix.extract(text)["tags"])
    except Exception:  # noqa: BLE001 — vocabulary is a nicety, never a hard dependency
        pass
    return [t.strip() for t in re.split(r"[,\n]", text) if t.strip()]


def _regionize_prompt(text: str, mode: str | None = None) -> str:
    """Re-order a comma/BREAK prompt into category BREAK regions (the going-forward shape). Any
    existing BREAK tokens are dropped and re-derived. Silent no-op passthrough if facets are
    unavailable. `mode` defaults to _BREAK_MODE ('coarse')."""
    if not text or not text.strip():
        return text or ""
    try:
        from ...tags.facets import regionize
        # split on commas AND any existing BREAK token, so this is IDEMPOTENT — combining several
        # already-regionized fragments and re-regionizing re-derives clean regions.
        tags = [t.strip() for t in re.split(r"\bBREAK\b|,", text) if t.strip()]
        return ", ".join(regionize(tags, mode or _BREAK_MODE))
    except Exception:  # noqa: BLE001
        return text


def _base_prompt(ch) -> str:
    """The default positive prompt for a character's base image: their own physical
    `appearance` (falls back to the persona/name — never a hard-coded gender) framed as
    a clean FULL-BODY template in plain swimwear. Minimal clothing on purpose — a complex
    outfit corrupts the identity capture; story outfits are layered on later (wardrobe).
    The workflow carries its own quality/style embeddings."""
    appearance = ((ch.fields or {}).get("appearance")
                  or ch.system or ch.name or "solo").strip()
    # Swimwear template by apparent gender (read the count tag in the appearance).
    male = re.search(r"\b1\s*(boy|man|male)\b", appearance.lower()) is not None
    swim = "swim trunks, bare chest" if male else "bikini"
    return _regionize_prompt(_safe_image_tags(
        f"{appearance}, solo, full body, standing, facing viewer, {swim}, "
        "grey background, simple background, full body shot, head to toe, feet visible"))


# -- base-appearance feature schema + assembler ------------------------------

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
                      "description": "the character's skin BRIGHTNESS/tone — MATCH their heritage "
                                     "(infer from name + persona; don't default non-white characters "
                                     "to pale): e.g. East-Asian-coded -> light skin, Latina/"
                                     "Mediterranean -> tan, South-Asian/African -> dark or very dark "
                                     "skin. TONE only; texture ('shiny skin') and marks ('freckles') "
                                     "go in the appearance list. Never 'olive'/'fair' (not real tags)."},
        # FORCED, VARIED hair colour — without it the model collapses to black/brown every time and a
        # cast is never blonde/red/etc. Hair colour is NOT dictated by ethnicity (any character can be
        # any colour); pick from the persona if it states one, else VARY it across the cast.
        "hair_color": {"type": "string",
                       "enum": ["black hair", "dark brown hair", "brown hair", "light brown hair",
                                "blonde hair", "platinum blonde", "strawberry blonde", "ginger",
                                "orange hair", "auburn hair", "red hair", "grey hair", "white hair",
                                "blue hair", "pink hair", "purple hair", "green hair"],
                       "description":
                           "the character's HAIR COLOUR. If the persona states one, use it; otherwise "
                           "CHOOSE and VARY across the cast — do NOT default everyone to black/brown, "
                           "and blonde/red/etc. are valid for ANY character (hair colour is NOT tied to "
                           "skin tone or ethnicity). For a natural redhead prefer 'auburn hair' over the "
                           "vivid 'red hair'. Reserve blue/pink/purple/green for deliberately stylised "
                           "characters."},
        "pose": {"type": "string",
                 "enum": ["arms at sides", "hand on hip", "crossed arms", "arms behind back",
                          "hands in pockets", "contrapposto"],
                 "description": "ONE subtle STANDING reference pose that suits the PERSONALITY "
                                "(the base stays standing, full-body, facing viewer — these are "
                                "template-safe, NOT dynamic/action poses). confident/assertive -> "
                                "'hand on hip' or 'crossed arms'; shy/formal/reserved -> 'arms "
                                "behind back'; casual/relaxed -> 'hands in pockets' or "
                                "'contrapposto'; neutral default -> 'arms at sides'."},
        "gaze": {"type": "string",
                 "enum": ["looking at viewer", "looking to the side", "looking away"],
                 "description":
                     "where the character's EYES point. DEFAULT to 'looking at viewer' — most "
                     "characters MEET the viewer's gaze (engaged, present, making eye contact). "
                     "Choose 'looking to the side' or 'looking away' ONLY for a genuinely shy, timid, "
                     "demure, aloof or evasive personality. A reference face must NEVER stare blankly "
                     "at nothing — always commit to a clear gaze."},
        # FORCED, DERIVED body axes — the biggest anti-sameness lever. Free-text body description
        # collapses to "slim, average height" every time; explicit DERIVED picks do not. HEIGHT and
        # FIGURE are SEPARATE so combinations (a short + curvy 'short stack', a tall + slender model)
        # are reachable. The model must commit and justify by the character's life, not default.
        "height_cm": {"type": "integer",
                      "description":
                          "the character's height in CENTIMETRES — a REALISTIC number derived from "
                          "sex, age, build and species, and VARIED across the cast (do NOT make "
                          "everyone the same). Rough human ranges: adult women ~150-178, adult men "
                          "~165-195, a petite/doll-like adult ~148-156, a tall/imposing one 180+; "
                          "children scale by age; non-human species may exceed these. This is used to "
                          "SCALE the sprite (compositing), NOT as an image tag — give an honest "
                          "number, and make a cast genuinely span short to tall."},
        "build": {"type": "string",
                  "enum": ["petite", "slim", "slender", "toned", "athletic",
                           "curvy", "voluptuous", "plump", "muscular"],
                  "description":
                      "the character's FIGURE — DERIVE it from CONCRETE persona facts (age, "
                      "profession, training, lifestyle, species/role); NEVER just default to slim. "
                      "Aim for VARIETY across the cast — 'petite', 'curvy', 'voluptuous', 'plump', "
                      "'slender', 'toned' should all show up. A dancer/runner -> 'toned'/'slender'; a "
                      "noble/scholar -> 'slim'/'petite'; a hearty cook / earth-mother -> 'plump'/"
                      "'voluptuous'; a bombshell / pin-up -> 'curvy'/'voluptuous'; a small doll-like "
                      "character -> 'petite'. 'muscular' is ONLY for male characters or a true female "
                      "bodybuilder — do NOT make ordinary women muscular. Combine with height for a "
                      "'short stack' (short + curvy/voluptuous) or a 'tall and slender' look."},
        "bust": {"type": "string",
                 "enum": ["flat chest", "small breasts", "medium breasts", "large breasts",
                          "huge breasts"],
                 "description":
                     "chest size, CONSISTENT with the figure + persona (ignored for male/child "
                     "characters in code). Do NOT default everyone to medium — a petite / athletic / "
                     "slender frame usually reads small or flat; a curvy / voluptuous / plump one "
                     "large or huge. Vary it with the build."},
        "distinguishing_feature": {"type": "array", "items": {"type": "string"},
            "description":
                "1-2 DISTINCTIVE facial identity hooks that make THIS face unmistakable and "
                "DIFFERENT from the model's default pretty-anime face — the single biggest lever "
                "against a same-y cast, so NEVER leave it empty. Pick HIGH-SIGNAL booru tags that "
                "render, and VARY the CATEGORY character-to-character (do NOT put the same hook on "
                "everyone — especially don't default everyone to a mole). Draw from DIFFERENT "
                "categories: a skin mark (a mole at some placement, freckles, a beauty mark, a "
                "scar); an eye distinction (heterochromia, a distinctive eye shape, eyeliner or "
                "eyeshadow); eyewear (glasses); or a feature like a fang. Choose what fits the "
                "persona and differs from the rest of the cast. Marks/features only — NOT hair / "
                "clothing / expression / pose."},
        # ATOMIC QUALIFIED DESCRIPTORS — a constrained list, not prose. Each item is ONE explicit
        # attribute (qualifier + head noun); the system grounds each to a real booru tag. Flexible
        # wording, structured shape, single meaning per item.
        "appearance": {"type": "array", "items": {"type": "string"},
                       "description":
                           "A LIST of ~10-20 short, EXPLICIT visual descriptors for THIS character's physical "
                           "look — specific and flattering, what makes them distinct. NOT prose, NOT "
                           "sentences. RULES:\n"
                           "(1) ONE concept per item — a head noun with its qualifier(s) for a SINGLE "
                           "attribute, of the FORM '<length> hair', '<hairstyle>', '<bang style>', "
                           "'<colour> eyes', '<eye shape>', '<skin texture>', '<facial mark>'. Fill the "
                           "placeholders from THIS character; do NOT copy these literally. NEVER cram "
                           "attributes into one item — '<length> <texture> hair' is WRONG; split into "
                           "'<length> hair' + '<texture> hair' (separate items).\n"
                           "(2) EXPLICIT, LITERAL words only — no metaphor/figurative phrasing (avoid "
                           "the likes of 'raven', 'almond eyes', 'emerald', 'olive skin'; use plain "
                           "real terms).\n"
                           "(3) No 'she has', no connectives, no full sentences. Descriptive wording "
                           "is fine (the system grounds each item to a real tag).\n"
                           "COVER: hair LENGTH + ONE primary style + a detail (as SEPARATE items — the "
                           "base COLOUR is the `hair_color` field, do NOT repeat it; only add "
                           "highlights/streaks if any; an afro/dreadlocks/cornrows is all-over coily, "
                           "never with bangs or straight/wavy hair); eyes (colour + shape, eyes OPEN); "
                           "skin texture + any marks; secondary proportions. CHOOSE values that fit "
                           "THIS persona and VARY them across the cast (don't reuse the same features). "
                           "NOT hair base-colour / height / build / bust (separate fields), NO "
                           "expression, clothing, pose, background or scene."},
    },
}

# Tags that cannot truthfully co-exist — the model sometimes emits several (e.g. 'large eyes'
# AND 'small eyes'). Keep only the FIRST seen from each group, drop the rest.
_EXCLUSIVE_GROUPS = [
    {"small eyes", "large eyes"},
    # ONE hair colour — the derived hair_color is injected first, so it wins over any colour the
    # appearance list slips in (keeps a character from being two hair colours at once).
    {"black hair", "dark brown hair", "brown hair", "light brown hair", "blonde hair", "blond hair",
     "platinum blonde", "strawberry blonde", "ginger", "orange hair", "auburn hair", "red hair",
     "grey hair", "gray hair", "white hair", "silver hair", "blue hair", "pink hair", "purple hair",
     "green hair", "aqua hair", "dark blue hair"},
    # ONE gaze — the derived `gaze` is injected first, so it wins over any framing default
    {"looking at viewer", "looking to the side", "looking away", "looking afar",
     "looking up", "looking down", "looking back"},
    {"youthful face", "adult face", "mature face"},
    {"tall", "short", "very short", "average height"},
    # main body build — keep the FIRST the model picks (wide hips / narrow waist may co-exist)
    {"petite", "slim", "slender", "toned", "athletic", "curvy", "voluptuous", "plump",
     "muscular", "muscular female", "muscular male"},
    # bust size — exactly one
    {"flat chest", "small breasts", "medium breasts", "large breasts", "huge breasts",
     "gigantic breasts"},
    # ONE primary tie/updo hairstyle — stops stacking ponytail + bun + twintails (looks odd)
    {"ponytail", "low ponytail", "high ponytail", "side ponytail", "folded ponytail",
     "twintails", "low twintails", "hair bun", "double bun", "single hair bun", "hime cut",
     "drill hair", "twin drills"},
    # ONE base hair texture, and ONE "disorder" tag — avoid wavy+straight or messy+flyaway piles
    {"straight hair", "wavy hair", "curly hair"},
    {"messy hair", "flyaway hair", "disheveled hair", "disheveled hair"},
]


def _assemble_base_prompt(f: dict) -> str:
    """Assemble the base-image prompt from the model's free-form `appearance` tag list plus the
    fixed neutral / full-body / swimwear / grey framing. The model writes rich descriptors
    freely; this only enforces the guardrails it gets wrong: a strong CANONICAL sex anchor by
    age, transient/scene/clothing leakage filtered, and contradictory tags reduced to one."""
    count = (f.get("count") or "1girl").strip().lower()
    male = re.search(r"\b1\s*(boy|man|male)\b", count) is not None

    # Adult vs minor from the apparent age.
    age = (f.get("apparent_age") or "").lower()
    m = re.search(r"\d+", age)
    if m:
        minor = int(m.group()) < 18
    elif any(w in age for w in ("child", "teen", "kid")):
        minor = True
    else:
        minor = False

    # SEX ANCHOR (canonical). '1man'/'1woman' are NOT real Danbooru tags (≈0 images), so a
    # female-skewed style LoRA happily genderbends them; '1boy'/'1girl' are the real tags for
    # ALL ages. AGE is honest, not forced: ADULTS get 'mature male'/'mature female' (the real
    # grown-up anchors) + the minimal swimwear identity-capture template; MINORS get neither
    # 'mature' nor adult-physique anchors, and a MODEST base outfit (never swimwear) — their
    # real age comes through the appearance tags. 'male focus' resists the genderbend either
    # way. (Sexualisation guards — loli/shota — stay in the workflow negatives regardless.)
    if minor:
        gender = ["1boy", "male focus"] if male else ["1girl"]
        attire = "t-shirt, shorts"
    elif male:
        gender = ["1boy", "male focus", "mature male", "pectorals", "flat chest"]
        attire = "swim trunks, bare chest"
    else:
        gender = ["1girl", "mature female"]
        attire = "bikini"

    # Free-form appearance tags: split any crammed strings, drop transient/scene/clothing/pose
    # leakage AND any person-count tag the model slips in (e.g. '1woman', '1girl') — the sex
    # anchor above is authoritative, so a leaked count would only duplicate/contradict it.
    count_re = re.compile(r"^\d+\s*(boy|girl|man|woman|male|female|other)s?$")

    def _clean_tags(items):
        out = []
        for item in (items or []):
            for atom in str(item).split(","):
                a = atom.strip()
                if a and not count_re.match(a.lower()) and not any(b in a.lower() for b in _APPEARANCE_BLOCK):
                    out.append(a)
        return out

    # `appearance` is a LIST of ATOMIC descriptors — filter leakage/count tags; the closing
    # _snap_prompt grounds each item to a real booru tag (and decomposes any compound that slips in).
    app = _clean_tags(f.get("appearance"))
    # DISTINCTIVE FACE HOOKS (mole/freckles/heterochromia/glasses/makeup/…) — placed EARLY so
    # they carry prompt weight and break Illustrious's "house face" prior that otherwise renders
    # every character with the same default anime face.
    face_hooks = _clean_tags(f.get("distinguishing_feature"))
    # Skin TONE is a guaranteed brightness tag (the model used to give only 'shiny skin', a
    # texture, and never a tone). Validate against the gradient; default to a mid 'light skin'.
    skin = (f.get("skin_tone") or "").strip().lower()
    if skin not in ("pale skin", "light skin", "tan", "dark skin", "very dark skin"):
        skin = "light skin"
    # DERIVED HAIR COLOUR (anti-collapse): forced + varied so a cast isn't always black/brown. Placed
    # before *app so it wins the hair-colour _EXCLUSIVE_GROUP. Default 'brown hair' (NOT black) if absent.
    _HAIR = ("black hair", "dark brown hair", "brown hair", "light brown hair", "blonde hair",
             "platinum blonde", "strawberry blonde", "ginger", "orange hair", "auburn hair",
             "red hair", "grey hair", "white hair", "blue hair", "pink hair", "purple hair", "green hair")
    hair = (f.get("hair_color") or "").strip().lower()
    if hair not in _HAIR:
        hair = "brown hair"
    # DERIVED FIGURE (anti-sameness). The body description collapses to "slim" without an explicit,
    # persona-justified pick. (Absolute HEIGHT is NOT a tag — it can't render in a solo full-body
    # shot; it's captured as numeric `height_cm` metadata and applied by sprite scaling at composite
    # time.) Picked before *app so it wins its _EXCLUSIVE_GROUP if the prose leaked a stray body word.
    body_anchor = []
    # FIGURE — default to 'athletic' (NOT 'slim') when missing, the mean we're fighting. 'muscular'
    # is male-only per the user's aesthetic: clamp a muscular WOMAN to 'athletic'.
    _BUILDS = ("petite", "slim", "slender", "toned", "athletic", "muscular",
               "curvy", "voluptuous", "plump")
    build = (f.get("build") or "").strip().lower()
    if build not in _BUILDS:
        build = "athletic"
    if build == "muscular" and not male:
        build = "athletic"
    # MINOR SAFETY: never put an adult/sexualised frame on a child — clamp to a neutral youthful
    # figure and inject NO bust tag (loli/shota guards also live in the workflow negatives).
    if minor and build in ("curvy", "voluptuous", "plump", "muscular"):
        build = "slim"
    body_anchor.append(build)
    if not male and not minor:                       # bust only for adult women (males get
        bust = (f.get("bust") or "").strip().lower() # 'flat chest' from the sex anchor)
        if bust not in ("flat chest", "small breasts", "medium breasts",
                        "large breasts", "huge breasts"):
            bust = "medium breasts"
        body_anchor.append(bust)
    parts = [*gender, skin, hair, *body_anchor, *face_hooks, *app]
    # Persistent RESTING expression by personality (NOT 'neutral expression' — a near-dead tag
    # that renders a cold resting-bitch-face). Fall back to a warm 'light smile'; never let a
    # neutral/expressionless value through. Sprites still vary emotion on top of this base.
    expr = (f.get("expression") or "").strip().lower()
    if not expr or "neutral" in expr or "expressionless" in expr:
        expr = "light smile"
    # Subtle STANDING reference pose by personality (template-safe; default 'arms at sides').
    pose = (f.get("pose") or "").strip().lower()
    if pose not in ("arms at sides", "hand on hip", "crossed arms", "arms behind back",
                    "hands in pockets", "contrapposto"):
        pose = "arms at sides"
    # GAZE — default 'looking at viewer' (eye contact); the model picks 'looking away'/'to the side'
    # only for shy/aloof personas. A reference must never stare blankly at nothing.
    gaze = (f.get("gaze") or "").strip().lower()
    if gaze not in ("looking at viewer", "looking to the side", "looking away"):
        gaze = "looking at viewer"
    # full-body swimwear template framing, on neutral grey (RMBG/Inspyrenet mattes it).
    parts += [expr, gaze, "solo", "full body", "standing", pose, "facing viewer", attire,
              "grey background", "simple background", "full body shot", "head to toe", "feet visible"]
    # normalise underscores → spaces; drop blanks; de-dup; resolve contradictions (keep first).
    seen, used_groups, out = set(), set(), []
    for p in parts:
        p = p.replace("_", " ").strip().strip(",").strip()
        if not p or p.lower() in seen:
            continue
        grp = next((i for i, g in enumerate(_EXCLUSIVE_GROUPS) if p.lower() in g), None)
        if grp is not None:
            if grp in used_groups:
                continue            # already have a tag from this exclusive group
            used_groups.add(grp)
        seen.add(p.lower()); out.append(p)
    low = {t.lower() for t in out}
    # A bun means the hair is gathered UP — a flowing-length tag alongside it ('long hair' +
    # 'hair bun') reads as two hairstyles at once. Drop the length when an updo is present.
    if low & {"hair bun", "double bun", "single hair bun"}:
        out = [t for t in out if t.lower() not in
               ("long hair", "very long hair", "absurdly long hair", "medium hair")]
    # All-over COILY styles (afro / dreadlocks / cornrows) have no separate fringe and aren't
    # smooth — so 'parted bangs + afro' or 'straight hair + dreadlocks' is physically impossible.
    # When one is present, drop every bangs tag and any contradicting smooth texture. ('curly
    # hair' is consistent with an afro, so it stays.)
    if low & {"afro", "dreadlocks", "cornrows"}:
        out = [t for t in out if "bangs" not in t.lower()
               and t.lower() not in ("straight hair", "wavy hair")]
    # (Build + bust are now DERIVED, forced enum fields injected above — no blanket default here.)
    # literal-tag normalizer (olive->tan, …), snap to real booru tags (unknowns kept), then split
    # into coarse BREAK regions (subject · appearance · outfit · details) — the going-forward shape.
    return _regionize_prompt(_snap_prompt(_safe_image_tags(", ".join(out))))
