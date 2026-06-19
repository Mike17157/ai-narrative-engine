"""Generate the serverless worker artifacts from your ComfyUI workflows.

Scans workflow JSONs, works out which custom-node repos and which model files they
need, and writes:
    Dockerfile             union of custom-node repos (one image runs every workflow)
    extra_model_paths.yaml the model dirs ComfyUI should read from the volume
    models_manifest.json    {local path -> volume key} for every model that exists
                            locally (consumed by upload_models.py)

A weight is only collected when its (class_type, input_key) is a real loader input
(see LOADER_INPUTS). A weight-looking string on a non-loader input — e.g. a stray
ckpt_name on UNETLoader, which only reads unet_name — is reported as IGNORED and
NOT uploaded, so a phantom field can't quietly bloat the volume.

Run from the project root:
    python runpod/worker/gen_worker.py                 # all workflows/*.json
    python runpod/worker/gen_worker.py anima_cutout_api sprite_api   # a subset
    python runpod/worker/gen_worker.py --models-dir D:/ComfyUI/models

Custom nodes are resolved with a curated map (below) first; anything unknown is
looked up in ComfyUI-Manager's published node map if --online is passed, else
reported as UNRESOLVED so you can add it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "workflows"
HERE = Path(__file__).resolve().parent
DEFAULT_MODELS_DIR = r"C:\Users\micha\Documents\ComfyUI\models"

# Nodes shipped with ComfyUI itself (core + comfy_extras). Not exhaustive, but
# covers everything these workflows use; unknown nodes get resolved as custom.
CORE_NODES = {
    "CheckpointLoaderSimple", "CheckpointLoader", "CLIPTextEncode", "KSampler", "KSamplerAdvanced",
    "EmptyLatentImage", "EmptySD3LatentImage", "VAEDecode", "VAEEncode", "VAEEncodeForInpaint",
    "SaveImage", "SaveImageWithAlpha", "PreviewImage", "LoadImage", "LoadImageMask",
    "LoraLoader", "LoraLoaderModelOnly", "ConditioningConcat", "ConditioningCombine",
    "ConditioningSetArea", "ConditioningSetAreaPercentage", "ConditioningZeroOut",
    "ConditioningAverage", "FluxGuidance", "UpscaleModelLoader", "ImageUpscaleWithModel",
    "ImageScale", "ImageScaleBy", "LatentUpscale", "LatentUpscaleBy", "VAELoader",
    "CLIPSetLastLayer", "CLIPLoader", "DualCLIPLoader", "UNETLoader", "ControlNetLoader",
    "ControlNetApply", "ControlNetApplyAdvanced", "InpaintModelConditioning", "RepeatLatentBatch",
    "EmptyImage", "ImageBatch", "ImagePadForOutpaint", "LatentFromBatch", "PrimitiveNode",
    "Note", "Reroute", "GrowMask", "MaskToImage", "ImageToMask", "InvertMask", "SolidMask",
    "FeatherMask", "SetLatentNoiseMask", "CLIPVisionLoader", "CLIPVisionEncode",
    "StyleModelLoader", "StyleModelApply", "unCLIPConditioning", "PerpNeg",
    "ModelSamplingDiscrete", "ModelSamplingSD3", "ModelSamplingFlux", "SamplerCustom",
    "SamplerCustomAdvanced", "BasicScheduler", "BasicGuider", "RandomNoise", "KSamplerSelect",
    "Mahiro", "RescaleCFG", "ImageCrop", "ImageInvert", "ImageBlend", "JoinImageWithAlpha",
    "SplitImageWithAlpha", "VAEEncodeTiled", "VAEDecodeTiled",
}

# Curated custom-node class -> (repo_url, friendly_name). Prefixes ending in '*'
# match by startswith (e.g. "easy " catches every ComfyUI-Easy-Use node).
NODE_REPO: dict[str, tuple[str, str]] = {
    # ComfyUI-Impact-Pack
    "FaceDetailer": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "SAMLoader": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "DetailerForEach": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "DetailerForEachDebug": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "BboxDetectorSEGS": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "SegmDetectorSEGS": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "ImpactSimpleDetectorSEGS": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "ImpactSwitch": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "PreviewBridge": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "SEGSPreview": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "MaskToSEGS": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    "EmptySegs": ("https://github.com/ltdrdata/ComfyUI-Impact-Pack.git", "Impact-Pack"),
    # ComfyUI-Impact-Subpack — must be a TOP-LEVEL sibling in custom_nodes/ (not nested inside Impact-Pack)
    "UltralyticsDetectorProvider": ("https://github.com/ltdrdata/ComfyUI-Impact-Subpack.git", "Impact-Subpack"),
    # ComfyUI_IPAdapter_plus
    "IPAdapterAdvanced": ("https://github.com/cubiq/ComfyUI_IPAdapter_plus.git", "IPAdapter_plus"),
    "IPAdapterTiled": ("https://github.com/cubiq/ComfyUI_IPAdapter_plus.git", "IPAdapter_plus"),
    "IPAdapterModelLoader": ("https://github.com/cubiq/ComfyUI_IPAdapter_plus.git", "IPAdapter_plus"),
    "IPAdapterUnifiedLoader": ("https://github.com/cubiq/ComfyUI_IPAdapter_plus.git", "IPAdapter_plus"),
    "IPAdapter": ("https://github.com/cubiq/ComfyUI_IPAdapter_plus.git", "IPAdapter_plus"),
    # ComfyUI-Easy-Use (prefix) — easy imageRemBg, easy loraStackApply, easy showAnything…
    "easy *": ("https://github.com/yolain/ComfyUI-Easy-Use.git", "Easy-Use"),
    # rgthree-comfy — Context / Context Big / Seed / KSampler Config (the Anima
    # All-In-One master workflow routes model+clip+vae+conditioning through these).
    "Context (rgthree)": ("https://github.com/rgthree/rgthree-comfy.git", "rgthree-comfy"),
    "Context Big (rgthree)": ("https://github.com/rgthree/rgthree-comfy.git", "rgthree-comfy"),
    "Seed (rgthree)": ("https://github.com/rgthree/rgthree-comfy.git", "rgthree-comfy"),
    "KSampler Config (rgthree)": ("https://github.com/rgthree/rgthree-comfy.git", "rgthree-comfy"),
    "Image Comparer (rgthree)": ("https://github.com/rgthree/rgthree-comfy.git", "rgthree-comfy"),
    # KJNodes — Get/Set virtual globals, switches, primitives, math, compile patches.
    "GetNode": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "SetNode": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "ComfySwitchNode": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "LazySwitchKJ": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "GetImageSize": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "ImageScaleToTotalPixels": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "PathchSageAttentionKJ": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "TorchCompileModelAdvanced": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "ModelPatchTorchSettings": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "PrimitiveStringMultiline": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "PrimitiveInt": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "PrimitiveFloat": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    "PrimitiveBoolean": ("https://github.com/kijai/ComfyUI-KJNodes.git", "KJNodes"),
    # ComfyMath — ComfyMathExpression
    "ComfyMathExpression": ("https://github.com/evanspearman/ComfyMath.git", "ComfyMath"),
    # WAS Node Suite — StringConcatenate, Masks Combine Batch
    "StringConcatenate": ("https://github.com/WASasquatch/was-node-suite-comfyui.git", "was-node-suite"),
    "Masks Combine Batch": ("https://github.com/WASasquatch/was-node-suite-comfyui.git", "was-node-suite"),
    # pysssss Custom Scripts — ShowText
    "ShowText|pysssss": ("https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git", "pysssss-Custom-Scripts"),
    # crystian Image Saver + its sampler/scheduler/unet selector nodes
    "Image Saver": ("https://github.com/crystian/ComfyUI_Comfyroll_CustomNodes.git", "Comfyroll-CustomNodes"),
    "Sampler Selector (Image Saver)": ("https://github.com/crystian/ComfyUI_Comfyroll_CustomNodes.git", "Comfyroll-CustomNodes"),
    "Scheduler Selector (Image Saver)": ("https://github.com/crystian/ComfyUI_Comfyroll_CustomNodes.git", "Comfyroll-CustomNodes"),
    "UNet loader with Name (Image Saver)": ("https://github.com/crystian/ComfyUI_Comfyroll_CustomNodes.git", "Comfyroll-CustomNodes"),
    # ComfyUI-Lora-Manager — Lora Stacker (the master's preset-style routing point)
    "Lora Stacker (LoraManager)": ("https://github.com/willmiao/ComfyUI-Lora-Manager.git", "Lora-Manager"),
    # ComfyUI-Spectrum-SDXL — SpectrumSDXL sampling-acceleration patch
    "SpectrumSDXL": ("https://github.com/ruwwww/comfyui-spectrum-sdxl.git", "Spectrum-SDXL"),
    # ComfyUI_UltimateSDUpscale
    "UltimateSDUpscale": ("https://github.com/ssannels/ComfyUI_UltimateSDUpscale.git", "UltimateSDUpscale"),
    # Anima-specific patches used by the All-In-One master workflow.
    "AnimaLLLiteApply": ("https://github.com/kohya-ss/ComfyUI-Anima-LLLite.git", "Anima-LLLite"),
    # NOTE on intentionally-absent packs (left for --online / manual resolution):
    #  - Merge Strings v2 [RvTools] / Lora Stack to String [RvTools]: the
    #    rvage/ComfyUI-RvTools repo is deprecated/removed. These only feed the
    #    prompt-merge chain, which Loom overwrites at injection — off the active path.
    #  - NAIARequestRandomWithOverride: a NovelAI random-artist node
    #    (bedovyy/ComfyUI_NAIDGenerator, needs a NAI account). Off the active path
    #    for local Anima rendering (same prompt-merge branch as RvTools).
    #  - SAM3_Detect: a NATIVE core node in current ComfyUI (no pack needed), NOT
    #    the custom node the ComfyUI-Manager online map wrongly suggests. Requires
    #    the sam3.1 weights under checkpoints/. Guarded in _FORBIDDEN_REPOS so the
    #    online resolver never tries to clone comfyanonymous/ComfyUI for it.
}

# Authoritative map of which (class_type, input_key) pairs actually LOAD a
# model, and the ComfyUI model dir to place each weight under. A value is
# collected as a weight ONLY if its (class, key) is listed here. Keying on the
# pair — not just the key — is what stops phantom fields like UNETLoader's
# stray ckpt_name (UNETLoader only reads unet_name) from dragging a 6.9 GB
# checkpoint onto the volume that nothing actually loads.
LOADER_INPUTS: dict[tuple[str, str], str] = {
    ("CheckpointLoaderSimple", "ckpt_name"): "checkpoints",
    ("CheckpointLoader", "ckpt_name"): "checkpoints",
    ("UNETLoader", "unet_name"): "diffusion_models",
    # Image Saver's UNet variant reads the same input; without this the Anima
    # UNet would be mis-flagged as a phantom weight and never reach the volume.
    ("UNet loader with Name (Image Saver)", "unet_name"): "diffusion_models",
    ("LoraLoader", "lora_name"): "loras",
    ("LoraLoaderModelOnly", "lora_name"): "loras",
    ("VAELoader", "vae_name"): "vae",
    # Modern ComfyUI layout: CLIP weights under text_encoders/ (legacy clip/
    # is an alias); CLIP-vision stays under clip_vision/.
    ("CLIPLoader", "clip_name"): "text_encoders",
    ("DualCLIPLoader", "clip_name1"): "text_encoders",
    ("DualCLIPLoader", "clip_name2"): "text_encoders",
    ("CLIPVisionLoader", "clip_name"): "clip_vision",
    ("ControlNetLoader", "control_net_name"): "controlnet",
    ("UpscaleModelLoader", "model_name"): "upscale_models",
    ("StyleModelLoader", "style_model_name"): "style_models",
    ("IPAdapterModelLoader", "ipadapter_file"): "ipadapter",
    # Impact-Pack / Impact-Subpack auxiliary loaders.
    ("SAMLoader", "model_name"): "sams",
    ("UltralyticsDetectorProvider", "model_name"): "ultralytics",
}
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".gguf", ".sft")
PLACEHOLDER = "REPLACE_WITH"


def resolve_node(ct: str) -> tuple[str, str] | None:
    if ct in NODE_REPO:
        return NODE_REPO[ct]
    for pat, repo in NODE_REPO.items():
        if pat.endswith("*") and ct.startswith(pat[:-1]):
            return repo
    return None


def model_dir(ct: str, key: str) -> str | None:
    """Dir a weight belongs under, or None if (ct, key) isn't a real loader input."""
    return LOADER_INPUTS.get((ct, key))


