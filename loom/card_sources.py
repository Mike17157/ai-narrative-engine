"""Import character cards from a site URL — the SillyTavern "import from URL"
feature, ported.

SillyTavern doesn't scrape pages. Each supported site has a small downloader
that hits that site's *API*, and the host of the pasted URL selects which one to
use (see `fetch_card`). Every downloader ends the same way: produce a character
dict (V1/V2 card shape) plus the avatar image bytes. We then hand the card off
to the same `to_character` normalizer the file-upload path uses, so a URL import
and a PNG import converge on one code path.

Networking uses httpx (already a dependency). A SillyTavern-style User-Agent is
sent because Chub's Cloudflare blocks generic/unknown agents.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from urllib.parse import urlparse

import httpx

from .cards import extract_card_json

UA = "SillyTavern:UNKNOWN:UNKNOWN"
_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _client() -> httpx.Client:
    return httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": UA})


def _from_embedded_png(data: bytes) -> tuple[dict, bytes | None]:
    """A source that returns a Tavern PNG (card embedded in the image)."""
    card = extract_card_json(data)
    avatar = data if data.startswith(b"\x89PNG\r\n\x1a\n") else None
    return card, avatar


# --- Chub / CharacterHub ---------------------------------------------------- #
def _chub_path(url: str) -> str | None:
    path = urlparse(url).path if "://" in url else url
    parts = [p for p in path.split("/") if p]
    if parts and parts[0].lower() in ("characters", "lorebooks"):
        parts = parts[1:]
    return f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else None


def _fetch_chub(url: str) -> tuple[dict, bytes | None]:
    cp = _chub_path(url)
    if not cp:
        raise ValueError("could not parse a Chub character path from the URL")
    creator, project = cp.split("/", 1)
    with _client() as c:
        r = c.get(f"https://api.chub.ai/api/characters/{creator}/{project}?full=true",
                  headers={"Accept": "application/json"})
        r.raise_for_status()
        node = r.json()["node"]
        d = node.get("definition") or {}
        # Chub stores fields under its own names — this remap mirrors ST exactly.
        card = {
            "spec": "chara_card_v2", "spec_version": "2.0",
            "data": {
                "name": d.get("name") or project,
                "description": d.get("personality"),
                "personality": d.get("tavern_personality"),
                "scenario": d.get("scenario"),
                "first_mes": d.get("first_message"),
                "mes_example": d.get("example_dialogs"),
                "creator_notes": d.get("description"),
                "system_prompt": d.get("system_prompt"),
                "post_history_instructions": d.get("post_history_instructions"),
                "alternate_greetings": d.get("alternate_greetings") or [],
                "tags": node.get("topics") or [],
                "creator": creator,
                "character_book": d.get("embedded_lorebook"),  # the lorebook
                "extensions": d.get("extensions"),
            },
        }
        avatar = None
        img = node.get("max_res_url") or node.get("avatar_url")
        if img:
            ir = c.get(img)
            if ir.is_success and ir.content.startswith(b"\x89PNG\r\n\x1a\n"):
                avatar = ir.content
    return card, avatar


# --- JanitorAI (via the jannyai.com download API) --------------------------- #
def _fetch_janny(url: str) -> tuple[dict, bytes | None]:
    uuid = _UUID.search(url)
    if not uuid:
        raise ValueError("no character UUID found in the JanitorAI URL")
    with _client() as c:
        r = c.post("https://api.jannyai.com/api/v1/download",
                   json={"characterId": uuid.group(0)}, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") != "ok" or not payload.get("downloadUrl"):
            raise ValueError(f"JanitorAI download failed: {payload}")
        png = c.get(payload["downloadUrl"])
        png.raise_for_status()
        return _from_embedded_png(png.content)


# --- aicharactercards.com (AICC png API) ------------------------------------ #
def _fetch_aicc(url: str) -> tuple[dict, bytes | None]:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        raise ValueError("could not parse an AICC author/character from the URL")
    ident = f"{parts[-2]}/{parts[-1]}"
    with _client() as c:
        r = c.get(f"https://aicharactercards.com/wp-json/pngapi/v1/image/{ident}")
        r.raise_for_status()
        return _from_embedded_png(r.content)


# --- Pygmalion (server export to V2 json) ----------------------------------- #
def _fetch_pygmalion(url: str) -> tuple[dict, bytes | None]:
    uuid = _UUID.search(url)
    if not uuid:
        raise ValueError("no character UUID found in the Pygmalion URL")
    with _client() as c:
        r = c.get(f"https://server.pygmalion.chat/api/export/character/{uuid.group(0)}/v2")
        r.raise_for_status()
        card = r.json().get("character") or {}
        avatar = None
        av = (card.get("data") or {}).get("avatar")
        if av and av.startswith("http"):
            ir = c.get(av)
            if ir.is_success and ir.content.startswith(b"\x89PNG\r\n\x1a\n"):
                avatar = ir.content
        return card, avatar


# --- RisuRealm (returns a V3 png; our parser reads the `ccv3` chunk) --------- #
def _fetch_risu(url: str) -> tuple[dict, bytes | None]:
    m = re.search(r"realm\.risuai\.net/character/([a-f0-9-]+)", url, re.I)
    if not m:
        raise ValueError("could not parse a RisuRealm character id from the URL")
    with _client() as c:
        r = c.get(f"https://realm.risuai.net/api/v1/download/png-v3/{m.group(1)}?non_commercial=true")
        r.raise_for_status()
        return _from_embedded_png(r.content)


# --- Perchance (gzipped JSON on a file host) -------------------------------- #
def _fetch_perchance(url: str) -> tuple[dict, bytes | None]:
    slug = url.split("~")[1] if "~" in url else ""
    if not slug:
        raise ValueError("could not parse a Perchance slug (expected name~hash.gz) from the URL")
    with _client() as c:
        r = c.get(f"https://user.uploads.dev/file/{slug}")
        r.raise_for_status()
        pc = json.loads(gzip.decompress(r.content)).get("addCharacter") or {}
        card = {
            "spec": "chara_card_v2", "spec_version": "2.0",
            "data": {
                "name": pc.get("name") or "Unnamed Perchance Character",
                "description": pc.get("roleInstruction") or "",
                "personality": pc.get("reminderMessage") or "",
                "creator": pc.get("metaTitle") or "",
                "creator_notes": pc.get("metaDescription") or "",
            },
        }
        avatar = None
        av = (pc.get("avatar") or {}).get("url") or ""
        if av.startswith("data:image/png;base64,"):
            avatar = base64.b64decode(av.split(",", 1)[1])
        elif av.startswith("http"):
            ir = c.get(av)
            if ir.is_success and ir.content.startswith(b"\x89PNG\r\n\x1a\n"):
                avatar = ir.content
        return card, avatar


# --- generic: any URL that points straight at a Tavern PNG ------------------ #
def _fetch_generic(url: str) -> tuple[dict, bytes | None]:
    with _client() as c:
        r = c.get(url)
        r.raise_for_status()
        return _from_embedded_png(r.content)


def fetch_card(url: str) -> tuple[dict, bytes | None]:
    """Dispatch on the URL's host to the matching downloader. Returns
    (raw_card_dict, avatar_png_bytes_or_None)."""
    url = url.strip()
    host = _host(url)
    if "chub.ai" in host or "characterhub.org" in host:
        return _fetch_chub(url)
    if "janitorai" in host:
        return _fetch_janny(url)
    if "aicharactercards.com" in host:
        return _fetch_aicc(url)
    if "pygmalion.chat" in host:
        return _fetch_pygmalion(url)
    if "realm.risuai.net" in host:
        return _fetch_risu(url)
    if "perchance.org" in host:
        return _fetch_perchance(url)
    return _fetch_generic(url)  # assume the URL is a direct Tavern PNG
