@echo off
REM Build and push the Anima serverless worker image.
REM
REM Usage:
REM   set IMAGE=youruser/loom-comfy-worker:1.0   (required)
REM   set BASE_TAG=3.4.0-base                    (optional, matches Dockerfile ARG)
REM   build.bat
REM
REM On Apple Silicon add --platform linux/amd64; on Windows/x64 it's set for you.
REM Rebuilds are only needed when the custom-node pack set changes (sync.py detects
REM that); LoRA/checkpoint changes go on the volume, not the image.

setlocal

if "%IMAGE%"=="" (
  echo ERROR: set the IMAGE env var first, e.g.  set IMAGE=youruser/loom-comfy-worker:1.0
  exit /b 1
)

if "%BASE_TAG%"=="" set BASE_TAG=3.6.0-base

echo Building %IMAGE% (BASE_TAG=%BASE_TAG%)...
docker build --platform linux/amd64 --build-arg BASE_TAG=%BASE_TAG% -t %IMAGE% .
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo Pushing %IMAGE%...
docker push %IMAGE%
if errorlevel 1 (
  echo Push failed.
  exit /b 1
)

echo Done. Point the serverless template/endpoint at %IMAGE%.
endlocal
