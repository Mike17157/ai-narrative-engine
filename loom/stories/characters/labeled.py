"""Labeled-text routing — the no-JSON layer for generation.

Reasoning models flake on strict json_schema (the grammar fights the reasoning → empty content) but
write labeled prose reliably. So generators emit `KEY: value` blocks and we route them into fields
here. Missing keys degrade to '' (a blank field), never a dead turn — graceful, not all-or-nothing.

`run_text` is the companion: one plain-text (no `emits`) generation call, so nothing has to satisfy a
schema. Self-check: python -m loom.stories.characters.labeled
"""
from __future__ import annotations

import re


def parse_labeled(text: str, keys: list[str]) -> dict:
    """Route `KEY: value` blocks into {key: value}. Order-independent; a value runs until the next known
    key or end (so values may span lines). Keys match case-insensitively at line start (leading bullet/
    quote marks tolerated). Absent keys → ''."""
    out = {k: "" for k in keys}
    if not text:
        return out
    pat = re.compile(r"(?im)^[ \t>*\-•]*(" + "|".join(re.escape(k) for k in keys) + r")[ \t]*:[ \t]*")
    ms = list(pat.finditer(text))
    for i, m in enumerate(ms):
        key = next(k for k in keys if k.lower() == m.group(1).lower())
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[key] = text[m.end():end].strip()
    return out


def parse_records(text: str, keys: list[str], sep: str = "---") -> list[dict]:
    """Parse a repeated record (e.g. cast members), records separated by a line of only `sep`.
    Returns [parse_labeled(chunk, keys), ...] for each non-empty chunk."""
    if not text:
        return []
    chunks = re.split(r"(?m)^[ \t]*" + re.escape(sep) + r"+[ \t]*$", text)
    return [parse_labeled(c, keys) for c in chunks if c.strip()]


def run_text(provider, system: str, prompt: str) -> str:
    """One plain-text (no-JSON) generation call — the model just writes prose, nothing to satisfy a
    schema. Returns stripped text, or '' on no-provider / failure."""
    if not hasattr(provider, "generate_text"):
        return ""
    try:
        from ..visibility import strip_model_hidden
        system = strip_model_hidden(system) or ""
        prompt = strip_model_hidden(prompt) or ""
        return (provider.generate_text(system=system, prompt=prompt).text or "").strip()
    except Exception:  # noqa: BLE001 — a bad turn is a blank field, not a crash
        return ""


def run_labeled(provider, system: str, prompt: str, fields: list[tuple[str, str]]) -> dict:
    """One no-JSON structured call — the adapter that replaces `emits=schema`. `fields` is
    [(KEY, hint), ...]; we append a labeled-output spec derived from it, call plain text, and parse
    tolerantly back into {key: value}. A missing field is '' — never a dead turn."""
    spec = "\n".join(f"{k}: <{h}>" for k, h in fields)
    sysp = f"{system}\n\nOutput ONLY these labelled lines, nothing else:\n{spec}"
    return parse_labeled(run_text(provider, sysp, prompt), [k for k, _ in fields])


def run_labeled_records(provider, system: str, prompt: str, fields: list[tuple[str, str]],
                        sep: str = "---") -> list[dict]:
    """Like run_labeled, but for a REPEATED record (a list of items). Records are separated by a line
    of only `sep`; returns [ {key: value}, ... ]."""
    spec = "\n".join(f"{k}: <{h}>" for k, h in fields)
    sysp = (f"{system}\n\nOutput ONE RECORD per item, records separated by a line containing only "
            f"'{sep}'. Each record is these labelled lines:\n{spec}")
    return parse_records(run_text(provider, sysp, prompt), [k for k, _ in fields], sep)


def demo() -> None:
    d = parse_labeled("NAME: Mira\nWOUND: the house\nburned down\nLIE: nobody stays", ["NAME", "WOUND", "LIE", "GOAL"])
    assert d["NAME"] == "Mira"
    assert d["WOUND"] == "the house\nburned down"          # value spans lines
    assert d["LIE"] == "nobody stays"
    assert d["GOAL"] == ""                                 # absent → blank, not a failure
    # tolerant of bullets/reordering/stray prose
    d2 = parse_labeled("blah\n- GOAL: survive\nNAME: Kip", ["NAME", "GOAL"])
    assert d2["GOAL"] == "survive" and d2["NAME"] == "Kip"
    # records
    recs = parse_records("NAME: A\nARCHETYPE: gremlin\n---\nNAME: B\nARCHETYPE: deadpan", ["NAME", "ARCHETYPE"])
    assert len(recs) == 2 and recs[1]["ARCHETYPE"] == "deadpan"
    assert run_text(object(), "s", "p") == ""             # no provider → '' , no crash

    class _P:                                             # the adapter round-trip
        def generate_text(self, *, system, prompt):
            assert "labelled lines" in system
            class _R: text = "A: one\nB: two"
            return _R()
    assert run_labeled(_P(), "s", "p", [("A", "x"), ("B", "y")]) == {"A": "one", "B": "two"}

    class _PR:
        def generate_text(self, *, system, prompt):
            class _R: text = "A: 1\n---\nA: 2"
            return _R()
    assert [r["A"] for r in run_labeled_records(_PR(), "s", "p", [("A", "x")])] == ["1", "2"]
    print("ok — labeled: tolerant parse + run_text + run_labeled/records adapter")


if __name__ == "__main__":
    demo()
