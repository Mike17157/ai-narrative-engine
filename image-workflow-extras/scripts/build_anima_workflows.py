"""Author the fractured Anima workflows from one shared, clean setup chain.

The legacy ``anima_master_api.json`` was a 117-node "All-In-One" graph that ran
every role through one switch-gated rrgthree Context bus, with several dead
branches (the FaceDetailer routed to ``EmptySegs``; TorchCompile/LLLite/DCW/
Spectrum were vestigial). Per the project principle — *workflows are cheap and
should each be one clear, contained function* — this script emits a catalog of
single-function API-format workflows, each a clean linear graph.

Every generator shares one model-setup chain (lifted from the master's live
path so render quality matches):

    UNETLoader → easy loraStackApply(empty root stacker, +CLIP) →
    ModelSamplingAuraFlow(1.73) → AnimaModGuidance → KSampler(er_sde/24/cfg4) →
    VAEDecode → <tail>

The ``easy loraStackApply`` fed by an empty root ``Lora Stacker (LoraManager)``
is the anchor Loom's image-preset injection expects (``stack.neutralize_baked_
stack`` + ``stack.inject_models``): with no preset it renders the vanilla base;
with one, the preset's LoRA chain hangs off the apply node.

The committed JSON files under ``workflows/`` are the source of truth — this
script is the reproducible authoring aid. Run: ``python scripts/build_anima_workflows.py``
"""

from __future__ import annotations

import json
from pathlib import Path

WF_DIR = Path(__file__).resolve().parent.parent / "workflows"

# ── constants lifted verbatim from anima_master_api.json (the live path) ──────
UNET = "anima/novaAnimeAM_v25.safetensors"
CLIP = "qwen_3_06b_base.safetensors"
VAE = "qwen_image_vae.safetensors"
UPSCALE_MODEL = "Illustrious/RealESRGAN_x4plus_anime_6B.pth"

POSITIVE = "masterpiece, best quality, amazing quality, very aesthetic, absurdres, {{image}}"
NEGATIVE = ("bad anatomy, bad hands, worst quality, low quality, signature, watermark, "
            "text, jpeg artifacts, missing fingers, extra digit, (chibi:1.1), toddler, "
            "(mature female:0.8)")
QUALITY = ("(newest:0.6), masterpiece, best quality, (score_8:0.65), (score_7:0.7), "
           "(highres, absurdres, very aesthetic:0.8), ")

# Sampler config (from the master's KSampler Config rgthree node).
SAMPLER, SCHED, STEPS, CFG = "er_sde", "simple", 24, 4


def _setup() -> dict:
    """The shared loaders + LoRA anchor + model patches + prompts. Returns the node
    dict; downstream tail nodes reference: model=[8,0], clip=[5,1], vae=[3,0],
    positive=[10,0], negative=[11,0]."""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET, "weight_dtype": "default"},
              "_meta": {"title": "Load Diffusion Model (Anima)"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "stable_diffusion", "device": "default"},
              "_meta": {"title": "Load CLIP (Qwen)"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": VAE}, "_meta": {"title": "Load VAE (Qwen)"}},
        # LoRA injection anchor: empty root stacker → loraStackApply. inject_models
        # hangs the active image preset's chain off node 5; empty = vanilla base.
        "4": {"class_type": "Lora Stacker (LoraManager)",
              "inputs": {"text": ""}, "_meta": {"title": "Lora Stacker (root / preset anchor)"}},
        "5": {"class_type": "easy loraStackApply",
              "inputs": {"lora_stack": ["4", 0], "model": ["1", 0], "optional_clip": ["2", 0]},
              "_meta": {"title": "easy loraStackApply"}},
        "6": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["5", 0], "shift": 1.73},
              "_meta": {"title": "ModelSamplingAuraFlow"}},
        "7": {"class_type": "PrimitiveStringMultiline",
              "inputs": {"value": QUALITY}, "_meta": {"title": "quality_tags"}},
        "8": {"class_type": "AnimaModGuidance",
              "inputs": {"model": ["6", 0], "clip": ["5", 1], "positive": ["10", 0],
                         "negative": ["11", 0], "quality_tags": ["7", 0],
                         "mod_w_profile": "step_i8_skip27"},
              "_meta": {"title": "AnimaModGuidance"}},
        "10": {"class_type": "CLIPTextEncode",
               "inputs": {"clip": ["5", 1], "text": POSITIVE},
               "_meta": {"title": "Positive prompt (inject here)"}},
        "11": {"class_type": "CLIPTextEncode",
               "inputs": {"clip": ["5", 1], "text": NEGATIVE},
               "_meta": {"title": "Negative prompt"}},
    }


