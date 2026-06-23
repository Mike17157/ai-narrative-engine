"""Connections — saved, typed API credentials + the connect/validate flow.

A connection is a credentialed backend of a given KIND:
  * text  — a chat provider (OpenRouter, OpenAI-compatible, Anthropic)
  * image — an image backend (ComfyUI, local or remote/RunPod)

Text and image have completely independent connections and independent "active"
selection, so the TextGen tab and the Images tab each manage their own. Keys
live in a gitignored secrets file, like SillyTavern's secrets.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Provider definitions, grouped by kind.
# --------------------------------------------------------------------------- #
class ProviderInfo(BaseModel):
    id: str
    label: str
    kind: str  # "text" | "image"
    default_base_url: str
    base_url_editable: bool = True
    needs_key: bool = True


# Known text providers have FIXED endpoints — the user only picks the provider and
# pastes a key, no base URL to configure (base_url_editable=False). All are
# OpenAI-compatible except Anthropic; "custom" is the escape hatch for any other
# OpenAI-compatible endpoint, where the URL IS editable.
def _t(id, label, url, editable=False, needs_key=True):
    return ProviderInfo(id=id, label=label, kind="text", default_base_url=url,
                        base_url_editable=editable, needs_key=needs_key)


PROVIDERS: dict[str, ProviderInfo] = {
    # Ollama — local OpenAI-compatible server (no key). Base URL editable so a remote/LAN
    # Ollama works too. Lists whatever models are pulled; pick any from the model picker.
    "ollama": _t("ollama", "Ollama (local)", "http://127.0.0.1:11434/v1", editable=True, needs_key=False),
    "openai": _t("openai", "OpenAI", "https://api.openai.com/v1"),
    "anthropic": _t("anthropic", "Anthropic (Claude)", "https://api.anthropic.com"),
    "google": _t("google", "Google AI Studio (Gemini)", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "openrouter": _t("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    "mistral": _t("mistral", "Mistral AI", "https://api.mistral.ai/v1"),
    "deepseek": _t("deepseek", "DeepSeek", "https://api.deepseek.com"),
    "groq": _t("groq", "Groq", "https://api.groq.com/openai/v1"),
    "xai": _t("xai", "xAI (Grok)", "https://api.x.ai/v1"),
    "together": _t("together", "Together AI", "https://api.together.xyz/v1"),
    "fireworks": _t("fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1"),
    "custom": _t("custom", "Custom (OpenAI-compatible)", "", editable=True),
    # image
    "comfyui": ProviderInfo(id="comfyui", label="ComfyUI (local / remote)", kind="image",
                            default_base_url="http://127.0.0.1:8188", needs_key=False),
}


def list_providers(kind: str | None = None) -> list[dict]:
    return [p.model_dump() for p in PROVIDERS.values() if kind is None or p.kind == kind]


def ping_comfyui(base_url: str | None) -> bool:
    """Reachability check for an image (ComfyUI) connection."""
    if not base_url:
        return False
    try:
        r = httpx.get(base_url.rstrip("/") + "/system_stats", timeout=8)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def test_connection(provider: str, api_key: str, base_url: str | None = None) -> list[dict]:
    """Validate a TEXT key and return its models as [{id, name}, ...]. Raises on failure.

    (Image connections are validated in the server via ping_comfyui, since their
    model list comes from the configured workflows, not the provider.)
    """
    info = PROVIDERS.get(provider)
    if info is None:
        raise RuntimeError(f"unknown provider '{provider}'")
    base = (base_url or info.default_base_url).rstrip("/")
    if info.needs_key and not api_key:
        raise RuntimeError("API key is required")

    # Keyless providers (Ollama) call /models with no Authorization header.
    bearer = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        if provider == "anthropic":
            r = httpx.get(f"{base}/v1/models",
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"}, timeout=20)
            r.raise_for_status()
            # Claude 3+ (everything the API lists today) accepts image input.
            return [{"id": m["id"], "name": m.get("display_name", m["id"]), "vision": True}
                    for m in r.json().get("data", [])]

        if provider == "openrouter":
            # /models is PUBLIC on OpenRouter, so validate the key against /key.
            httpx.get(f"{base}/key", headers=bearer, timeout=20).raise_for_status()
            r = httpx.get(f"{base}/models", headers=bearer, timeout=20)
        else:
            r = httpx.get(f"{base}/models", headers=bearer, timeout=20)

        r.raise_for_status()

        def _vision(m: dict) -> bool:
            # OpenRouter exposes input modalities under architecture; other OpenAI-compatible
            # providers may surface a modalities/vision hint. Default false when unknown.
            arch = m.get("architecture") or {}
            mods = arch.get("input_modalities") or arch.get("modality") or m.get("modalities") or []
            if isinstance(mods, str):
                return "image" in mods
            return "image" in mods

        out = [{"id": m["id"], "name": m.get("name", m["id"]), "vision": _vision(m)}
               for m in r.json().get("data", [])]
        out.sort(key=lambda m: m["name"].lower())
        return out
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise RuntimeError("invalid API key (authentication failed)") from exc
        raise RuntimeError(f"provider returned HTTP {code}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"could not reach provider: {exc}") from exc


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
class Connection(BaseModel):
    id: str                       # profile name, unique across all connections
    kind: str = "text"            # "text" | "image"
    provider: str
    base_url: str | None = None
    api_key: str = ""
    model: str | None = None

    def masked(self) -> dict:
        d = self.model_dump()
        k = self.api_key
        d["api_key"] = (k[:6] + "…" + k[-4:]) if len(k) > 12 else ("set" if k else "")
        d["has_key"] = bool(k)
        return d

    def to_model_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {"model": self.model, "api_key": self.api_key, "base_url": self.base_url}
        if self.provider == "ollama":
            # Local Ollama needs no key (any bearer is accepted), and a reasoning-capable
            # model returns EMPTY `content` over /v1 while thinking — "none" disables the
            # channel. A config's params can still override reasoning_effort.
            opts["api_key"] = self.api_key or "ollama"
            opts["reasoning_effort"] = "none"
        return opts


class ConnectionStore:
    """Loads/saves connections from <root>/secrets/connections.json, with an
    independent active selection per kind."""

    # Connection kinds (each has its own independent active selection):
    #   text          — the chat model's provider
    #   image_prompt  — the image-prompt generator's provider (its own, so it can
    #                   differ from chat; falls back to `text` when unset)
    #   image         — the image backend (ComfyUI)
    KINDS = ("text", "image_prompt", "image")

    def __init__(self, root: str | Path):
        self.path = Path(root) / "secrets" / "connections.json"
        self._active: dict[str, str | None] = {k: None for k in self.KINDS}
        self._conns: dict[str, Connection] = {}
        self._load()
        self._ensure_local_connection()

    def _ensure_local_connection(self) -> None:
        """Always offer a local Ollama provider so "run local" is a selectable option
        everywhere (preset editor + every point-of-use ⚙ modal) with no manual setup.
        Idempotent, and never changes the active selection — it's just an available choice."""
        if any(c.provider == "ollama" for c in self._conns.values()):
            return
        self._conns["ollama-local"] = Connection(
            id="ollama-local", kind="text", provider="ollama",
            base_url="http://127.0.0.1:11434/v1", api_key="", model=None)
        self._save()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        active = data.get("active")
        if isinstance(active, str):  # migrate old single-active format
            self._active = {"text": active, "image_prompt": None, "image": None}
        elif isinstance(active, dict):
            self._active = {k: active.get(k) for k in self.KINDS}
        self._conns = {c["id"]: Connection(**c) for c in data.get("connections", [])}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"active": self._active, "connections": [c.model_dump() for c in self._conns.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list(self, kind: str | None = None) -> list[Connection]:
        return [c for c in self._conns.values() if kind is None or c.kind == kind]

    def get(self, conn_id: str) -> Connection | None:
        return self._conns.get(conn_id)

    def upsert(self, conn: Connection, *, make_active: bool = True) -> None:
        self._conns[conn.id] = conn
        if make_active or self._active.get(conn.kind) is None:
            self._active[conn.kind] = conn.id
        self._save()

    def remove(self, conn_id: str) -> None:
        conn = self._conns.pop(conn_id, None)
        if conn and self._active.get(conn.kind) == conn_id:
            remaining = self.list(conn.kind)
            self._active[conn.kind] = remaining[0].id if remaining else None
        self._save()

    def set_active(self, conn_id: str) -> None:
        conn = self._conns.get(conn_id)
        if conn:
            self._active[conn.kind] = conn_id
            self._save()

    def active_id(self, kind: str) -> str | None:
        return self._active.get(kind)

    def active(self, kind: str) -> Connection | None:
        cid = self._active.get(kind)
        return self._conns.get(cid) if cid else None

    @property
    def active_map(self) -> dict[str, str | None]:
        return dict(self._active)
