"""krea2 LoRA injection + trigger-word suffix.

The krea2 workflow bakes an `easy loraStack` + `easy loraStackApply` anchor; preset LoRAs
must layer onto that anchor. LoRA trigger words append to the positive prompt text.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loom.comfy.stack import inject_models, _find_render_clip

WF = Path(__file__).resolve().parents[1] / "workflows" / "krea2_t2i_api.json"


def _graph():
    return json.load(open(WF, encoding="utf-8"))


def test_render_clip_resolves_to_the_render_encoder():
    # node 2 = CLIPLoaderGGUF (the render text encoder)
    assert _find_render_clip(_graph()) == "2"


def test_lora_chain_layers_onto_the_stack_anchor():
    # inject_models must LAYER preset LoRAs onto the `easy loraStackApply` anchor's MODEL/CLIP
    # outputs (keeping the anchor, where always-on base LoRAs live), and repoint the render
    # encoder + guider onto the LoRA tail.
    g = _graph()
    assert g["31"]["class_type"] == "easy loraStackApply"
    out = inject_models(g, None, [{"name": "x.safetensors", "weight": 0.8}])
    assert out["31"]["class_type"] == "easy loraStackApply"   # anchor preserved
    loras = {nid: n for nid, n in out.items()
             if n.get("class_type") in ("LoraLoader", "LoraLoaderModelOnly")}
    assert len(loras) == 1
    (lid, lnode), = loras.items()
    assert lnode["inputs"]["model"] == ["31", 0]
    assert lnode["inputs"]["clip"] == ["31", 1]
    assert out["4"]["inputs"]["clip"] == [lid, 1]
    assert out["10"]["inputs"]["model"] == [lid, 0]


def test_none_loras_is_noop():
    assert json.dumps(inject_models(_graph(), None, None)) == json.dumps(_graph())


# --- LoRA trigger-word suffix appends to the positive prompt -------------------
from loom.providers import _workflow as W


def test_trigger_suffix_appends_to_positive_prompt():
    # krea2: positive node 4's text holds the prompt (via the {{image}} token); the suffix appends.
    g = W.inject(_graph(), {"positive": {"node": "4", "field": "text"}},
                 "a castle", prompt_suffix="ohwx_style, m4me_token")
    assert g["4"]["inputs"]["text"] == "a castle, ohwx_style, m4me_token"


def test_trigger_suffix_noop_without_suffix():
    g = W.inject(_graph(), {"positive": {"node": "4", "field": "text"}}, "a castle")
    assert g["4"]["inputs"]["text"] == "a castle"


if __name__ == "__main__":
    test_render_clip_resolves_to_the_render_encoder()
    test_lora_chain_layers_onto_the_stack_anchor()
    test_none_loras_is_noop()
    test_trigger_suffix_appends_to_positive_prompt()
    test_trigger_suffix_noop_without_suffix()
    print("ok")
