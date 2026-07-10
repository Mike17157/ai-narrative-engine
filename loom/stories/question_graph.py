"""The EVOLVING QUESTION as a branching graph — divergence by CONSEQUENCE, not scope.

A story's core question doesn't sit still; it mutates as the protagonist answers it. We model that as
a tree: each NODE is the dilemma in its current form (+ its two sides); each BRANCH is the side the
protagonist took; each child dilemma is generated as the DIRECT CONSEQUENCE of that answer.

Why divergence runs on CONSEQUENCE + COMPLICITY (not widening scope): fanning by scope drifts a
person's choice into a policy seminar (personal ethics → "the town's rights" → cosmic law). Interesting
divergence keeps it FIRST-PERSON and turns the VIRTUE that drove the last answer into the protagonist's
crime — love becomes theft becomes murder, at rising stakes but always on a specific body. That is the
DIVERGENCE_RULE below; five checks enforce it.

Output is labeled prose (no json_schema — reasoning models flake on the grammar), routed via
loom.stories.labeled. Self-check: python -m loom.stories.question_graph
"""
from __future__ import annotations

from .labeled import run_labeled


# The divergence-generation rule — the theory, stated as operations the model must satisfy.
DIVERGENCE_RULE = (
    "You extend an EVOLVING QUESTION by exactly ONE step. The protagonist just faced a dilemma and "
    "took a side. Generate the NEXT dilemma as a direct CONSEQUENCE of that choice — the same person, "
    "deeper in. The engine is CONSEQUENCE + COMPLICITY, never widening scope for its own sake. All five "
    "must hold:\n"
    "1. CAUSED BY THE CHOICE. The new dilemma exists BECAUSE of how they answered — their own doing come "
    "back on them. If it would arise no matter what they chose, it is a topic, not a consequence.\n"
    "2. VIRTUE INTO CRIME. Name the value that drove their answer (love, loyalty, mercy) and turn it into "
    "the harm they now face. Their goodness metastasizes: the same virtue, one size larger, is the crime.\n"
    "3. FIRST PERSON. THIS person's specific choice about a specific body — never 'what should the town / "
    "society / state be allowed to do,' never rights or power or policy. Scale up what their one choice "
    "COSTS; never hand the choice to a group. The instant it becomes governance, it is dead.\n"
    "4. INCOMMENSURABLE + IRREDUCIBLE. The two sides are different KINDS of good (a life vs. a principle; "
    "one you love vs. many you don't) — no common unit, no third door, no compromise.\n"
    "5. STAKES UP, ABSTRACTION FLAT. More people, higher cost — but still visceral, on a body, in a room. "
    "The world gets bigger; the question does not get vaguer.\n"
    "Bend toward the MACRO-TURN: over successive steps, the protagonist's ORIGINAL want becomes the very "
    "thing that threatens everyone. Keep every field concrete and plain — no abstract-noun grandeur."
)

_FIELDS = [
    ("CONSEQUENCE", "the concrete, on-a-body result the chosen side actually CAUSED"),
    ("VIRTUE_TURNED", "the value that drove the chosen side, now revealed as the crime they face"),
    ("QUESTION", "the new FIRST-PERSON dilemma this consequence forces on the SAME protagonist"),
    ("SIDE_A", "one defensible answer (a kind of good)"),
    ("SIDE_B", "the other defensible answer (a different kind of good)"),
]


def expand_divergence(provider, *, protagonist: str, principle: str, parent_question: str, side: str) -> dict:
    """Generate the next dilemma as the consequence of the protagonist taking `side` on `parent_question`.
    Returns {consequence, virtue_turned, question, side_a, side_b} (or {} if the model returns nothing)."""
    prompt = (f"PROTAGONIST: {protagonist}\n"
              f"WORLD PRINCIPLE: {principle}\n"
              f"THEY JUST FACED THIS DILEMMA: {parent_question}\n"
              f"AND TOOK THIS SIDE: {side}\n\n"
              "Give the next dilemma this choice forces on them. Obey all five rules.")
    d = run_labeled(provider, DIVERGENCE_RULE, prompt, _FIELDS)
    if not d.get("QUESTION"):
        return {}
    return {"consequence": d["CONSEQUENCE"], "virtue_turned": d["VIRTUE_TURNED"],
            "question": d["QUESTION"], "side_a": d["SIDE_A"], "side_b": d["SIDE_B"]}


def build_question_graph(provider, *, protagonist: str, principle: str, question: str,
                         sides: list[str], depth: int = 2) -> dict:
    """Fan `question` into a branching graph `depth` levels deep: each side → a consequence-driven child
    dilemma (via expand_divergence), recursed. A node is
    {question, sides, branches:[{taken_side, consequence, virtue_turned, question, sides, branches}]}."""
    node = {"question": question, "sides": list(sides), "branches": []}
    if depth <= 0:
        return node
    for side in sides:
        child = expand_divergence(provider, protagonist=protagonist, principle=principle,
                                  parent_question=question, side=side)
        if not child.get("question"):
            continue
        sub = build_question_graph(provider, protagonist=protagonist, principle=principle,
                                   question=child["question"],
                                   sides=[child.get("side_a", ""), child.get("side_b", "")],
                                   depth=depth - 1)
        sub["taken_side"] = side
        sub["consequence"] = child.get("consequence", "")
        sub["virtue_turned"] = child.get("virtue_turned", "")
        node["branches"].append(sub)
    return node


def demo() -> None:
    class _Stub:
        def __init__(self): self.calls = 0
        def generate_text(self, *, system, prompt):
            self.calls += 1
            n = self.calls

            class _R:
                text = (f"CONSEQUENCE: c{n}\nVIRTUE_TURNED: v{n}\nQUESTION: q{n}\n"
                        f"SIDE_A: a{n}\nSIDE_B: b{n}")
            return _R()

    stub = _Stub()
    g = build_question_graph(stub, protagonist="P", principle="W",
                             question="Q0", sides=["keep", "release"], depth=2)
    assert stub.calls == 6                                # root -> 2 -> each 2 = 6 expands
    assert len(g["branches"]) == 2 and all(len(b["branches"]) == 2 for b in g["branches"])
    assert all(len(gc["branches"]) == 0 for b in g["branches"] for gc in b["branches"])
    assert g["branches"][0]["taken_side"] == "keep" and g["branches"][0]["virtue_turned"]
    assert "VIRTUE INTO CRIME" in DIVERGENCE_RULE and "FIRST PERSON" in DIVERGENCE_RULE
    print("ok — question_graph: consequence-divergence via labeled prose (no JSON) + graph build")


if __name__ == "__main__":
    demo()
