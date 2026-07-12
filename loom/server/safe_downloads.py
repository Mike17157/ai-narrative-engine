"""Validated streaming downloads for user-supplied remote URLs."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urljoin, urlsplit

import httpx


MAX_REFERENCE_DOWNLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_MODEL_DOWNLOAD_BYTES = 20 * 1024 * 1024 * 1024
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class DownloadError(ValueError):
    """A remote download failed validation or exceeded its resource limits."""


def max_model_download_bytes() -> int:
    raw = os.environ.get("LOOM_MAX_MODEL_DOWNLOAD_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_MODEL_DOWNLOAD_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise DownloadError("LOOM_MAX_MODEL_DOWNLOAD_BYTES must be a positive integer") from exc
    if value <= 0:
        raise DownloadError("LOOM_MAX_MODEL_DOWNLOAD_BYTES must be a positive integer")
    return value


def _resolve_public_host(host: str) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise DownloadError("remote host could not be resolved") from exc
    if not addresses:
        raise DownloadError("remote host did not resolve to an address")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise DownloadError("remote host resolves to a non-public address")


async def validate_public_https_url(value: str) -> str:
    """Validate an HTTPS URL and reject hosts that resolve outside the public internet."""
    url = (value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise DownloadError("a public HTTPS URL is required")
    if parsed.username or parsed.password:
        raise DownloadError("credentials in download URLs are not allowed")
    await asyncio.to_thread(_resolve_public_host, parsed.hostname)
    return url


async def get_public_response(client: httpx.AsyncClient, url: str, *, max_redirects: int = 3) -> httpx.Response:
    """Open a validated public response, validating each redirect before following it."""
    current = await validate_public_https_url(url)
    for _ in range(max_redirects + 1):
        response = await client.send(client.build_request("GET", current), stream=True)
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        location = response.headers.get("location")
        await response.aclose()
        if not location:
            raise DownloadError("redirect response has no location")
        current = await validate_public_https_url(urljoin(current, location))
    raise DownloadError("too many redirects")


def validate_content_length(response: httpx.Response, max_bytes: int) -> None:
    raw = response.headers.get("content-length")
    if not raw:
        return
    try:
        size = int(raw)
    except ValueError as exc:
        raise DownloadError("remote server returned an invalid content length") from exc
    if size < 0 or size > max_bytes:
        raise DownloadError(f"download exceeds the {max_bytes} byte limit")


async def read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a response body with a hard byte cap, including chunked responses."""
    validate_content_length(response, max_bytes)
    data = bytearray()
    async for chunk in response.aiter_bytes(1 << 20):
        data.extend(chunk)
        if len(data) > max_bytes:
            raise DownloadError(f"download exceeds the {max_bytes} byte limit")
    return bytes(data)


async def write_limited(response: httpx.Response, target, max_bytes: int) -> int:
    """Stream a response to an open binary file while enforcing a hard byte cap."""
    validate_content_length(response, max_bytes)
    total = 0
    async for chunk in response.aiter_bytes(1 << 20):
        total += len(chunk)
        if total > max_bytes:
            raise DownloadError(f"download exceeds the {max_bytes} byte limit")
        target.write(chunk)
    return total
