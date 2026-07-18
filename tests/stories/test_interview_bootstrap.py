"""Endpoint-level regression test for the direct-to-interview creation flow."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from loom.server.app import create_app


def test_new_story_bootstraps_with_an_assistant_turn_only():
    root = Path(tempfile.mkdtemp())
    # The app's settings loader needs the normal configuration shape, but this
    # test gets an isolated relational story database and never calls a model.
    shutil.copytree(Path("configs"), root / "configs")
    (root / "configs" / "stories.db").unlink(missing_ok=True)
    client = TestClient(create_app(root))

    created = client.post("/api/stories/new", json={"name": "Bootstrap test"})
    assert created.status_code == 200
    key = created.json()["key"]

    started = client.post(f"/api/stories/{key}/interview",
                          json={"bootstrap": True, "focus": "world", "messages": []})
    assert started.status_code == 200
    assert started.json()["reply"].startswith("Give me any starting spark")

    story = client.get(f"/api/stories/{key}").json()
    assert "interview_history" not in story["fields"]
    history = client.get(f"/api/stories/{key}/interview-history").json()
    assert history["history"] == [{
        "role": "assistant",
        "text": started.json()["reply"],
    }]


if __name__ == "__main__":
    test_new_story_bootstraps_with_an_assistant_turn_only()
    print("ok — new stories enter an assistant-only interview bootstrap")
