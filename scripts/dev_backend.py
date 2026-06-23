#!/usr/bin/env python
"""Dev launcher for the Loom backend under Claude preview.

Guarantees a clean, dev-mode server on every start so preview debugging is consistent:

  1. Frees the target port first — kills any stale orphan from a previous preview run, so
     we never silently debug a ghost process that's still holding :8000.
  2. Forces LOOM_DEV=1 — the app then SKIPS serving the prebuilt prod SPA (frontend/build),
     so hitting :8000 never masks live frontend work. The real UI is the Vite dev server on
     :5173 (which proxies /api back here). LOOM_DEV is set ONLY here, not in .env, so CLI /
     headless / production runs keep their normal prod behavior.

Used by .claude/launch.json's `backend` config. Honors LOOM_PORT (default 8000).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))              # import loom.*
sys.path.insert(0, str(ROOT / "scripts"))  # import free_port

PORT = int(os.environ.get("LOOM_PORT", "8000"))

# 1) clean the port — best-effort, never block boot
try:
    from free_port import free_port
    killed = free_port(PORT)
    print(f"[dev_backend] port {PORT}: "
          f"{'freed ' + str(killed) if killed else 'already free'}", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"[dev_backend] free_port skipped: {exc}", flush=True)

# 2) force dev mode
os.environ.setdefault("LOOM_ROOT", str(ROOT))
os.environ["LOOM_DEV"] = "1"

import uvicorn  # noqa: E402

from loom.server.app import create_app  # noqa: E402

if __name__ == "__main__":
    print(f"[dev_backend] LOOM_DEV=1 - serving API on :{PORT}; UI at http://localhost:5173",
          flush=True)
    uvicorn.run(create_app(os.environ["LOOM_ROOT"]),
                host="127.0.0.1", port=PORT, log_level="warning")
