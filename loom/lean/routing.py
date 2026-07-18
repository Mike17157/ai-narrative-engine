"""Route filtering for the lean application's migration substrate.

Some mature Story modules still contain a few adjacent legacy endpoints.  A
filtered facade lets the lean composition root reuse their actual Story
handlers without accidentally publishing those adjacent surfaces.
"""

from __future__ import annotations

from collections.abc import Callable


RouteAllowed = Callable[[str, str], bool]


class FilteredDomainApp:
    """FastAPI decorator facade that tags and selectively admits routes."""

    _HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

    def __init__(self, app, domain: str, allow: RouteAllowed):
        self._app = app
        self.domain = domain
        self._allow = allow

    def __getattr__(self, name: str):
        target = getattr(self._app, name)
        if name not in self._HTTP_METHODS:
            return target

        def register(path: str, *args, **kwargs):
            if not self._allow(path, name):
                # FastAPI route decorators return an identity decorator.  Do
                # the same when a legacy-adjacent route is outside the lean
                # contract, so its handler remains importable but unpublished.
                return lambda endpoint: endpoint
            tags = list(kwargs.pop("tags", ()) or ())
            if self.domain not in tags:
                tags.append(self.domain)
            return target(path, *args, tags=tags, **kwargs)

        return register


def register_filtered_domain(module, app, ctx, domain: str, allow: RouteAllowed) -> None:
    """Register just the routes admitted by ``allow`` from a route module."""
    module.register(FilteredDomainApp(app, domain, allow), ctx)
