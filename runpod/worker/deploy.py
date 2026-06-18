"""Create the RunPod serverless infrastructure via the REST API.

Automates the parts that don't need a browser: the network volume, the template
(which points at your pushed worker image), and the serverless endpoint. Uses
RUNPOD_API_KEY from the environment / .env. Idempotent by name — re-running finds
the existing volume/endpoint instead of duplicating it.

What it CANNOT do for you (hard manual steps): build & push the Docker image
(needs your registry login), and create S3 API keys for the upload (RunPod console
-> Settings -> S3 API Keys). See README.md for the full order.

Typical use:
    # 1. just the volume (so you can make S3 keys + upload models):
    python runpod/worker/deploy.py --volume-only

    # 2. after the image is pushed and models uploaded, create template+endpoint:
    python runpod/worker/deploy.py --image YOURUSER/loom-comfy-worker:1.0

Options:
    --name           base name for the resources (default: loom-comfy)
    --datacenter     volume datacenter; MUST support the S3 API (default: US-KS-2)
    --size           volume size in GB (default: 20 — fits the ~6.4 GB Anima set with headroom)
    --gpu            GPU type id (default: "NVIDIA GeForce RTX 4090")
    --container-disk worker container disk in GB (default: 20)
    --registry-auth-id   RunPod container-registry auth id, if the image is private
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

BASE = "https://rest.runpod.io/v1"
# Datacenters that currently offer network volumes (from the API). Not all support
# the S3 upload API — EU-RO-1 / EUR-IS-1 / US-KS-2 are the usual S3-capable ones.
KNOWN_DCS = {"AP-JP-1", "CA-MTL-3", "CA-MTL-4", "EU-CZ-1", "EU-FR-1", "EU-NL-1", "EU-RO-1",
             "EU-SE-1", "EUR-IS-1", "EUR-IS-3", "EUR-NO-1", "EUR-NO-2", "US-CA-2", "US-GA-2",
             "US-IL-1", "US-KS-2", "US-MO-1", "US-MO-2", "US-NC-1", "US-NC-2", "US-NE-1",
             "US-TX-3", "US-WA-1"}


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _client() -> httpx.Client:
    _load_dotenv()
    key = os.environ.get("RUNPOD_API_KEY", "")
    if not key:
        sys.exit("RUNPOD_API_KEY not set (env or .env).")
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"}, timeout=60)


def _ok(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 300:
        sys.exit(f"{what} failed ({r.status_code}): {r.text}")
    return r.json() if r.text else {}


def ensure_volume(c: httpx.Client, name: str, dc: str, size: int) -> str:
    existing = _ok(c.get("/networkvolumes"), "list volumes")
    for v in existing:
        if v.get("name") == name:
            print(f"  volume exists: {name} -> {v['id']} ({v.get('dataCenterId')}, {v.get('size')} GB)")
            return v["id"]
    print(f"  creating volume {name} in {dc} ({size} GB)...")
    v = _ok(c.post("/networkvolumes", json={"name": name, "size": size, "dataCenterId": dc}),
            "create volume")
    print(f"  volume created -> {v['id']}")
    return v["id"]


def create_template(c: httpx.Client, name: str, image: str, disk: int, auth_id: str | None) -> str:
    body: dict = {"name": name, "imageName": image, "containerDiskInGb": disk, "isServerless": True}
    if auth_id:
        body["containerRegistryAuthId"] = auth_id
    t = _ok(c.post("/templates", json=body), "create template")
    print(f"  template created -> {t.get('id')}  (image {image})")
    return t["id"]


def ensure_endpoint(c: httpx.Client, name: str, template_id: str, volume_id: str,
                    gpu: str, dc: str) -> str:
    for e in _ok(c.get("/endpoints"), "list endpoints"):
        if e.get("name") == name:
            print(f"  endpoint exists: {name} -> {e['id']} (leaving as-is)")
            return e["id"]
    body = {
        "name": name,
        "templateId": template_id,
        "networkVolumeId": volume_id,
        "gpuTypeIds": [gpu],
        "gpuCount": 1,
        "workersMin": 0,
        "workersMax": 2,  # account worker quota is shared across endpoints
        "idleTimeout": 5,
        "flashboot": True,
        "scalerType": "QUEUE_DELAY",
        "scalerValue": 4,
        "executionTimeoutMs": 600000,
        "dataCenterIds": [dc],
    }
    e = _ok(c.post("/endpoints", json=body), "create endpoint")
    print(f"  endpoint created -> {e['id']}")
    return e["id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="loom-comfy")
    ap.add_argument("--datacenter", default="US-KS-2")
    ap.add_argument("--size", type=int, default=20)
    ap.add_argument("--gpu", default="NVIDIA GeForce RTX 4090")
    ap.add_argument("--container-disk", type=int, default=20)
    ap.add_argument("--image", default=None, help="pushed worker image; required for template+endpoint")
    ap.add_argument("--registry-auth-id", default=None)
    ap.add_argument("--volume-only", action="store_true")
    args = ap.parse_args()

    if args.datacenter not in KNOWN_DCS:
        sys.exit(f"datacenter '{args.datacenter}' not in known list: {sorted(KNOWN_DCS)}")

    with _client() as c:
        print(f"RunPod deploy: base name '{args.name}', datacenter {args.datacenter}\n")
        volume_id = ensure_volume(c, f"{args.name}-vol", args.datacenter, args.size)

        if args.volume_only or not args.image:
            print("\nVolume ready. Next:")
            print("  1. RunPod console -> Settings -> S3 API Keys -> create")
            print(f"  2. Put in .env:  RUNPOD_VOLUME_ID={volume_id}")
            print(f"                   RUNPOD_S3_DATACENTER={args.datacenter}")
            print("                   RUNPOD_S3_ACCESS_KEY=...  RUNPOD_S3_SECRET_KEY=...")
            print("  3. python runpod/worker/upload_models.py")
            print("  4. re-run this script WITH --image <your pushed image> to make the endpoint")
            return

        template_id = create_template(c, f"{args.name}-tmpl", args.image, args.container_disk,
                                      args.registry_auth_id)
        endpoint_id = ensure_endpoint(c, args.name, template_id, volume_id, args.gpu, args.datacenter)

        print("\nDone. Add to .env:")
        print(f"  RUNPOD_ENDPOINT_ID={endpoint_id}")
        print(f"  RUNPOD_VOLUME_ID={volume_id}")
        print("\nThen render:")
        print(f"  python test_serverless.py --endpoint {endpoint_id} --model anima --prompt \"...\"")


if __name__ == "__main__":
    main()
