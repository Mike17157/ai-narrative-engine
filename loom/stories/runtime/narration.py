"""Narrative generation safeguards and dialogue-line formatting."""

from __future__ import annotations

from pathlib import Path
import re

"""Output guards with retry — data-driven off the `_refusal` guard lorebook.

A guard entry is a trigger→action rule: its `keywords` are phrases matched against the
START of the model's OUTPUT (so an in-fiction "I can't" in dialogue never trips it), and
its `script` is the action to run on a hit. v1 registry:

    fallback  → re-run the turn on the configured fallback model

So a refusal ("As an AI, I can't continue…") matches the rule's keywords and fires its
script. Different keyword sets in different entries can point at different scripts. The
built-in floor below always applies (script=fallback) even if the book is emptied.

Every narrator/scribe call routes through `generate_guarded`: run the primary model, scan
its output against the rules, and on a hit (or provider error / invalid structured output)
run the matched script — currently always "reroute to the fallback model".
"""

from pathlib import Path

GUARD_SCOPE = "_refusal"
SCRIPTS = {"fallback"}          # the v1 action registry

# The floor — always active even if the user empties the guard book. Kept HIGH-PRECISION
# (AI-refusal register, not natural dialogue) so an in-fiction "I can't stay" never trips
# it. Generic phrases like "i can't" / "i can not" are deliberately excluded.
BUILTIN_REFUSALS = [
    "as an ai", "as a language model", "i can't assist with", "i cannot assist with",
    "i can't help with that request", "i'm not able to continue", "i'm unable to provide",
    "i cannot provide", "i cannot fulfill", "against my guidelines", "content policy",
    "i won't be able to help with",
]

# Refusals LEAD the reply — scan only the opening so an in-fiction line buried in narration
# can't trip a guard.
_HEAD = 160


def load_rules(root: Path) -> list[dict]:
    """Output guard rules: each `{phrases:[...], script}` from enabled `_refusal` entries
    whose trigger is 'output', plus the builtin floor (script='fallback')."""
    rules: list[dict] = [{"phrases": list(BUILTIN_REFUSALS), "script": "fallback"}]
    try:
        from ...server.services import lorebook_store as LS
        for e in LS.load_lorebook(root, GUARD_SCOPE):
            if not e.enabled or e.trigger != "output":
                continue
            phrases = [k.strip().lower() for k in e.keywords if k.strip()]
            # tolerate the legacy shape where the phrase lived in content
            if e.content.strip():
                phrases.append(e.content.strip().lower())
            if phrases:
                rules.append({"phrases": phrases, "script": (e.script or "fallback")})
    except Exception:  # noqa: BLE001 — never let guard config break generation
        pass
    return rules


def detect(text: str, rules: list[dict]) -> dict | None:
    """Return the first matching rule (with the matched phrase) or None."""
    head = (text or "")[:_HEAD].lower()
    if not head.strip():
        return None
    for rule in rules:
        for p in rule.get("phrases", []):
            if p and p in head:
                return {"phrase": p, "script": rule.get("script", "fallback")}
    return None


def _attempt(provider, *, system, prompt, emits, rules, on_delta=None):
    """Run one provider. Returns (res, error_str|None, hit_rule|None)."""
    try:
        res = provider.generate_text(system=system, prompt=prompt, emits=emits, on_delta=on_delta)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc), None
    text = getattr(res, "text", "") or ""
    data = getattr(res, "data", None)
    # For a structured call, empty/missing data is itself a failure (often a soft refusal).
    if emits is not None and not data:
        return res, None, (detect(text, rules) or {"phrase": "invalid-structured-output", "script": "fallback"})
    return res, None, detect(text, rules)


