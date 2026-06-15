"""LoRA training via kohya sd-scripts (the standard SDXL/Pony/Illustrious trainer).

ComfyUI can't train LoRAs — it only runs them. So Loom drives kohya's
`sdxl_train_network.py` as a subprocess: we generate the dataset config and the
full command, then stream its stdout (log + tqdm progress) to the UI, with the
same start/stream/cancel/replay job model as image generation.
"""

from . import kohya

__all__ = ["kohya"]