# Repos the ComfyUI-Manager online map returns that are NOT real custom-node
# packs — cloning any of these into custom_nodes/ is wrong or harmful. The map
# is community-maintained and carries false positives (e.g. it maps some node
# class names to comfyanonymous/ComfyUI itself). We refuse these on install and
# report them as UNRESOLVED so the real pack is found by hand.
_FORBIDDEN_REPOS = {
    "https://github.com/comfyanonymous/ComfyUI",
    "https://github.com/comfyanonymous/ComfyUI.git",
}


def online_resolve(unknown: set[str]) -> dict[str, str]:
    """Best-effort: map remaining node classes to repos via ComfyUI-Manager's DB.
    Drops the known false-positive repos in _FORBIDDEN_REPOS (they'd clone core
    ComfyUI into custom_nodes, which breaks the install)."""
    import urllib.request
    url = "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/extension-node-map.json"
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=30).read())
    except Exception as e:  # noqa: BLE001
        print(f"  [online] could not fetch node map: {e}")
        return {}
    found = {}
    for repo, payload in data.items():
        if repo in _FORBIDDEN_REPOS:
            continue
        classes = payload[0] if isinstance(payload, list) and payload else []
        for ct in unknown:
            if ct in classes:
                found[ct] = repo if repo.endswith(".git") else repo + ".git"
    return found


