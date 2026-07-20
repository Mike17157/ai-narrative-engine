@echo off
rem Launch the Vite dev server in its own window (used by agent startup).
cd /d "%~dp0..\frontend"
npm run dev
