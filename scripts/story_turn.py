"""Drive one /interview turn against a running backend and print everything: the exact
system prompt and user prompt the model received, its raw unparsed completion, and the
parsed reply/patch. Faster than the frontend for iterating on prompt/model changes, and
the only way to actually see what the model got and said (the normal API response never
includes the system prompt or raw completion).

Usage:
  python scripts/story_turn.py --key live_test --focus cast --message "Give Mara a real backstory."
  python scripts/story_turn.py --key live_test --focus world -m @message.txt
  echo "..." | python scripts/story_turn.py --key live_test --focus arcs -m -

Requires the backend running (loom's dev server, default http://localhost:8000).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Windows consoles default to cp1252, which can't print em dashes/arrows the
# model happily generates. Widen stdout rather than crash mid-print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _resolve_message(value: str) -> str:
    if value == "-":
        return sys.stdin.read().strip()
    if value.startswith("@"):
        with open(value[1:], encoding="utf-8") as f:
            return f.read().strip()
    return value


def _section(title: str, body: str) -> None:
    print(f"\n{'=' * 8} {title} {'=' * 8}")
    print(body if body else "(empty)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--key", required=True, help="Story key (e.g. live_test)")
    p.add_argument("--focus", required=True,
                    choices=["world", "premise", "first_day", "time_system", "cast", "arcs"])
    p.add_argument("--message", "-m", required=True,
                    help="The author message. '-' reads stdin, '@file' reads a file.")
    p.add_argument("--host", default="http://localhost:8000")
    p.add_argument("--model", default=None,
                    help="One-off model override (e.g. minimax/minimax-m3), bypassing story_builder.json "
                         "for just this call — handy for A/B testing a model without editing config.")
    p.add_argument("--json", action="store_true", help="Print the full raw JSON response instead of sections.")
    args = p.parse_args()

    payload = {
        "focus": args.focus,
        "messages": [{"role": "user", "text": _resolve_message(args.message)}],
        "debug": True,
    }
    if args.model:
        payload["model"] = args.model
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{args.host}/api/stories/{args.key}/interview", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        data = json.loads(exc.read())

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    model_input = data.get("model_input") or {}
    if data.get("error"):
        print(f"ERROR: {data['error']}")
        # Still show what the model actually said, when we have it — this is
        # usually a validation failure on the model's own malformed output.
        if model_input.get("raw_response"):
            _section("RAW MODEL RESPONSE (unparsed — this is what failed validation)",
                     model_input["raw_response"])
        return 1

    _section("MODEL", data.get("model_route", {}).get("selected_model", "?"))
    _section("SYSTEM (what the model always gets)", model_input.get("system", ""))
    _section("PROMPT (this turn's specific context)", model_input.get("prompt", ""))
    _section("RAW MODEL RESPONSE (unparsed)", model_input.get("raw_response", ""))
    _section("PARSED REPLY", data.get("reply", ""))
    _section("PARSED PATCH", json.dumps(data.get("patch"), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
