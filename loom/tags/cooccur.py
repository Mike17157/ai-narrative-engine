"""Tag co-occurrence retrieval from real Danbooru character bundles.

`loom/data/danbooru_character.csv` lists characters with their `core_tags` — the appearance
tags that actually appear TOGETHER for that character (mined from posts, frequency-ordered).
Treating each bundle as a "document", we retrieve, for a draft set of tags, the bundles that
overlap it most and surface their OTHER tags as **companions** — tags real artists package
with what the draft already has. This is a far better enrichment signal than name-similarity
(which only finds siblings of the same facet), and it's what informs the 2nd pass of base-image
prompt generation.

Companions are filtered toward PERSISTENT PHYSICAL identity: clothing / accessories / pose /
scene / count / meta are dropped (the base layers outfits separately), as are sleepy-eye tags and
any sibling that contradicts a facet the draft already fixed (e.g. no 'long hair' when the draft
says 'very short hair'). NSFW is deliberately KEPT — this pool is fed to deepseek, which curates
the final list under its own clothed/non-sexualised rules; only minor-sexualisation tags
(loli/shota) are hard-blocked here.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path
from threading import Lock

_DATA = Path(__file__).resolve().parent.parent / "data" / "danbooru_character.csv"

# Substring blocklist — a companion is dropped if its name contains any of these. Covers
# clothing, accessories / headwear / fantasy parts, pose/camera, transient expression, scene,
# NSFW, and the sleepy-eye tags we never want (mirrors app.py's _AESTHETIC_BLOCK).
_BLOCK = (
    # clothing
    "shirt", "dress", "skirt", "jacket", "coat", "uniform", "hoodie", "sweater", "pants",
    "shorts", "bikini", "swimsuit", "swimwear", "gloves", "boots", "shoes", "socks",
    "thighhighs", "kneehighs", "sleeves", "leotard", "bra", "panties", "apron", "cape",
    "armor", "kimono", "robe", "cardigan", "vest", "blazer", "tank top", "camisole", "lingerie",
    "underwear", "bodysuit", "cloak", "hood", "jeans", "overalls", "romper", "onesie", "clothes",
    "costume", "outfit", "attire", "buttons", "zipper", "frills",
    # accessory / headwear / fantasy parts
    "hat", "headwear", "cap", "helmet", "ribbon", "ornament", "hairclip", "hairband", "hairpin",
    "glasses", "eyewear", "scarf", "necktie", "collar", "belt", "jewelry", "earring", "necklace",
    "choker", "bracelet", "crown", "tiara", "veil", "mask", "wings", "tail", "horns", "animal ear",
    "halo", "feather", "wand", "staff", "sword", "weapon", "bowtie", "hair bow",
    # pose / camera / transient
    "holding", "sitting", "lying", "kneeling", "standing", "walking", "running", "jumping",
    "looking", "arms ", "arm ", "hand ", "hands ", "leg up", "legs up", "spread", "from ", "pov",
    "portrait", "upper body", "cowboy shot", "full body", "smile", "smiling", "blush", "crying",
    "laughing", "open mouth", ":d", ":o", "grin", "frown", "wink", "tongue", "teeth",
    # scene
    "background", "indoors", "outdoors", "sky", "tree", "water", "wall", "scenery", "petals",
    # sleepy / ugly eyes
    "closed eyes", "half-closed", "half closed", "jitome", "eyebag", "empty eyes", "rolling eyes",
    # count / meta
    "virtual youtuber", "solo", "multiple ", "2girls", "3girls", "1other",
)
# NSFW is intentionally NOT blocked — these companions are a SUGGESTION pool fed to deepseek,
# which writes the final physical-identity list under its own (clothed, non-sexualised) rules;
# pre-filtering would lose real signal. The ONE firm exception is minor-sexualisation tags,
# which are always dropped here regardless.
_COUNT_RE = re.compile(r"^\d+\s*(boy|girl|man|woman|male|female|other|koma)s?$")
_EXACT_BLOCK = {"loli", "shota"}  # minor-safety only — always dropped

# Clothing / worn-accessory substrings — the INVERSE of the body-identity filter, used by
# related_clothing() to surface garments + accessories + piercings real characters wear together
# (the wardrobe counterpart of the appearance enrichment).
_CLOTHING_KEEP = (
    "shirt", "dress", "skirt", "jacket", "coat", "uniform", "hoodie", "sweater", "pants", "shorts",
    "bikini", "swimsuit", "swimwear", "gloves", "boots", "shoes", "socks", "thighhighs", "kneehighs",
    "sleeves", "leotard", "bra", "panties", "apron", "cape", "armor", "kimono", "robe", "cardigan",
    "vest", "blazer", "tank top", "camisole", "lingerie", "bodysuit", "cloak", "jeans", "overalls",
    "romper", "onesie", "buttons", "zipper", "frills", "serafuku", "pantyhose", "legwear", "loafers",
    "sneakers", "heels", "sandals", "necktie", "scarf", "belt", "choker", "bracelet", "earring",
    "earrings", "necklace", "piercing", "anklet", "garter", "wristband", "armband", "headband",
    "ribbon", "bowtie", "hair bow", "glasses", "hat", "beret", "crown", "tiara", "veil",
)
# clothing-substring false positives that are actually BODY tags — never treat as clothing.
_CLOTH_EXCLUDE = {"collarbone", "navel", "cleavage", "midriff"}

# Facet buckets (shared with graph.py) — the curation lever lives in facets.py.
from .facets import (  # noqa: E402
    FACETS_APPEARANCE as _FACETS_APPEARANCE,
    FACETS_CLOTHING as _FACETS_CLOTHING,
    MAKEUP_KEEP as _MAKEUP_KEEP,
)

# If the draft fixes a member of one of these facets, don't suggest a conflicting sibling.
_EXCLUSIVE = [
    {"very short hair", "short hair", "medium hair", "long hair", "very long hair", "absurdly long hair"},
    {"flat chest", "small breasts", "medium breasts", "large breasts", "huge breasts", "gigantic breasts"},
    {"petite", "slim", "toned", "athletic", "curvy", "plump", "muscular"},
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


class CooccurIndex:
    """Inverted index over character tag-bundles for companion retrieval."""

    def __init__(self, path: Path | str = _DATA):
        self.path = Path(path)
        self.rows: list[tuple[frozenset[str], int]] = []     # (tagset, solo_count)
        self.inv: dict[str, list[int]] = {}                  # tag -> row indices
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                tags = [_norm(t) for t in (row.get("core_tags") or "").split(",") if t.strip()]
                if len(tags) < 3:
                    continue
                try:
                    sc = int(row.get("solo_count") or 0)
                except ValueError:
                    sc = 0
                idx = len(self.rows)
                self.rows.append((frozenset(tags), sc))
                for t in set(tags):
                    self.inv.setdefault(t, []).append(idx)

    @property
    def ready(self) -> bool:
        return bool(self.rows)

    def _ok(self, t: str) -> bool:
        return not _COUNT_RE.match(t) and not any(b in t for b in _BLOCK)

    def _is_clothing(self, t: str) -> bool:
        if _COUNT_RE.match(t) or t in _EXACT_BLOCK or t in _CLOTH_EXCLUDE:
            return False
        if t.startswith("no ") or "braid" in t:  # absence tag / hairstyle — not a worn item
            return False
        return any(c in t for c in _CLOTHING_KEEP)

    def _candidates(self, q: set[str], k_chars: int) -> list[int]:
        """The k best-matching character bundles for a draft tag set (shared by related/_clothing)."""
        cand: set[int] = set()
        for t in sorted(q, key=lambda t: len(self.inv.get(t, []))):
            post = self.inv.get(t)
            if not post:
                continue
            if len(post) < 25000:
                cand.update(post[:6000])
            if len(cand) > 120000:
                break
        if not cand:
            return []
        return sorted(cand, key=lambda i: (len(self.rows[i][0] & q), self.rows[i][1]),
                      reverse=True)[:k_chars]

    def related_clothing(self, draft: list[str], limit: int = 22, k_chars: int = 60) -> list[str]:
        """Companion CLOTHING / accessory / piercing tags real characters wear together with the
        outfit draft — the wardrobe counterpart of related(). Keeps garments + worn accessories,
        drops body / pose / scene / expression."""
        if not self.rows:
            return []
        q = {_norm(t) for t in draft if t and t.strip()}
        top = self._candidates(q, k_chars)
        if not top:
            return []
        comp: Counter[str] = Counter()
        for i in top:
            for t in self.rows[i][0]:
                if t not in q and self._is_clothing(t):
                    comp[t] += 1
        return [t for t, c in comp.most_common() if c >= 2][:limit]

    def _sample_lines(self, draft: list[str], n: int, keep) -> list[str]:
        """Up to `n` raw reference LINES — each ONE real matching character's tag set (filtered by
        `keep`). An unorganized 'soup' of real examples for the author model to CONSTRUCT from. The
        STANDARD retrieval for image-prompt generation (base appearance AND outfits), preferred over
        the ranked flat companion list (`related`) because intact real examples ground the model."""
        if not self.rows:
            return []
        q = {_norm(t) for t in draft if t and t.strip()}
        top = self._candidates(q, n * 2)
        lines, seen = [], set()
        for i in top:
            tags = sorted(t for t in self.rows[i][0] if keep(t))
            if len(tags) < 2:
                continue
            line = ", ".join(tags)
            if line in seen:
                continue
            seen.add(line)
            lines.append(line)
            if len(lines) >= n:
                break
        return lines

    def sample_clothing_lines(self, draft: list[str], n: int = 50) -> list[str]:
        """Soup of real OUTFITS (each line one character's clothing/accessory tags)."""
        return self._sample_lines(draft, n, self._is_clothing)

    def sample_appearance_lines(self, draft: list[str], n: int = 50) -> list[str]:
        """Soup of real APPEARANCES (each line one character's persistent physical-identity tags)."""
        return self._sample_lines(draft, n, self._ok)

    def _is_wearable(self, t: str) -> bool:
        """Clothing/accessory/piercing OR makeup — the keep filter for the CLOTHING palette."""
        return self._is_clothing(t) or any(m in t for m in _MAKEUP_KEEP)

    def _companions(self, draft: list[str], keep, k_chars: int = 400) -> list[tuple[str, float]]:
        """Companion tags ranked by PMI/lift, not raw frequency. For the top `k_chars` matching
        character bundles, score each companion by how much MORE it appears among matches than at
        random across the whole corpus: lift = (co/K) / (df/N). Surfaces tags SPECIFICALLY
        associated with the draft (e.g. 'garter straps' for 'thighhighs') over ubiquitous filler
        ('1girl'). Support floor co>=2 cuts one-off noise. Returns [(tag, score)] score-desc."""
        if not self.rows:
            return []
        q = {_norm(t) for t in draft if t and t.strip()}
        top = self._candidates(q, k_chars)
        if not top:
            return []
        K, N = len(top), len(self.rows)
        co: Counter[str] = Counter()
        for i in top:
            for t in self.rows[i][0]:
                if t not in q and keep(t):
                    co[t] += 1
        scored: list[tuple[str, float]] = []
        for t, c in co.items():
            if c < 2:
                continue
            df = len(self.inv.get(t, [])) or 1
            lift = (c / K) / (df / N)
            scored.append((t, lift))
        scored.sort(key=lambda x: -x[1])
        return scored

    def _expand(self, draft: list[str], keep, k_chars: int = 400, rounds: int = 1
                ) -> list[tuple[str, float]]:
        """Pseudo-relevance feedback (Rocchio-style): retrieve companions, fold the strongest back
        into the query, retrieve again, merge by max score. Grows the tag set with coherent
        second-order tags no single bundle contained. One round by default (cheap, bounded)."""
        q = [_norm(t) for t in draft if t and t.strip()]
        comp = self._companions(q, keep, k_chars)
        for _ in range(max(0, rounds)):
            if not comp:
                break
            q2 = list(dict.fromkeys(q + [t for t, _ in comp[:15]]))
            merged = dict(comp)
            for t, s in self._companions(q2, keep, k_chars):
                if s > merged.get(t, 0.0):
                    merged[t] = s
            comp = sorted(merged.items(), key=lambda x: -x[1])
        return comp

    def faceted_palette(self, draft: list[str], kind: str = "appearance",
                        per_facet: int = 24, include_misc: bool = False) -> dict[str, list[str]]:
        """An ORGANIZED palette of real, PMI-ranked booru tags for the draft, bucketed by facet —
        the breadth signal for image-prompt construction (replaces the flat 50-line soup). `kind`:
        'appearance' (hair/eyes/skin/body/face) or 'clothing' (top/bottom/dress/outerwear/legwear/
        footwear/headwear/accessories/swimwear/makeup/piercing). Each facet holds its top
        `per_facet` tags. Tags matching no facet (mostly copyright/object noise) are dropped unless
        `include_misc`."""
        if kind == "clothing":
            facets, keep = _FACETS_CLOTHING, self._is_wearable
        else:
            facets, keep = _FACETS_APPEARANCE, self._ok
        comp = self._expand(draft, keep)
        out: dict[str, list[str]] = {f: [] for f, _ in facets}
        out["misc"] = []
        for t, _s in comp:
            for fname, subs in facets:
                if any(sub in t for sub in subs):
                    if len(out[fname]) < per_facet:
                        out[fname].append(t)
                    break
            else:
                if len(out["misc"]) < per_facet:
                    out["misc"].append(t)
        if not include_misc:
            out.pop("misc", None)
        return {f: v for f, v in out.items() if v}

    def related(self, draft: list[str], limit: int = 22, k_chars: int = 60) -> list[str]:
        """Companion tags co-packaged with the draft, ranked by how many of the best-matching
        character bundles contain them. Filtered to persistent physical identity."""
        if not self.rows:
            return []
        q = {_norm(t) for t in draft if t and t.strip()}
        forbid: set[str] = set()
        for fam in _EXCLUSIVE:
            if q & fam:
                forbid |= (fam - q)
        # Gather candidate characters from the MOST SPECIFIC draft tags (fewest postings) — common
        # tags like 'long hair' would drag in tens of thousands and dilute the match.
        cand: set[int] = set()
        for t in sorted(q, key=lambda t: len(self.inv.get(t, []))):
            post = self.inv.get(t)
            if not post:
                continue
            if len(post) < 25000:
                cand.update(post[:6000])
            if len(cand) > 120000:
                break
        if not cand:
            return []
        top = sorted(cand, key=lambda i: (len(self.rows[i][0] & q), self.rows[i][1]),
                     reverse=True)[:k_chars]
        comp: Counter[str] = Counter()
        for i in top:
            for t in self.rows[i][0]:
                if t not in q and t not in forbid and self._ok(t):
                    comp[t] += 1
        # require support from >=2 bundles to cut one-off noise
        return [t for t, c in comp.most_common() if c >= 2][:limit]


_index: CooccurIndex | None = None
_lock = Lock()


def get_cooccur() -> CooccurIndex:
    """Process-wide singleton; built on first use."""
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = CooccurIndex()
    return _index
