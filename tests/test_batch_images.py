from __future__ import annotations

import unittest
from unittest.mock import patch

from loom.providers.runpod_serverless_provider import RunPodServerlessProvider
from loom.server.services import batch_images


class _Ctx:
    runpod_config = {
        "enabled": True,
        "api_key": "configured-key",
        "serverless_endpoint_id": "configured-endpoint",
        "max_instances": 2,
    }


class _LocalProvider:
    workflow = {}


def _capture_fan_out():
    captured = {}

    def capture(prompts, factory, cap, *_args):
        captured["cap"] = cap
        captured["provider"] = factory()
        return []

    return captured, capture


class BatchImagesTests(unittest.TestCase):
    def test_batch_keeps_a_locally_selected_provider_local(self):
        """RunPod credentials must not override the model's routing decision."""
        captured, capture = _capture_fan_out()

        with patch.object(batch_images, "_fan_out", capture):
            batch_images.render_batch(_LocalProvider(), [{"prompt": "test"}], ctx=_Ctx())

        self.assertEqual(captured["cap"], 2)
        self.assertIsInstance(captured["provider"], _LocalProvider)


    def test_batch_uses_the_selected_serverless_provider_and_endpoint_cap(self):
        captured, capture = _capture_fan_out()
        selected = RunPodServerlessProvider({
            "endpoint_id": "selected-endpoint",
            "api_key": "selected-key",
            "workflow": {},
        })

        with patch.object(batch_images, "_fan_out", capture):
            batch_images.render_batch(selected, [{"prompt": "test"}], ctx=_Ctx())

        self.assertEqual(captured["cap"], 2)
        self.assertIsInstance(captured["provider"], RunPodServerlessProvider)
        self.assertEqual(captured["provider"].endpoint_id, "selected-endpoint")
        self.assertEqual(captured["provider"].api_key, "selected-key")
