"""Generate the config for the native (sd-scripts-based) Anima LoRA trainer.

Anima is a DiT, so kohya can't train it — but a native-Windows sd-scripts fork
can (Anima-Standalone-Trainer / circlestone-labs sd-scripts impl). We deliberately
do NOT manage that trainer's venv or run it: Loom just writes the
`Anima_lora_configs.toml` + dataset TOML it consumes — paths resolved from the
model scan and our own dataset — and hands back the command to run there. Keeps
the heavy trainer external (it has its own UI); Loom's value is the correct config.
"""

from __future__ import annotations

from pathlib import Path

# From the Anima trainer's example config + the model's documented LoRA params.
DEFAULTS = {
    "network_dim": 32,            # Anima wants 32 (unlike SDXL's 16)
    "network_alpha": 16,
    "learning_rate": 5e-5,        # low — the base already knows a lot
    "text_encoder_lr": 5e-5,
    "optimizer_type": "AdamW8bit",
    "lr_scheduler": "cosine",
    "lr_warmup_steps": 100,
    "max_train_epochs": 20,
    "save_every_n_epochs": 1,
    "mixed_precision": "bf16",
    "train_batch_size": 4,
    "resolution": 1024,
    "num_repeats": 10,
    "seed": 42022,
    "network_module": "networks.lora_anima",
}


def merge_params(body: dict) -> dict:
    p = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in (body or {}) and body[k] not in (None, ""):
            p[k] = body[k]
    return p


def write_config(config_path: str | Path, *, dit_path: str | Path, qwen_path: str | Path,
                 vae_path: str | Path, dataset_toml: str | Path, output_dir: str | Path,
                 output_name: str, p: dict) -> str:
    """Write Anima_lora_configs.toml (the trainer's --config_file). Returns its text."""
    def q(x) -> str:
        return Path(x).resolve().as_posix()

    toml = f'''[model_arguments]
dit_path = "{q(dit_path)}"
qwen3_path = "{q(qwen_path)}"
vae_path = "{q(vae_path)}"

[dataset_arguments]
dataset_config = "{q(dataset_toml)}"
cache_latents_to_disk = true
cache_text_encoder_outputs = true

[training_arguments]
output_dir = "{q(output_dir)}"
output_name = "{output_name}"
save_model_as = "safetensors"
max_train_epochs = {int(p["max_train_epochs"])}
save_every_n_epochs = {int(p["save_every_n_epochs"])}
learning_rate = {p["learning_rate"]}
text_encoder_lr = {p["text_encoder_lr"]}
optimizer_type = "{p["optimizer_type"]}"
lr_scheduler = "{p["lr_scheduler"]}"
lr_warmup_steps = {int(p["lr_warmup_steps"])}
mixed_precision = "{p["mixed_precision"]}"
gradient_checkpointing = true
max_data_loader_n_workers = 4
persistent_data_loader_workers = true
seed = {int(p["seed"])}

[network_arguments]
network_module = "{p["network_module"]}"
network_dim = {int(p["network_dim"])}
network_alpha = {int(p["network_alpha"])}
network_train_unet_only = true
'''
    Path(config_path).write_text(toml, encoding="utf-8")
    return toml
