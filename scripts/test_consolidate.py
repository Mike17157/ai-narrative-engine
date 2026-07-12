"""Self-check for stage_tools.consolidate_cast — story summary + card updates, self-gating.
Run: python -m scripts.test_consolidate
"""
import types

from loom.stories import stage_tools as ST


class _Prov:
    def generate_text(self, *, system, prompt, **k):
        class _R: pass
        r = _R()
        r.text = ("They met on the night ward and started talking." if "STORY-SO-FAR" in system
                  else "NAME: Rin\nCARD: a night nurse who is starting to let the new hire in")
        return r


def _ctx():
    bs = types.SimpleNamespace(stories={"s": types.SimpleNamespace(cast=[])}, characters={})
    return types.SimpleNamespace(base_settings=bs, root=".")


def main():
    ST._as_agent = lambda ctx, role: (_Prov(), None)          # stub the play agent

    # 55 turns since the last watermark, Rin appears in them → fires + updates
    ws = {"transcript": [f"Rin did something on turn {i}" for i in range(55)],
          "people": {"Rin": {"born": True, "card": "a cold nurse"}}}
    out = ST.consolidate_cast(_ctx(), "s", ws)
    assert ws["story_so_far"].startswith("They met") and out["summary"]
    assert ws["cards"]["Rin"].startswith("a night nurse") and "Rin" in out["cards"]
    assert ws["people"]["Rin"]["card"].startswith("a night nurse")   # born card grew too
    assert ws["cast_consolidated_through"] == 55

    # under the threshold → no-op, nothing written
    ws2 = {"transcript": ["Rin waited"] * 10}
    assert ST.consolidate_cast(_ctx(), "s", ws2) == {"summary": "", "cards": []}
    assert "story_so_far" not in ws2 and "cast_consolidated_through" not in ws2

    # a character not seen in the recent window isn't touched
    ws3 = {"transcript": [f"turn {i} with no one named" for i in range(55)],
           "people": {"Rin": {"born": True, "card": "a cold nurse"}}}
    ST.consolidate_cast(_ctx(), "s", ws3)
    assert ws3["people"]["Rin"]["card"] == "a cold nurse"            # untouched, wasn't on stage
    print("ok — consolidate_cast: summary + card updates + watermark; self-gates under 50; skips off-stage")


if __name__ == "__main__":
    main()
