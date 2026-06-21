"""One-shot sync: diff all local .safetensors against the RunPod volume, then upload missing."""
import os, sys
from pathlib import Path

# Load .env
env = Path('.env')
if env.is_file():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from loom.runpod.volume import VolumeConfig, volume_key

cfg = VolumeConfig()
if not cfg.configured:
    print('RunPod volume not configured - missing env vars')
    sys.exit(1)

models_dir = Path(r'C:\Users\micha\Documents\ComfyUI\models')
print(f'Models dir: {models_dir}')
print(f'S3 endpoint: {cfg.endpoint}  bucket: {cfg.volume_id}')
print()

s3 = cfg.client()
xfer = cfg.transfer_config()
print('Listing volume keys...', flush=True)
remote = cfg.list_keys(s3)
print(f'  {len(remote)} keys on volume')
print()

# Skip non-anima asset folders — SDXL checkpoints aren't used by the anima DiT worker.
SKIP_SUBDIRS = {'checkpoints', 'clip_vision', 'ipadapter', 'controlnet', 'ultralytics', 'upscale_models', 'sams'}
def _keep(p: Path) -> bool:
    parts = p.relative_to(models_dir).parts
    return parts[0] not in SKIP_SUBDIRS

files = sorted(p for p in models_dir.rglob('*.safetensors') if _keep(p))
missing = []
for p in files:
    rel = str(p.relative_to(models_dir)).replace('\\', '/')
    key = volume_key(rel)
    size_mb = round(p.stat().st_size / 1e6, 1)
    if key in remote:
        print(f'  skip  (exists)  {key}  [{size_mb:.0f} MB]')
    else:
        missing.append((rel, key, size_mb, p))

if not missing:
    print('\nVolume is up to date.')
    sys.exit(0)

total_gb = sum(mb for _, _, mb, _ in missing) / 1000
print(f'\n{len(missing)} files to upload ({total_gb:.2f} GB):')
for rel, key, mb, _ in missing:
    print(f'  + [{mb:7.0f} MB]  {key}')

print('\nStarting upload...\n')
for rel, key, mb, p in missing:
    print(f'  uploading  {key}  [{mb:.0f} MB] ...', flush=True)
    s3.upload_file(str(p), cfg.volume_id, key, Config=xfer)
    print(f'             done.')

print('\nAll done.')
