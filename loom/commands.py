"""Shared CMD transport for model-authored mutations.

Plain-text model calls use a fenced ``CMD`` block instead of relying on prose
parsing.  The transport is deliberately domain-neutral; each feature owns the
allowlist and applies the decoded commands through its normal mutation path.

    ```CMD
    MERGE world {"genre":"mystery"}
    SET premise "A woman returns home by ferry."
    ```
"""
from __future__ import annotations

import json
import re
from typing import Any


# Models routinely tag the fence with the block's apparent content type
# ("```json") even when told to use ```CMD or a bare fence — measured live
# (minimax-m3, and not just for "json"; a fixed whitelist of tags missed
# others like "```python"). Any single tag word is accepted, but ONLY when
# followed by a real line break before the command — that's what stops a
# same-line fence like "```SET premise ..." from having "SET" itself
# swallowed as the tag, leaving no command behind.
_FENCE_TAG = r"(?:[ \t]*[A-Za-z0-9_-]+[ \t]*\r?\n)?"
CMD_BLOCK = re.compile(r"```" + _FENCE_TAG + r"\s*CMD\s*\n(.*?)```", re.I | re.S)
BARE_CMD_BLOCK = re.compile(r"```" + _FENCE_TAG + r"\s*((?:SET|MERGE|APPEND)\b[\s\S]*?)```", re.I)
# Some text models follow the command *shape* but omit Markdown fences.  A
# standalone paired ``CMD`` line is still an unambiguous transport marker:
# only the text between the two markers is executable, so ordinary prose that
# happens to contain an imperative is never reinterpreted as a write.
NAKED_CMD_BLOCK = re.compile(
    r"(?ims)^[ \t]*CMD[ \t]*\r?\n(?P<commands>.*?)(?:\r?\n[ \t]*CMD[ \t]*(?=\r?\n|\Z))"
)
# A model sometimes formats one short command as inline code (single backtick) rather
# than a fenced block, especially alongside a one-line NEXT_FOCUS marker — measured
# live (deepseek-chat: `` `SET fields.open_questions [...]` ``). [^`\n] keeps this to
# one line so it can never swallow unrelated prose the way a greedier span would.
INLINE_CMD = re.compile(r"`\s*((?:SET|MERGE|APPEND)\b[^`\n]*)`", re.I)
OPS = frozenset({"SET", "MERGE", "APPEND"})


def split_cmd_response(text: str) -> tuple[str, list[dict[str, Any]] | None]:
    """Separate reader-facing prose from commands; ``None`` means no CMD block."""
    match = CMD_BLOCK.search(text or "")
    if match:
        return CMD_BLOCK.sub("", text).strip(), parse_cmd_block(match.group(1))
    # Text models occasionally omit the CMD label. Treat a fenced block whose
    # first token is a command identically; never leak its implementation text.
    match = BARE_CMD_BLOCK.search(text or "")
    if match:
        return BARE_CMD_BLOCK.sub("", text).strip(), parse_cmd_block(match.group(1))
    # DeepSeek and some smaller models occasionally emit the paired ``CMD``
    # delimiters literally instead of wrapping them in a fenced block.  Keep
    # parsing as strict as the fenced transport: every command still has to
    # be a known verb with a valid JSON value, and prose before/after remains
    # reader-facing text.
    matches = list(NAKED_CMD_BLOCK.finditer(text or ""))
    if matches:
        commands: list[dict[str, Any]] = []
        for naked in matches:
            commands.extend(parse_cmd_block(naked.group("commands")))
        prose = NAKED_CMD_BLOCK.sub("", text).strip()
        # Removing a line-delimited block can join the blank line before its
        # opening marker to the blank line after its closing marker.  Retain a
        # normal paragraph break without rendering an accidental tall gap.
        prose = re.sub(r"(?<=\S)(?:\r?\n){3,}(?=\S)", "\n\n", prose)
        return prose, commands
    inline_matches = list(INLINE_CMD.finditer(text or ""))
    if inline_matches:
        commands = []
        for inline in inline_matches:
            commands.extend(parse_cmd_block(inline.group(1)))
        prose = INLINE_CMD.sub("", text).strip()
        return prose, commands
    return (text or "").strip(), None


def parse_cmd_block(block: str) -> list[dict[str, Any]]:
    """Parse one command per line into ``{op, path, value}`` records."""
    commands: list[dict[str, Any]] = []
    source, pos, decoder = block or "", 0, json.JSONDecoder()
    head = re.compile(r"(SET|MERGE|APPEND)\s+(\S+)\s+", re.I)
    while pos < len(source):
        while pos < len(source) and source[pos].isspace():
            pos += 1
        if pos >= len(source):
            break
        if source[pos] == "#":
            newline = source.find("\n", pos)
            pos = len(source) if newline < 0 else newline + 1
            continue
        match = head.match(source, pos)
        if not match:
            raise ValueError(f"invalid CMD instruction near: {source[pos:pos + 80]}")
        try:
            value, end = decoder.raw_decode(source, match.end())
        except json.JSONDecodeError as exc:
            raise ValueError(f"CMD value is not valid JSON: {exc.msg}") from exc
        commands.append({"op": match.group(1).upper(), "path": match.group(2), "value": value})
        pos = end
    return commands
