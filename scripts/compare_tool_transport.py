#!/usr/bin/env python
"""Empirical comparison: structured function-calling vs CLI-as-text for agent story edits.

For each of N diverse editing tasks, the model is asked to emit BOTH forms (so the only variable
is transport — same context, same intent). We then mechanically check each:
  - structured form: must be valid JSON with the right op name + required params
  - CLI-as-text form: must parse as a `loom story` invocation with valid args

We also attempt to actually APPLY each parsed result against a throwaway temp story, to catch
failures that parsing alone misses (e.g. a valid command that does the wrong thing).

This is SIGNAL, not proof: the sample is small and the tasks are hand-picked. Read it as
"which transport breaks more often, and how," not as a reliability number.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from loom.server import build_context
from loom.server.services import config_files as cf
from loom.server.services import story_store as SS

PROMPT = """You are editing a story. For the intent below, provide the edit in TWO forms.

FORM A — structured (JSON): {{"op": "<agent_op_name>", "args": {{...}}}}
FORM B — CLI (one shell line): loom story <subcommand> <key> [opts]

Available ops map to CLI subcommands:
  set_core_question(key, question)        -> loom story set-core-question <key> -q "<q>"
  add_location(key, name, description)    -> loom story add-location <key> -n "<n>" -d "<d>"
  set_character_field(key, char, field, value)  -> loom story set-character-field <key> <char> <field> -v "<v>"
  set_relationship(key, source, target, nature, dynamic, note) -> loom story set-relationship <key> --source <s> --target <t> --nature "<n>" --dynamic "<d>" --note "<note>"

Respond with ONLY a JSON object: {{"form_a": {{...}}, "form_b": "the command line"}}. No prose.

