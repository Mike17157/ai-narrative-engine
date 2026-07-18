"""Focused coverage for the named high-level Story Agent route."""
from __future__ import annotations

import os

from loom.providers.openai_compat import OpenAICompatProvider
from loom.server.context_providers import ProviderContextMixin
from loom.server.services.config_files import story_agent_route


class _Provider:
    def __init__(self, api_key: str | None):
        self.api_key = api_key


class _Context(ProviderContextMixin):
    def __init__(self, cfg: dict, providers: dict[tuple[str, str | None], object | None]):
        self._cfg = cfg
        self.providers = providers
        self.calls: list[tuple[str, str | None, dict]] = []

    def load_story_builder(self):
        return self._cfg

    def text_provider_for(self, model_sel, params=None, connection=None):
        self.calls.append((model_sel or "", connection, params or {}))
        return self.providers.get((model_sel or "", connection))


def test_story_agent_config_uses_named_profile_then_active_fallback():
    cfg = {
        "story_agent": {
            "model": "story_agent_deepseek_v4_pro",
            "fallback": "active",
            "params": {"temperature": 0.35, "reasoning_effort": "high"},
        }
    }
    ctx = _Context(cfg, {
        ("story_agent_deepseek_v4_pro", None): _Provider(""),
        ("", None): _Provider("active-key"),
    })

    provider, route = ctx.story_agent_provider({})

    assert provider is ctx.providers[("", None)]
    assert route["selected_model"] == "active"
    assert route["used_fallback"] is True
    assert route["available"] is True
    assert ctx.calls == [
        ("story_agent_deepseek_v4_pro", None, {"temperature": 0.35, "reasoning_effort": "high"}),
        ("", None, {
            "temperature": 0.35,
            "reasoning_effort": "high",
            "request_timeout_s": 32,
            "max_retries": 0,
        }),
    ]


def test_explicit_story_agent_model_never_silently_downgrades():
    ctx = _Context(
        {"story_agent": {"model": "story_agent_deepseek_v4_pro", "fallback": "active"}},
        {("experiment", None): _Provider("")},
    )

    provider, route = ctx.story_agent_provider({"model": "experiment"})

    assert provider is None
    assert route["source"] == "request"
    assert route["requested"] is True
    assert route["available"] is False
    assert ctx.calls == [("experiment", None, {})]


def test_legacy_v4_pro_override_is_bound_to_its_named_gateway_profile():
    route = story_agent_route({}, "deepseek/deepseek-v4-pro")

    assert route["requested_model"] == "deepseek/deepseek-v4-pro"
    assert route["model"] == "story_agent_deepseek_v4_pro"
    assert route["requested"] is True


def test_story_agent_route_keeps_legacy_stage_model_and_param_overrides():
    route = story_agent_route({
        "models": {"story_agent": "legacy-profile"},
        "params": {"story_agent": {"temperature": 0.2}},
        "story_agent": {"fallback": "active", "params": {"top_p": 0.8}},
    }, params={"temperature": 0.4})

    assert route["model"] == "legacy-profile"
    assert route["fallback"] == "active"
    assert route["params"] == {"temperature": 0.4, "top_p": 0.8}
    assert route["fallback_params"] == {
        "temperature": 0.4,
        "top_p": 0.8,
        "request_timeout_s": 32,
        "max_retries": 0,
    }


def test_explicit_configured_fallback_is_a_bounded_separate_selection():
    cfg = {
        "story_agent": {
            "model": "story_agent_deepseek_v4_pro",
            "fallback": "active",
            "params": {"temperature": 0.35},
        }
    }
    fallback = _Provider("active-key")
    ctx = _Context(cfg, {
        ("story_agent_deepseek_v4_pro", None): _Provider("primary-key"),
        ("", None): fallback,
    })

    provider, route = ctx.story_agent_provider({"use_fallback": True})

    assert provider is fallback
    assert route["available"] is True
    assert route["used_fallback"] is True
    assert route["fallback_selected"] is True
    assert route["fallback_available"] is True
    assert route["fallback_target"] == {"model": "active", "connection": "active"}
    # Selecting the fallback does not even construct the primary route for
    # this attempt, much less call it as an invisible second model pass.
    assert ctx.calls == [
        ("", None, {"temperature": 0.35, "request_timeout_s": 32, "max_retries": 0}),
    ]


def test_explicit_model_override_cannot_be_combined_with_configured_fallback():
    ctx = _Context(
        {"story_agent": {"model": "story_agent_deepseek_v4_pro", "fallback": "active"}},
        {("experiment", None): _Provider("test-key")},
    )

    provider, route = ctx.story_agent_provider({"model": "experiment", "use_fallback": True})

    assert provider is None
    assert route["available"] is False
    assert route["fallback_selected"] is True
    assert route["fallback_available"] is False
    assert "cannot be combined" in route["error"]
    assert ctx.calls == []


def test_direct_deepseek_profile_reads_its_own_environment_key():
    previous = {
        key: os.environ.get(key)
        for key in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")
    }
    try:
        os.environ["DEEPSEEK_API_KEY"] = "deepseek-test-key"
        os.environ["OPENROUTER_API_KEY"] = "gateway-test-key"
        os.environ.pop("OPENAI_API_KEY", None)
        direct = OpenAICompatProvider({"base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
        gateway = OpenAICompatProvider({"base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-v4-pro"})
        assert direct.api_key == "deepseek-test-key"
        assert gateway.api_key == "gateway-test-key"
        os.environ.pop("DEEPSEEK_API_KEY", None)
        direct_without_own_key = OpenAICompatProvider({"base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
        assert direct_without_own_key.api_key == ""
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    test_story_agent_config_uses_named_profile_then_active_fallback()
    test_explicit_story_agent_model_never_silently_downgrades()
    test_legacy_v4_pro_override_is_bound_to_its_named_gateway_profile()
    test_story_agent_route_keeps_legacy_stage_model_and_param_overrides()
    test_explicit_configured_fallback_is_a_bounded_separate_selection()
    test_explicit_model_override_cannot_be_combined_with_configured_fallback()
    test_direct_deepseek_profile_reads_its_own_environment_key()
    print("ok — named Story Agent route")
