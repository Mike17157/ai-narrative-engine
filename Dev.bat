@echo off
rem ===========================================================================
rem  Loom DEV launcher — hot-reload for frontend work.
rem  Opens two windows: the FastAPI API (:8000) and the Vite dev server (:5173,
rem  which proxies /api to :8000 and hot-reloads the Svelte UI). Use :5173.
rem ===========================================================================
setlocal
pushd "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo  Run Start.bat once first to set up the environment.
  pause & popd & exit /b 1
)

rem --dev forces dev mode (reload) regardless of LOOM_MODE; --no-vite keeps Vite to the
rem dedicated window below so it isn't started twice on :5173.
start "Loom API" cmd /k ".venv\Scripts\python.exe -m loom.cli serve --dev --no-vite --no-open --host 127.0.0.1 --port 8000"
start "Loom UI (dev)" cmd /k "cd frontend && npm run dev"

echo  Dev servers starting...
echo    API: http://127.0.0.1:8000
echo    UI : http://127.0.0.1:5173   ^<-- open this one
start "" http://127.0.0.1:5173
popd
endlocal
