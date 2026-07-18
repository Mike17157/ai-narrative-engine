"""Regression checks for relational Story-card persistence."""
from __future__ import annotations

import tempfile
from pathlib import Path

import libsql

from loom.server.services import story_store as store


def test_time_system_survives_new_and_legacy_databases():
    root = Path(tempfile.mkdtemp())
    db = root / "configs" / "stories.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    # Model the exact database shape before time_system became a first-class
    # column.  _conn must migrate it before the first real write.
    legacy_schema = store._SCHEMA.replace(
        "    time_system      TEXT NOT NULL DEFAULT '{}',        -- JSON object (entity activity windows)\n", ""
    )
    con = libsql.connect(str(db))
    con.executescript(legacy_schema)
    con.commit()
    con.close()
    store._inited.discard(str(db))

    schedule = {"slots": ["morning", "evening", "night"], "entity_periods": [
        {"id": "night-hunt", "slots": ["night"], "state": "hunting",
         "capabilities": ["mimicry"], "constraint": "no daylight action"}
    ]}
    store.save_story(root, "loop", {"name": "Loop", "time_system": schedule})
    loaded = store.load_story(root, "loop")
    assert loaded is not None
    assert loaded[0]["time_system"] == schedule


def test_location_history_survives_new_and_legacy_databases():
    """The `locations` table is explicit SQL columns, not a JSON blob — adding
    `Location.history` to the pydantic schema (loom/config/schema.py) did nothing on its
    own. Confirmed live: a location's `history` silently round-tripped to "" through
    save/load because the CREATE TABLE, INSERT, and SELECT statements here never
    mentioned the column at all. Mirrors test_time_system_survives_new_and_legacy_databases."""
    root = Path(tempfile.mkdtemp())
    db = root / "configs" / "stories.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    legacy_schema = store._SCHEMA.replace(
        "    history          TEXT NOT NULL DEFAULT '',\n", ""
    )
    con = libsql.connect(str(db))
    con.executescript(legacy_schema)
    con.commit()
    con.close()
    store._inited.discard(str(db))

    history = "A concrete, specific history paragraph for this location."
    store.save_story(root, "haunted-house", {
        "name": "Haunted House",
        "locations": [{"id": "attic", "name": "The Attic", "description": "Dusty.", "history": history}],
    })
    loaded = store.load_story(root, "haunted-house")
    assert loaded is not None
    assert loaded[0]["locations"][0]["history"] == history


def test_update_story_atomically_rebuilds_and_saves_one_current_aggregate():
    """The updater sees and writes through the same immediate transaction.

    Public capability routes use this primitive after an external model call:
    their callback can reject a stale public revision without a write, or build
    its candidate from the latest raw/private card before committing it.
    """
    root = Path(tempfile.mkdtemp())
    store.save_story(root, "atomic", {
        "name": "Atomic",
        "fields": {"private_marker": "keep this exact current value"},
    })

    def updater(story, characters):
        assert story["fields"]["private_marker"] == "keep this exact current value"
        story["premise"] = "A committed public change."
        return story, characters, {"committed_premise": story["premise"]}

    outcome = store.update_story_atomically(root, "atomic", updater)
    assert outcome.found is True
    assert outcome.committed is True
    assert outcome.result == {"committed_premise": "A committed public change."}
    loaded = store.load_story(root, "atomic")
    assert loaded is not None
    assert loaded[0]["premise"] == "A committed public change."
    assert loaded[0]["fields"]["private_marker"] == "keep this exact current value"

    aborted = store.update_story_atomically(root, "atomic", lambda _story, _characters: None)
    assert aborted.found is True
    assert aborted.committed is False
    assert store.load_story(root, "atomic")[0]["premise"] == "A committed public change."

    missing = store.update_story_atomically(root, "missing", lambda _story, _characters: None)
    assert missing.found is False
    assert missing.committed is False


if __name__ == "__main__":
    test_time_system_survives_new_and_legacy_databases()
    test_location_history_survives_new_and_legacy_databases()
    test_update_story_atomically_rebuilds_and_saves_one_current_aggregate()
    print("ok — story store preserves time systems and location histories")
