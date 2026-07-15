from __future__ import annotations

import unittest
from unittest.mock import patch

from loom.server.services import batch_images


class _LocalProvider:
    workflow = {}


class BatchImagesTests(unittest.TestCase):
    def test_batch_renders_locally_with_the_two_worker_cap(self):
        captured = {}

        def capture(prompts, factory, cap, *_args):
            captured["cap"] = cap
            captured["provider"] = factory()
            return []

        with patch.object(batch_images, "_fan_out", capture):
            batch_images.render_batch(_LocalProvider(), [{"prompt": "test"}])

        self.assertEqual(captured["cap"], 2)
        self.assertIsInstance(captured["provider"], _LocalProvider)
