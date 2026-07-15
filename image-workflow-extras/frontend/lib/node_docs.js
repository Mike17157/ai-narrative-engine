// Curated one-line descriptions of the ComfyUI node types used in Loom's
// workflows (Anima master + friends). Keyed by class_type. These are the
// authoritative blurbs shown in the graph pane's info panel; when a node isn't
// listed here we fall back to ComfyUI's own `description` from object_info
// (see nodeDoc()).
export const NODE_DOCS = {
  // ── Loaders / model assembly ────────────────────────────────────────────
  'UNet loader with Name (Image Saver)': 'Loads the diffusion UNet (the denoiser) and exposes its filename so the Image Saver can stamp it into metadata.',
  'CLIPLoader': 'Loads the text encoder (CLIP) that turns the prompt into the conditioning the sampler steers toward.',
  'VAELoader': 'Loads the VAE — the codec that converts between pixel images and the latent space the sampler works in.',
  'UpscaleModelLoader': 'Loads an ESRGAN-style upscale model used by the UltimateSDUpscale tiled upscaler.',
  'ModelSamplingAuraFlow': 'Configures AuraFlow/flow-matching sampling on the model (shift/sigma schedule) — required for Anima-family checkpoints.',
  'ModelPatchTorchSettings': 'Tweaks low-level PyTorch settings (precision/memory) on the model for this run.',
  'PathchSageAttentionKJ': 'Swaps in SageAttention — a faster fused attention kernel — to speed up sampling.',

  // ── LoRA ────────────────────────────────────────────────────────────────
  'Lora Stacker (LoraManager)': 'Builds a stack of LoRAs (name + strength each) as a style preset, without applying them yet.',
  'easy loraStackApply': 'Applies a LoRA stack to the model + CLIP — this is where Loom rebuilds the LoRA chain via inject_models.',
  'ImpactSwitch': 'Selects one input among many by index — here it picks which LoRA style-preset stack is active.',

  // ── Prompt / conditioning ───────────────────────────────────────────────
  'CLIPTextEncode': 'Encodes a text prompt into conditioning. Loom writes the positive prompt into this node; BREAK splits it into chained regions.',
  'AnimaModGuidance': 'Anima-specific guidance patch — modifies how classifier-free guidance (CFG) is computed for the Anima architecture.',
  'AnimaLLLiteApply': 'Applies an Anima ControlNet-LLLite: a lightweight low-rank attention correction steered by a control image.',

  // ── Sampling ────────────────────────────────────────────────────────────
  'KSampler': 'The denoising loop — turns noise into a latent image guided by the conditioning. The core render step.',
  'KSampler Config (rgthree)': 'A reusable bundle of sampler settings (steps, cfg, sampler, scheduler) fed into the KSamplers.',
  'Sampler Selector (Image Saver)': 'Picks the sampler algorithm by name and reports it for metadata.',
  'Scheduler Selector (Image Saver)': 'Picks the noise schedule by name and reports it for metadata.',
  'Seed (rgthree)': 'Provides the random seed; controls whether a run is reproducible or re-rolled each time.',
  'DCWModelPatch': 'Patches the model with DCW (SNR-bias correction + frequency-adaptive CFG) for cleaner, more coherent samples.',
  'SpectrumSDXL': 'Training-free sampler acceleration — forecasts redundant UNet steps and skips them (~2× faster).',
  'DiTSpectrumPatch': 'Spectrum acceleration variant targeting the DiT (transformer) backbone used in hi-res passes.',

  // ── Latent / resolution ─────────────────────────────────────────────────
  'EmptyLatentImage': 'Creates the blank latent canvas at a chosen width×height — the starting point for txt2img. Loom overrides its size per render.',
  'LatentUpscaleBy': 'Scales the latent up by a factor before a second sampling pass (hi-res fix).',
  'ImageScaleBy': 'Scales a pixel image up/down by a factor.',
  'ImageScaleToTotalPixels': 'Resizes an image to a target megapixel count, preserving aspect ratio.',
  'GetImageSize': 'Reads an image’s width and height, usually to drive a downstream resize or upscale.',
  'SetLatentNoiseMask': 'Attaches a mask to a latent so sampling only repaints the masked region (inpaint).',

  // ── Refinement ──────────────────────────────────────────────────────────
  'UltimateSDUpscale': 'Tiled upscaler: upscales then re-samples the image tile-by-tile for high-resolution detail. Slow but sharp.',
  'EmptySegs': 'An empty detection-segments set — the placeholder the FaceDetailer branch starts from when no faces are passed in.',

  // ── VAE conversion ──────────────────────────────────────────────────────
  'VAEEncode': 'Encodes a pixel image into latent space (used for img2img and between hi-res passes).',
  'VAEDecode': 'Decodes a latent back into a viewable pixel image.',

  // ── I/O ─────────────────────────────────────────────────────────────────
  'LoadImage': 'Loads a source image from disk — the img2img input. Loom feeds the reference image here.',
  'Image Saver': 'Writes the final image to disk with full generation metadata (prompt, seed, model, sampler).',
  'PreviewBridge': 'Shows a live preview and passes the image through, so downstream nodes can still use it.',

  // ── Routing / logic ─────────────────────────────────────────────────────
  'ComfySwitchNode': 'A boolean gate: routes one of two inputs through depending on a switch. Loom flips these to enable detailer / upscale / hi-res.',
  'LazySwitchKJ': 'Lazy boolean switch — picks one of two inputs and only evaluates the chosen branch.',
  'Context Big (rgthree)': 'A bus that bundles many pipeline values (model, clip, vae, latent, prompts…) into one wire to keep the graph tidy.',
  'StringConcatenate': 'Joins two strings — used to assemble prompts and filename/metadata text.',
  'ComfyMathExpression': 'Evaluates a math expression on numeric inputs (e.g. computing tile or upscale dimensions).',

  // ── Primitives ──────────────────────────────────────────────────────────
  'PrimitiveBoolean': 'A literal on/off value feeding a switch or toggle.',
  'PrimitiveInt': 'A literal integer value (dimensions, steps, counts).',
  'PrimitiveFloat': 'A literal decimal value (strengths, scale factors).',
  'PrimitiveStringMultiline': 'A literal multi-line text value (prompt fragments, presets).',
};

// Resolve a node's description: curated dict first, then ComfyUI's own
// object_info description, else empty. `objectInfo` is img.objectInfo.
export function nodeDoc(classType, objectInfo = {}) {
  return NODE_DOCS[classType] || (objectInfo?.[classType]?.description || '').trim();
}
