"""Route-domain registration helpers.

Endpoint modules keep their existing ``register(app, ctx)`` contract.  The application
composition root supplies a ``DomainApp`` so every route is tagged by its stable API
domain without changing URLs or endpoint behavior.  Those domains are the attachment
point for future authorization, rate limits, and telemetry.
"""
from __future__ import annotations

class DomainApp:
    """FastAPI decorator facade that applies one OpenAPI domain tag to routes."""

    _HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

    def __init__(self, app, domain: str):
        self._app = app
        self.domain = domain

    def __getattr__(self, name: str):
        target = getattr(self._app, name)
        if name not in self._HTTP_METHODS:
            return target

        def register(*args, **kwargs):
            tags = list(kwargs.pop("tags", ()) or ())
            if self.domain not in tags:
                tags.append(self.domain)
            return target(*args, tags=tags, **kwargs)

        return register


def register_domain(module, app, ctx, domain: str) -> None:
    """Register one existing route module under its API domain."""
    module.register(DomainApp(app, domain), ctx)