def generate_guarded(primary, *, system: str, prompt: str, root: Path,
                     emits: dict | None = None, fallback=None, on_delta=None) -> dict:
    """Generate with guard rules. Try *primary*; if the output matches a guard rule (or the
    call errors / returns invalid structured output), run the matched rule's script. v1:
    every script reroutes to *fallback*. Returns:

        { res, text, data, tripped: phrase|None, script: str|None, used_fallback: bool, error }
    """
    # Runtime calls can receive test/dummy providers directly rather than via
    # AppContext.  Apply the same final redaction here so raw history, a scene
    # plan, or a retry prompt cannot reveal author-only ``[[hidden]]`` text.
    from ..visibility import strip_model_hidden
    system = strip_model_hidden(system) or ""
    prompt = strip_model_hidden(prompt) or ""
    rules = load_rules(root)
    res, err, hit = _attempt(primary, system=system, prompt=prompt, emits=emits,
                             rules=rules, on_delta=on_delta)
    script = (hit or {}).get("script") if hit else None

    # v1: the only action is 'fallback' (reroute). A provider error also reroutes.
    if (err or hit) and fallback is not None and fallback is not primary:
        res2, err2, hit2 = _attempt(fallback, system=system, prompt=prompt, emits=emits,
                                    rules=rules, on_delta=on_delta)
        if not err2 and not hit2:
            return {"res": res2, "text": getattr(res2, "text", "") or "",
                    "data": getattr(res2, "data", None),
                    "tripped": (hit or {}).get("phrase") or err, "script": script or "fallback",
                    "used_fallback": True, "error": None}
        best = res2 or res
        return {"res": best, "text": getattr(best, "text", "") or "",
                "data": getattr(best, "data", None),
                "tripped": (hit or {}).get("phrase") or (hit2 or {}).get("phrase") or err,
                "script": script or "fallback", "used_fallback": True,
                "error": err2 or err or f"refusal: {(hit2 or hit or {}).get('phrase')}"}

    return {"res": res, "text": getattr(res, "text", "") or "",
            "data": getattr(res, "data", None),
            "tripped": (hit or {}).get("phrase") if hit else None,
            "script": script, "used_fallback": False,
            "error": err or (f"refusal: {hit['phrase']}" if hit else None)}


# --- dialogue and narration line formatting ---

"""VN/webnovel LINE presentation — the narration split into speaker-attributed lines.

The writer keeps producing flowing PROSE (structured output degrades voice — the whole
prose/scribe split exists for this). Presentation-splitting is done here instead:

  • segment_prose  — CODE cuts the narration into quote/narration segments, verbatim by
    construction (the model never re-copies text, so nothing can be paraphrased away);
  • the scribe     — already reading every turn — only LABELS the numbered segments
    (which cast member speaks each quote; which narration is the player's inner voice);
  • assemble_lines — zips segments + labels into [{speaker, kind, text}] for the UI:
    kind ∈ dialogue | narration | thought ("echoes of the mc's thoughts").

Unlabeled or mislabeled segments degrade gracefully to plain narration/unattributed
dialogue — the flat prose text always remains the source of truth.
"""

import re

# “smart” or "straight" quoted spans — one dialogue line each.
_QUOTE = re.compile(r'“[^”]+”|"[^"]+"')


def segment_prose(text: str) -> list[dict]:
    """Cut prose into ordered segments [{kind: 'quote'|'narr', text}]. Pure string surgery:
    paragraphs split on newlines, quoted spans lifted out as their own segments, the
    surrounding narration (including dialogue tags) kept as narr segments."""
    segs: list[dict] = []
    for para in (p.strip() for p in re.split(r"\n+", text or "")):
        if not para:
            continue
        pos = 0
        for m in _QUOTE.finditer(para):
            before = para[pos:m.start()].strip()
            if before:
                segs.append({"kind": "narr", "text": before})
            segs.append({"kind": "quote", "text": m.group(0)})
            pos = m.end()
        tail = para[pos:].strip()
        if tail:
            segs.append({"kind": "narr", "text": tail})
    return segs


