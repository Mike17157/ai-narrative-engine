"""Prose discipline for story DATA — the low-level half of the description-units
technique (loom/stories/runtime/units.py renders; this module guards).

One rule, everywhere text is authored, stored, or migrated: **complete sentences or
nothing**. Budgets are enforced by cutting at SENTENCE boundaries (`tighten`), never
by slicing words — the destructive `split()[:6]` caps that produced fragments like
"deflects Rinka's pitches with a new" are the bug class this module exists to kill.

Neutral home (no stories/config imports) so both loom.config.schema and
loom.stories.* can depend on it without a layering inversion.
"""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…])$")
_TERMINAL = tuple(".!?…\"”’*)]")
# Words a complete clause does not END on. A text ending here is almost always a
# truncated fragment, not a stylistic choice.
_DANGLING = {
    "a", "an", "the", "and", "or", "but", "so", "yet", "nor", "for", "with",
    "without", "of", "to", "in", "on", "at", "by", "from", "into", "onto",
    "upon", "over", "under", "toward", "towards", "through", "against",
    "between", "among", "about", "before", "after", "while", "when", "because",
    "although", "though", "unless", "until", "if", "as", "like", "than",
    "that", "which", "who", "whose", "whom", "what", "whatever", "their",
    "his", "her", "its", "our", "your", "my", "this", "these", "those",
    "some", "any", "every", "each", "no", "not", "very", "too", "more",
    "most", "less", "least", "such", "own", "same", "new", "old", "first",
    "last", "next", "other", "another", "both", "either", "neither",
}


def tighten(text: str, max_words: int) -> str:
    """Budget a block to `max_words` by dropping trailing SENTENCES. Never cuts a word.
    If even the first sentence exceeds the budget it is kept whole (a complete overlong
    sentence beats a fragment)."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    if len(text.split()) <= max_words:
        return text
    sentences = [s for s in _SENTENCE_END.split(text) if s.strip()]
    out: list[str] = []
    total = 0
    for s in sentences:
        n = len(s.split())
        if out and total + n > max_words:
            break
        out.append(s)
        total += n
    return " ".join(out)


def looks_like_fragment(text: str) -> bool:
    """Heuristic: does this text read as a TRUNCATED unit rather than a complete one?
    Complete = ends on terminal punctuation; a short census label ("rival", "mom's
    kitchen") is also fine. A text of 4+ words ending on a dangling function word, or
    on plain letters with no terminal mark, is a fragment."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith(_TERMINAL):
        return False
    words = t.split()
    if len(words) <= 3:                     # census labels and name-like phrases pass
        return False
    tail = re.sub(r"[^\w']+", "", words[-1]).lower()
    if tail in _DANGLING:
        return True
    return True                             # long, unpunctuated, non-label → fragment


def lint_text(text: str, *, path: str, max_words: int = 40) -> list[dict]:
    """One text field → zero or more prose issues (machine-readable, compiler-shaped:
    code/severity/path/message/fix). Never mutates — callers decide repair policy."""
    t = (text or "").strip()
    if not t:
        return []
    issues = []
    if looks_like_fragment(t):
        issues.append({
            "code": "prose.fragment", "severity": "warning", "path": path,
            "message": f"reads as a truncated fragment: \"{t[-60:]}\"",
            "fix": "rewrite as a complete sentence (or a short label if it is a census term)",
        })
    elif len(t.split()) > max_words:
        issues.append({
            "code": "prose.over_budget", "severity": "info", "path": path,
            "message": f"{len(t.split())} words (budget {max_words}) — the narrator reads every one, every turn",
            "fix": "tighten to the load-bearing sentences",
        })
    return issues


def lint_story_texts(story: dict) -> list[dict]:
    """Lint every narrator-facing text field of a story card (dict shape, pre-Story-model).
    The write gate and the scenario compiler both call this so a fragment is caught at
    ingest AND surfaced at readiness. Covers: premise/tone, relationships, locations,
    scenes, first-day events, conditions, arcs, cast home scenes."""
    issues: list[dict] = []
    premise = str(story.get("premise") or "")
    issues += lint_text(premise, path="premise", max_words=45)
    for rel in story.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        rid = rel.get("id") or f"{rel.get('source')}→{rel.get('target')}"
        for fld in ("dynamic", "target_dynamic", "note", "potential", "trajectory"):
            issues += lint_text(str(rel.get(fld) or ""), path=f"relationships[{rid}].{fld}",
                                max_words=35)
    for loc in story.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lid = loc.get("id") or loc.get("name")
        issues += lint_text(str(loc.get("description") or ""),
                            path=f"locations[{lid}].description", max_words=45)
        for sc in loc.get("scenes") or []:
            if isinstance(sc, dict):
                issues += lint_text(str(sc.get("backstory") or ""),
                                    path=f"locations[{lid}].scenes[{sc.get('id') or sc.get('name')}].backstory",
                                    max_words=35)
    fields = story.get("fields") if isinstance(story.get("fields"), dict) else {}
    plan = fields.get("first_day_plan") if isinstance(fields.get("first_day_plan"), dict) else {}
    for ev in plan.get("events") or []:
        if not isinstance(ev, dict):
            continue
        eid = ev.get("id") or ev.get("title")
        for fld in ("visible", "hook"):
            issues += lint_text(str(ev.get(fld) or ""), path=f"first_day_plan[{eid}].{fld}",
                                max_words=60)
    for cond in story.get("conditions") or []:
        if isinstance(cond, dict):
            issues += lint_text(str(cond.get("description") or ""),
                                path=f"conditions[{cond.get('id') or cond.get('name')}].description",
                                max_words=40)
    return issues
