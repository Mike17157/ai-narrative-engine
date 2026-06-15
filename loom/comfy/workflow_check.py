"""Extract a workflow's model references and check them against what's installed.

Works on ComfyUI **API-format** workflows (node-id keyed {class_type, inputs}) —
the same format the app uses. Model names live in labelled input fields, so we
read them directly, see which files are missing from the model directory, and
match the missing ones to the ComfyUI Manager catalog for one-click download.
"""

from __future__ import annotations

from pathlib import Path

# input field -> model kind
_FIELD_KIND = {
    "ckpt_name": "checkpoint", "unet_name": "diffusion", "vae_name": "vae",
    "lora_name": "lora", "control_net_name": "controlnet", "style_model_name": "style_models",
    "clip_name": "clip", "clip_name1": "clip", "clip_name2": "clip", "clip_name3": "clip",
    "ipadapter_file": "ipadapter", "gligen_name": "gligen",
}
# `model_name` is reused by several loaders — disambiguate by node class
_MODELNAME_CLASS = {
    "UltralyticsDetectorProvider": "ultralytics", "SAMLoader": "sam", "UpscaleModelLoader": "upscale",
}
# kind -> ComfyUI folders it may live in (relative to models/)
_KIND_FOLDERS = {
    "checkpoint": ["checkpoints"], "diffusion": ["diffusion_models", "unet"], "vae": ["vae"],
    "lora": ["loras"], "controlnet": ["controlnet"], "style_models": ["style_models"],
    "clip": ["text_encoders", "clip"], "ipadapter": ["ipadapter"], "gligen": ["gligen"],
    "ultralytics": ["ultralytics"], "sam": ["sams", "sam"], "upscale": ["upscale_models"],
}


def model_refs(graph: dict) -> list[dict]:
    """Every model file a workflow references, as {node, class_type, field, kind, name}."""
    refs = []
    for nid, n in (graph or {}).items():
        if not isinstance(n, dict):
            continue
        ct, ins = n.get("class_type"), n.get("inputs")
        if not isinstance(ins, dict):
            continue
        for field, val in ins.items():
            if not isinstance(val, str) or not val:
                continue
            kind = _MODELNAME_CLASS.get(ct) if field == "model_name" else _FIELD_KIND.get(field)
            if kind:
                refs.append({"node": nid, "class_type": ct, "field": field, "kind": kind, "name": val})
    return refs


def check_workflow(graph: dict, models_dir: str | Path, catalog_entries: list[dict]) -> dict:
    """Dedupe refs, flag which are installed, and match missing ones to the catalog."""
    models_dir = Path(models_dir)
    cat_by_base: dict[str, dict] = {}
    for e in catalog_entries:
        base = Path((e.get("filename") or "").replace("\\", "/")).name.lower()
        if base:
            cat_by_base.setdefault(base, e)

    out: dict[tuple, dict] = {}
    for r in model_refs(graph):
        key = (r["kind"], r["name"])
        if key in out:
            continue
        rel = r["name"].replace("\\", "/")
        folders = _KIND_FOLDERS.get(r["kind"], [r["kind"]])
        installed = any((models_dir / f / rel).is_file() for f in folders)
        cat = None
        if not installed:
            c = cat_by_base.get(Path(rel).name.lower())
            if c:
                cat = {"url": c["url"], "rel": c["rel"], "name": c.get("name", "")}
        out[key] = {**r, "installed": installed, "catalog": cat}

    refs = list(out.values())
    return {"refs": refs, "missing": [r for r in refs if not r["installed"]]}
