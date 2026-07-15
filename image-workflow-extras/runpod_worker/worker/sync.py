"""Resync the serverless deployment after you add / swap / edit workflows.

One command to run whenever `workflows/` changes. It:
  1. re-runs gen_worker.py (rescans every workflow -> Dockerfile / extra_model_paths / manifest)
  2. detects whether the custom-node set changed -> whether the IMAGE must be rebuilt
  3. uploads any newly-referenced models to the volume (incremental; existing files skipped)

So the loop is: swap a workflow's nodes/models -> `python runpod/worker/sync.py` ->
(if it says so) rebuild+push the image -> done. Workflows themselves are sent per
request by Loom, so swapping which workflow a model uses is just a models.yaml edit;
this script keeps the WORKER (nodes) and VOLUME (models) in step with them.

    python runpod/worker/sync.py                 # regenerate + upload deltas
    python runpod/worker/sync.py --no-upload      # just regenerate + report
    python runpod/worker/sync.py --base-tag 3.6.0-base
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCKERFILE = HERE / "Dockerfile"
REPO_RE = re.compile(r"git clone --depth 1 (\S+)")


def _repos() -> set[str]:
    if not DOCKERFILE.is_file():
        return set()
    return set(REPO_RE.findall(DOCKERFILE.read_text(encoding="utf-8")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true", help="skip the model upload step")
    ap.add_argument("--base-tag", default=None, help="pass through to gen_worker.py")
    ap.add_argument("--online", action="store_true", help="resolve unknown nodes via ComfyUI-Manager")
    args = ap.parse_args()

    before = _repos()

    gen = [sys.executable, str(HERE / "gen_worker.py")]
    if args.base_tag:
        gen += ["--base-tag", args.base_tag]
    if args.online:
        gen += ["--online"]
    print("==> regenerating artifacts from workflows/ ...\n")
    if subprocess.run(gen).returncode != 0:
        sys.exit("gen_worker.py failed")

    after = _repos()
    added, removed = after - before, before - after

    print("\n" + "=" * 60)
    if added or removed:
        print("IMAGE REBUILD REQUIRED — custom-node set changed:")
        for r in sorted(added):
            print(f"  + {r}")
        for r in sorted(removed):
            print(f"  - {r}")
        print("\n  Rebuild & push, then redeploy (or just bump the existing endpoint's image):")
        print("    cd runpod/worker")
        print("    docker build --platform linux/amd64 -t YOURUSER/loom-comfy-worker:NEXT .")
        print("    docker push YOURUSER/loom-comfy-worker:NEXT")
    else:
        print("Image unchanged — same custom-node set. No rebuild needed.")
    print("=" * 60 + "\n")

    if args.no_upload:
        print("(--no-upload) Skipping volume sync. New models, if any, are in models_manifest.json.")
        return

    print("==> uploading any new models to the volume (existing skipped) ...\n")
    up = subprocess.run([sys.executable, str(HERE / "upload_models.py")])
    if up.returncode != 0:
        sys.exit("upload_models.py failed (check S3 env vars).")
    print("\nSync complete.")


if __name__ == "__main__":
    main()
