"""Regression coverage for the Story Architect's bounded model route."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import patch

import httpx
import yaml
from fastapi.testclient import TestClient

from loom.providers.base import ModelRequestTimeout, TextResult
from loom.providers.openai_compat import OpenAICompatProvider
from loom.server.app import create_app
from loom.server.context_providers import ProviderContextMixin
from loom.stories.workflows import WorkflowTrace, model_task
import loom.stories.authoring.card_graph as card_graph
import loom.stories.authoring.interview_graph as interview_graph


@contextmanager
def _isolated_client():
    root = Path(tempfile.mkdtemp(prefix="loom-story-agent-timeout-"))
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    for card in (root / "configs" / "characters").glob("*.yaml"):
        card.unlink()
    client = TestClient(create_app(root))
    try:
        yield client
    finally:
        client.close()
        shutil.rmtree(root, ignore_errors=True)


def _new_story(client: TestClient) -> str:
    response = client.post("/api/stories/new", json={"name": "Timeout test"})
    assert response.status_code == 200, response.text
    return response.json()["key"]


def test_story_agent_profile_has_one_short_attempt():
    config = yaml.safe_load((Path("configs") / "models.yaml").read_text(encoding="utf-8"))
    options = config["models"]["story_agent_deepseek_v4_pro"]["options"]

    assert options["request_timeout_s"] == 32
    assert options["max_retries"] == 0


def test_openai_compatible_provider_uses_configured_timeout_and_never_retries_it():
    provider = OpenAICompatProvider({
        "api_key": "test-key",
        "model": "test/slow-model",
        "request_timeout_s": 7.5,
        "max_retries": 0,
    })
    calls: list[dict] = []

    def late_response(*_args, **kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout("the provider stalled")

    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["reply"], "properties": {"reply": {"type": "string"}},
    }
    with patch("loom.providers.openai_compat.httpx.post", side_effect=late_response):
        try:
            provider.generate_text(system="system", prompt="prompt", emits=schema)
            raise AssertionError("expected a bounded timeout")
        except ModelRequestTimeout as exc:
            assert "7.5s" in str(exc)
            assert "No story changes were made" in str(exc)

    assert len(calls) == 1
    assert calls[0]["timeout"] == 7.5


def test_model_task_enforces_the_total_budget_and_signals_stream_cancellation():
    class SlowProvider:
        request_timeout_s = 0.03
        supports_cancellation = True
        model = "slow-test-model"

        def __init__(self):
            self.cancelled = False

        def generate_text(self, *, cancel=None, **_kwargs):
            while not (cancel and cancel()):
                time.sleep(0.002)
            self.cancelled = True
            return TextResult(text="late")

    provider = SlowProvider()

    async def run():
        started = time.monotonic()
        try:
            await model_task(WorkflowTrace("timeout-test"), "slow", provider,
                             system="system", prompt="prompt")
            raise AssertionError("expected a bounded timeout")
        except ModelRequestTimeout:
            return time.monotonic() - started

    elapsed = asyncio.run(run())

    assert elapsed < 0.2
    assert provider.cancelled is True


def test_authoring_endpoints_and_architect_return_a_retryable_504_without_writing():
    original_card_operation = card_graph.run_card_operation
    original_interview_turn = interview_graph.run_interview_turn
    original_route = ProviderContextMixin.story_agent_provider

    async def timeout_card_operation(**_kwargs):
        raise ModelRequestTimeout(model="test/story-agent", timeout_s=1)

    async def timeout_interview_turn(**_kwargs):
        raise ModelRequestTimeout(model="test/story-agent", timeout_s=1)

    card_graph.run_card_operation = timeout_card_operation
    interview_graph.run_interview_turn = timeout_interview_turn
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (
        object(), {"available": True, "selected_model": "test/story-agent", "used_fallback": False}
    )
    try:
        with _isolated_client() as client:
            key = _new_story(client)
            developed = client.post(f"/api/stories/{key}/card/develop", json={
                "scope": "world", "brief": "A ferry comes home.",
            })
            interviewed = client.post(f"/api/stories/{key}/interview", json={
                "focus": "world", "messages": [{"role": "user", "text": "A ferry comes home."}],
            })
            # The primary Architect delegates to the same bounded development
            # endpoint, so its caller sees the same meaningful failure rather
            # than an empty UI abort.
            architect = client.post(f"/api/stories/{key}/architect/turn", json={
                "message": "A ferry comes home.",
            })

        for response in (developed, interviewed, architect):
            assert response.status_code == 504, response.text
            data = response.json()
            assert data["retryable"] is True
            assert "No story changes were made" in data["error"]
            assert data["model_route"]["selected_model"] == "test/story-agent"
        architect_data = architect.json()
        assert architect_data["phase"] == "develop"
        assert architect_data["bounded"] == {"model_calls": 1, "maximum_model_calls": 1}
    finally:
        card_graph.run_card_operation = original_card_operation
        interview_graph.run_interview_turn = original_interview_turn
        ProviderContextMixin.story_agent_provider = original_route


def test_interview_recovery_timeout_is_not_swallowed_as_a_success():
    """The optional standalone recovery keeps the same retryable timeout contract."""
    original_card_operation = card_graph.run_card_operation
    original_interview_turn = interview_graph.run_interview_turn
    original_route = ProviderContextMixin.story_agent_provider

    async def empty_interview(**_kwargs):
        return interview_graph.InterviewResult(reply="I need a little more.", patch={})

    async def timeout_recovery(**_kwargs):
        raise ModelRequestTimeout(model="test/recovery", timeout_s=1)

    card_graph.run_card_operation = timeout_recovery
    interview_graph.run_interview_turn = empty_interview
    ProviderContextMixin.story_agent_provider = lambda _self, _body=None, **_kwargs: (
        object(), {"available": True, "selected_model": "test/recovery"}
    )
    try:
        with _isolated_client() as client:
            key = _new_story(client)
            response = client.post(f"/api/stories/{key}/interview", json={
                "focus": "world", "messages": [{"role": "user", "text": "A ferry comes home."}],
            })

        assert response.status_code == 504, response.text
        assert response.json()["retryable"] is True
        assert response.json()["model_route"]["selected_model"] == "test/recovery"
    finally:
        card_graph.run_card_operation = original_card_operation
        interview_graph.run_interview_turn = original_interview_turn
        ProviderContextMixin.story_agent_provider = original_route


def test_architect_forwards_an_explicit_fallback_retry_to_its_model_boundary():
    """A retry click selects the configured fallback once; it never cascades."""
    original_card_operation = card_graph.run_card_operation
    original_route = ProviderContextMixin.story_agent_provider
    routed_bodies: list[dict] = []

    async def timeout_card_operation(**_kwargs):
        raise ModelRequestTimeout(model="test/configured-fallback", timeout_s=1)

    def select_route(_self, body=None, **_kwargs):
        payload = dict(body or {})
        routed_bodies.append(payload)
        selected_fallback = payload.get("use_fallback") is True
        return object(), {
            "available": True,
            "selected_model": "active" if selected_fallback else "test/primary",
            "used_fallback": selected_fallback,
            "fallback_selected": selected_fallback,
            "fallback_available": True,
            "fallback_target": {"model": "active", "connection": "active"},
        }

    card_graph.run_card_operation = timeout_card_operation
    ProviderContextMixin.story_agent_provider = select_route
    try:
        with _isolated_client() as client:
            key = _new_story(client)
            response = client.post(f"/api/stories/{key}/architect/turn", json={
                "message": "A ferry comes home.",
                "use_fallback": True,
            })

        assert response.status_code == 504, response.text
        data = response.json()
        assert data["retryable"] is True
        assert data["model_route"]["used_fallback"] is True
        assert data["model_route"]["fallback_selected"] is True
        # The coordinator passed the flag into its one delegated development
        # call.  It did not invoke the primary first or trigger an extra retry.
        assert len(routed_bodies) == 1
        assert routed_bodies[0]["use_fallback"] is True
    finally:
        card_graph.run_card_operation = original_card_operation
        ProviderContextMixin.story_agent_provider = original_route


if __name__ == "__main__":
    test_story_agent_profile_has_one_short_attempt()
    test_openai_compatible_provider_uses_configured_timeout_and_never_retries_it()
    test_model_task_enforces_the_total_budget_and_signals_stream_cancellation()
    test_authoring_endpoints_and_architect_return_a_retryable_504_without_writing()
    test_architect_forwards_an_explicit_fallback_retry_to_its_model_boundary()
    print("ok — Story Agent timeout boundary")
