"""Danbooru tag vocabulary index — the canonical word list the image model actually knows.

Loaded once from ``loom/data/danbooru_tags.csv`` (the merged Danbooru+e621 dump:
``name, category, post_count, "alias,alias,..."``). Powers two things:

* **autocomplete** (`search`) — type-to-find real tags, ranked by post count, and
* **snapping** (`snap`) — force an LLM's free-text tags onto real ones, dropping nothing
  silently: exact → alias → token-reorder → *high-threshold* typo fix, else flagged unknown
  (with suggestions). The typo step REQUIRES a shared token so ``salt spray hair`` can never
  "fix" to ``spray_can`` (the lexical trap that started this) — it's left unknown instead.

There is intentionally NO semantic/embedding step here: it needs a model (sentence-transformers
+ torch) that isn't in this env, and pure string-nearest is actively harmful for the cases we
care about. Unknowns are surfaced to the UI rather than guessed at.
"""
from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# Danbooru tag categories. e621 rows reuse some numbers; we only special-case the ones
# we want to keep OUT of a character/scene description by default (artist/meta/lore names).
CATEGORY = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta"}
# Categories an everyday subject/scene prompt almost never wants from autocomplete.
_NOISY_CATEGORIES = {1, 5}  # artist, meta

# Loom's deliberate control vocabulary — framing / age-safety tokens that AREN'T Danbooru
# tags (the model still parses them) but are intentional. Treated as valid so the editor
# never flags them red. Stored normalized; see CONTROL_TAGS for the source spellings.
CONTROL_TAGS = (
    "1woman", "1man", "1girl", "1boy", "adult",
    "full body shot", "head to toe", "feet visible", "empty hands",
    "BREAK",   # region separator (conditioning concat) — a valid control token, never flagged
)

_DATA = Path(__file__).resolve().parent.parent / "data" / "danbooru_tags.csv"


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace; spaces and underscores are equivalent."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _key(s: str) -> str:
    """Canonical lookup key: normalized, underscores (how the CSV stores tag names)."""
    return _norm(s).replace(" ", "_")


def _display(name: str) -> str:
    """Prompt form of a tag name — underscores back to spaces (escaped parens stay literal)."""
    return name.replace("_", " ")


# Function words that aren't tags and only interrupt multi-word tag matches when the model writes
# prose ("scar across his eye" → drop "his" so "scar across eye" matches). NOT exhaustive on
# purpose — words that ARE part of tags (across/over/under/no) are deliberately kept.
_STOP = frozenset((
    "a", "an", "the", "her", "his", "their", "its", "with", "and", "to", "of", "as", "at", "is",
    "are", "be", "on", "in", "this", "that", "some", "very", "featuring", "wearing", "wears",
    "has", "have", "while", "they", "she", "he", "it", "for",
))


