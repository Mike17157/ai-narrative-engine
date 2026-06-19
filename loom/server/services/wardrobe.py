"""Portrait wardrobe pipeline — thin re-export shim.

Implementation lives in loom/pipeline/s07_manifest.py (apply_manifest, plan_and_apply).
"""

from loom.pipeline.s07_manifest import apply_manifest, plan_and_apply

__all__ = ["apply_manifest", "plan_and_apply"]
