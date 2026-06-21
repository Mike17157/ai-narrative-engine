@echo off
rem ===========================================================================
rem  Loom launcher (double-click me).
rem  Sets up Python + the Svelte frontend, then launches the web app.
rem  Honors LOOM_MODE in .env:
rem    LOOM_MODE=dev  -> launches BOTH the Vite dev server (HMR, :5173) and the
rem                      auto-reloading API (:8000); opens :5173. No build.
rem    LOOM_MODE=prod -> builds the UI and serves the static SPA on :8000.
rem  Safe to run repeatedly.
rem ===========================================================================
setlocal enabledelayedexpansion
pushd "%~dp0"
title Loom

echo(
echo  ============================================
echo   Loom
echo  ============================================
echo(

rem --- read LOOM_MODE from .env (default prod) -----------------------------
set "LOOM_MODE=prod"
if exist ".env" for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
  if /i "%%A"=="LOOM_MODE" set "LOOM_MODE=%%B"
)
for /f "tokens=* delims= " %%M in ("!LOOM_MODE!") do set "LOOM_MODE=%%M"
if /i "!LOOM_MODE!"=="development" set "LOOM_MODE=dev"
echo  [*] Mode: !LOOM_MODE!   ^(set LOOM_MODE in .env to change^)
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

rem --- Frontend (Svelte) ---------------------------------------------------
where npm >nul 2>nul
if errorlevel 1 (
  echo  [!] Node/npm not found - skipping UI. The server will serve a basic
  echo      fallback page. Install Node 20+ to get the full Svelte UI.
) else (
  if not exist "frontend\node_modules" (
    echo  [*] Installing frontend packages ^(first run only^) ...
    pushd frontend & call npm install & popd
  )
  if /i "!LOOM_MODE!"=="dev" (
    echo  [*] Dev mode - skipping the production build ^(Vite serves the UI live^).
  ) else (
    echo  [*] Building the UI ...
    pushd frontend & call npm run build & popd
  )
)

rem --- launch --------------------------------------------------------------
echo(
if /i "!LOOM_MODE!"=="dev" (
  echo  [*] Starting Loom ^(dev^): UI http://127.0.0.1:5173 ^(HMR^) + API http://127.0.0.1:8000
  echo      ^(close this window to stop both^)
  echo(
  "%PY%" -m loom.cli serve --host 127.0.0.1 --port 8000
) else (
  echo  [*] Starting Loom at http://127.0.0.1:8000  ^(close this window to stop^)
  echo(
  "%PY%" -m loom.cli serve --prod --host 127.0.0.1 --port 8000
)

echo(
echo  Loom has stopped.
pause
popd
endlocal
