# One-time setup for LoRA training (kohya sd-scripts).
#
# Clones kohya-ss/sd-scripts, builds a Python 3.11 venv (kohya's deps do NOT work
# on 3.13), and installs the CUDA PyTorch stack matched to YOUR GPU. We install
# requirements first and then force the CUDA torch/torchvision on top, because
# `diffusers[torch]` in requirements.txt otherwise drags in a CPU-only torch.
# xformers is skipped — training uses PyTorch's built-in --sdpa attention.
#
# The CUDA build is auto-detected from the GPU's compute capability (Blackwell /
# RTX 50-series needs cu128; older cards use cu124). Override with -Cuda if needed.
#
#   pwsh scripts/setup_trainer.ps1 -Configure            # auto-detect CUDA build
#   pwsh scripts/setup_trainer.ps1 -DetectOnly           # just print the pick
#   pwsh scripts/setup_trainer.ps1 -Cuda cu128 -Configure
#   pwsh scripts/setup_trainer.ps1 -Recreate -Configure  # rebuild the venv
#
# Requires: git, Python 3.11 (or 3.10) via the `py` launcher, an NVIDIA GPU.

param(
  [string]$Dir = "",
  [string]$Cuda = "auto",    # auto | cu128 | cu124 | cu121 — auto picks from the GPU
  [switch]$Configure,        # write the path into configs/trainer.json
  [switch]$Recreate,         # delete and rebuild the venv
  [switch]$DetectOnly        # print the detected GPU + CUDA build, then exit
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Dir) { $Dir = Join-Path $root "trainer\sd-scripts" }

# Map a GPU's compute capability to the right PyTorch CUDA build. Newer archs
# need newer CUDA: Blackwell (sm_120, RTX 50-series) has no kernels in cu124.
function Resolve-CudaBuild([string]$requested) {
  if ($requested -and $requested -ne "auto") { return $requested }
  $cap = $null; $name = ""
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try {
      $line = (& nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader 2>$null | Select-Object -First 1)
      if ($line) {
        $parts = $line.Split(','); $name = $parts[0].Trim()
        if ($parts.Count -ge 2) { $cap = [double]($parts[1].Trim()) }
      }
    } catch {}
  }
  if (-not $cap) {
    Write-Host "==> Couldn't detect GPU compute capability — defaulting to cu124" -ForegroundColor Yellow
    return "cu124"
  }
  $build = if ($cap -ge 12.0) { "cu128" }   # Blackwell  (RTX 50-series)
           elseif ($cap -ge 7.5) { "cu124" } # Turing/Ampere/Ada/Hopper
           else { "cu121" }                  # Volta and older
  Write-Host "==> Detected GPU: $name (compute capability $cap) -> $build" -ForegroundColor Cyan
  return $build
}

$Cuda = Resolve-CudaBuild $Cuda
if ($DetectOnly) { Write-Host "CUDA build: $Cuda" -ForegroundColor Green; exit 0 }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git not found on PATH" }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "the Python 'py' launcher was not found" }

# kohya supports 3.10/3.11 — NOT 3.13. Pick 3.11, else 3.10.
$pyArgs = $null
foreach ($ver in @('3.11', '3.10')) {
  & py "-$ver" --version *> $null
  if ($LASTEXITCODE -eq 0) { $pyArgs = @("-$ver"); break }
}
if (-not $pyArgs) {
  throw "Need Python 3.11 or 3.10 (kohya's deps don't support 3.13). Install it from python.org and re-run."
}
Write-Host "==> Using Python $($pyArgs[0]) for the trainer venv" -ForegroundColor Cyan

if (-not (Test-Path $Dir)) {
  Write-Host "==> Cloning kohya sd-scripts -> $Dir" -ForegroundColor Cyan
  git clone --depth 1 https://github.com/kohya-ss/sd-scripts $Dir
}

$venv = Join-Path $Dir "venv"
$py = Join-Path $venv "Scripts\python.exe"

# Recreate the venv if it's missing, on the wrong Python, or -Recreate was passed.
$needVenv = $true
if (Test-Path $py) {
  $cur = (& $py --version 2>&1)
  if (-not $Recreate -and $cur -match '3\.1[01]\.') { $needVenv = $false }
  elseif (Test-Path $venv) { Write-Host "==> Replacing venv ($cur)" -ForegroundColor Yellow; Remove-Item -Recurse -Force $venv }
}
if ($needVenv) { & py $pyArgs -m venv $venv }

Write-Host "==> Upgrading pip" -ForegroundColor Cyan
& $py -m pip install --upgrade pip

Write-Host "==> Installing kohya requirements (pulls a CPU torch we replace next)" -ForegroundColor Cyan
& $py -m pip install -r (Join-Path $Dir "requirements.txt")

Write-Host "==> Installing CUDA PyTorch + torchvision ($Cuda) — several GB" -ForegroundColor Cyan
& $py -m pip install --upgrade --force-reinstall --no-deps torch torchvision --index-url "https://download.pytorch.org/whl/$Cuda"

Write-Host "==> Installing onnxruntime (for the WD14 local tagger)" -ForegroundColor Cyan
& $py -m pip install onnxruntime

Write-Host "==> Verifying" -ForegroundColor Cyan
& $py -c "import torch, torchvision; print('torch', torch.__version__, '| torchvision', torchvision.__version__, '| cuda', torch.cuda.is_available())"
if ($LASTEXITCODE -ne 0) { throw "torch/torchvision failed to import — check the output above" }

Write-Host ""
Write-Host "==> Done." -ForegroundColor Green
Write-Host "    sd-scripts dir : $Dir"
Write-Host "    venv python    : $py"
Write-Host "    CUDA build     : $Cuda"
Write-Host "    If 'cuda' printed False, override the auto-pick: re-run with -Cuda cu128 -Recreate (or cu124)."

if ($Configure) {
  $cfgPath = Join-Path $root "configs\trainer.json"
  $existing = @{}
  if (Test-Path $cfgPath) { $existing = Get-Content $cfgPath -Raw | ConvertFrom-Json -AsHashtable }
  $existing["sd_scripts_dir"] = $Dir
  $existing["python"] = $py
  ($existing | ConvertTo-Json) | Set-Content -Path $cfgPath -Encoding utf8
  Write-Host "    wrote configs\trainer.json" -ForegroundColor Green
}
