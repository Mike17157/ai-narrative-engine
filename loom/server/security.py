"""Local-first API access controls."""
from __future__ import annotations

import hmac
import ipaddress
import os

from fastapi.responses import JSONResponse


def is_loopback_host(host: str) -> bool:
    """Return whether a server bind host is limited to the local machine."""
    value = (host or "").strip().lower().strip("[]")
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def api_token() -> str:
    return os.environ.get("LOOM_API_TOKEN", "").strip()


def require_token_for_bind(host: str) -> None:
    """Reject an externally reachable bind without an explicit API token."""
    if not is_loopback_host(host) and not api_token():
        raise RuntimeError(
            "LOOM_API_TOKEN is required when binding Loom beyond localhost. "
            "Set a long random token in the environment or .env file."
        )


def configure_api_auth(app) -> None:
    """Require a bearer token for API requests when ``LOOM_API_TOKEN`` is configured."""
    token = api_token()
    required = os.environ.get("LOOM_REQUIRE_AUTH", "").strip().lower() in {"1", "true", "yes"}
    if required and not token:
        raise RuntimeError("LOOM_REQUIRE_AUTH is set but LOOM_API_TOKEN is missing")
    if not token:
        app.state.api_auth_enabled = False
        return

    app.state.api_auth_enabled = True

    @app.middleware("http")
    async def require_api_token(request, call_next):
        if request.url.path.startswith("/api/"):
            bearer = request.headers.get("authorization", "")
            supplied = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
            supplied = supplied or request.headers.get("x-api-key", "")
            if not hmac.compare_digest(supplied, token):
                return JSONResponse(
                    {"error": "authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)
