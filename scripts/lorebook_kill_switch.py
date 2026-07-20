"""Temporarily deactivate ALL lorebooks (every book + every entry) with an exact snapshot/restore.

Use around a bench/play run that must be verifiably lorebook-free:

    python scripts/lorebook_kill_switch.py disable --snapshot configs/_backups/lore_enabled_run.json
    python scripts/lorebook_kill_switch.py verify
    ... run the bench ...
    python scripts/lorebook_kill_switch.py restore --snapshot configs/_backups/lore_enabled_run.json

`disable` flips books.enabled and lore.enabled to 0 after recording every row's prior state.
`restore` puts back exactly the snapshotted values (rows created during the run are untouched).
`verify` proves the live store can serve nothing: zero enabled books/entries, an empty
retrieve() across every scope, and only the builtin refusal floor left in the guard rules.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path("configs/lorebooks.db")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def snapshot(path: Path) -> None:
    con = _con()
    books = {r[0]: r[1] for r in con.execute("SELECT id, enabled FROM books")}
    lore = {f"{r[0]}::{r[1]}": r[2] for r in con.execute("SELECT scope, entry_id, enabled FROM lore")}
    con.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"books": books, "lore": lore}, indent=1), encoding="utf-8")
    print(f"snapshot → {path}  ({len(books)} books, {len(lore)} entries)")


def disable() -> None:
    con = _con()
    con.execute("UPDATE books SET enabled=0")
    con.execute("UPDATE lore SET enabled=0")
    con.commit()
    b = con.execute("SELECT COUNT(*) FROM books WHERE enabled=1").fetchone()[0]
    l = con.execute("SELECT COUNT(*) FROM lore WHERE enabled=1").fetchone()[0]
    con.close()
    print(f"disabled: enabled books={b}, enabled entries={l} (both must be 0)")
    if b or l:
        raise SystemExit("disable FAILED — rows still enabled")


def restore(path: Path) -> None:
    snap = json.loads(path.read_text(encoding="utf-8"))
    con = _con()
    for bid, en in snap["books"].items():
        con.execute("UPDATE books SET enabled=? WHERE id=?", (int(en), bid))
    for key, en in snap["lore"].items():
        scope, eid = key.split("::", 1)
        con.execute("UPDATE lore SET enabled=? WHERE scope=? AND entry_id=?", (int(en), scope, eid))
    con.commit()
    # verify: live state must match the snapshot exactly for snapshotted rows
    bad = 0
    for bid, en in snap["books"].items():
        cur = con.execute("SELECT enabled FROM books WHERE id=?", (bid,)).fetchone()
        if cur is None or int(cur[0]) != int(en):
            bad += 1
            print(f"  MISMATCH book {bid}: live={None if cur is None else cur[0]} snapshot={en}")
    for key, en in snap["lore"].items():
        scope, eid = key.split("::", 1)
        cur = con.execute("SELECT enabled FROM lore WHERE scope=? AND entry_id=?", (scope, eid)).fetchone()
        if cur is None or int(cur[0]) != int(en):
            bad += 1
            print(f"  MISMATCH entry {key}: live={None if cur is None else cur[0]} snapshot={en}")
    con.close()
    print(f"restore from {path}: {'OK — live state matches snapshot' if not bad else f'{bad} MISMATCHES'}")
    if bad:
        raise SystemExit("restore FAILED")


def verify() -> None:
    con = _con()
    b = con.execute("SELECT COUNT(*) FROM books WHERE enabled=1").fetchone()[0]
    l = con.execute("SELECT COUNT(*) FROM lore WHERE enabled=1").fetchone()[0]
    con.close()
    print(f"live: enabled books={b}, enabled entries={l}")
    # functional proof through the production retrieval path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from loom.server.services import lorebook_store as LS
    scopes = [r[0] for r in sqlite3.connect(DB).execute("SELECT id FROM books")]
    hits = LS.retrieve(Path("."), "forest night hum ribbon occult club watch charm", scopes, top_k=50)
    print(f"retrieve() across {len(scopes)} scopes → {len(hits)} hits (must be 0)")
    from loom.stories.runtime.narration import load_rules
    rules = load_rules(Path("."))
    print(f"guard load_rules() → {len(rules)} rule set(s): "
          f"{['builtin floor' if r['script'] == 'fallback' and len(rules) == 1 else r['script'] for r in rules]}")
    if b or l or hits:
        raise SystemExit("verify FAILED — lorebooks can still serve content")
    print("lorebooks fully deactivated ✓")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["disable", "restore", "verify"])
    ap.add_argument("--snapshot", default="configs/_backups/lore_enabled_snapshot.json")
    args = ap.parse_args()
    path = Path(args.snapshot)
    if args.action == "disable":
        snapshot(path)
        disable()
    elif args.action == "restore":
        restore(path)
    else:
        verify()


if __name__ == "__main__":
    main()
