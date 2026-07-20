@echo off
rem Spawn the Vite dev server in its own minimized window and return immediately.
start "LoomUI" /min cmd /k "%~dp0dev_ui.bat"