def _lemma(w: str) -> str:
    """Crude stem so 'crocheted'/'crocheting' can match the tag 'crochet' (only for longer words)."""
    if len(w) > 4:
        for suf in ("ing", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                return w[: -len(suf)]
    return w


@dataclass(slots=True)
class Tag:
    name: str          # canonical, underscored (the CSV key)
    category: int
    count: int


class TagIndex:
    """In-memory index over the canonical tag vocabulary. Build once; query freely."""

    def __init__(self, path: Path | str = _DATA):
        self.path = Path(path)
        self.canon: dict[str, Tag] = {}            # name -> Tag
        self.alias: dict[str, str] = {}            # alias name -> canonical name
        self._by_count: list[str] = []             # canon names, count-desc (search scan order)
        self._by_tokens: dict[frozenset[str], str] = {}   # {sorted tokens} -> best canon name
        self._token_idx: dict[str, list[str]] = {}        # token -> canon names (lazy)
        self._token_built = False
        self._prefix3: dict[str, list[str]] = {}          # first-3-chars -> canon names (lazy)
        self._prefix_built = False
        self._control = {_key(t) for t in CONTROL_TAGS}
        self._load()

    # ---- build -------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return  # index simply stays empty; callers treat that as "no validation available"
        with self.path.open(encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh):
                if len(row) < 3 or not row[0]:
                    continue
                name = row[0].strip()
                try:
                    cat, count = int(row[1] or 0), int(row[2] or 0)
                except ValueError:
                    continue
                self.canon[name] = Tag(name, cat, count)
                if len(row) > 3 and row[3]:
                    for a in row[3].split(","):
                        a = a.strip()
                        if a and a not in self.canon:
                            self.alias.setdefault(a, name)
        self._by_count = sorted(self.canon, key=lambda n: -self.canon[n].count)
        # token-reorder map: prefer the highest-count tag for a given token set
        for n in self._by_count:                    # already count-desc → first wins = highest
            self._by_tokens.setdefault(frozenset(n.split("_")), n)

    def _build_token_index(self) -> None:
        if self._token_built:
            return
        for n in self._by_count:                    # keep per-token lists count-desc
            for t in set(n.split("_")):
                self._token_idx.setdefault(t, []).append(n)
        self._token_built = True

    def _build_prefix(self) -> None:
        if self._prefix_built:
            return
        for n in self._by_count:                    # count-desc buckets keyed by first 3 chars
            self._prefix3.setdefault(n[:3], []).append(n)
        self._prefix_built = True

    @property
    def ready(self) -> bool:
        return bool(self.canon)

    # ---- query -------------------------------------------------------------
    def search(self, query: str, limit: int = 20, include_noisy: bool = False,
               only_cat: int | None = None) -> list[dict]:
        """Autocomplete: canonical tags matching `query`, prefix-first then substring,
        ranked within each band by post count. Returns display-ready dicts. `only_cat` restricts
        to one Danbooru category (e.g. 4 = character) — a 'special category' search."""
        qk = _key(query)
        def _catok(n): return only_cat is None or self.canon[n].category == only_cat
        out: list[str] = []
        if not qk:
            out = [n for n in self._by_count
                   if (include_noisy or self.canon[n].category not in _NOISY_CATEGORIES) and _catok(n)][:limit]
        else:
            prefix, sub = [], []
            for n in self._by_count:
                if not include_noisy and self.canon[n].category in _NOISY_CATEGORIES:
                    continue
                if not _catok(n):
                    continue
                if n.startswith(qk):
                    prefix.append(n)
                elif qk in n:
                    sub.append(n)
                if len(prefix) >= limit:
                    break
            out = (prefix + sub)[:limit]
            if only_cat is not None:
                return [self._fmt(n) for n in out]   # category search: skip the fuzzy descriptor backfill
            # Backfill with SIMILAR tags (typo-tolerant) when literal matches are thin, so the
            # combobox still suggests e.g. "purple eyes" for "purpel". Pool = tags sharing a
            # token OR the first 3 chars (cheap). Rank by per-token similarity (NOT whole-string,
            # which would penalise "purple eyes" for being longer than "purpel") then post count.
            if len(out) < limit:
                self._build_prefix()
                have = set(out)
                qt = qk.split("_")
                # fuzzy suggestions should be descriptors, not names — exclude artist/copyright/
                # character/meta (still reachable by exact substring above) unless noisy is asked.
                skip = set() if include_noisy else {1, 3, 4, 5}
                pool = set(self._candidates(qk)) | set(self._prefix3.get(qk[:3], []))
                pool = sorted(
                    (n for n in pool if n not in have and self.canon[n].category not in skip),
                    key=lambda n: -self.canon[n].count)[:1500]

                def _score(n: str) -> float:
                    nt = n.split("_")
                    return sum(max(difflib.SequenceMatcher(None, q, t).ratio()
                                   for t in nt) for q in qt) / len(qt)
                scored = sorted(((s, n) for n in pool if (s := _score(n)) >= 0.66),
                                key=lambda sn: (-sn[0], -self.canon[sn[1]].count))
                out += [n for _, n in scored[:limit - len(out)]]
        return [self._fmt(n) for n in out]

    def resolve(self, raw: str) -> dict:
        """Snap one free-text tag to a real one. Returns
        {input, status, tag, display, suggestions}. status:
        ok | alias | reorder | typo | unknown."""
        raw = (raw or "").strip()
        k = _key(raw)
        if not k:
            return {"input": raw, "status": "empty", "tag": None, "display": "", "suggestions": []}
        if k in self._control:
            return {"input": raw, "status": "control", "tag": k,
                    "display": _display(k), "suggestions": []}
        if k in self.canon:
            return self._verdict(raw, "ok", k)
        if k in self.alias:
            return self._verdict(raw, "alias", self.alias[k])
        toks = frozenset(k.split("_"))
        if toks in self._by_tokens:
            return self._verdict(raw, "reorder", self._by_tokens[toks])
        # high-threshold typo fix — MUST share a token AND the head noun (last token) must
        # itself be close. Sharing only a modifier ("heart-shaped_face" vs "..._cake") is the
        # classic false snap; requiring head-noun similarity rejects it, keeps "purpel eyes".
        head = k.rsplit("_", 1)[-1]
        for b in difflib.get_close_matches(k, self._candidates(k), n=4, cutoff=0.88):
            if (frozenset(b.split("_")) & toks
                    and difflib.SequenceMatcher(None, head, b.rsplit("_", 1)[-1]).ratio() >= 0.7):
                return self._verdict(raw, "typo", b)
        return {"input": raw, "status": "unknown", "tag": None, "display": _display(k),
                "suggestions": [self._fmt(n) for n in self._suggest(k)]}

    def extract(self, text: str, max_n: int = 4) -> dict:
        """Ground free natural language into real booru tags: drop function words, then greedy
        LONGEST-MATCH n-grams (with a crude lemma + token-reorder fallback) against the canonical
        vocabulary. Leftover content words are kept as free-text. Returns
        {tags:[real…], free:[words…], coverage: matched/total}. ('crocheted rainbow bikini' →
        tags=['crochet','rainbow bikini']; 'scar across his eye' → ['scar across eye'].)"""
        raw = re.findall(r"[a-z0-9']+", (text or "").lower())
        words = [w for w in raw if w not in _STOP]
        total = len(words)
        tags: list[str] = []
        free: list[str] = []
        matched = 0
        i = 0
        while i < len(words):
            hit = None
            for n in range(min(max_n, len(words) - i), 0, -1):
                window = words[i:i + n]
                for variant in (window, [_lemma(x) for x in window]):
                    k = "_".join(variant)
                    if k in self.canon:
                        hit = (_display(k), n); break
                    if k in self.alias:
                        hit = (_display(self.alias[k]), n); break
                if hit:
                    break
                fs = frozenset(window)               # token-reorder ('blue navy' → 'navy blue')
                if fs in self._by_tokens:
                    hit = (_display(self._by_tokens[fs]), n); break
            if hit:
                tags.append(hit[0]); matched += hit[1]; i += hit[1]
            else:
                if len(words[i]) > 2:
                    free.append(words[i])
                i += 1
        out, seen = [], set()
        for t in tags:                               # dedup, keep order
            if t not in seen:
                seen.add(t); out.append(t)
        return {"tags": out, "free": free, "coverage": round(matched / total, 2) if total else 0.0}

    def _decompose(self, part: str):
        """If an unknown phrase is really several real tags crammed together ('crochet rainbow
        bikini'), split it into ([real tags], [leftover words]). Returns None unless extraction
        covers most of the phrase (so a genuine single descriptor like 'salt spray hair' is kept
        whole rather than shredded into 'hair' + junk)."""
        ex = self.extract(part)
        if ex["tags"] and (ex["coverage"] >= 0.6 or any(" " in t for t in ex["tags"])):
            return ex["tags"], ex["free"]
        return None

    def snap(self, prompt: str) -> dict:
        """Snap a whole comma-separated prompt. Resolves what it safely can; an unknown phrase that
        is really several real tags is SPLIT into them (+ leftover words kept); truly-unknown text
        is KEPT verbatim (never silently dropped). Dedups; reports changes + unmatched."""
        seen: set[str] = set()
        tags: list[str] = []
        items: list[dict] = []
        changed: list[dict] = []
        unknown: list[dict] = []

        def _emit(disp: str):
            dk = _key(disp)
            if dk not in seen:
                seen.add(dk); tags.append(disp)

        for part in (prompt or "").split(","):
            if not part.strip():
                continue
            r = self.resolve(part)
            if r["status"] == "empty":
                continue
            if r["status"] == "unknown":
                dec = self._decompose(part)
                if dec:                              # split a crammed compound into real sub-tags
                    rtags, residue = dec
                    for t in rtags + residue:
                        _emit(t)
                    changed.append({"from": part.strip(), "to": ", ".join(rtags + residue), "how": "split"})
                    items.append({"input": part, "status": "split", "tag": None,
                                  "display": ", ".join(rtags + residue), "suggestions": []})
                    continue
                unknown.append(r)
            elif r["display"] != _norm(r["input"]):
                changed.append({"from": r["input"].strip(), "to": r["display"], "how": r["status"]})
            items.append(r)
            _emit(r["display"])
        return {"prompt": ", ".join(tags), "tags": tags, "items": items,
                "changed": changed, "unknown": unknown}

    # ---- helpers -----------------------------------------------------------
    def _verdict(self, raw: str, status: str, name: str) -> dict:
        return {"input": raw, "status": status, "tag": name,
                "display": _display(name), "suggestions": []}

    def _fmt(self, name: str) -> dict:
        t = self.canon[name]
        # Only surface the categories worth flagging in the picker (artist/character/copyright);
        # general/meta/e621-numeric stay blank so the list isn't cluttered with "7" labels.
        label = CATEGORY.get(t.category, "") if t.category in (1, 3, 4) else ""
        return {"name": name, "tag": _display(name), "count": t.count, "category": label}

    def _candidates(self, k: str) -> list[str]:
        """Tags sharing at least one token with `k` — the only pool the typo/suggest steps
        consider (so matching stays conceptually anchored, never pure string distance)."""
        self._build_token_index()
        pool: list[str] = []
        for t in set(k.split("_")):
            pool.extend(self._token_idx.get(t, [])[:400])   # cap per token (count-desc)
        return pool

    def _suggest(self, k: str, n: int = 5) -> list[str]:
        """Best replacement guesses for an unknown tag: token-overlap pool, ranked by
        shared-token count then post count, with a couple of fuzzy near-misses folded in."""
        toks = set(k.split("_"))
        pool = dict.fromkeys(self._candidates(k))            # preserves count-desc order, dedup
        scored = sorted(
            pool,
            key=lambda nm: (-len(set(nm.split("_")) & toks), -self.canon[nm].count),
        )[:n]
        for f in difflib.get_close_matches(k, list(pool), n=2, cutoff=0.7):
            if f not in scored:
                scored.append(f)
        return scored[:n]


_index: TagIndex | None = None
_lock = Lock()


def get_index() -> TagIndex:
    """Process-wide singleton; built on first use (the CSV parse takes ~1s)."""
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = TagIndex()
    return _index
