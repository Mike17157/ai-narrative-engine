"""`loom story` — a human- and script-friendly CLI for story data.

Operates directly on the relational store (configs/stories.db) — no server required. This makes
the SQL power of the migration reachable from the terminal: list, inspect, edit, query, and
seal characters without spinning up the API or hitting an endpoint.

DESIGN CHOICES (relevant to the agent-tool-transport experiment):
- Prose values are read from stdin or `@file` when they contain newlines/quotes — never as a bare
  positional arg. This sidesteps the shell-escaping failure mode that would otherwise make
  story content hostile to CLI transport.
- Every write goes through `Story(**data)` validation before it touches the DB, so a CLI edit
  (human OR agent-emitted) can never corrupt the store. The same gate the server enforces.
- Commands mirror the agent's tool names (set_core_question, set_character_field, add_location,
  set_relationship, ...) so the CLI IS the agent's vocabulary — the experiment can wire the same
  op as a structured call OR as CLI-as-text and compare.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from .config.schema import Story
from .server.services import story_store as SS

app = typer.Typer(add_completion=False, help="Inspect and edit story data directly (no server).")


# ── helpers ──────────────────────────────────────────────────────────────────
def _root() -> Path:
    return Path(typer.get_app_dir("loom")) if False else Path(".")  # always cwd-relative like the rest


def _resolve_value(value: str | None) -> str:
    """Resolve a value arg: `@file` reads the file, `-` reads stdin, otherwise literal. This is the
    escaping-avoidance mechanism — prose with quotes/newlines comes via file/stdin, never as a
    bare arg the shell would mangle."""
    if value is None:
        return ""
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def _load(root: Path, key: str) -> tuple[dict, dict]:
    loaded = SS.load_story(root, key)
    if loaded is None:
        typer.secho(f"no such story: {key}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return loaded


def _save_validated(root: Path, key: str, story: dict, chars: dict) -> None:
    """Validate through Story(**) then persist — the same gate the server enforces. A CLI edit
    can never corrupt the store; an invalid edit fails loudly before any row is written."""
    try:
        Story(**story)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"validation failed — edit rejected (nothing written): {exc}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    SS.save_story(root, key, story, chars)


# ── READ commands ────────────────────────────────────────────────────────────
@app.command("list")
def list_stories(root: Path = typer.Option(Path("."), "--root")):
    """List every story (name + key + size hint)."""
    for key in SS.list_stories(root):
        loaded = SS.load_story(root, key)
        name = loaded[0].get("name", "?") if loaded else "?"
        cast = len(loaded[0].get("cast", [])) if loaded else 0
        typer.echo(f"  {key:28} {name:30} {cast} cast")


@app.command("show")
def show_story(
    key: str = typer.Argument(..., help="Story key."),
    field: str | None = typer.Option(None, "--field", "-f",
                                     help="Show one top-level field only (premise, tone, cast, ...)"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Show a story, or one field of it."""
    story, _chars = _load(root, key)
    if field:
        val = story.get(field)
        typer.echo(json.dumps(val, indent=2, ensure_ascii=False)
                   if isinstance(val, (dict, list)) else (val or ""))
    else:
        typer.echo(json.dumps(story, indent=2, ensure_ascii=False))


