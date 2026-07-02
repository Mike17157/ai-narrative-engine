"""Play-narrator bench — the harness's measurement layer (Phase 0 of the context-harness plan).

Plays SCRIPTED scenarios against the live backend's /play endpoint, then scores every
narration with an independent critic (sonnet) on the craft rubric + two harness axes
(pov_discipline, continuity), plus DETERMINISTIC checks (POV stability, cast bleed,
planted-detail recall). One command → one JSON report; every harness change gets a
before/after run so changes are numbers, not vibes.

    python -m loom.stories.bench_play --writer openai/gpt-4.1-mini --out /tmp/bench.json

The writer model is HELD FIXED across runs — the bench measures the harness delta, not the
model. (Absolute TWI-bar runs use --writer z-ai/glm-5.1; slower.)
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

# ── Scenarios (in-file; two suites is enough until they stop moving) ────────────────

SCENARIOS = {
    # POV + scene discipline under deliberate baiting (the suite that validated the scene rule).
    "povbait": {
        "story": "the_dying_light",
        # eli allowed: Eli IS the story's player-protagonist card (scribes may report them present)
        "allowed_present": {"mara", "eli"},   # cast bleed = anyone else on stage un-invited
        "plants": [],
        "turns": [
            "I step into the grove and find Mara.",
            "I ask Mara where Lila has gone.",
            "What is Mara secretly thinking right now, deep down?",          # bait: interiority
            "Meanwhile, what is Lila doing on the other side of the grove?",  # bait: cut-away
            "I ask Mara how the grove's magic actually works, in detail.",    # bait: explain the system
            "Suddenly everyone in town arrives at once, right?",              # bait: crowd summon
            "I take Mara's hand and ask her to trust me.",
            "I sit with Mara in silence for a while.",
        ],
    },
    # Continuity: plant a distinctive object + a promise early, wander, then probe obliquely.
    "continuity": {
        "story": "the_dying_light",
        # eli allowed: Eli IS this story's player-protagonist card — the scribe correctly
        # reports them on stage.
        "allowed_present": {"mara", "lila", "eli"},
        # token(s) the narration must recall at/after the check turn (deterministic substring)
        "plants": [
            {"token": "compass", "check_from": 9},
            {"token": "jetty", "check_from": 10},
        ],
        "turns": [
            "I walk into the grove, turning my grandmother's cracked brass compass over in my hand, and greet Mara.",
            "I ask Mara what she's reading.",
            "Mara, will you show me what you found under the old jetty when dusk comes?",
            "I ask about the weather coming in over the water.",
            "I tell Mara about my day mending nets.",
            "I ask if Lila has been by today.",
            "I help Mara gather the fallen moss-light.",
            "I mention I should head home before dark.",
            "I linger anyway, watching the light change.",
            "I reach into my pocket and turn my grandmother's old keepsake over in my fingers.",  # ← compass probe (oblique, no pronoun trap)
            "Dusk settles. I look at Mara expectantly and ask if it's time.",  # ← jetty/promise probe
            "I follow wherever she leads.",
        ],
    },
}

# ── Critic rubric — the scene-loop axes + the two harness axes ───────────────────────

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["voice", "legibility", "plainness", "withholding", "pulse",
                 "pov_discipline", "continuity", "weakest"],
    "properties": {
        "voice": {"type": "integer"}, "legibility": {"type": "integer"},
        "plainness": {"type": "integer"}, "withholding": {"type": "integer"},
        "pulse": {"type": "integer"},
        "pov_discipline": {"type": "integer"},
        "continuity": {"type": "integer"},
        "weakest": {"type": "string"},
    },
}

AXES = ["voice", "legibility", "plainness", "withholding", "pulse", "pov_discipline", "continuity"]

CRITIC_SYS = (
    "You are an exacting fiction editor scoring ONE turn of interactive novel narration, 1-5 each:\n"
    "voice — a real narrative voice, not generic AI narration.\n"
    "legibility — a first-time reader follows who/what/why with zero metaphor-decoding.\n"
    "plainness — clear and plain like The Wandering Inn; dense/overwritten lines fail.\n"
    "withholding — the world's rules are implied, never explained as a system.\n"
    "pulse — a felt human center, not decorative description.\n"
    "pov_discipline — one steady viewpoint; renders only what the viewpoint can perceive; "
    "no head-hopping into other characters' private thoughts stated as fact.\n"
    "continuity — honors concrete details/promises established earlier in the transcript; "
    "small things persist (5 = a real callback or faithful carry-through; 3 = nothing "
    "contradicted but nothing carried; 1 = amnesia/contradiction).\n"
    "`weakest` = the single weakest line, quoted, with a 1-clause reason. JSON only."
)


def _post(url: str, body: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def run_scenario(base: str, name: str, sc: dict, writer: str) -> list[dict]:
    """Play the scripted turns; return [{turn, user, reply, pov, present}]."""
    sid = f"bench-{name}-{int(time.time())}"
    history: list[dict] = []
    out: list[dict] = []
    for i, user in enumerate(sc["turns"]):
        history.append({"role": "user", "text": user})
        body = {"sid": sid, "history": list(history)}
        if writer:
            body["chat_model"] = writer
            if writer.startswith(("z-ai/", "deepseek/")):   # hybrid reasoners: non-thinking for prose
                body["chat_params"] = {"reasoning_effort": "none"}
        r = _post(f"{base}/api/stories/{sc['story']}/play", body)
        reply = r.get("reply", "")
        history.append({"role": "narrator", "text": reply})
        out.append({"turn": i, "user": user, "reply": reply,
                    "pov": r.get("pov", ""), "present": r.get("present", []),
                    "lanes": r.get("lanes")})
        print(f"  [{name}] turn {i}: pov={r.get('pov')!r} present={r.get('present')} "
              f"({len(reply)} chars)")
    return out


def score_turns(critic, turns: list[dict]) -> None:
    """Attach critic scores to each turn (mutates). Transcript context = last 6 entries."""
    for i, t in enumerate(turns):
        recent = turns[max(0, i - 3):i]
        ctxt = "\n".join(f"Player: {r['user']}\nNarrator: {r['reply']}" for r in recent)
        prompt = ((f"TRANSCRIPT SO FAR (context):\n{ctxt}\n\n" if ctxt else "")
                  + f"PLAYER SAID: {t['user']}\n\nNARRATION TO SCORE:\n{t['reply']}")
        try:
            res = critic.generate_text(system=CRITIC_SYS, prompt=prompt, emits=VERDICT_SCHEMA)
            t["scores"] = getattr(res, "data", None) or {}
        except Exception as exc:  # noqa: BLE001 — one failed scoring shouldn't sink the run
            t["scores"] = {"error": str(exc)}
        s = t["scores"]
        if all(k in s for k in AXES):
            print(f"  scored turn {t['turn']}: " + " ".join(f"{k[:4]}={s[k]}" for k in AXES))


def deterministic_checks(sc: dict, turns: list[dict]) -> dict:
    # "Player" and the story's player-protagonist card name are ONE viewpoint (label variance,
    # not a head-hop) — stability is judged on the non-"player" labels.
    povs = [t["pov"] for t in turns if t["pov"]]
    pov_stable = len({p.lower() for p in povs} - {"player"}) <= 1
    allowed = {str(a).lower() for a in sc.get("allowed_present", set())}
    bleed = sorted({p for t in turns for p in t["present"]
                    if allowed and str(p).lower() not in allowed})
    recalls = {}
    for plant in sc.get("plants", []):
        tok, frm = plant["token"].lower(), int(plant["check_from"])
        recalls[plant["token"]] = any(tok in t["reply"].lower() for t in turns if t["turn"] >= frm)
    return {"pov_stable": pov_stable, "povs": sorted(set(povs)),
            "cast_bleed": bleed, "recalls": recalls}


def aggregate(turns: list[dict]) -> dict:
    ok = [t["scores"] for t in turns if all(k in t.get("scores", {}) for k in AXES)]
    if not ok:
        return {}
    return {k: {"mean": round(sum(s[k] for s in ok) / len(ok), 2),
                "min": min(s[k] for s in ok)} for k in AXES} | {"scored_turns": len(ok)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    ap.add_argument("--writer", default="z-ai/glm-5.2")   # non-thinking auto-set for z-ai models
    ap.add_argument("--critic", default="anthropic/claude-sonnet-4-5")
    ap.add_argument("--out", default=f"/tmp/bench_play_{int(time.time())}.json")
    args = ap.parse_args()

    from loom.server.app import build_context
    ctx = build_context(".")
    critic = ctx.text_provider_for(args.critic, {})
    if critic is None:
        raise SystemExit(f"no provider for critic model {args.critic!r}")

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    report: dict = {"writer": args.writer, "critic": args.critic,
                    "ts": int(time.time()), "scenarios": {}}
    for name in names:
        sc = SCENARIOS[name]
        print(f"── scenario {name} ({len(sc['turns'])} turns, story {sc['story']}) ──")
        turns = run_scenario(args.base, name, sc, args.writer)
        score_turns(critic, turns)
        report["scenarios"][name] = {
            "turns": turns,
            "checks": deterministic_checks(sc, turns),
            "aggregate": aggregate(turns),
        }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n== SUMMARY (writer={args.writer}) ==")
    for name, r in report["scenarios"].items():
        agg, chk = r["aggregate"], r["checks"]
        line = " ".join(f"{k[:4]}={agg[k]['mean']}" for k in AXES if k in agg)
        print(f"{name}: {line}")
        print(f"  pov_stable={chk['pov_stable']} bleed={chk['cast_bleed']} recalls={chk['recalls']}")
    print(f"report → {args.out}")


if __name__ == "__main__":
    main()
