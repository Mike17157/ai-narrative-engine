"""RunPod network-volume helpers — S3-compatible API for model sync.

Factored out from runpod/worker/upload_models.py so the server can call the
same logic at import time (smart-upload auto-push) and via the sync API.
"""
from __future__ import annotations

import os

# Local sub-dir → path inside the volume's models/ prefix.
# Mirrors _SUBDIR_TO_VOLUME in upload_models.py; keep both in sync.
_SUBDIR_TO_VOLUME: dict[str, str] = {
    "checkpoints": "models/checkpoints",
    "diffusion_models": "models/diffusion_models",
    "loras": "models/loras",
    "vae": "models/vae",
    "text_encoders": "models/text_encoders",
    "clip_vision": "models/clip_vision",
    "embeddings": "models/embeddings",
    "ipadapter": "models/ipadapter",
    "controlnet": "models/controlnet",
    "upscale_models": "models/upscale_models",
    "sams": "models/sams",
    "ultralytics/bbox": "models/ultralytics/bbox",
    "ultralytics/segm": "models/ultralytics/segm",
}


def volume_key(rel: str) -> str:
    """Local path relative to the models dir → S3 object key on the volume.

    e.g. ``loras/anima/my_lora.safetensors`` → ``models/loras/anima/my_lora.safetensors``
    """
    parts = rel.replace("\\", "/").split("/")
    for prefix_len in (2, 1):  # try nested (ultralytics/bbox) before flat
        prefix = "/".join(parts[:prefix_len])
        if prefix in _SUBDIR_TO_VOLUME:
            return f"{_SUBDIR_TO_VOLUME[prefix]}/{'/'.join(parts[prefix_len:])}"
    return f"models/{rel.replace(chr(92), '/')}"


class VolumeConfig:
    """RunPod S3 credentials + helpers, read from environment."""

    def __init__(self) -> None:
        self.volume_id = os.environ.get("RUNPOD_VOLUME_ID", "")
        self.datacenter = os.environ.get("RUNPOD_S3_DATACENTER", "")
        # Support both naming conventions: RUNPOD_S3_* (preferred) and RUNPOD_*
        self.access_key = (os.environ.get("RUNPOD_S3_ACCESS_KEY")
                           or os.environ.get("RUNPOD_ACCESS_KEY", ""))
        self.secret_key = (os.environ.get("RUNPOD_S3_SECRET_KEY")
                           or os.environ.get("RUNPOD_SECRET_ACCESS_KEY", ""))

    @property
    def configured(self) -> bool:
        return bool(self.volume_id and self.datacenter and self.access_key and self.secret_key)

    @property
    def endpoint(self) -> str:
        return f"https://s3api-{self.datacenter.lower()}.runpod.io"

    def client(self):
        """Return a boto3 S3 client. Raises ImportError if boto3 not installed."""
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.datacenter,
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    @staticmethod
    def transfer_config():
        from boto3.s3.transfer import TransferConfig
        # RunPod S3: parts >200 MB → 413; too many small parts get dropped.
        return TransferConfig(
            multipart_threshold=128 * 1024 ** 2,
            multipart_chunksize=128 * 1024 ** 2,
            max_concurrency=2,
            use_threads=True,
        )

    def key_exists(self, s3, key: str) -> bool:
        try:
            s3.head_object(Bucket=self.volume_id, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def list_keys(self, s3, prefix: str = "models/") -> set[str]:
        """Return all S3 object keys under *prefix*."""
        keys: set[str] = set()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.volume_id, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.add(obj["Key"])
        return keys

    def delete_key(self, s3, key: str) -> None:
        s3.delete_object(Bucket=self.volume_id, Key=key)