def _ksampler(latent_ref, denoise=1.0) -> dict:
    return {"class_type": "KSampler",
            "inputs": {"model": ["8", 0], "positive": ["10", 0], "negative": ["11", 0],
                       "latent_image": latent_ref, "seed": 42, "steps": STEPS, "cfg": CFG,
                       "sampler_name": SAMPLER, "scheduler": SCHED, "denoise": denoise},
            "_meta": {"title": "KSampler (Anima)"}}


def _empty_latent(w: int, h: int) -> dict:
    return {"class_type": "EmptyLatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
            "_meta": {"title": "Empty Latent"}}


def _vae_decode(samples_ref) -> dict:
    return {"class_type": "VAEDecode",
            "inputs": {"samples": samples_ref, "vae": ["3", 0]},
            "_meta": {"title": "VAE Decode"}}


def _save(images_ref) -> dict:
    return {"class_type": "SaveImage",
            "inputs": {"filename_prefix": "loom", "images": images_ref},
            "_meta": {"title": "Save Image"}}


def _rembg(images_ref) -> dict:
    return {"class_type": "easy imageRemBg",
            "inputs": {"images": images_ref, "rem_mode": "Inspyrenet",
                       "image_output": "Save", "save_prefix": "loom", "add_background": "none"},
            "_meta": {"title": "Remove background (Inspyrenet → transparent)"}}


def _load_image() -> dict:
    return {"class_type": "LoadImage",
            "inputs": {"image": "loom_init.png"}, "_meta": {"title": "Load Image (input)"}}


def _detector_nodes() -> dict:
    """Face/person detectors + SAM for the FaceDetailer (lifted from anima_cutout_api.json.bak)."""
    return {
        "16": {"class_type": "UltralyticsDetectorProvider",
               "inputs": {"model_name": "bbox/face_yolov8m.pt"},
               "_meta": {"title": "BBox Detector (face)"}},
        "17": {"class_type": "SAMLoader",
               "inputs": {"model_name": "sam_vit_b_01ec64.pth", "device_mode": "Prefer GPU"},
               "_meta": {"title": "SAMLoader"}},
        "18": {"class_type": "UltralyticsDetectorProvider",
               "inputs": {"model_name": "segm/person_yolov8m-seg.pt"},
               "_meta": {"title": "Segm Detector (person)"}},
    }


def _face_detailer(image_ref) -> dict:
    return {"class_type": "FaceDetailer",
            "inputs": {
                "image": image_ref, "model": ["8", 0], "clip": ["5", 1], "vae": ["3", 0],
                "positive": ["10", 0], "negative": ["11", 0],
                "bbox_detector": ["16", 0], "sam_model_opt": ["17", 0],
                "segm_detector_opt": ["18", 1],
                "guide_size": 384, "guide_size_for": True, "max_size": 768,
                "seed": 42, "steps": STEPS, "cfg": CFG, "sampler_name": SAMPLER,
                "scheduler": SCHED, "denoise": 0.3, "feather": 10, "noise_mask": True,
                "force_inpaint": True, "bbox_threshold": 0.5, "bbox_dilation": 10,
                "bbox_crop_factor": 2, "sam_detection_hint": "center-1", "sam_dilation": 0,
                "sam_threshold": 0.93, "sam_bbox_expansion": 0, "sam_mask_hint_threshold": 0.7,
                "sam_mask_hint_use_negative": "False", "drop_size": 10, "wildcard": "",
                "cycle": 1, "inpaint_model": False, "noise_mask_feather": 20,
                "tiled_encode": False, "tiled_decode": False},
            "_meta": {"title": "FaceDetailer"}}


# ── the 8 workflows ───────────────────────────────────────────────────────────