def lines_prompt(segs: list[dict], cast_names: list[str], player_name: str) -> str:
    """The scribe's labeling addendum: the numbered segments + the labeling rule."""
    if not segs:
        return ""
    def _show(s):
        return s["text"][:160] if s["kind"] == "quote" else f"(narration) {s['text'][:80]}"
    numbered = "\n".join(f"{i + 1}. {_show(s)}" for i, s in enumerate(segs))
    return (f"\n\nLINES — label EVERY numbered segment below in `lines` (one entry per number):\n"
            f"- a quoted line → set `speaker` to the exact name ({', '.join(cast_names)}, or "
            f"{player_name} for the player's own spoken words) AND set `emotion` to how the "
            f"speaker feels ON THIS LINE — one key from that character's emotion range listed "
            f"above; the sprite changes per line, so let a shift mid-exchange show.\n"
            f"- narration → `speaker` 'narrator', or 'thought' if it is the player's inner voice "
            f"(what they think/tell themselves, not what happens); leave `emotion` ''.\n{numbered}")


def assemble_lines(segs: list[dict], labels: list | None,
                   cast_names: set[str], player_name: str) -> list[dict]:
    """Zip segments with the scribe's labels → [{speaker, kind, text, emotion?}]. Quotes get a
    validated speaker ('' when unknown — the UI shows an unattributed line) + the speaker's
    per-line `emotion` (passed through raw; the UI resolves it to a sprite, falling back to the
    turn emotion if unknown); narration is 'narrator' unless labeled the player's inner voice."""
    by_i = {}
    for l in labels or []:
        if isinstance(l, dict) and isinstance(l.get("i"), int):
            by_i[l["i"]] = l
    valid = {n.lower(): n for n in cast_names | {player_name} if n}
    out = []
    for i, s in enumerate(segs):
        lab = by_i.get(i + 1) or {}
        speaker = (lab.get("speaker") or "").strip()
        if s["kind"] == "quote":
            out.append({"speaker": valid.get(speaker.lower(), ""), "kind": "dialogue",
                        "text": s["text"], "emotion": (lab.get("emotion") or "").strip()})
        elif speaker.lower() == "thought":
            out.append({"speaker": player_name, "kind": "thought", "text": s["text"]})
        else:
            out.append({"speaker": "narrator", "kind": "narration", "text": s["text"]})
    return out


if __name__ == "__main__":   # ponytail: one runnable check — segment, label, assemble
    prose = ('The library smells of dust and rain.\n\n'
             '“You’re late,” Mara says, not looking up. “Again.”\n\n'
             'It’s just a book, you tell yourself. Just a book.\n"Sorry," you manage.')
    segs = segment_prose(prose)
    kinds = [s["kind"] for s in segs]
    assert kinds == ["narr", "quote", "narr", "quote", "narr", "quote", "narr"], kinds
    assert segs[1]["text"] == "“You’re late,”" and segs[3]["text"] == "“Again.”"
    p = lines_prompt(segs, ["Mara", "Eli"], "You")
    assert "1. (narration)" in p and "2. “You’re late,”" in p
    labels = [{"i": 1, "speaker": "narrator"}, {"i": 2, "speaker": "Mara", "emotion": "stern"},
              {"i": 3, "speaker": "narrator"}, {"i": 4, "speaker": "Mara", "emotion": "annoyed"},
              {"i": 5, "speaker": "thought"}, {"i": 6, "speaker": "You", "emotion": "sheepish"}]
    lines = assemble_lines(segs, labels, {"Mara", "Eli"}, "You")
    assert lines[1] == {"speaker": "Mara", "kind": "dialogue", "text": "“You’re late,”", "emotion": "stern"}
    assert lines[3]["emotion"] == "annoyed"          # emotion can shift mid-exchange
    assert lines[4]["kind"] == "thought" and lines[4]["speaker"] == "You"
    assert lines[5] == {"speaker": "You", "kind": "dialogue", "text": '"Sorry,"', "emotion": "sheepish"}
    # graceful degradation: no labels → plain narration + unattributed dialogue (no emotion)
    bare = assemble_lines(segs, None, {"Mara"}, "You")
    assert bare[1]["speaker"] == "" and bare[1]["emotion"] == "" and bare[0]["speaker"] == "narrator"
    # bogus speaker name rejected, not invented
    bad = assemble_lines(segs, [{"i": 2, "speaker": "Zorg", "emotion": "x"}], {"Mara"}, "You")
    assert bad[1]["speaker"] == ""
    print("ok — segment / prompt / assemble / degrade")
