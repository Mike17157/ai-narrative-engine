@echo off
rem ===========================================================================
rem  Loom launcher (double-click me).
rem  Sets up Python + the Svelte frontend, builds the UI, and opens the web app.
rem  Safe to run repeatedly.
rem ===========================================================================
setlocal
pushd "%~dp0"
title Loom

echo(
echo  ============================================
echo   Loom
echo  ============================================
echo(

rem --- locate Python -------------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
  echo  [X] Python was not found on your PATH.
  echo      Install Python 3.11+ from https://www.python.org/downloads/
  echo      and tick "Add python.exe to PATH", then run this again.
  echo(
  pause & popd & exit /b 1
)

rem --- Python venv + Loom --------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo  [*] Creating virtual environment ^(.venv^) ...
  python -m venv .venv
  if errorlevel 1 ( echo  [X] Failed to create venv. & pause & popd & exit /b 1 )
)
set "PY=.venv\Scripts\python.exe"
echo  [*] Installing / updating Python dependencies ...
"%PY%" -m pip install --upgrade pip >nul 2>nul
"%PY%" -m pip install -e .
if errorlevel 1 ( echo  [X] Python dependency install failed. & pause & popd & exit /b 1 )

rem --- Frontend (Svelte) build --------------------------------------------
where npm >nul 2>nul
if errorlevel 1 (
  echo  [!] Node/npm not found - skipping UI build. The server will serve a
  echo      basic fallback page. Install Node 20+ to get the full Svelte UI.
) else (
  if not exist "frontend\node_modules" (
    echo  [*] Installing frontend packages ^(first run only^) ...
    pushd frontend & call npm install & popd
  )
  echo  [*] Building the UI ...
  pushd frontend & call npm run build & popd
)

rem --- launch --------------------------------------------------------------
echo(
echo  [*] Starting Loom at http://127.0.0.1:8000  ^(close this window to stop^)
echo(
"%PY%" -m loom.cli serve --host 127.0.0.1 --port 8000

echo(
echo  Loom has stopped.
pause
popd
endlocal
