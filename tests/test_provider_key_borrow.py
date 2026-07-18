"""Registered text models (models.yaml) carry no secrets: the file is committable
config. When neither a profile's options nor the process environment yields an API
key, provider construction borrows the selected/active text connection's key — but
only when both point at the SAME base_url, so a gateway credential can never leak
to a different endpoint.
"""
from __future__ import annotations

import types

import pytest

from loom.config.schema import ModelDef
from loom.server.context_providers import ProviderContextMixin

OPENROUTER = "https://openrouter.ai/api/v1"


class _Conn:
    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url
        self.api_key = api_key


class _Store:
    def __init__(self, active):
        self._active = active

    def get(self, _id):
        return None

    def active(self, _kind):
        return self._active


def _registered(md: ModelDef, conn):
    """Call the unbound helper with a minimal ctx stand-in (only .store is used)."""
    fake = types.SimpleNamespace(store=_Store(conn))
    return ProviderContextMixin._registered_text_provider(fake, md)


def _glm52() -> ModelDef:
    return ModelDef(provider="openrouter", kind="text",
                    options={"base_url": OPENROUTER, "model": "z-ai/glm-5.2"})


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_borrows_connection_key_for_same_base_url():
    prov = _registered(_glm52(), _Conn(OPENROUTER, "sk-secret"))
    assert prov.api_key == "sk-secret"  # through the visibility wrapper's __getattr__


def test_base_url_match_is_case_and_slash_insensitive():
    prov = _registered(_glm52(), _Conn("HTTPS://OPENROUTER.AI/API/V1/", "sk-secret"))
    assert prov.api_key == "sk-secret"


def test_no_borrow_for_different_endpoint():
    # A direct-DeepSeek profile must never receive an OpenRouter credential.
    md = ModelDef(provider="deepseek", kind="text",
                  options={"base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
    prov = _registered(md, _Conn(OPENROUTER, "sk-secret"))
    assert prov.api_key == ""


def test_no_borrow_when_connection_has_no_key():
    prov = _registered(_glm52(), _Conn(OPENROUTER, ""))
    assert prov.api_key == ""


def test_env_key_wins_over_connection(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    prov = _registered(_glm52(), _Conn(OPENROUTER, "sk-conn"))
    assert prov.api_key == "sk-env"


def test_profile_key_wins_over_connection():
    md = ModelDef(provider="openrouter", kind="text",
                  options={"base_url": OPENROUTER, "model": "m", "api_key": "sk-profile"})
    prov = _registered(md, _Conn(OPENROUTER, "sk-conn"))
    assert prov.api_key == "sk-profile"


def test_borrow_does_not_mutate_shared_model_def():
    md = _glm52()
    _registered(md, _Conn(OPENROUTER, "sk-secret"))
    assert "api_key" not in md.options
