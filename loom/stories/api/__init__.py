"""HTTP adapters for the story feature.

Each module owns one coherent route family and exposes ``register(app, ctx)``.
The package deliberately keeps FastAPI wiring separate from story domain logic.
"""
