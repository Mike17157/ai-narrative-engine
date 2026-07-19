"""Sessions + beats in the relational store (configs/stories.db).

story_sessions.py keeps its legacy signatures but persists to libSQL now: the
`sessions` table holds the console state as JSON columns; `session_beats` is the
UNBOUNDED episodic chronicle fed by apply_deltas' beat_sink — the State doc's capped
20-line log stays just the model's working window. Legacy per-sid JSON files are
folded into the tables on first touch and renamed .json.migrated.
"""
from __future__ import annotations

import json

from loom.server.services import story_sessions as ss
from loom.stories.runtime.state import apply_deltas, empty_state


# ── Beat capture at the engine level ───────────────────────────────────────────

def test_beat_sink_collects_log_ops_beyond_the_cap():
    ws = empty_state()
    ws["log"] = [f"old {i}" for i in range(20)]          # already at _LOG_CAP
    sink: list = []
    deltas = [{"op": "log", "name": "", "key": "", "value": f"new {i}", "title": "", "keywords": []}
              for i in range(3)]
    out = apply_deltas(ws, deltas, beat_sink=sink)
    assert len(out["log"]) == 20                          # in-doc window stays capped
    assert sink == ["new 0", "new 1", "new 2"]            # …but nothing is forgotten
    assert out["log"][-3:] == sink


def test_beat_sink_collects_move_gate_notes():
    sink: list = []
    apply_deltas(empty_state(),
                 [{"op": "move", "name": "Mara", "key": "", "value": "Moon",
                   "title": "", "keywords": []}],
                 validate_move=lambda ws, name, target: False, beat_sink=sink)
    assert sink == ["(Mara could not have reached Moon yet)"]


def test_no_sink_means_no_collection():
    out = apply_deltas(empty_state(),
                       [{"op": "log", "name": "", "key": "", "value": "x",
                         "title": "", "keywords": []}])
    assert out["log"] == ["x"]                            # unchanged legacy behavior


# ── SQL session store ──────────────────────────────────────────────────────────

def test_roundtrip(tmp_path):
    ss.save_session(tmp_path, "s1", {
        "character": "mara", "messages": [{"role": "user", "text": "hi"}],
        "graph": {"nodes": []}, "draft": {"premise": "x"}, "lorebooks": ["craft"],
        "world_state": {"log": ["b1"], "location": "lib"},
        "prologue": {"text": "once"}})
    got = ss.load_session(tmp_path, "s1")
    assert got is not None
    assert got["character"] == "mara"
    assert got["messages"] == [{"role": "user", "text": "hi"}]
    assert got["lorebooks"] == ["craft"]
    assert got["prologue"] == {"text": "once"}            # the JSON file used to drop this
    assert isinstance(got["state"], dict) and isinstance(got["world_state"], dict)


def test_missing_and_unsafe_sids(tmp_path):
    assert ss.load_session(tmp_path, "nope") is None
    assert ss.load_session(tmp_path, "") is None
    assert ss.load_session(tmp_path, "!!!") is None


def test_beats_archive_is_unbounded_and_ordered(tmp_path):
    ss.save_session(tmp_path, "s2", {"messages": []})
    assert ss.append_beats(tmp_path, "s2", [f"beat {i}" for i in range(25)], step=7) == 25
    beats = ss.beats_for(tmp_path, "s2")
    assert len(beats) == 25                               # no _LOG_CAP here
    assert beats[0]["text"] == "beat 0" and beats[-1]["text"] == "beat 24"
    assert beats[-1]["step"] == 7 and beats[0]["seq"] == 0
    ss.append_beats(tmp_path, "s2", ["one more"])
    tail = ss.beats_for(tmp_path, "s2", limit=3)
    assert [b["text"] for b in tail] == ["beat 23", "beat 24", "one more"]


def test_delete_removes_session_and_beats(tmp_path):
    ss.save_session(tmp_path, "s3", {"messages": []})
    ss.append_beats(tmp_path, "s3", ["x"])
    ss.delete_session(tmp_path, "s3")
    assert ss.load_session(tmp_path, "s3") is None
    assert ss.beats_for(tmp_path, "s3") == []


# ── Legacy JSON → DB migration ─────────────────────────────────────────────────

def test_legacy_json_folded_into_db(tmp_path):
    d = tmp_path / "configs" / "story_sessions"
    d.mkdir(parents=True)
    (d / "legacy.json").write_text(json.dumps({
        "id": "legacy", "character": "mara",
        "messages": [{"role": "user", "text": "hi"}],
        "world_state": {"log": ["b1", "b2", "b3"]}, "state": {}}), encoding="utf-8")
    got = ss.load_session(tmp_path, "legacy")             # first touch triggers migration
    assert got is not None and got["character"] == "mara"
    assert got["messages"] == [{"role": "user", "text": "hi"}]
    assert not (d / "legacy.json").exists()               # folded away…
    assert (d / "legacy.json.migrated").exists()          # …but reversible
    assert [b["text"] for b in ss.beats_for(tmp_path, "legacy")] == ["b1", "b2", "b3"]


def test_corrupt_legacy_file_stays_put(tmp_path):
    d = tmp_path / "configs" / "story_sessions"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    assert ss.load_session(tmp_path, "bad") is None
    assert (d / "bad.json").exists()                      # left for manual inspection


# ── Story partitioning (copackaging) ───────────────────────────────────────────

def test_story_key_derived_from_play_sid(tmp_path):
    ss.save_session(tmp_path, "play-school", {"messages": []})
    assert ss.load_session(tmp_path, "play-school")["story_key"] == "school"
    ss.append_beats(tmp_path, "play-school", ["the whistle changes hands"])
    assert ss.sessions_for_story(tmp_path, "school") == ["play-school"]
    beats = ss.beats_for_story(tmp_path, "school")
    assert [b["text"] for b in beats] == ["the whistle changes hands"]


def test_explicit_story_key_survives_resaves(tmp_path):
    ss.save_session(tmp_path, "wizard-9", {"messages": []}, story_key="undergrowth")
    ss.save_session(tmp_path, "wizard-9", {"messages": [{"role": "user", "text": "hi"}]})
    assert ss.load_session(tmp_path, "wizard-9")["story_key"] == "undergrowth"
    ss.append_beats(tmp_path, "wizard-9", ["spore count rises"])   # inherits the anchor
    assert len(ss.beats_for_story(tmp_path, "undergrowth")) == 1


def test_delete_story_cascades_sessions_and_beats(tmp_path):
    from loom.server.services import story_store
    ss.save_session(tmp_path, "play-school", {"messages": []})
    ss.append_beats(tmp_path, "play-school", ["clover found"])
    ss.save_session(tmp_path, "play-undergrowth", {"messages": []})
    ss.append_beats(tmp_path, "play-undergrowth", ["mycelium hums"])
    con = story_store._conn(tmp_path)
    con.execute("INSERT INTO stories (key, name) VALUES ('school', 'School')")
    con.execute("INSERT INTO stories (key, name) VALUES ('undergrowth', 'Undergrowth')")
    con.commit()
    story_store.delete_story(tmp_path, "school")
    assert ss.load_session(tmp_path, "play-school") is None
    assert ss.beats_for(tmp_path, "play-school") == []
    assert ss.load_session(tmp_path, "play-undergrowth") is not None   # other story untouched
    assert [b["text"] for b in ss.beats_for(tmp_path, "play-undergrowth")] == ["mycelium hums"]


def test_delete_story_sessions_without_tables_is_safe(tmp_path):
    # A story deleted before any session was ever saved must not raise.
    assert ss.delete_story_sessions(tmp_path, "ghost") == (0, 0)
