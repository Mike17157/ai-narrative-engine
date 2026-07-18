<#
  Development-only Story Host smoke.

  It deliberately removes LOOM_PYTHON and Python/WindowsApps PATH entries from
  the child launch environment. The staged Bun executable must therefore find
  the checkout's .venv through its --root argument, rather than accidentally
  passing because a globally installed Python happens to contain Loom.
#>

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontendRoot = Join-Path $repoRoot 'frontend'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$sidecar = Join-Path $frontendRoot 'src-tauri\binaries\story-host-x86_64-pc-windows-msvc.exe'

if (-not (Test-Path -LiteralPath $python)) {
  throw "The checkout virtual environment is missing: $python. Run 'uv sync' from $repoRoot."
}

Push-Location $frontendRoot
try {
  & npm.cmd run desktop:prepare
  if ($LASTEXITCODE -ne 0) {
    throw "Desktop sidecar staging failed with exit code $LASTEXITCODE."
  }
} finally {
  Pop-Location
}

if (-not (Test-Path -LiteralPath $sidecar)) {
  throw "The staged Story Host sidecar is missing: $sidecar."
}

$previousPython = $env:LOOM_PYTHON
$hadPython = Test-Path Env:LOOM_PYTHON
$previousNativeList = $env:LOOM_STORY_HOST_BUN_LIST
$hadNativeList = Test-Path Env:LOOM_STORY_HOST_BUN_LIST
$previousPath = $env:PATH
try {
  Remove-Item Env:LOOM_PYTHON -ErrorAction SilentlyContinue
  $env:PATH = (($previousPath -split ';') | Where-Object {
    $_ -notmatch '(?i)python|windowsapps'
  }) -join ';'

  function Invoke-StoryHost([bool]$UseNativeList) {
    if ($UseNativeList) { $env:LOOM_STORY_HOST_BUN_LIST = '1' }
    else { Remove-Item Env:LOOM_STORY_HOST_BUN_LIST -ErrorAction SilentlyContinue }
    $requests = @(
      @{ id = 'health'; op = 'host.health'; payload = @{} },
      @{ id = 'list'; op = 'story.list'; payload = @{} }
    ) | ForEach-Object { $_ | ConvertTo-Json -Compress }
    $rawResponses = @($requests | & $sidecar --root $repoRoot 2>&1)
    if ($LASTEXITCODE -ne 0) {
      throw "The staged Story Host exited with code $LASTEXITCODE."
    }
    $responses = @($rawResponses | ForEach-Object {
      try { $_ | ConvertFrom-Json -ErrorAction Stop } catch { throw "The staged Story Host emitted invalid JSONL." }
    })
    $byId = @{}
    foreach ($response in $responses) { $byId[$response.id] = $response }
    Write-Output -NoEnumerate $byId
  }

  $pythonResponses = Invoke-StoryHost $false
  if (-not $pythonResponses.ContainsKey('health') -or -not $pythonResponses.health.ok -or -not $pythonResponses.health.result.pythonBridge) {
    throw 'The staged Story Host did not resolve the checkout Python/Loom bridge.'
  }
  if (-not $pythonResponses.ContainsKey('list') -or -not $pythonResponses.list.ok -or $null -eq $pythonResponses.list.result.stories) {
    throw 'The staged Story Host could not read the local Story library.'
  }
  $nativeResponses = Invoke-StoryHost $true
  if (-not $nativeResponses.ContainsKey('list') -or -not $nativeResponses.list.ok) {
    throw 'The staged Bun Story library preview did not return a library response.'
  }
  $pythonLibrary = $pythonResponses.list.result | ConvertTo-Json -Depth 20 -Compress
  $nativeLibrary = $nativeResponses.list.result | ConvertTo-Json -Depth 20 -Compress
  if ($pythonLibrary -cne $nativeLibrary) {
    throw 'The Bun Story library preview diverged from the Python bridge projection.'
  }

  [PSCustomObject]@{
    ok = $true
    operations = @('host.health', 'story.list', 'story.list Bun/Python parity')
    runtime = 'checkout-.venv'
  } | ConvertTo-Json -Compress
} finally {
  $env:PATH = $previousPath
  if ($hadPython) { $env:LOOM_PYTHON = $previousPython }
  else { Remove-Item Env:LOOM_PYTHON -ErrorAction SilentlyContinue }
  if ($hadNativeList) { $env:LOOM_STORY_HOST_BUN_LIST = $previousNativeList }
  else { Remove-Item Env:LOOM_STORY_HOST_BUN_LIST -ErrorAction SilentlyContinue }
}