def _repo_dir_name(repo_url: str) -> str:
    """The custom_nodes/ folder name a repo clones into (last URL segment, no .git)."""
    return repo_url.rsplit("/", 1)[-1].removesuffix(".git")


def install_local(repos: dict[str, str], comfy_base: Path) -> int:
    """Git-clone every resolved custom-node pack into ``<comfy_base>/custom_nodes/``,
    then install each pack's Python requirements. Already-present packs are skipped
    (refreshed with git pull). Returns the number of packs newly installed.

    This is the LOCAL install path (the mirror of the worker Dockerfile): point it
    at your ComfyUI base directory and the Anima All-In-One master workflow's packs
    land where ComfyUI scans them, so the workflow runs without a RunPod round-trip.
    """
    import subprocess
    custom_nodes = comfy_base / "custom_nodes"
    if not custom_nodes.is_dir():
        sys.exit(f"not a ComfyUI base dir (no custom_nodes/ found): {comfy_base}")
    installed = 0
    print(f"\n==> installing {len(repos)} pack(s) into {custom_nodes} ...\n")
    for url in sorted(repos):
        name = _repo_dir_name(url)
        target = custom_nodes / name
        if target.is_dir():
            # Already there — fast-forward so the latest node defs are in place.
            print(f"  ~ {name}: present, pulling latest")
            subprocess.run(["git", "-C", str(target), "pull", "--ff-only"],
                           capture_output=True)
            continue
        print(f"  + {name}: cloning")
        rc = subprocess.run(["git", "clone", "--depth", "1", url, str(target)]).returncode
        if rc != 0:
            print(f"    FAILED to clone {url}")
            continue
        installed += 1
        # Install this pack's Python deps (best-effort; never fatal — a missing
        # optional dep shouldn't abort the whole install).
        req = target / "requirements.txt"
        if req.is_file():
            py = sys.executable
            subprocess.run([py, "-m", "pip", "install", "-r", str(req)],
                           capture_output=True)
    print(f"\nDone. {installed} new pack(s) installed; restart ComfyUI to load them.")
    return installed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflows", nargs="*", help="workflow stems (default: all in workflows/)")
    ap.add_argument("--models-dir", default=DEFAULT_MODELS_DIR)
    ap.add_argument("--base-tag", default="3.6.0-base", help="runpod-worker-comfy base image tag")
    ap.add_argument("--online", action="store_true", help="resolve unknown nodes via ComfyUI-Manager DB")
    ap.add_argument("--install-local", metavar="COMFY_BASE",
                    help=("install the resolved custom-node packs directly into a LOCAL ComfyUI "
                          "install (path to its base dir, the one containing custom_nodes/), "
                          "instead of just writing the worker Dockerfile. Skips packs already "
                          "present. Requires --online to resolve everything (else warns on gaps)."))
    ap.add_argument("--allow-unresolved", action="store_true",
                    help=("with --install-local, proceed even if some nodes have no known repo "
                          "(they're reported but not installed). Use when the unresolved nodes are "
                          "off the active render path or get bypassed by prompt injection."))
    args = ap.parse_args()

    files = ([WORKFLOWS / f"{w}.json" for w in args.workflows] if args.workflows
             else sorted(WORKFLOWS.glob("*.json")))
    files = [f for f in files if f.is_file()]
    if not files:
        sys.exit("no workflow files found")

    models_dir = Path(args.models_dir)
    repos: dict[str, str] = {}            # repo_url -> friendly name
    unresolved: set[str] = set()
    model_refs: dict[str, str] = {}       # forward-slash name -> dir
    ignored: list[str] = []               # looks like a weight but isn't a real loader input
    print(f"Scanning {len(files)} workflow(s)...\n")

    for f in files:
        g = json.loads(f.read_text(encoding="utf-8"))
        for n in g.values():
            if not isinstance(n, dict):
                continue
            ct = n.get("class_type")
            if ct and ct not in CORE_NODES:
                hit = resolve_node(ct)
                if hit:
                    repos[hit[0]] = hit[1]
                else:
                    unresolved.add(ct)
            for k, v in (n.get("inputs") or {}).items():
                if not (isinstance(v, str) and v.lower().endswith(MODEL_EXTS)):
                    continue
                if PLACEHOLDER in v:
                    continue
                d = model_dir(ct, k)
                if d:
                    model_refs[v.replace("\\", "/")] = d
                else:
                    # A weight-looking string on a non-loader input (e.g. a stray
                    # ckpt_name on UNETLoader) — almost certainly a phantom.
                    ignored.append(f"{v}  ({ct}.{k} in {f.name})")

    if unresolved and args.online:
        print(f"Resolving {len(unresolved)} unknown node(s) online...")
        for ct, repo in online_resolve(unresolved).items():
            repos[repo] = repo.rsplit("/", 1)[-1].removesuffix(".git")
            unresolved.discard(ct)

    # ---- Dockerfile ----------------------------------------------------------
    clone_block = " && \\\n    ".join(f"git clone --depth 1 {url}" for url in sorted(repos))

    dockerfile = f"""# AUTO-GENERATED by gen_worker.py — do not hand-edit; re-run the generator.
# One image that runs ALL scanned workflows. Custom-node packs: {', '.join(sorted(repos.values()))}.
# Models are NOT baked in — they live on a RunPod network volume (see extra_model_paths.yaml).
ARG BASE_TAG={args.base_tag}
FROM timpietruskyblibla/runpod-worker-comfy:${{BASE_TAG}}

# Update ComfyUI to latest so newer samplers (er_sde, sa_solver, etc.) are available.
# The base image pins a release tag; fetch+checkout master gets us current HEAD.
RUN cd /comfyui && git fetch origin && git checkout origin/master && pip install --no-cache-dir -r requirements.txt

WORKDIR /comfyui/custom_nodes
# Impact-Subpack MUST be a top-level sibling here (not nested inside ComfyUI-Impact-Pack/).
# ComfyUI only scans the top level of custom_nodes/; nesting it means UltralyticsDetectorProvider
# never registers → 400.
RUN {clone_block}

# Install each pack's Python deps. Do NOT swallow failures (|| true) — a broken
# pip install leaves ComfyUI unable to load the pack → 400.
# Skip sam2 (heavy; not needed by the Anima FaceDetailer path which uses sam_vit_b).
# Replace opencv-python (needs GUI/GTK libs absent in this headless image) with the
# headless variant so cv2 and ultralytics import without libgthread errors.
RUN for d in */ ; do \\
        if [ -f "$d/requirements.txt" ]; then \\
            grep -viE '^(sam2|git\\+https://github.com/facebookresearch/sam2)' "$d/requirements.txt" > /tmp/req.txt || true ; \\
            if [ -s /tmp/req.txt ]; then pip install --no-cache-dir -r /tmp/req.txt ; fi ; \\
        fi ; \\
    done && \\
    pip install --no-cache-dir "ultralytics>=8.0.0" "segment-anything" && \\
    pip uninstall -y opencv-python opencv-python-headless && \\
    pip install --no-cache-dir opencv-python-headless

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
WORKDIR /
"""
    (HERE / "Dockerfile").write_text(dockerfile, encoding="utf-8")

    # ---- extra_model_paths.yaml ---------------------------------------------
    dirs = sorted(set(model_refs.values()) | {"checkpoints", "loras", "vae", "embeddings"})
    SPECIAL = {"ultralytics": ["ultralytics_bbox: models/ultralytics/bbox",
                               "ultralytics_segm: models/ultralytics/segm"]}
    lines = ["# AUTO-GENERATED by gen_worker.py.",
             "# ComfyUI in the worker reads all weights from the attached network volume.",
             "runpod_volume:", "  base_path: /runpod-volume/"]
    for d in dirs:
        if d == "ultralytics":
            for s in SPECIAL[d]:
                lines.append(f"  {s}")
        elif d == "sams":
            lines.append("  sams: models/sams")
        else:
            lines.append(f"  {d}: models/{d}")
    (HERE / "extra_model_paths.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- models_manifest.json (only files that exist locally) ----------------
    manifest, missing = [], []
    for name, d in sorted(model_refs.items()):
        local_rel = f"{d}/{name}"
        # ultralytics names already carry bbox/ or segm/ subdir; others are flat under their dir
        local_path = models_dir / Path(local_rel.replace("/", "\\")) if "\\" in str(models_dir) else models_dir / local_rel
        local_path = models_dir.joinpath(*local_rel.split("/"))
        key = f"models/{local_rel}"
        if local_path.is_file():
            manifest.append({"local": local_rel, "key": key, "bytes": local_path.stat().st_size})
        else:
            missing.append(local_rel)
    (HERE / "models_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # ---- report --------------------------------------------------------------
    total_gb = sum(m["bytes"] for m in manifest) / 1e9
    print(f"\nDockerfile           -> {len(repos)} custom-node repo(s): {', '.join(sorted(repos.values()))}")
    print(f"extra_model_paths    -> dirs: {', '.join(dirs)}")
    print(f"models_manifest.json -> {len(manifest)} files present locally, {total_gb:.1f} GB")
    if missing:
        print(f"\n  MISSING locally ({len(missing)}) — not added to manifest:")
        for m in missing:
            print(f"    - {m}")
    if ignored:
        print(f"\n  IGNORED ({len(ignored)}) — weight-looking value on a non-loader input (likely a phantom, NOT uploaded):")
        for v in ignored:
            print(f"    - {v}")
    if unresolved:
        print(f"\n  UNRESOLVED custom nodes ({len(unresolved)}) — add to NODE_REPO (or re-run with --online):")
        for u in sorted(unresolved):
            print(f"    - {u}")

    if args.install_local:
        # Installing locally needs every pack resolved — force online if anything
        # is still unknown so we don't ship a half-working install.
        if unresolved and not args.online:
            print("\n  --install-local: forcing --online to resolve remaining nodes...")
            for ct, repo in online_resolve(unresolved).items():
                repos[repo] = repo.rsplit("/", 1)[-1].removesuffix(".git")
                unresolved.discard(ct)
        if unresolved:
            msg = (f"\n--install-local: {len(unresolved)} node(s) unresolved (no safe repo): "
                   f"{', '.join(sorted(unresolved))}")
            if args.allow_unresolved:
                print(msg + "\n  (--allow-unresolved) proceeding without them.")
            else:
                sys.exit(msg + "\n  Add them to NODE_REPO, or pass --allow-unresolved to skip them.")
        install_local(repos, Path(args.install_local))
        return

    print("\nNext: build & push the image, then `python runpod/worker/upload_models.py`.")


if __name__ == "__main__":
    main()
