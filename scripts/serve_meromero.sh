#!/usr/bin/env bash
# Serve the local MeroMero model (G4-MeroMero-26B-A4B, an uncensored MoE) for the
# world-state engine's scribe/narrator role via llama.cpp's OpenAI-compatible server.
#
# Why llama-server: it compiles the request's `response_format: {type:json_schema}`
# (which loom's openai_compat provider already sends) into a GBNF grammar and CONSTRAINS
# decoding to it — so the scribe's state_deltas come back schema-valid every turn, even
# from a small abliterated model.
#
# After it's up, wire it in the app:
#   1. ⚙ Models → add a text connection:
#        provider  : openai            (any name; routed through the OpenAI-compat adapter)
#        base_url  : http://127.0.0.1:8080/v1
#        model     : meromero
#        api_key   : sk-noop           (llama-server ignores it)
#      …and set it as the ACTIVE text connection (the narrator/scribe primary).
#   2. PUT /api/text-roles { "fallback": "<your deepseek connection id>" }
#      so a refusal re-runs on DeepSeek V3.2.
#
# Requires `llama-server` (from llama.cpp) on PATH. Adjust --n-gpu-layers to fit VRAM
# (999 = offload all; lower it if it OOMs against ComfyUI).
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${MODEL:-G4-MeroMero-26B-A4B-it-uncensored-heretic-Q4_K_S.gguf}"
PORT="${PORT:-8080}"
NGL="${NGL:-999}"
CTX="${CTX:-8192}"

exec llama-server \
  -m "$MODEL" \
  --alias meromero \
  --host 127.0.0.1 --port "$PORT" \
  --ctx-size "$CTX" \
  --n-gpu-layers "$NGL" \
  --jinja
