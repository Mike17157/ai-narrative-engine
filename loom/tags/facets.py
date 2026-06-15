"""Facet classification for booru tags — the shared vocabulary buckets used by both the
co-occurrence palette (`cooccur.py`) and the similarity graph (`graph.py`).

A *facet* is a slot in an appearance or outfit (hair, eyes, top, legwear, …). Classification is by
ordered substring match — first match wins, so put the more specific buckets first (swimwear before
top/dress; piercing/makeup before accessories). This is the curation lever: edit these lists to
change how tags are bucketed everywhere.
"""
from __future__ import annotations

# Makeup substrings — worn but not garments; included in the CLOTHING facets (alongside garments +
# piercings) so outfits get makeup, without polluting the appearance facets.
MAKEUP_KEEP = ("lipstick", "eyeshadow", "makeup", "eyeliner", "mascara", "nail polish", "lip gloss")

# ORDER MATTERS — first substring match wins.
FACETS_CLOTHING = [
    ("swimwear", ("bikini", "swimsuit", "swimwear", "swim trunks", "one-piece swimsuit")),
    ("piercing", ("piercing",)),
    ("makeup", MAKEUP_KEEP),
    ("dress", ("dress", "gown", "kimono", "yukata", "cheongsam", "qipao", "leotard", "bodysuit",
               "overalls", "romper", "jumpsuit", "sundress")),
    ("top", ("shirt", "blouse", "tank top", "camisole", "sweater", "crop top", "t-shirt",
             "tube top", "halterneck", "turtleneck", "serafuku", "sailor collar", "vest",
             "school uniform")),
    ("bottom", ("skirt", "pants", "shorts", "jeans", "trousers", "bloomers", "hakama",
                "miniskirt", "buruma")),
    ("outerwear", ("jacket", "coat", "hoodie", "cardigan", "blazer", "cape", "cloak", "robe",
                   "apron", "poncho", "shrug", "bolero", "capelet")),
    ("legwear", ("thighhighs", "kneehighs", "socks", "pantyhose", "legwear", "garter",
                 "stockings", "leggings")),
    ("footwear", ("boots", "shoes", "sneakers", "heels", "sandals", "loafers", "mary janes",
                  "slippers", "geta")),
    ("headwear", ("hat", "beret", "cap", "helmet", "crown", "tiara", "veil", "headband",
                  "hairband", "hair bow", "hair ornament", "hairclip", "hair flower",
                  "headdress", "headphones", "hood")),
    ("sleeves", ("sleeves",)),
    ("accessories", ("gloves", "scarf", "necktie", "belt", "choker", "bracelet", "necklace",
                     "earring", "anklet", "wristband", "armband", "jewelry", "bowtie",
                     "collar", "bag", "glasses", "ribbon", "wings", "bow")),
]
FACETS_APPEARANCE = [
    ("hair", ("hair", "bangs", "ahoge", "ponytail", "twintails", "braid", "bun", "sidelocks",
              "hime cut", "drill")),
    ("eyes", ("eyes", "eyelashes", "heterochromia", "tsurime", "tareme", "eyebrows", "pupils")),
    ("skin", ("skin",)),
    ("body", ("breasts", "chest", "petite", "slim", "toned", "athletic", "curvy", "plump",
              "muscular", "thighs", "hips", "waist", "navel", "build", "abs", "collarbone")),
    ("ears_tail", ("animal ears", "cat ears", "fox ears", "dog ears", "rabbit ears", "wolf ears",
                   "tail", "horns", "halo")),
    ("face", ("mole", "freckles", "scar", "fang", "makeup", "lipstick", "eyeshadow",
              "beauty mark", "mustache", "beard", "stubble", "teeth", "glasses")),
]

# Unified, distinct categories across BOTH kinds — appearance first, then clothing. First substring
# match wins, so every tag lands in exactly ONE category. Used by the editor's "all" view.
FACETS_ALL = FACETS_APPEARANCE + FACETS_CLOTHING

_FACETSETS = {"appearance": FACETS_APPEARANCE, "clothing": FACETS_CLOTHING, "all": FACETS_ALL}


def facet_of(tag: str, kind: str = "clothing") -> str | None:
    """The facet a tag belongs to for `kind` ('appearance' | 'clothing' | 'all'), or None if it
    matches no facet (mostly pose/expression/scene/meta/copyright — deliberately excluded)."""
    for fname, subs in _FACETSETS.get(kind, FACETS_CLOTHING):
        if any(s in tag for s in subs):
            return fname
    return None


def facet_of_any(tag: str) -> str | None:
    """The unified category for a tag across both kinds (appearance-first)."""
    return facet_of(tag, "all")


def head_noun(tag: str) -> str:
    """The garment/feature noun a tag describes — its last word ('navy blue skirt' -> 'skirt')."""
    return (tag or "").strip().rsplit(" ", 1)[-1]


def same_variant(a: str, b: str) -> bool:
    """True if a and b are interchangeable variants of the same item — same head noun, different
    tag (e.g. 'black skirt' vs 'red skirt'). Used to keep the graph connecting COMPLEMENTARY slots
    rather than colour variants of one garment."""
    return a != b and head_noun(a) == head_noun(b)
