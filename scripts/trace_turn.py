"""One live play turn with per-model-call tracing. Run: python scripts/trace_turn.py"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from loom.providers import openai_compat

calls = []
_orig = openai_compat.OpenAICompatProvider.generate_text


def traced(self, **kwargs):
    t0 = time.time()
    try:
        out = _orig(self, **kwargs)
        dt = time.time() - t0
        calls.append({
            "model": getattr(self, "model", "?"),
            "emits": bool(kwargs.get("emits")),
            "sys": (kwargs.get("system") or "")[:70].replace("\n", " "),
            "secs": round(dt, 1),
            "ok": True,
            "reply_chars": len(getattr(out, "text", "") or ""),
            "data_keys": sorted((getattr(out, "data", None) or {}).keys()) if getattr(out, "data", None) else None,
        })
        return out
    except Exception as e:  # noqa: BLE001
        calls.append({"model": getattr(self, "model", "?"), "secs": round(time.time() - t0, 1),
                      "ok": False, "err": f"{type(e).__name__}: {e}"[:200],
                      "sys": (kwargs.get("system") or "")[:70].replace("\n", " ")})
        raise


openai_compat.OpenAICompatProvider.generate_text = traced

from loom.lean.app import create_lean_app  # noqa: E402

app = create_lean_app(".", comfy_enabled=False)
c = TestClient(app)

t0 = time.time()
r = c.post("/api/stories/undergrowth/play",
           json={"text": "I drift toward the window and watch the tree line past the gym roof."})
total = time.time() - t0
print(f"PLAY status={r.status_code} total={total:.1f}s calls={len(calls)}")
for i, call in enumerate(calls):
    print(f"  [{i}] {json.dumps(call, default=str)[:260]}")
body = r.json()
print("pov:", body.get("pov"), "| present:", body.get("present"))
print("reply:", (body.get("reply") or "")[:300])
