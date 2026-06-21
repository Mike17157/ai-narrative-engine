"""Output guards with retry — data-driven off the `_refusal` guard lorebook.

A guard entry is a trigger→action rule: its `keywords` are phrases matched against the
START of the model's OUTPUT (so an in-fiction "I can't" in dialogue never trips it), and
its `script` is the action to run on a hit. v1 registry:

    fallback  → re-run the turn on the configured fallback model

So a refusal ("As an AI, I can't continue…") matches the rule's keywords and fires its
script. Different keyword sets in different entries can point at different scripts. The
built-in floor below always applies (script=fallback) even if the book is emptied.

Every narrator/scribe call routes through `generate_guarded`: run the primary model, scan
its output against the rules, and on a hit (or provider error / invalid structured output)
run the matched script — currently always "reroute to the fallback model".
"""
from __future__ import annotations

from pathlib import Path

GUARD_SCOPE = "_refusal"
SCRIPTS = {"fallback"}          # the v1 action registry

# The floor — always active even if the user empties the guard book. Kept HIGH-PRECISION
# (AI-refusal register, not natural dialogue) so an in-fiction "I can't stay" never trips
# it. Generic phrases like "i can't" / "i can not" are deliberately excluded.
BUILTIN_REFUSALS = [
    "as an ai", "as a language model", "i can't assist with", "i cannot assist with",
    "i can't help with that request", "i'm not able to continue", "i'm unable to provide",
    "i cannot provide", "i cannot fulfill", "against my guidelines", "content policy",
    "i won't be able to help with",
]

# Refusals LEAD the reply — scan only the opening so an in-fiction line buried in narration
# can't trip a guard.
_HEAD = 160


def load_rules(root: Path) -> list[dict]:
    """Output guard rules: each `{phrases:[...], script}` from enabled `_refusal` entries
    whose trigger is 'output', plus the builtin floor (script='fallback')."""
    rules: list[dict] = [{"phrases": list(BUILTIN_REFUSALS), "script": "fallback"}]
    try:
        from ..server.services import lorebook_store as LS
        for e in LS.load_lorebook(root, GUARD_SCOPE):
            if not e.enabled or e.trigger != "output":
                continue
            phrases = [k.strip().lower() for k in e.keywords if k.strip()]
            # tolerate the legacy shape where the phrase lived in content
            if e.content.strip():
                phrases.append(e.content.strip().lower())
            if phrases:
                rules.append({"phrases": phrases, "script": (e.script or "fallback")})
    except Exception:  # noqa: BLE001 — never let guard config break generation
        pass
    return rules


def detect(text: str, rules: list[dict]) -> dict | None:
    """Return the first matching rule (with the matched phrase) or None."""
    head = (text or "")[:_HEAD].lower()
    if not head.strip():
        return None
    for rule in rules:
        for p in rule.get("phrases", []):
            if p and p in head:
                return {"phrase": p, "script": rule.get("script", "fallback")}
    return None


# Back-compat alias (older callers / tests).
def detect_refusal(text: str, rules) -> str | None:
    hit = detect(text, rules if rules and isinstance(rules[0], dict) else
                 [{"phrases": rules, "script": "fallback"}])
    return hit["phrase"] if hit else None


def _attempt(provider, *, system, prompt, emits, rules, on_delta=None):
    """Run one provider. Returns (res, error_str|None, hit_rule|None)."""
    try:
        res = provider.generate_text(system=system, prompt=prompt, emits=emits, on_delta=on_delta)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc), None
    text = getattr(res, "text", "") or ""
    data = getattr(res, "data", None)
    # For a structured call, empty/missing data is itself a failure (often a soft refusal).
    if emits is not None and not data:
        return res, None, (detect(text, rules) or {"phrase": "invalid-structured-output", "script": "fallback"})
    return res, None, detect(text, rules)


def generate_guarded(primary, *, system: str, prompt: str, root: Path,
                     emits: dict | None = None, fallback=None, on_delta=None) -> dict:
    """Generate with guard rules. Try *primary*; if the output matches a guard rule (or the
    call errors / returns invalid structured output), run the matched rule's script. v1:
    every script reroutes to *fallback*. Returns:

        { res, text, data, tripped: phrase|None, script: str|None, used_fallback: bool, error }
    """
    rules = load_rules(root)
    res, err, hit = _attempt(primary, system=system, prompt=prompt, emits=emits,
                             rules=rules, on_delta=on_delta)
    script = (hit or {}).get("script") if hit else None

    # v1: the only action is 'fallback' (reroute). A provider error also reroutes.
    if (err or hit) and fallback is not None and fallback is not primary:
        res2, err2, hit2 = _attempt(fallback, system=system, prompt=prompt, emits=emits,
                                    rules=rules, on_delta=on_delta)
        if not err2 and not hit2:
            return {"res": res2, "text": getattr(res2, "text", "") or "",
                    "data": getattr(res2, "data", None),
                    "tripped": (hit or {}).get("phrase") or err, "script": script or "fallback",
                    "used_fallback": True, "error": None}
        best = res2 or res
        return {"res": best, "text": getattr(best, "text", "") or "",
                "data": getattr(best, "data", None),
                "tripped": (hit or {}).get("phrase") or (hit2 or {}).get("phrase") or err,
                "script": script or "fallback", "used_fallback": True,
                "error": err2 or err or f"refusal: {(hit2 or hit or {}).get('phrase')}"}

    return {"res": res, "text": getattr(res, "text", "") or "",
            "data": getattr(res, "data", None),
            "tripped": (hit or {}).get("phrase") if hit else None,
            "script": script, "used_fallback": False,
            "error": err or (f"refusal: {hit['phrase']}" if hit else None)}
