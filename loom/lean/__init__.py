"""The intentionally small Loom product surface.

The legacy application remains available while this package grows to parity.
It composes only the Story domain, its trusted model gateway, and an optional
minimal Krea2/Comfy image capability.
"""

from .app import build_lean_context, create_lean_app, dev_lean_app

__all__ = ("build_lean_context", "create_lean_app", "dev_lean_app")
