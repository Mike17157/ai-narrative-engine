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
from __future__ import annotations

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
