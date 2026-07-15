"""End-to-end test for the RunPod Serverless image path.

Runs the *real* configured workflow through the RunPod serverless provider and
writes the resulting image(s) to disk so you can actually look at them. It uses
the same .env loader, settings, workflow file and input map the app uses — so a
pass here means the app's serverless path works, not a toy.

Usage (from the project root):

    # 1. See exactly what workflow JSON would be sent — no API call, no endpoint
    #    needed. Proves prompt injection / BREAK / latent are applied.
    python test_serverless.py --dry-run

    # 2. Real run against your endpoint. Needs RUNPOD_API_KEY and
    #    RUNPOD_ENDPOINT_ID in .env (or pass --endpoint).
    python test_serverless.py --prompt "1girl, red dress, city street, night"

    # Pick a different configured image model / workflow:
    python test_serverless.py --model sprite --prompt "..."

Outputs land in ./runpod_test_output/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from loom.config.loader import load_settings
from loom.server.app import _load_dotenv


def _pick_model(settings, requested: str | None) -> tuple[str, object]:
    """Resolve the model key to test: explicit, else the first comfyui/runpod_serverless image model."""
    image_models = {k: m for k, m in settings.models.items()
                    if m.kind == "image" and m.provider in ("comfyui", "runpod_serverless")}
    if requested:
        if requested not in settings.models:
            sys.exit(f"model '{requested}' not in models.yaml. image models: {list(image_models)}")
        return requested, settings.models[requested]
    if not image_models:
        sys.exit("no comfyui/runpod_serverless image models found in models.yaml")
    key = next(iter(image_models))
    return key, image_models[key]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="image model key from models.yaml (default: first comfyui image model)")
    ap.add_argument("--prompt", default="1girl, solo, looking at viewer, detailed background, masterpiece")
    ap.add_argument("--negative", default=None, help="optional negative prompt override")
    ap.add_argument("--endpoint", default=None, help="RunPod serverless endpoint id (else RUNPOD_ENDPOINT_ID env)")
    ap.add_argument("--count", type=int, default=1, help="how many images to render")
    ap.add_argument("--latent", default=None, help="canvas WxH, e.g. 832x1216")
    ap.add_argument("--checkpoint", default=None,
                    help="UNet/checkpoint name to inject (e.g. anima/anisnuff_v15-000024.safetensors). "
                         "Required when the workflow has a placeholder model path.")
    ap.add_argument("--detailer", action="store_true", help="enable FaceDetailer pass (character gens)")
    ap.add_argument("--upscale", action="store_true", help="enable UltimateSDUpscale pass")
    ap.add_argument("--sage", action="store_true", help="enable SageAttention kernel (faster; needs sageattention in the worker)")
    ap.add_argument("--dry-run", action="store_true", help="print the prepared workflow JSON; make no API call")
    args = ap.parse_args()

    _load_dotenv(ROOT)
    settings = load_settings(ROOT)

    model_key, model = _pick_model(settings, args.model)
    opts = model.options
    print("=" * 64)
    print(f"Model:      {model_key}  (provider={model.provider})")
    print(f"Workflow:   {opts.get('workflow')}")
    print(f"Inputs:     {opts.get('inputs')}")
    print(f"OutputNode: {opts.get('output_node')}")
    print("=" * 64)

    latent = None
    if args.latent:
        w, h = args.latent.lower().split("x")
        latent = (int(w), int(h))

    flags: dict[str, bool] = {}
    if args.detailer:
        flags["detailer"] = True
    if args.upscale:
        flags["upscale"] = True
    if args.sage:
        flags["sage"] = True

    # ---- dry run: show the prepared graph, no network -------------------------
    if args.dry_run:
        from loom.comfy.stack import inject_models
        from loom.providers import _workflow
        wf = json.loads(Path(opts["workflow"]).read_text(encoding="utf-8"))
        if args.checkpoint:
            wf = inject_models(wf, args.checkpoint, loras=[])
        graph = _workflow.inject(wf, opts.get("inputs", {}), args.prompt, args.negative,
                                 out_prefix="loom/_test/serverless", latent=latent,
                                 flags=flags or None)
        print("\nPrepared workflow (this is exactly what would be sent):\n")
        print(json.dumps(graph, indent=2)[:4000])
        print("\n[dry-run] No API call made. The positive node should now hold your prompt.")
        return

    # ---- real run ------------------------------------------------------------
    endpoint_id = args.endpoint or os.environ.get("RUNPOD_ENDPOINT_ID", "")
    api_key = os.environ.get("RUNPOD_API_KEY", "")

    if not endpoint_id:
        sys.exit("No endpoint id. Set RUNPOD_ENDPOINT_ID in .env or pass --endpoint <id>.\n"
                 "(This is the serverless endpoint id from the RunPod dashboard, NOT a pod id.)")
    if not api_key:
        sys.exit("No RUNPOD_API_KEY in environment / .env.")

    from loom.comfy.stack import inject_models
    from loom.providers.runpod_serverless_provider import RunPodServerlessProvider

    provider = RunPodServerlessProvider({
        "endpoint_id": endpoint_id,
        "api_key": api_key,
        "workflow": opts["workflow"],          # path — provider loads it
        "inputs": opts.get("inputs", {}),
        "output_node": opts.get("output_node"),
        "timeout_s": float(opts.get("timeout_s", 600)),
    })

    if args.checkpoint:
        provider.workflow = inject_models(provider.workflow, args.checkpoint, loras=[])
        print(f"Checkpoint: {args.checkpoint}")
    if flags:
        print(f"Flags:      {flags}")

    print(f"Endpoint:   {provider.base_url}")
    print(f"Prompt:     {args.prompt!r}")
    print(f"Rendering {args.count} image(s)...\n")

    out_dir = ROOT / "runpod_test_output"
    out_dir.mkdir(exist_ok=True)
    saved: list[str] = []

    for i in range(args.count):
        t0 = time.monotonic()
        try:
            result = provider.generate_image(
                prompt=args.prompt,
                negative_prompt=args.negative,
                out_prefix=f"loom/_test/serverless_{i}",
                latent=latent,
                flags=flags or None,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}] FAILED: {type(e).__name__}: {e}")
            continue

        dt = time.monotonic() - t0
        print(f"  [{i}] job={result.meta.get('job_id')}  images={len(result.images)}  {dt:.1f}s")
        for j, img in enumerate(result.images):
            path = out_dir / f"serverless_{model_key}_{i}_{j}.png"
            path.write_bytes(img)
            saved.append(str(path))
            print(f"        saved {len(img):,} bytes -> {path}")

    print("\n" + "=" * 64)
    if saved:
        print(f"DONE — {len(saved)} image(s) written. Open them to verify:")
        for p in saved:
            print(f"  {p}")
    else:
        print("No images were produced. Check the endpoint logs on the RunPod dashboard.")
    print("=" * 64)


if __name__ == "__main__":
    main()
