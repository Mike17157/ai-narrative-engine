"""Generic JSON-schema → labeled-text adapter for structured generation.

Reasoning models flake on strict `json_schema` (the grammar fights the reasoning → empty content) but
write labeled prose reliably. This turns a schema into a labeled-output INSTRUCTION + a PARSER, so a
`generate_text(emits=schema)` call can route through prose while keeping the exact {field: value}
contract — no call site changes.

`schema_to_labeled` returns None for shapes too complex to round-trip faithfully (nested objects,
arrays of nested objects); the provider then keeps `json_schema` for those. `parse_schema_labeled`
fills EVERY field with a typed default when absent, so callers always get the keys they expect.

Handles: an object of scalar fields (string/int/number/bool, incl. enums), arrays of scalars, and
arrays of flat objects. Self-check: python -m loom.providers.labeled_structured
"""
from __future__ import annotations

import re

_SCALARS = ("string", "integer", "number", "boolean")


def _hint(spec: dict, key: str) -> str:
    h = spec.get("description") or key
    if spec.get("enum"):
        h += " (one of: " + ", ".join(str(e) for e in spec["enum"]) + ")"
    return h


def schema_to_labeled(schema):
    """→ (instruction_str, plan) or None if too complex. plan = [(key, kind[, subkeys]), ...]."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    props = schema.get("properties") or {}
    if not props:
        return None
    lines, plan = [], []
    for key, spec in props.items():
        if not isinstance(spec, dict):
            return None
        t, LK = spec.get("type"), key.upper()
        if t in _SCALARS:
            lines.append(f"{LK}: <{_hint(spec, key)}>")
            plan.append((key, t))
        elif t == "array":
            items = spec.get("items") or {}
            it = items.get("type")
            if it in ("string", "integer", "number"):
                lines.append(f"{LK}: (one item per line)\n- <{_hint(spec, key)}>")
                plan.append((key, "list", it))
            elif it == "object":
                sub = items.get("properties") or {}
                if not sub or any(not isinstance(v, dict) or v.get("type") not in _SCALARS for v in sub.values()):
                    return None
                subkeys = list(sub.keys())
                fields = "\n".join(f"  {sk}: <{_hint(sub[sk], sk)}>" for sk in subkeys)
                lines.append(f"{LK}: (one or more records; separate records with a line of only '---'):\n{fields}")
                plan.append((key, "objlist", subkeys))
            else:
                return None
        else:
            return None                                  # nested object / unknown → keep json_schema
    return "Output ONLY these labelled sections, nothing else:\n" + "\n".join(lines), plan


def _blocks(text: str, keys: list[str]) -> dict:
    out = {k: "" for k in keys}
    if not text:
        return out
    pat = re.compile(r"(?im)^[ \t>*\-•]*(" + "|".join(re.escape(k) for k in keys) + r")[ \t]*:[ \t]*")
    ms = list(pat.finditer(text))
    for i, m in enumerate(ms):
        k = next(kk for kk in keys if kk.lower() == m.group(1).lower())
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        out[k] = text[m.end():end].strip()
    return out


def _num(s: str, t: str):
    m = re.search(r"-?\d+(\.\d+)?", s or "")
    if not m:
        return 0
    return int(float(m.group())) if t == "integer" else float(m.group())


def parse_schema_labeled(text: str, plan) -> dict:
    """Route labeled `text` back into the schema shape (all keys present, typed)."""
    raw = _blocks(text or "", [p[0].upper() for p in plan])
    out = {}
    for p in plan:
        key, kind = p[0], p[1]
        block = raw.get(key.upper(), "")
        if kind == "string":
            out[key] = block
        elif kind in ("integer", "number"):
            out[key] = _num(block, kind)
        elif kind == "boolean":
            out[key] = block.strip().lower() in ("true", "yes", "1", "y")
        elif kind == "list":
            out[key] = [ln.strip(" -*•\t") for ln in block.splitlines() if ln.strip()]
        elif kind == "objlist":
            subkeys = p[2]
            items = []
            for rec in re.split(r"(?m)^[ \t]*-{3,}[ \t]*$", block):
                if rec.strip():
                    b = _blocks(rec, subkeys)
                    if any(b.values()):
                        items.append({sk: b.get(sk, "") for sk in subkeys})
            out[key] = items
    return out


def demo() -> None:
    schema = {"type": "object", "properties": {
        "reply": {"type": "string", "description": "the reply"},
        "n": {"type": "integer"},
        "mode": {"type": "string", "enum": ["a", "b"]},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "ops": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string"}, "op": {"type": "string"}}}},
    }}
    conv = schema_to_labeled(schema)
    assert conv is not None
    instr, plan = conv
    assert "one of: a, b" in instr and "separate records" in instr
    text = ("REPLY: hello there\n"
            "N: 3 items\n"
            "MODE: b\n"
            "SUGGESTIONS:\n- name the rival\n- add a beat\n"
            "OPS:\npath: premise\nop: set\n---\npath: tone\nop: merge")
    d = parse_schema_labeled(text, plan)
    assert d["reply"] == "hello there" and d["n"] == 3 and d["mode"] == "b"
    assert d["suggestions"] == ["name the rival", "add a beat"]
    assert d["ops"] == [{"path": "premise", "op": "set"}, {"path": "tone", "op": "merge"}]
    # too-complex schema bails → provider keeps json_schema
    assert schema_to_labeled({"type": "object", "properties": {"x": {"type": "object"}}}) is None
    # absent fields → typed defaults, keys always present
    d2 = parse_schema_labeled("REPLY: hi", plan)
    assert d2["suggestions"] == [] and d2["ops"] == [] and d2["n"] == 0
    print("ok - labeled_structured: schema-to-labeled instr + round-trip parse + bail + defaults")


if __name__ == "__main__":
    demo()
