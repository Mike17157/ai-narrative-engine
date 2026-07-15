"""Upload Loom's local ComfyUI models to a RunPod network volume (no pod needed).

Uses RunPod's S3-compatible API for network volumes. Each model file is uploaded
to the exact key the worker's extra_model_paths.yaml expects, so the Illustrious
workflow resolves every checkpoint / LoRA / detector once the volume is attached.

Prerequisites
-------------
1. Create a network volume in a datacenter that supports the S3 API
   (e.g. EUR-IS-1, EU-RO-1, US-KS-2 — see RunPod docs "S3-compatible API").
2. Create S3 API credentials: RunPod console -> Settings -> S3 API Keys.
3. pip install boto3

Set these env vars (or put them in the project .env):
    RUNPOD_VOLUME_ID        the network volume id (this is the S3 *bucket*)
    RUNPOD_S3_DATACENTER    the volume's datacenter, e.g. EU-RO-1
    RUNPOD_S3_ACCESS_KEY    from the S3 API key you created
    RUNPOD_S3_SECRET_KEY    from the S3 API key you created
Optional:
    COMFY_MODELS_DIR        local models dir (default: the path in user.yaml)

Run:
    python runpod/worker/upload_models.py            # upload everything missing
    python runpod/worker/upload_models.py --force    # re-upload even if present
    python runpod/worker/upload_models.py --check     # list only, upload nothing

Adding / removing a single LoRA (no image rebuild, no full regen needed):
    python runpod/worker/upload_models.py --add loras/my_new_lora.safetensors
    python runpod/worker/upload_models.py --rm  loras/my_new_lora.safetensors

The --add path is local-path-relative-to-the-models-dir and infers the volume
key from the subdir (loras/ -> models/loras/, etc.); the --rm path takes the
same relative path. Reference the LoRA in a LoraLoader node and it just works.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_MODELS_DIR = r"C:\Users\micha\Documents\ComfyUI\models"
MANIFEST = Path(__file__).resolve().parent / "models_manifest.json"


def _load_file_map() -> dict[str, str]:
    """Local file (relative to the ComfyUI models dir) -> key on the volume.

    Read from models_manifest.json, which gen_worker.py produces by scanning the
    workflows. Run `python runpod/worker/gen_worker.py` first."""
    if not MANIFEST.is_file():
        sys.exit(f"{MANIFEST.name} not found — run: python runpod/worker/gen_worker.py")
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {r["local"]: r["key"] for r in rows}


def _load_dotenv() -> None:
    """Minimal .env loader so the script works standalone (existing env wins)."""
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


# subdir under the models dir -> ComfyUI model dir on the volume. Lets --add
# infer the volume key from the local path without a full gen_worker regen.
# ultralytics/bbox and ultralytics/segm are the only nested cases.
_SUBDIR_TO_VOLUME: dict[str, str] = {
    "checkpoints": "models/checkpoints", "diffusion_models": "models/diffusion_models",
    "loras": "models/loras", "vae": "models/vae", "text_encoders": "models/text_encoders",
    "clip_vision": "models/clip_vision", "embeddings": "models/embeddings",
    "ipadapter": "models/ipadapter", "controlnet": "models/controlnet",
    "upscale_models": "models/upscale_models", "sams": "models/sams",
    "ultralytics/bbox": "models/ultralytics/bbox", "ultralytics/segm": "models/ultralytics/segm",
}


def _volume_key(rel: str) -> str:
    """Local relative path (e.g. loras/foo.safetensors) -> volume key (models/loras/foo.safetensors)."""
    parts = rel.replace("\\", "/").split("/")
    for prefix_len in (2, 1):  # match nested (ultralytics/bbox) before flat
        if "/".join(parts[:prefix_len]) in _SUBDIR_TO_VOLUME:
            return f"{_SUBDIR_TO_VOLUME['/'.join(parts[:prefix_len])]}/{'/'.join(parts[prefix_len:])}"
    # Unknown subdir: assume it's already a flat <dir>/<file> and just prefix models/.
    return f"models/{rel.replace(chr(92), '/')}"


def _read_manifest() -> list[dict]:
    if not MANIFEST.is_file():
        return []
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _write_manifest(rows: list[dict]) -> None:
    # Sort by volume key for stable diffs, drop duplicates on key.
    seen, dedup = set(), []
    for r in sorted(rows, key=lambda r: r["key"]):
        if r["key"] not in seen:
            seen.add(r["key"])
            dedup.append(r)
    MANIFEST.write_text(json.dumps(dedup, indent=2), encoding="utf-8")


def _single_file_op(args: argparse.Namespace, models_dir: Path) -> None:
    rel = args.add or args.rm
    rel = rel.replace("\\", "/")
    key = _volume_key(rel)

    volume_id = os.environ.get("RUNPOD_VOLUME_ID", "")
    datacenter = os.environ.get("RUNPOD_S3_DATACENTER", "")
    access_key = os.environ.get("RUNPOD_S3_ACCESS_KEY", "")
    secret_key = os.environ.get("RUNPOD_S3_SECRET_KEY", "")
    for name, val in [("RUNPOD_VOLUME_ID", volume_id), ("RUNPOD_S3_DATACENTER", datacenter),
                      ("RUNPOD_S3_ACCESS_KEY", access_key), ("RUNPOD_S3_SECRET_KEY", secret_key)]:
        if not val:
            sys.exit(f"Missing env var {name}. See the header of this file.")

    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config
    except ImportError:
        sys.exit("boto3 not installed. Run: pip install boto3")

    endpoint = f"https://s3api-{datacenter.lower()}.runpod.io"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=datacenter,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )
    # ponytail: 128MB parts started hitting consistent 504 Gateway Timeouts on the
    # RunPod S3 endpoint (every part, every retry). Smaller parts at concurrency=1
    # finish faster per-request and stop competing for bandwidth; bump if the
    # gateway timeout tightens further, shrink if "too many parts" issues return.
    xfer = TransferConfig(multipart_threshold=32 * 1024**2, multipart_chunksize=32 * 1024**2,
                          max_concurrency=1, use_threads=True)

    rows = _read_manifest()

    # ---- add -------------------------------------------------------------
    if args.add:
        local = models_dir.joinpath(*rel.split("/"))
        if not local.is_file():
            sys.exit(f"Not found locally: {local}\nCheck --models-dir / the relative path.")
        size_mb = local.stat().st_size / 1e6
        print(f"Uploading {rel} -> {key} [{size_mb:.0f} MB] ...", flush=True)
        s3.upload_file(str(local), volume_id, key, Config=xfer)
        print("done.")
        rows.append({"local": rel, "key": key, "bytes": local.stat().st_size})
        _write_manifest(rows)
        print(f"Appended to {MANIFEST.name}. Reference '{rel.split('/')[-1]}' in a LoraLoader (no rebuild).")
        return

    # ---- rm --------------------------------------------------------------
    print(f"Deleting {key} from volume ...", flush=True)
    s3.delete_object(Bucket=volume_id, Key=key)
    print("done.")
    rows = [r for r in rows if r["key"] != key]
    _write_manifest(rows)
    print(f"Removed from {MANIFEST.name}. Unreference it in any workflow before relying on this.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-upload even if the object already exists")
    ap.add_argument("--check", action="store_true", help="report local/remote status; upload nothing")
    ap.add_argument("--models-dir", default=None, help="override the local ComfyUI models dir")
    ap.add_argument("--add", metavar="REL", help="upload a single model (path relative to the models dir, "
                                                  "e.g. loras/my_lora.safetensors) and append it to the manifest")
    ap.add_argument("--rm", metavar="REL", help="remove a single model from the volume and the manifest "
                                                "(path relative to the models dir)")
    args = ap.parse_args()

    _load_dotenv()
    models_dir = Path(args.models_dir or os.environ.get("COMFY_MODELS_DIR") or DEFAULT_MODELS_DIR)

    # Single-file add / remove short-circuit the bulk path. No image rebuild is
    # ever needed for these — the worker reads weights from the volume at run time.
    if args.add or args.rm:
        _single_file_op(args, models_dir)
        return

    file_map = _load_file_map()

    # Validate local files first so a typo fails fast and free.
    missing_local = [src for src in file_map if not (models_dir.joinpath(*src.split("/"))).is_file()]
    if missing_local:
        print(f"Local models dir: {models_dir}")
        print("MISSING local files:")
        for m in missing_local:
            print(f"  - {m}")
        sys.exit("Fix the paths / --models-dir, or re-run gen_worker.py, before uploading.")

    total_gb = sum((models_dir.joinpath(*src.split("/"))).stat().st_size for src in file_map) / 1e9
    print(f"Local models dir: {models_dir}")
    print(f"{len(file_map)} files, {total_gb:.1f} GB total\n")

    if args.check and not (os.environ.get("RUNPOD_VOLUME_ID")):
        for src, key in file_map.items():
            size = (models_dir / src).stat().st_size / 1e6
            print(f"  [{size:7.0f} MB]  {src}  ->  {key}")
        print("\n[check] No RUNPOD_VOLUME_ID set; listed local plan only.")
        return

    volume_id = os.environ.get("RUNPOD_VOLUME_ID", "")
    datacenter = os.environ.get("RUNPOD_S3_DATACENTER", "")
    access_key = os.environ.get("RUNPOD_S3_ACCESS_KEY", "")
    secret_key = os.environ.get("RUNPOD_S3_SECRET_KEY", "")
    for name, val in [("RUNPOD_VOLUME_ID", volume_id), ("RUNPOD_S3_DATACENTER", datacenter),
                      ("RUNPOD_S3_ACCESS_KEY", access_key), ("RUNPOD_S3_SECRET_KEY", secret_key)]:
        if not val:
            sys.exit(f"Missing env var {name}. See the header of this file.")

    try:
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config
    except ImportError:
        sys.exit("boto3 not installed. Run: pip install boto3")

    endpoint = f"https://s3api-{datacenter.lower()}.runpod.io"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=datacenter,
        config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
    )
    # RunPod's S3 has TWO multipart constraints we have to thread:
    #   - parts must be <= ~128 MB (200 MB returns 413 Content Too Large)
    #   - too many tiny parts get dropped on CompleteMultipartUpload
    # ponytail: 128MB parts started hitting consistent 504 Gateway Timeouts (every
    # part, every retry) — dropped to 32MB @ concurrency=1 so each part finishes
    # faster and doesn't compete for bandwidth; bump back up if that was a one-off.
    xfer = TransferConfig(multipart_threshold=32 * 1024**2, multipart_chunksize=32 * 1024**2,
                          max_concurrency=1, use_threads=True)

    print(f"S3 endpoint: {endpoint}   bucket(volume): {volume_id}\n")

    def _exists(key: str) -> bool:
        try:
            s3.head_object(Bucket=volume_id, Key=key)
            return True
        except Exception:  # noqa: BLE001  (404 / NoSuchKey)
            return False

    for src, key in file_map.items():
        path = models_dir.joinpath(*src.split("/"))
        size_mb = path.stat().st_size / 1e6
        if not args.force and _exists(key):
            print(f"  skip  (exists)  {key}  [{size_mb:.0f} MB]")
            continue
        if args.check:
            print(f"  would upload    {key}  [{size_mb:.0f} MB]")
            continue
        print(f"  uploading       {key}  [{size_mb:.0f} MB] ...", flush=True)
        s3.upload_file(str(path), volume_id, key, Config=xfer)
        print("                  done.")

    print("\nUpload complete. Attach this volume to your serverless endpoint and render.")


if __name__ == "__main__":
    main()