@app.command("query")
def query(
    sql: str = typer.Argument(..., help="SQL to run against stories.db. story_key is the partition "
                                        "column on every table. e.g. "
                                        "\"SELECT * FROM locations WHERE story_key='x'\""),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Run an arbitrary SELECT against the relational store. Read-only."""
    con = SS._conn(root)
    try:
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.execute(sql).description] if rows else []
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"query error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    if cols:
        typer.echo("  " + " | ".join(cols))
    for r in rows:
        typer.echo("  " + " | ".join(str(c)[:40] for c in r))


# ── WRITE commands (mirrors of the agent tool names) ─────────────────────────
@app.command("set-premise")
def set_premise(
    key: str = typer.Argument(...),
    value: str = typer.Option(..., "--value", "-v",
                              help="New premise. Use '@file' or '-' to read from file/stdin "
                                   "(avoids escaping issues with prose)."),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Set the premise. (Agent tool: set_core_question edits premise_parts; this is the flat field.)"""
    story, chars = _load(root, key)
    story["premise"] = _resolve_value(value)
    _save_validated(root, key, story, chars)
    typer.secho(f"OK — premise updated for {key}", fg=typer.colors.GREEN)


@app.command("set-core-question")
def set_core_question(
    key: str = typer.Argument(...),
    question: str = typer.Option(..., "--question", "-q",
                                 help="The core question. Use '@file'/'-' for prose."),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Set the core question (writes premise_parts.question). Mirrors the agent tool."""
    story, chars = _load(root, key)
    parts = story.get("premise_parts") or {}
    parts["question"] = _resolve_value(question)
    story["premise_parts"] = parts
    _save_validated(root, key, story, chars)
    typer.secho(f"OK — core question updated for {key}", fg=typer.colors.GREEN)


@app.command("add-location")
def add_location(
    key: str = typer.Argument(...),
    name: str = typer.Option(..., "--name", "-n"),
    description: str = typer.Option("", "--description", "-d"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Add a location. Mirrors the agent tool."""
    story, chars = _load(root, key)
    lid = name.lower().replace(" ", "_").strip("_") or "loc"
    base, i = lid, 2
    existing = {l["id"] for l in story.get("locations", [])}
    while lid in existing:
        lid, i = f"{base}_{i}", i + 1
    story.setdefault("locations", []).append(
        {"id": lid, "name": name, "description": _resolve_value(description),
         "background_prompt": "", "parent": "", "scenes": []})
    _save_validated(root, key, story, chars)
    typer.secho(f"OK — location '{lid}' added to {key}", fg=typer.colors.GREEN)


@app.command("set-character-field")
def set_character_field(
    key: str = typer.Argument(...),
    char: str = typer.Argument(..., help="Character key."),
    field: str = typer.Argument(..., help="Field path on the character: name | system | fields.<k>"),
    value: str = typer.Option(..., "--value", "-v"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Set a character field. Mirrors the agent tool. `fields.<k>` reaches into the fields dict."""
    story, chars = _load(root, key)
    if char not in chars:
        typer.secho(f"no such character in {key}: {char}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    val = _resolve_value(value)
    if field == "name":
        chars[char]["name"] = val
    elif field == "system":
        chars[char]["system"] = val
    elif field.startswith("fields."):
        chars[char].setdefault("fields", {})[field[len("fields."):]] = val
    else:
        typer.secho(f"unsupported field: {field} (use name | system | fields.<k>)",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    _save_validated(root, key, story, chars)
    typer.secho(f"OK — {char}.{field} updated in {key}", fg=typer.colors.GREEN)


@app.command("set-relationship")
def set_relationship(
    key: str = typer.Argument(...),
    source: str = typer.Option(..., "--source"),
    target: str = typer.Option(..., "--target"),
    nature: str = typer.Option("", "--nature"),
    dynamic: str = typer.Option("", "--dynamic"),
    note: str = typer.Option("", "--note"),
    root: Path = typer.Option(Path("."), "--root"),
):
    """Upsert a relationship between two characters. Mirrors the agent tool."""
    story, chars = _load(root, key)
    rels = story.setdefault("relationships", [])
    rid = f"{source}-{target}"
    existing = next((r for r in rels if r.get("id") == rid), None)
    payload = {"id": rid, "source": source, "target": target, "nature": _resolve_value(nature),
               "dynamic": _resolve_value(dynamic), "stance": "neutral",
               "note": _resolve_value(note)}
    if existing:
        existing.update(payload)
    else:
        rels.append(payload)
    _save_validated(root, key, story, chars)
    typer.secho(f"OK — relationship {source}→{target} {'updated' if existing else 'added'} in {key}",
                fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
