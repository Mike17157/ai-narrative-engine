from io import BytesIO

from PIL import Image

from loom.stories.manga import (body_lora_allowed, build_background_prompt, build_composition_guide,
                                build_composed_t2i_prompt, build_guided_panel_prompt, build_integration_prompt, build_panel_prompt,
                                build_character_prompt, composite_panel,
                                normalize_panel_plan, panel_body_lora_allowed)


CAST = {
    "petite": {"name": "Mina", "appearance": "A petite slim woman with a small bust and black bob."},
    "athlete": {"name": "Rae", "appearance": "A tall athletic woman with a firm small bust."},
    "curvy": {"name": "Vera", "appearance": "A tall voluptuous woman with a dramatic hourglass figure and very large heavy bust."},
}
CHAPTERS = [{"title": "The Platform"}, {"title": "The Call"}]


def test_body_lora_range_matches_mimic_sweep():
    assert body_lora_allowed(CAST["petite"]["appearance"])
    assert body_lora_allowed(CAST["athlete"]["appearance"])
    assert not body_lora_allowed(CAST["curvy"]["appearance"])


def test_plump_male_castmate_does_not_disable_a_compatible_woman():
    cast = {
        "maid": {"appearance": "A slender adult woman with a full heavy bust and blonde twin tails."},
        "lord": {"appearance": "A plump kind noble with chestnut hair."},
    }
    assert panel_body_lora_allowed(["maid", "lord"], cast)


def test_plan_requires_multiple_real_characters_and_blocks_volatile_body_lora():
    plan = normalize_panel_plan({"panels": [
        {"chapter": 0, "moment": "Mina catches Rae's sleeve before she leaves.",
         "characters": ["Mina", "Rae"], "composition": "Mina in foreground, Rae at the train door.",
         "shot": "wide two-shot", "body_lora": True},
        {"chapter": 1, "moment": "Vera folds the letter between them.",
         "characters": ["Vera", "Mina"], "composition": "The letter divides the frame.",
         "shot": "medium two-shot", "body_lora": True},
        {"chapter": 1, "moment": "bad solo", "characters": ["Mina"],
         "composition": "solo", "shot": "close-up", "body_lora": False},
    ]}, chapters=CHAPTERS, cast=CAST)

    assert [panel["chapter"] for panel in plan] == [0, 1]
    assert plan[0]["body_lora"] is True
    assert plan[1]["body_lora"] is False


def test_panel_prompt_uses_plan_and_canonical_cast_details():
    panel = normalize_panel_plan({"panels": [{
        "chapter": 0, "moment": "Mina catches Rae's sleeve before she leaves.",
        "characters": ["petite", "athlete"], "composition": "Mina in foreground, Rae at the train door.",
        "shot": "wide two-shot", "body_lora": False,
    }]}, chapters=CHAPTERS, cast=CAST)[0]
    prompt = build_panel_prompt(panel, CAST, style="Moonlit school drama")
    assert "Mina:" in prompt and "Rae:" in prompt
    assert "No text, no speech bubbles" in prompt
    assert "No watercolor, no painterly wash" in prompt


def test_plan_preserves_explicit_layout_and_rejects_partial_layouts():
    raw = {"panels": [{
        "chapter": 0, "moment": "Mina catches Rae's sleeve.", "characters": ["petite", "athlete"],
        "composition": "Mina faces Rae across the train door.", "shot": "wide two-shot", "body_lora": False,
        "layout": [
            {"character": "petite", "slot": "left", "depth": "foreground", "scale": .82, "action": "holding a letter"},
            {"character": "athlete", "slot": "right", "depth": "middle", "scale": .64, "action": "watching the platform"},
        ],
    }]}
    panel = normalize_panel_plan(raw, chapters=CHAPTERS, cast=CAST)[0]
    assert panel["layout"][0] == {"character": "petite", "slot": "left", "depth": "foreground", "scale": .82, "action": "holding a letter"}
    assert panel["layout"][1]["slot"] == "right"


def test_segment_prompts_and_composite_keep_actor_slots():
    panel = normalize_panel_plan({"panels": [{
        "chapter": 0, "moment": "Mina catches Rae's sleeve.", "characters": ["petite", "athlete"],
        "composition": "Mina faces Rae across the train door.", "shot": "wide two-shot", "body_lora": False,
    }]}, chapters=CHAPTERS, cast=CAST)[0]
    assert "No people" in build_background_prompt(panel)
    assert "Setting only" in build_background_prompt(panel)
    assert "Mina" not in build_background_prompt(panel)
    assert "One isolated" in build_character_prompt(panel, CAST["petite"], panel["layout"][0])
    assert "Rae" not in build_character_prompt(panel, CAST["petite"], panel["layout"][0])
    assert "Preserve the exact cast count" in build_integration_prompt(panel, CAST)
    assert "No people" not in build_integration_prompt(panel, CAST)

    def png(size, colour):
        image = Image.new("RGBA", size, colour)
        out = BytesIO(); image.save(out, format="PNG"); return out.getvalue()

    final = composite_panel(png((100, 80), (10, 20, 30, 255)), [
        ({"slot": "left", "depth": "middle", "scale": .50}, png((10, 20), (255, 0, 0, 255))),
        ({"slot": "right", "depth": "foreground", "scale": .50}, png((10, 20), (0, 255, 0, 255))),
    ])
    image = Image.open(BytesIO(final)).convert("RGB")
    assert image.getpixel((18, 60)) == (255, 0, 0)
    assert image.getpixel((82, 60)) == (0, 255, 0)


def test_guided_prompt_uses_a_single_scene_brief_and_generates_a_guide():
    panel = normalize_panel_plan({"panels": [{
        "chapter": 0, "moment": "Mina and Rae work across a desk.", "characters": ["petite", "athlete"],
        "composition": "Two people across a desk.", "shot": "medium two-shot", "body_lora": False,
        "setting": "A quiet office with a wooden desk.", "lighting": "Warm window light.",
        "layout": [
            {"character": "petite", "slot": "center_left", "depth": "foreground", "scale": .75, "action": "offering a folder"},
            {"character": "athlete", "slot": "center_right", "depth": "foreground", "scale": .75, "action": "reaching for the folder"},
        ],
    }]}, chapters=CHAPTERS, cast=CAST)[0]
    prompt = build_guided_panel_prompt(panel, CAST)
    assert "exactly 2 recurring adult characters" in prompt
    assert "Follow the supplied composition guide" in prompt
    guide = Image.open(BytesIO(build_composition_guide(panel)))
    assert guide.size == (1216, 832)
    t2i = build_composed_t2i_prompt(panel, CAST)
    assert "No third person" in t2i
    assert "left" in t2i and "right" in t2i