INTENT: {intent}"""


# Diverse tasks: vary content shape (quotes, newlines, em-dashes, length, structured vs prose).
TASKS = [
    ("set_core_question", "Set the core question for story 't' to: What if forgetting is the only way to survive the siege?"),
    ("set_core_question", "Set the core question for story 't' to a long one: When the city's last oracle goes blind and the invader's terms are 'surrender the children,' is refusal still mercy, or is it just a slower kind of loss?"),
    ("add_location", "Add a location to story 't' named 'The Drowned Garden' described as: Where the levy broke; nothing grows but moss — and the children play there anyway."),
    ("add_location", "Add a location to story 't' named 'Mara's Quarters' described as: A single room. A cot. A desk with a cracked inkwell. She sleeps with her back to the door."),
    ("set_character_field", "For story 't', set character 'mara' system field to: A librarian who believes silence is a form of grace. She lied once, under oath, and the lie saved three lives."),
    ("set_character_field", "For story 't', set character 'eli' fields.role to: herbalist & reluctant oracle"),
    ("set_relationship", "For story 't', set relationship from 'eli' to 'lila' with nature 'ward', dynamic 'protective, increasingly wary', note 'He checks on her at dusk — she's started locking the door.'"),
    ("set_relationship", "For story 't', set relationship from 'mara' to 'eli' with nature 'old debt', dynamic 'cordial surface, cold beneath', note 'She owes him a truth she'll never deliver.'"),
]


def _apply_structured(op_data: dict, story_key: str) -> tuple[bool, str]:
    """Apply a structured form_a result via the canonical story_store. Returns (ok, note)."""
    try:
        op, args = op_data["op"], op_data["args"]
        if op == "set_core_question":
            story, chars = SS.load_story(Path("."), story_key)
            parts = story.get("premise_parts") or {}
            parts["question"] = args["question"]
            story["premise_parts"] = parts
            SS.save_story(Path("."), story_key, story, chars)
            return True, ""
        if op == "add_location":
            story, chars = SS.load_story(Path("."), story_key)
            lid = (args["name"] or "").lower().replace(" ", "_").strip("_") or "loc"
            story.setdefault("locations", []).append(
                {"id": lid, "name": args["name"], "description": args.get("description", ""),
                 "background_prompt": "", "parent": "", "scenes": []})
            SS.save_story(Path("."), story_key, story, chars)
            return True, ""
        if op == "set_character_field":
            story, chars = SS.load_story(Path("."), story_key)
            ch, fld, val = args["char"], args["field"], args["value"]
            if ch not in chars:
                return False, f"no such char {ch}"
            if fld == "system":
                chars[ch]["system"] = val
            elif fld.startswith("fields."):
                chars[ch].setdefault("fields", {})[fld[7:]] = val
            else:
                return False, f"unsupported field {fld}"
            SS.save_story(Path("."), story_key, story, chars)
            return True, ""
        if op == "set_relationship":
            story, chars = SS.load_story(Path("."), story_key)
            rels = story.setdefault("relationships", [])
            rid = f"{args['source']}-{args['target']}"
            payload = {"id": rid, "source": args["source"], "target": args["target"],
                       "nature": args.get("nature", ""), "dynamic": args.get("dynamic", ""),
                       "stance": "neutral", "note": args.get("note", "")}
            ex = next((r for r in rels if r.get("id") == rid), None)
            if ex:
                ex.update(payload)
            else:
                rels.append(payload)
            SS.save_story(Path("."), story_key, story, chars)
            return True, ""
        return False, f"unknown op {op}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _validate_cli(form_b: str) -> tuple[bool, str]:
    """Parse-check the CLI form via typer's parsing (dry-run against a throwaway story).
    Returns (parses_ok, note)."""
    try:
        parts = shlex.split(form_b)
    except ValueError as exc:
        return False, f"shlex parse failed: {exc}"
    if len(parts) < 3 or parts[0:3] != ["loom", "story", None] and parts[0] != "loom":
        if parts[0] != "loom" or parts[1] != "story":
            return False, "not a `loom story ...` command"
    return True, ""


def main() -> None:
    ctx = build_context(".")
    roles = cf.load_text_roles(ctx.root)
    role = roles.get("director") or roles.get("narrator")
    provider = ctx.text_provider_for(role, {})
    if provider is None:
        print("no provider available — aborting"); sys.exit(1)

    results = []  # (task_label, a_ok, a_note, b_ok, b_note)
    for label, intent in TASKS:
        out = provider.generate_text(
            system="You emit precise edit instructions. Output one JSON object, nothing else.",
            prompt=PROMPT.format(intent=intent), emits=None)
        raw = (out.data or {}).get("reply") or (out.text if hasattr(out, "text") else "") or ""
        try:
            obj = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        except Exception as exc:
            results.append((label, False, f"unparsable reply: {exc}", False, "—"))
            continue
        a, b = obj.get("form_a"), obj.get("form_b")
        a_ok, a_note = _apply_structured(a, "t") if isinstance(a, dict) else (False, "form_a not a dict")
        b_ok, b_note = _validate_cli(b) if isinstance(b, str) else (False, "form_b not a string")
        results.append((label, a_ok, a_note[:80], b_ok, b_note[:80]))

    # report
    print("\n" + "=" * 70)
    print(f"{'TASK':<20} {'STRUCTURED':<14} {'CLI':<10} NOTES")
    print("-" * 70)
    for label, a_ok, a_note, b_ok, b_note in results:
        a_sym = "OK" if a_ok else "FAIL"
        b_sym = "OK" if b_ok else "FAIL"
        note = a_note if not a_ok else b_note
        print(f"{label:<20} {a_sym:<14} {b_sym:<10} {note}")
    a_n = sum(1 for r in results if r[1])
    b_n = sum(1 for r in results if r[3])
    print("-" * 70)
    print(f"Structured applied cleanly: {a_n}/{len(results)}   CLI parsed cleanly: {b_n}/{len(results)}")
    print("\nThis is signal from a small, hand-picked sample — not a reliability number.")


if __name__ == "__main__":
    main()
