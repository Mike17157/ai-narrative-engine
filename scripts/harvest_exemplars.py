"""Harvest raw reference material into the sources DB, then distill it into exemplar decks.

    python scripts/harvest_exemplars.py --fandom thewanderinginn --limit 15
    python scripts/harvest_exemplars.py --fandom shadow-slave --limit 12
    python scripts/harvest_exemplars.py --anilist 25
    python scripts/harvest_exemplars.py --distill 10 [--model deepseek/deepseek-v4-pro]
    python scripts/harvest_exemplars.py --stats

Harvest stores RAW text + provenance (configs/sources.db); distill compresses source FACTS
into study cards (`_char_exemplars`) — the model only compresses, it can't invent substance
that must come from the source. Both steps are idempotent and independently rerunnable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom.server.services import sources as SRC  # noqa: E402

ROOT = Path(".")
UA = {"User-Agent": "loom-exemplar-harvester/1.0 (private research use)"}


def _get(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _post_json(url: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ── Fandom (MediaWiki API): Category:Characters → per-page wikitext ─────────────────

def harvest_fandom(sub: str, limit: int, category: str = "Category:Characters") -> int:
    base = f"https://{sub}.fandom.com"
    q = (f"{base}/api.php?action=query&list=categorymembers&cmtitle="
         f"{urllib.parse.quote(category)}&cmlimit={min(limit * 2, 200)}&format=json")
    members = [m["title"] for m in _get(q)["query"]["categorymembers"] if m.get("ns") == 0]
    n = 0
    for title in members[:limit]:
        p = (f"{base}/api.php?action=parse&page={urllib.parse.quote(title)}"
             f"&prop=wikitext&format=json")
        try:
            wikitext = _get(p)["parse"]["wikitext"]["*"]
        except Exception as exc:  # noqa: BLE001 — one bad page shouldn't stop the sweep
            print(f"  skip {title}: {exc}")
            continue
        slug = re.sub(r"[^\w]+", "_", title.lower()).strip("_")
        SRC.upsert_source(ROOT, id=f"character:{sub}:{slug}", kind="character", series=sub,
                          title=title, url=f"{base}/wiki/{urllib.parse.quote(title)}",
                          raw=wikitext, meta={"source": "fandom", "category": category})
        n += 1
        print(f"  + {title} ({len(wikitext)} chars)")
        time.sleep(0.4)                      # polite pacing
    return n


# ── AniList (GraphQL): top characters by favourites ─────────────────────────────────

_ANILIST_Q = """query($page:Int,$per:Int){ Page(page:$page,perPage:$per){
  characters(sort:FAVOURITES_DESC){ id name{full} favourites description(asHtml:false)
    media(perPage:1,sort:POPULARITY_DESC){nodes{title{romaji}}} } } }"""


def harvest_anilist(limit: int) -> int:
    n, page = 0, 1
    while n < limit:
        per = min(25, limit - n)
        d = _post_json("https://graphql.anilist.co",
                       {"query": _ANILIST_Q, "variables": {"page": page, "per": per}})
        chars = d["data"]["Page"]["characters"]
        if not chars:
            break
        for c in chars:
            desc = (c.get("description") or "").strip()
            if len(desc) < 200:              # too thin to distill honestly
                continue
            media = ((c.get("media") or {}).get("nodes") or [{}])
            series = ((media[0].get("title") or {}).get("romaji") or "") if media else ""
            SRC.upsert_source(ROOT, id=f"character:anilist:{c['id']}", kind="character",
                              series=series, title=c["name"]["full"],
                              url=f"https://anilist.co/character/{c['id']}",
                              raw=desc, meta={"source": "anilist",
                                              "favourites": c.get("favourites")})
            n += 1
            print(f"  + {c['name']['full']} [{series}] (fav {c.get('favourites')})")
        page += 1
        time.sleep(0.7)
    return n


# ── Distill: source facts → study cards in _char_exemplars ──────────────────────────

_CARD_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["who", "want", "introduced_as", "revealed_by", "facets", "usable"],
    "properties": {
        "who": {"type": "string", "description": "one line of grounded context"},
        "want": {"type": "string",
                 "description": "their simple, profound motivation, stated plainly — ONLY if "
                                "the source supports it"},
        "introduced_as": {"type": "string", "description": "how thin their first appearance was"},
        "revealed_by": {"type": "string",
                        "description": "2-3 specific things they DID, as concrete behaviors "
                                       "('kept feeding X even after Y') — NEVER chapter/episode "
                                       "references, never 'as shown in', never meta-description"},
        "facets": {"type": "array", "maxItems": 6,
                   "items": {"type": "string", "maxLength": 24},
                   "description": "1-2 WORD occupation/situation tags ('innkeeper', 'blind', "
                                  "'seer', 'debt') — single words or short compounds, never sentences"},
        "usable": {"type": "boolean",
                   "description": "false if the source is too thin/listy to support an honest card"},
    },
}

_DISTILL_SYS = (
    "You compress SOURCE MATERIAL about a fictional character into a compact study card. Use "
    "ONLY facts present in the source — never invent, never embellish; write in your own plain "
    "words (no quoted sentences). No trait adjectives: behaviors and facts only. revealed_by "
    "must be CONCRETE ACTIONS the character took, in scene terms — citing chapters, episodes, "
    "or 'description and dialogue' is a FAILURE; do the work of naming what they actually did. "
    "If the source is too thin or list-like to support an honest card, set usable=false "
    "(a stub page about a minor character should usually be usable=false). JSON only."
)


def distill(limit: int, model: str) -> int:
    from loom.config.schema import LoreEntry
    from loom.server.app import build_context
    from loom.server.services import lorebook_store as LS

    ctx = build_context(".")
    prov = ctx.text_provider_for(model, {"reasoning_effort": "none", "max_tokens": 2000})
    if prov is None:
        raise SystemExit(f"no provider for {model!r}")
    rows = SRC.undistilled(ROOT, "character", limit)
    done, n = [], 0
    for s in rows:
        prompt = (f"SOURCE MATERIAL — {s['title']} (from {s['series']}):\n{s['raw'][:7000]}\n\n"
                  f"Compress into a study card.")
        try:
            res = prov.generate_text(system=_DISTILL_SYS, prompt=prompt, emits=_CARD_SCHEMA)
            d = getattr(res, "data", None) or {}
        except Exception as exc:  # noqa: BLE001
            print(f"  fail {s['title']}: {str(exc)[:100]}")
            continue
        done.append(s["id"])                 # processed either way — don't re-grind rejects
        if not d.get("usable") or not d.get("want"):
            print(f"  reject {s['title']} (source too thin)")
            continue
        content = (f"WHO: {d['who']}\nWANT: {d['want']}\n"
                   f"INTRODUCED AS: {d['introduced_as']}\nREVEALED BY: {d['revealed_by']}")
        eid = "ex-h-" + re.sub(r"[^\w\-]+", "-", s["id"].split(":", 1)[1])[:48]
        LS.upsert_entry(ROOT, "_char_exemplars", LoreEntry(
            id=eid, title=f"{s['title']} ({s['series']})",
            keywords=[k for k in (d.get("facets") or []) if k][:6],
            content=content, priority=1, source="auto"))
        n += 1
        print(f"  ✓ {s['title']} ({s['series']})")
    SRC.mark_distilled(ROOT, done)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fandom", help="fandom subdomain, e.g. thewanderinginn")
    ap.add_argument("--category", default="Category:Characters")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--anilist", type=int, help="harvest top-N AniList characters")
    ap.add_argument("--distill", type=int, help="distill N undistilled character sources")
    ap.add_argument("--model", default="deepseek/deepseek-v4-pro")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.fandom:
        print(f"harvested {harvest_fandom(a.fandom, a.limit, a.category)} from {a.fandom}")
    if a.anilist:
        print(f"harvested {harvest_anilist(a.anilist)} from AniList")
    if a.distill:
        print(f"distilled {distill(a.distill, a.model)} cards into _char_exemplars")
    if a.stats or not (a.fandom or a.anilist or a.distill):
        print("sources:", json.dumps(SRC.stats(ROOT), indent=1))


if __name__ == "__main__":
    main()
