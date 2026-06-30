"""IMAGE PRESETS — the first-class "look" layer for image generation, managed centrally
like model presets and lorebooks.

An image preset is a named, self-describing **LoRA stack**: a list of ``{name, weight}``
applied at render time, optionally with a base-checkpoint override. It replaces the rigid
8 style stacks baked into the anima workflow (``style_1..8`` Lora Stacker nodes picked by
an ImpactSwitch) — those are EXTRACTED here on first load so nothing is lost, then injected
externally via ``loom.comfy.stack.inject_models`` (see ``AppContext._apply_image_preset``).

Layering mirrors ``presets.py``:  a global ``active`` preset is the default look; callers may
override per-render (point-of-use). The sentinel ``id:"none"`` (empty ``loras``) means
"vanilla base model, no preset" — the floor, like ``default`` for model presets.

Stored in ``configs/image_presets.json`` as ``{active, presets:[{id,name,family,category,
base_checkpoint,loras,enabled,builtin,order}]}``. The Image Presets manager UI edits them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_LORA_TAG = re.compile(r"<lora:([^:>]+):([0-9.]+)>")
_ANIMA_WORKFLOW = "anima_master_api.json"


# ── data shape ────────────────────────────────────────────────────────────────
def _default_image_preset() -> dict:
    """The 'none' floor: no LoRAs, never deletable, selectable to render vanilla."""
    return {"id": "none", "name": "None", "description": "No preset — the base model as-is.",
            "family": "", "category": "", "base_checkpoint": "",
            "loras": [], "enabled": True, "builtin": True, "order": -1}


def _clean_loras(raw) -> list[dict]:
    """Normalise a LoRA stack: {name, weight:float, trigger?:str}, drop blanks, dedupe by name
    last-wins, preserve order. `trigger` (optional) is text appended to every prompt while this
    LoRA is active — see AppContext._apply_image_preset. Matches inject_models semantics."""
    order: list[str] = []
    merged: dict[str, dict] = {}
    for lr in raw or []:
        if isinstance(lr, str):
            lr = {"name": lr}
        name = str((lr or {}).get("name") or "").strip()
        if not name:
            continue
        try:
            weight = float(lr.get("weight", 0.8))
        except (TypeError, ValueError):
            weight = 0.8
        entry = {"name": name, "weight": weight}
        trigger = str(lr.get("trigger") or "").strip()
        if trigger:
            entry["trigger"] = trigger
        if name not in merged:
            order.append(name)
        merged[name] = entry
    return [merged[n] for n in order]


def _clean_image_preset(raw: dict) -> dict:
    p = _default_image_preset()
    p.update({k: v for k, v in (raw or {}).items()
              if k in ("id", "name", "description", "family", "category",
                       "base_checkpoint", "loras", "enabled", "builtin", "order")})
    p["id"] = str(p.get("id") or "").strip() or "preset"
    p["name"] = str(p.get("name") or p["id"]).strip()
    p["description"] = str(p.get("description") or "").strip()
    p["family"] = str(p.get("family") or "").strip()
    p["category"] = str(p.get("category") or "").strip()
    p["base_checkpoint"] = str(p.get("base_checkpoint") or "").strip()
    p["loras"] = _clean_loras(p.get("loras"))
    p["enabled"] = bool(p.get("enabled", True))
    p["builtin"] = bool(p.get("builtin", False))
    try:
        p["order"] = int(p.get("order") or 0)
    except (TypeError, ValueError):
        p["order"] = 0
    return p


# ── seed: extract the 8 baked stacks out of the anima workflow ──────────────────
def _parse_lora_tags(text: str) -> list[dict]:
    return [{"name": m.group(1).strip(), "weight": float(m.group(2))}
            for m in _LORA_TAG.finditer(text or "")]


def _walk_stacker_chain(graph: dict, node_id: str, _seen: set | None = None) -> list[dict]:
    """Gather a Lora Stacker's full effective stack: its upstream ``lora_stack`` chain first
    (base), then its own ``text`` tags — so the result reproduces what the baked node emitted."""
    _seen = _seen if _seen is not None else set()
    if node_id in _seen or node_id not in graph:
        return []
    _seen.add(node_id)
    node = graph.get(node_id) or {}
    ins = node.get("inputs") or {}
    out: list[dict] = []
    base = ins.get("lora_stack")
    if isinstance(base, list) and len(base) == 2:
        out += _walk_stacker_chain(graph, str(base[0]), _seen)
    out += _parse_lora_tags(ins.get("text"))
    return out


def _seed_image_presets(root: Path) -> list[dict]:
    """Parse the anima workflow's authored ``style_N`` LoRA stacks into self-describing presets.

    Extracts by node title (every ``Lora Stacker (LoraManager)`` titled ``style_N``), not by
    ImpactSwitch wiring — so an authored-but-unwired style (e.g. style_4, whose switch slot is
    taken by the base stacker) is still captured. The shared base stacker is folded into each
    style via the upstream ``lora_stack`` chain walk, so presets are fully self-describing.
    Tolerant: returns whatever parses, never raises (must not break startup)."""
    wf_path = root / "workflows" / _ANIMA_WORKFLOW
    if not wf_path.is_file():
        return []
    try:
        graph = json.loads(wf_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(graph, dict):
        return []

    presets: list[dict] = []
    for nid, node in graph.items():
        if not isinstance(node, dict) or node.get("class_type") != "Lora Stacker (LoraManager)":
            continue
        title = ((node.get("_meta") or {}).get("title") or "").strip()
        m = re.fullmatch(r"style[_ ]?(\d+)", title, re.I)
        if not m:
            continue
        n = int(m.group(1))
        loras = _clean_loras(_walk_stacker_chain(graph, str(nid)))
        if not loras:
            continue
        presets.append(_clean_image_preset({
            "id": f"anima_style_{n}", "name": f"Style {n}",
            "description": "Extracted from the anima workflow.",
            "family": "anima", "category": "Anima baked",
            "loras": loras, "builtin": True, "order": n,
        }))
    presets.sort(key=lambda p: p["order"])
    return presets


# ── store (mirrors presets.py) ──────────────────────────────────────────────────
def _path(root: Path) -> Path:
    return root / "configs" / "image_presets.json"


def load_image_presets(root: Path) -> dict:
    """The image-preset library: {active, presets:[...]}. Always returns at least the 'none'
    floor + the builtin anima styles (lazily seeded/backfilled on first load), so the manager
    is never empty. A deleted builtin re-appears next load; user edits are preserved."""
    path = _path(root)
    data = {"active": "none", "presets": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded.get("presets"), list):
                data = loaded
        except (ValueError, OSError):
            pass
    data["presets"] = [_clean_image_preset(p) for p in data.get("presets") or []]
    by_id = {p["id"]: p for p in data["presets"]}
    have = set(by_id)
    added = False
    if "none" not in have:
        data["presets"].insert(0, _default_image_preset()); added = True; have.add("none")
    # Backfill builtin anima styles (only add missing ids — never clobber user edits).
    for seed in _seed_image_presets(root):
        if seed["id"] not in have:
            data["presets"].append(seed); added = True; have.add(seed["id"])
    if data.get("active") not in have:
        data["active"] = "none"; added = True
    if added:
        data = save_image_presets(root, data)
    return data


def save_image_presets(root: Path, data: dict) -> dict:
    presets = [_clean_image_preset(p) for p in (data or {}).get("presets") or []]
    if not any(p["id"] == "none" for p in presets):
        presets.insert(0, _default_image_preset())
    ids = {p["id"] for p in presets}
    active = (data or {}).get("active") or "none"
    out = {"active": active if active in ids else "none", "presets": presets}
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def get_image_preset(root: Path, preset_id: str | None) -> dict | None:
    if not preset_id:
        return None
    for p in load_image_presets(root)["presets"]:
        if p["id"] == preset_id:
            return p
    return None


def upsert_image_preset(root: Path, body: dict) -> dict:
    """Create or update one preset. A PARTIAL body merges onto the existing preset."""
    lib = load_image_presets(root)
    body = dict(body or {})
    existing = next((p for p in lib["presets"] if p["id"] == body.get("id")), None)
    clean = _clean_image_preset({**existing, **body} if existing else body)
    presets = [p for p in lib["presets"] if p["id"] != clean["id"]]
    presets.append(clean)
    save_image_presets(root, {"active": lib.get("active"), "presets": presets})
    return clean


def delete_image_preset(root: Path, preset_id: str) -> dict:
    lib = load_image_presets(root)
    if preset_id == "none":
        return lib                      # the floor; never delete it
    presets = [p for p in lib["presets"] if p["id"] != preset_id]
    return save_image_presets(root, {"active": lib.get("active"), "presets": presets})


def set_active(root: Path, preset_id: str) -> dict:
    lib = load_image_presets(root)
    if any(p["id"] == preset_id for p in lib["presets"]):
        lib["active"] = preset_id
        lib = save_image_presets(root, lib)
    return lib


def active_image_preset(root: Path) -> dict:
    """The global-default preset. Always returns a preset (falls back to 'none')."""
    lib = load_image_presets(root)
    return get_image_preset(root, lib.get("active")) or lib["presets"][0]
