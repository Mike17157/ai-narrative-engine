"""Model librarian — reorganize the ComfyUI model tree by detected architecture,
and keep references intact.

Built on the signature scan. It is deliberately *surgical*: it only proposes
moving files that are **misfiled** (their detected arch conflicts with the
dominant arch of the subfolder they're in) or loose at the folder root. Folders
that are already arch-consistent are left alone — which preserves sub-family
splits the scan can't see (Pony vs Illustrious are both 'sdxl' by signature).

`apply_moves` performs the moves and rewrites every reference inside Loom's own
configs (loras.yaml, character cards, workflows/). It cannot rewrite ComfyUI's
*own* saved workflows — those reference moved files by path and must be
re-pointed in ComfyUI; `apply_moves` returns the old→new map so the caller can
warn about that.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import yaml

from .scan import scan_models

_ORGANIZABLE = ("lora", "checkpoint", "diffusion")
_ROLES = ("detail", "theme", "character")


def _parse_family_role(rel: str) -> tuple[str, str | None, str]:
    """Split a lora rel into (family, current_role, basename). The family is the
    model-type folder you maintain (Illustrious/Pony/Anima); a known role
    subfolder (detail/theme/character) under it is recognised so we don't treat
    it as the family."""
    parts = rel.split("/")
    base = parts[-1]
    if len(parts) >= 3 and parts[1] in _ROLES:      # family/role/file
        return parts[0], parts[1], base
    if len(parts) == 2 and parts[0] in _ROLES:      # role/file  (root family)
        return "", parts[0], base
    if len(parts) >= 2:                              # family/file
        return parts[0], None, base
    return "", None, base                            # loose at root


def plan_moves(models_dir: str | Path, classifications: dict[str, str] | None = None) -> dict:
    """Propose folder moves that reflect LoRA classification, preserving the
    model-type (family) folder. A LoRA classified detail/theme/character is filed
    into ``<family>/<classification>/``. Also reports arch-mismatch *warnings*
    (a file whose detected arch differs from its family folder's dominant arch —
    likely truly misfiled), which are surfaced, not auto-moved (sub-family isn't
    detectable). Returns {moves, warnings, counts}."""
    classifications = classifications or {}
    scan = scan_models(models_dir)
    items = scan["items"]

    # dominant arch per family folder (for mismatch warnings)
    buckets: dict[str, list[str]] = {}
    for it in items:
        if it["kind"] == "lora" and it["arch"] not in ("", "unknown"):
            buckets.setdefault(it["arch_dir"], []).append(it["arch"])
    dominant = {k: Counter(v).most_common(1)[0][0] for k, v in buckets.items()}

    moves, warnings = [], []
    for it in items:
        if it["kind"] != "lora":
            continue
        rel = it["rel"]
        family, cur_role, base = _parse_family_role(rel)
        cls = classifications.get(rel)
        if cls in _ROLES:
            dst = f"{family}/{cls}/{base}" if family else f"{cls}/{base}"
            if dst != rel:
                moves.append({"top": "loras", "kind": "lora", "arch": it["arch"],
                              "family": family or "(root)", "cls": cls,
                              "src": rel, "dst": dst, "name": base, "reason": f"→ {cls}"})
        dom = dominant.get(it["arch_dir"])
        if dom and it["arch"] not in ("", "unknown") and dom != it["arch"]:
            warnings.append({"name": base, "family": it["arch_dir"], "arch": it["arch"], "dominant": dom, "rel": rel})

    moves.sort(key=lambda m: (m["family"], m["cls"], m["name"]))
    return {"moves": moves, "warnings": warnings, "counts": scan["counts"]}


def _top_to_ref_kind(top: str) -> str:
    return {"loras": "lora", "checkpoints": "checkpoint", "diffusion_models": "unet"}.get(top, "")


def apply_moves(models_dir: str | Path, root: str | Path, moves: list[dict]) -> dict:
    """Move the chosen files, then rewrite Loom references. `moves` items are
    {top, src, dst} (rels within the top folder). Returns results + the old→new
    name maps (ComfyUI '\\'-style) so the caller can warn about external refs."""
    models_dir = Path(models_dir)
    moved, failed = [], []
    # old->new name maps per reference-kind (backslash form = how ComfyUI/configs store them)
    maps: dict[str, dict[str, str]] = {"lora": {}, "checkpoint": {}, "unet": {}}

    for mv in moves:
        top = mv["top"]
        src = (models_dir / top / mv["src"].replace("\\", "/")).resolve()
        dst = (models_dir / top / mv["dst"].replace("\\", "/")).resolve()
        base = (models_dir / top).resolve()
        # safety: both inside the top folder, source exists, no overwrite
        if base not in src.parents or base not in dst.parents:
            failed.append({"src": mv["src"], "error": "outside top folder"}); continue
        if not src.is_file():
            failed.append({"src": mv["src"], "error": "not found"}); continue
        if dst.exists():
            failed.append({"src": mv["src"], "error": "destination exists"}); continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except Exception as exc:  # noqa: BLE001
            failed.append({"src": mv["src"], "error": str(exc)}); continue
        moved.append({"src": mv["src"], "dst": mv["dst"], "top": top})
        rk = _top_to_ref_kind(top)
        if rk:
            maps[rk][mv["src"].replace("/", "\\")] = mv["dst"].replace("/", "\\")
            maps[rk][mv["src"].replace("\\", "/")] = mv["dst"].replace("\\", "/")  # tolerate either separator

    rewritten = _rewrite_references(Path(root), maps) if moved else []
    return {"moved": moved, "failed": failed, "rewritten": rewritten, "maps": maps}


def _rewrite_references(root: Path, maps: dict[str, dict[str, str]]) -> list[str]:
    """Rewrite lora/checkpoint/unet names across loras.yaml, character cards and
    workflows/*.json. Returns the list of files changed."""
    lora_map, ckpt_map, unet_map = maps["lora"], maps["checkpoint"], maps["unet"]
    changed: list[str] = []

    def remap(val, m):
        return m.get(val, val) if isinstance(val, str) else val

    # 1. loras.yaml (library + stack members)
    lp = root / "configs" / "loras.yaml"
    if lp.is_file() and lora_map:
        doc = yaml.safe_load(lp.read_text(encoding="utf-8")) or {}
        touched = False
        for entry in doc.get("library", []) or []:
            nv = remap(entry.get("name"), lora_map)
            if nv != entry.get("name"):
                entry["name"] = nv; touched = True
        for st in doc.get("stacks", []) or []:
            for m in st.get("loras", []) or []:
                nv = remap(m.get("name"), lora_map)
                if nv != m.get("name"):
                    m["name"] = nv; touched = True
        if touched:
            lp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
            changed.append("configs/loras.yaml")

    # 2. character cards (image.loras[].name + image.checkpoint)
    cdir = root / "configs" / "characters"
    if cdir.is_dir() and (lora_map or ckpt_map):
        for p in cdir.glob("*.yaml"):
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            img = doc.get("image") or {}
            touched = False
            for l in img.get("loras", []) or []:
                nv = remap(l.get("name"), lora_map)
                if nv != l.get("name"):
                    l["name"] = nv; touched = True
            if img.get("checkpoint") and remap(img["checkpoint"], ckpt_map) != img["checkpoint"]:
                img["checkpoint"] = remap(img["checkpoint"], ckpt_map); touched = True
            if touched:
                p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
                changed.append(f"configs/characters/{p.name}")

    # 3. workflows/*.json (LoraLoader / CheckpointLoaderSimple / UNETLoader)
    wdir = root / "workflows"
    if wdir.is_dir():
        for p in wdir.glob("*.json"):
            try:
                g = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(g, dict):
                continue
            touched = False
            for node in g.values():
                if not isinstance(node, dict):
                    continue
                ins = node.get("inputs", {})
                ct = node.get("class_type")
                if "lora_name" in ins and remap(ins["lora_name"], lora_map) != ins["lora_name"]:
                    ins["lora_name"] = remap(ins["lora_name"], lora_map); touched = True
                if ct == "CheckpointLoaderSimple" and "ckpt_name" in ins and remap(ins["ckpt_name"], ckpt_map) != ins["ckpt_name"]:
                    ins["ckpt_name"] = remap(ins["ckpt_name"], ckpt_map); touched = True
                if ct in ("UNETLoader", "UnetLoaderGGUF") and "unet_name" in ins and remap(ins["unet_name"], unet_map) != ins["unet_name"]:
                    ins["unet_name"] = remap(ins["unet_name"], unet_map); touched = True
            if touched:
                p.write_text(json.dumps(g, indent=2), encoding="utf-8")
                changed.append(f"workflows/{p.name}")

    return changed
