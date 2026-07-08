"""One-time migration: consolidate each story into its own folder.

Before: a story was ``configs/stories/<key>.json`` and its generated images lived in a GLOBAL,
character-keyed pool (``configs/characters/<key>.ref.png`` / ``.png`` and
``configs/characters/portraits/<key>/…``), mixed in with unrelated imported library cards.

After: one folder per story —

    configs/stories/<key>/
      story.json                       (moved from configs/stories/<key>.json)
      chars/<charkey>.png|.ref.png      (owned characters' avatar + reference)
      chars/portraits/<charkey>/…       (owned characters' sprite sets + manifest)

Only STORY-OWNED (embedded) characters move; global/imported library cards stay in
``configs/characters``. Every move is copy → verify (size / file count) → remove, so a mistake
can't lose data. Idempotent: re-running skips anything already in the new layout. The lazy
folder-or-legacy resolution in story_db / context tolerates a half-run.

Run once:  ``python -m loom.stories.migrate_assets_to_story_dirs``  (migrates configs/ under CWD),
or call ``migrate_dir(story_dir, char_dir)`` directly.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .story_db import _safe_key, iter_story_files, load_story


def _copy_verify_remove(src: Path, dst: Path) -> bool:
    """Copy a FILE src→dst, verify byte size, then remove src. Returns True ONLY when a move
    actually happened; False for already-migrated (dst exists), nothing-to-move (src missing), or a
    failed verification (src kept)."""
    if dst.exists() or not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        src.unlink()
        return True
    return False


def _copy_verify_remove_tree(src: Path, dst: Path) -> bool:
    """Same, for a DIRECTORY tree (verify by file count). True only on an actual move."""
    if dst.exists() or not src.is_dir():
        return False
    shutil.copytree(src, dst)
    if sum(1 for _ in dst.rglob("*")) == sum(1 for _ in src.rglob("*")):
        shutil.rmtree(src, ignore_errors=True)
        return True
    return False


def migrate_dir(story_dir, char_dir) -> list[str]:
    """Migrate every story under `story_dir` to the per-story folder layout, relocating its
    embedded characters' assets out of the global `char_dir` pool. Returns a list of moves made."""
    story_dir, char_dir = Path(story_dir), Path(char_dir)
    moved: list[str] = []
    if not story_dir.is_dir():
        return moved

    # 1. legacy flat <key>.json → <key>/story.json
    for p in sorted(story_dir.glob("*.json")):
        key = p.stem
        dest = story_dir / key / "story.json"
        if dest.exists():                 # folder form already present → drop the flat duplicate
            if p.is_file():
                p.unlink()
            continue
        if _copy_verify_remove(p, dest):
            moved.append(f"{p.name} -> {key}/story.json")

    # 2. each story's OWNED (embedded) characters' assets → <key>/chars/
    for key, spath in iter_story_files(story_dir):
        try:
            _story, chars = load_story(spath)
        except Exception:  # noqa: BLE001 — a broken story file never blocks the rest
            continue
        chars_root = story_dir / _safe_key(key) / "chars"
        for ck in chars:
            safe = _safe_key(ck)
            for fn in (f"{safe}.png", f"{safe}.ref.png"):     # avatar + reference
                if _copy_verify_remove(char_dir / fn, chars_root / fn):
                    moved.append(f"characters/{fn} -> {key}/chars/{fn}")
            if _copy_verify_remove_tree(char_dir / "portraits" / safe,     # sprite set + manifest
                                        chars_root / "portraits" / safe):
                moved.append(f"characters/portraits/{safe} -> {key}/chars/portraits/{safe}")
    return moved


if __name__ == "__main__":
    import sys, tempfile, os
    from .story_db import save_story

    # ── self-check on a throwaway fixture ──────────────────────────────────────
    tmp = Path(tempfile.mkdtemp())
    sdir, cdir = tmp / "stories", tmp / "characters"
    sdir.mkdir(); (cdir / "portraits").mkdir(parents=True)
    # a story with ONE owned (embedded) char "eli" + a global library card "libcard"
    save_story(sdir / "mystory.json", {"name": "My Story", "type": "novel"},
               {"eli": {"name": "Eli", "system": "x"}})
    (cdir / "eli.png").write_bytes(b"AVATAR")
    (cdir / "eli.ref.png").write_bytes(b"REFERENCE")
    (cdir / "portraits" / "eli").mkdir(); (cdir / "portraits" / "eli" / "base.png").write_bytes(b"BASE")
    (cdir / "libcard.png").write_bytes(b"LIBRARY")           # unrelated global card
    (cdir / "portraits" / "libcard").mkdir(); (cdir / "portraits" / "libcard" / "base.png").write_bytes(b"L")

    moves = migrate_dir(sdir, cdir)

    # story file relocated
    assert (sdir / "mystory" / "story.json").is_file() and not (sdir / "mystory.json").exists()
    # owned char's assets moved under the story, bytes intact
    ch = sdir / "mystory" / "chars"
    assert (ch / "eli.png").read_bytes() == b"AVATAR"
    assert (ch / "eli.ref.png").read_bytes() == b"REFERENCE"
    assert (ch / "portraits" / "eli" / "base.png").read_bytes() == b"BASE"
    assert not (cdir / "eli.png").exists() and not (cdir / "portraits" / "eli").exists()
    # library card UNTOUCHED
    assert (cdir / "libcard.png").read_bytes() == b"LIBRARY"
    assert (cdir / "portraits" / "libcard" / "base.png").is_file()
    # idempotent: a second run does nothing and doesn't error
    assert migrate_dir(sdir, cdir) == []
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"ok — migrate_assets_to_story_dirs: {len(moves)} moves on the fixture, library card untouched, idempotent")

    # ── optional real run: `python -m ... --run` migrates ./configs ──────────────
    if "--run" in sys.argv:
        root = Path(os.getcwd()) / "configs"
        done = migrate_dir(root / "stories", root / "characters")
        print(f"migrated {len(done)} item(s):")
        for m in done:
            print("  ", m)
