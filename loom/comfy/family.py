"""Model FAMILY taxonomy — the sub-architecture lineage that tensor signatures can't see.

[scan.py](scan.py) classifies by ARCH (flux / sdxl / sd15 / dit) read from the weights. That isolates
Anima (a DiT) and Flux for free, but Pony, Illustrious and NoobAI are all SDXL fine-tunes —
tensor-identical — so a second signal is needed to tell them apart: the civitai baseModel string, the
folder a file is filed under, or a manual override. This module layers that `family` on top of arch and
answers "can this LoRA pair with this model?" so the UI can containerize compatible models with their
compatible LoRAs.
"""

from __future__ import annotations

# Ordered registry — first match wins, so the SPECIFIC sub-families come before the generic per-arch
# fallbacks. `folders` matches the top-level folder a file sits in; `civitai` matches the civitai
# baseModel string; `hints` are filename substrings (last resort for unfiled root files).
FAMILIES: list[dict] = [
    {"id": "anima", "label": "Anima", "arch": "dit",
     "folders": ["anima", "dit"], "civitai": ["anima"], "hints": ["anima"]},
    {"id": "flux", "label": "Flux", "arch": "flux",
     "folders": ["flux", "flux2", "flux1"], "civitai": ["flux"], "hints": ["flux"]},
    {"id": "pony", "label": "Pony", "arch": "sdxl",
     "folders": ["pony", "ponyxl", "ponydiffusion"], "civitai": ["pony"], "hints": ["pony", "pdxl"]},
    {"id": "noobai", "label": "NoobAI", "arch": "sdxl",
     "folders": ["noobai", "noob"], "civitai": ["noobai", "noob"], "hints": ["noobai", "noob"]},
    {"id": "illustrious", "label": "Illustrious", "arch": "sdxl",
     "folders": ["illustrious", "illustriousxl", "ilxl"], "civitai": ["illustrious"],
     "hints": ["illustrious", "_il_", "_il-", "_il.", "ilxl", "illus"]},
    # generic per-arch fallbacks (no specific lineage detected)
    {"id": "sdxl", "label": "SDXL (other)", "arch": "sdxl",
     "folders": ["sdxl"], "civitai": ["sdxl", "sd xl", "sd_xl"], "hints": []},
    {"id": "sd15", "label": "SD 1.5", "arch": "sd15",
     "folders": ["sd15", "sd1.5"], "civitai": ["sd 1.5", "sd1.5"], "hints": []},
    {"id": "unknown", "label": "Unknown", "arch": "", "folders": [], "civitai": [], "hints": []},
]

_BY_ID = {f["id"]: f for f in FAMILIES}
FAMILY_IDS = [f["id"] for f in FAMILIES]
# Canonical top-level folder name per family — where "Categorize by name" files models so the folder
# itself reflects the type (one folder per family; folder-based detection then resolves correctly).
FOLDER = {"illustrious": "Illustrious", "pony": "Pony", "anima": "Anima", "noobai": "NoobAI",
          "flux": "Flux", "sdxl": "SDXL", "sd15": "SD15"}


def family_folder(family_id: str) -> str | None:
    """The canonical folder for a family, or None for unknown/un-foldered families."""
    return FOLDER.get(family_id)
# arch -> the generic fallback family when no specific lineage is detected. ("sd" = ambiguous
# SD-family from the LoRA classifier; default it to sdxl, the common case.)
_ARCH_FALLBACK = {"dit": "anima", "flux": "flux", "sdxl": "sdxl", "sd15": "sd15", "sd": "sdxl"}


def _norm(s: str | None) -> str:
    return (s or "").lower().replace("\\", "/")


def arch_of(family_id: str) -> str:
    return (_BY_ID.get(family_id) or {}).get("arch", "")


def family_label(family_id: str) -> str:
    return (_BY_ID.get(family_id) or {}).get("label", family_id or "Unknown")


def families_for_arch(arch: str) -> list[str]:
    a = (arch or "").lower()
    return [f["id"] for f in FAMILIES if f["arch"] == a]


def family_of(rel_name: str | None, *, arch: str | None = None,
              civitai_base: str | None = None, override: str | None = None) -> str:
    """Resolve a file's family. Order: override → civitai baseModel → folder/filename hint → arch."""
    if override and override in _BY_ID:
        return override
    # 1. civitai baseModel string (most authoritative when present)
    if civitai_base:
        cb = civitai_base.lower()
        for f in FAMILIES:
            if any(c in cb for c in f["civitai"]):
                return f["id"]
    n = _norm(rel_name)
    # 2. top-level folder (Illustrious/, Pony/, Anima/ …)
    top = n.split("/")[0] if "/" in n else ""
    if top:
        for f in FAMILIES:
            if top in f["folders"]:
                return f["id"]
    # 3. filename substrings (unfiled root files like *_IL_*.safetensors)
    for f in FAMILIES:
        if any(h in n for h in f["hints"]):
            return f["id"]
    # 4. arch generic fallback
    if arch:
        return _ARCH_FALLBACK.get(arch.lower(), "unknown")
    return "unknown"


def family_compat(lora_family: str, model_family: str) -> str:
    """Soft compatibility: 'native' (same family), 'cross' (same arch, different sub-family — usually
    works), or 'incompatible' (different arch). Unknowns are treated as cross (don't hard-hide)."""
    if not lora_family or not model_family or "unknown" in (lora_family, model_family):
        return "cross"
    if lora_family == model_family:
        return "native"
    la, ma = arch_of(lora_family), arch_of(model_family)
    if la and ma and la == ma:
        return "cross"
    return "incompatible"