def _generator(latent_wh, *, detailer: bool, cutout: bool) -> tuple[dict, dict]:
    """A txt2img generator: shared setup + latent + sampler + decode + optional
    detailer + optional cutout tail. Returns (graph, meta)."""
    g = _setup()
    w, h = latent_wh
    g["12"] = _empty_latent(w, h)
    g["13"] = _ksampler(["12", 0])
    g["14"] = _vae_decode(["13", 0])
    img = ["14", 0]
    if detailer:
        g.update(_detector_nodes())
        g["19"] = _face_detailer(img)
        img = ["19", 0]
    if cutout:
        g["20"] = _rembg(img)
        out = "20"
    else:
        g["15"] = _save(img)
        out = "15"
    meta = {"positive_node": "10", "negative_node": "11", "output_node": out}
    return g, meta


def _img2img() -> tuple[dict, dict]:
    g = _setup()
    g["30"] = _load_image()
    g["31"] = {"class_type": "VAEEncode",
               "inputs": {"pixels": ["30", 0], "vae": ["3", 0]},
               "_meta": {"title": "VAE Encode (init image)"}}
    g["13"] = _ksampler(["31", 0], denoise=0.5)
    g["14"] = _vae_decode(["13", 0])
    g["15"] = _save(["14", 0])
    return g, {"positive_node": "10", "negative_node": "11", "output_node": "15"}


def _upscale() -> tuple[dict, dict]:
    """LoadImage → UltimateSDUpscale (1.5×, RealESRGAN + tiled refine) → Save."""
    g = _setup()
    g.pop("8")  # mod-guidance not needed for a tiled refine pass…
    g.pop("7")  # …and its now-orphaned quality_tags primitive
    g["30"] = _load_image()
    g["40"] = {"class_type": "UpscaleModelLoader",
               "inputs": {"model_name": UPSCALE_MODEL}, "_meta": {"title": "UpscaleModelLoader"}}
    g["41"] = {"class_type": "UltimateSDUpscale",
               "inputs": {"image": ["30", 0], "model": ["6", 0], "positive": ["10", 0],
                          "negative": ["11", 0], "vae": ["3", 0], "upscale_model": ["40", 0],
                          "upscale_by": 1.5, "seed": 42, "steps": STEPS, "cfg": CFG,
                          "sampler_name": SAMPLER, "scheduler": SCHED, "denoise": 0.2,
                          "mode_type": "Linear", "tile_width": 1024, "tile_height": 1024,
                          "mask_blur": 8, "tile_padding": 32, "seam_fix_mode": "None",
                          "seam_fix_denoise": 1.0, "seam_fix_width": 64, "seam_fix_mask_blur": 8,
                          "seam_fix_padding": 16, "force_uniform_tiles": True,
                          "tiled_decode": False, "batch_size": 1},
               "_meta": {"title": "UltimateSDUpscale (1.5×)"}}
    g["15"] = _save(["41", 0])
    return g, {"positive_node": "10", "negative_node": "11", "output_node": "15"}


def _detailer_only() -> tuple[dict, dict]:
    """LoadImage → FaceDetailer → Save (standalone face-refine pass)."""
    g = _setup()
    g.update(_detector_nodes())
    g["30"] = _load_image()
    g["19"] = _face_detailer(["30", 0])
    g["15"] = _save(["19", 0])
    return g, {"positive_node": "10", "negative_node": "11", "output_node": "15"}


def _rembg_only() -> tuple[dict, dict]:
    """LoadImage → easy imageRemBg → Save. Pure background removal (no prompt)."""
    g = {"30": _load_image(), "20": _rembg(["30", 0])}
    return g, {"positive_node": None, "negative_node": None, "output_node": "20"}


BUILDERS = {
    "anima_scene":     lambda: _generator((1216, 832), detailer=False, cutout=False),
    "anima_character": lambda: _generator((952, 1536), detailer=True, cutout=True),
    "anima_sprite":    lambda: _generator((832, 1216), detailer=True, cutout=True),
    "anima_txt2img":   lambda: _generator((1024, 1536), detailer=False, cutout=False),
    "anima_img2img":   _img2img,
    "anima_upscale":   _upscale,
    "anima_detailer":  _detailer_only,
    "anima_rembg":     _rembg_only,
}


def main() -> None:
    for name, build in BUILDERS.items():
        graph, meta = build()
        (WF_DIR / f"{name}_api.json").write_text(
            json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        meta_out = {"source": "anima_master_api.json (fractured)", **meta}
        (WF_DIR / f"{name}_api.meta.json").write_text(
            json.dumps(meta_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {name}_api.json  (output_node={meta['output_node']})")


if __name__ == "__main__":
    main()
