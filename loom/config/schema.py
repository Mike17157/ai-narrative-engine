"""Config schemas — the contract for the whole engine.

Everything flexible about Loom flows through these models. Pydantic validates
each config file on load, so "flexible" never means "silently broken": a typo in
a pipeline step or a missing model reference fails loudly at startup, not three
calls into a generation.

The central design choice — the one that fixes SillyTavern's conflation of chat
and image models — lives here: a `Character` carries no model binding, and a
`Model` is just a named, provider-backed resource. A pipeline wires them together
by name, so the chat brain and the image model are always independent objects.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Models (named provider instances)
# --------------------------------------------------------------------------- #
class ModelDef(BaseModel):
    """A named, configured instance of a provider.

    A pipeline references models by their key in `models.yaml`, never by
    provider class — so swapping `opus` for a different chat model, or `ponyxl`
    for a different image workflow, is a one-line edit and the two are wholly
    orthogonal.
    """

    provider: str = Field(..., description="Registered provider name, e.g. 'anthropic' or 'comfyui'.")
    kind: Literal["text", "image"] = Field(..., description="What this model produces. Enforced against step types.")
    # Provider-specific settings. Validated by the provider itself, not here, so
    # adding a backend never means touching this schema.
    options: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Characters / personas (NO *chat*-model binding — the SillyTavern fix)
# --------------------------------------------------------------------------- #
class LoraRef(BaseModel):
    name: str
    weight: float = 1.0


class CharacterImage(BaseModel):
    """Portrait preset for a character: the checkpoint + LoRA base used to keep
    their generated images consistent. This is an *image* preference (visual
    identity), not the decoupled chat model.

    `loras` is a simple always-on base. For state-aware routing (outfit, mood,
    theme…) a character instead points at a named stack in the LoRA subsystem via
    `stack` — keeping all that complexity out of the character card."""
    checkpoint: str | None = None
    loras: list[LoraRef] = Field(default_factory=list)
    stack: str | None = None  # name of a LoraStack in the global LoRA library


class Character(BaseModel):
    name: str
    # The persona — *who the character is*, independent of any situation. Rendered
    # as the system prompt for chat steps that opt in via `{{ character.system }}`.
    # The situational framing (setting, opening, world) lives on a Scenario, not
    # here: a character is reusable across many scenarios.
    system: str = ""
    # Optional first message / greeting to seed a fresh session. DEPRECATED as a
    # situational field — the opening line now lives on a Scenario; kept for
    # back-compat / characters with no scenario.
    greeting: str | None = None
    # Free-form extra fields a pipeline template may reference
    # (appearance blurb for image prompts, speaking style, etc.).
    fields: dict[str, Any] = Field(default_factory=dict)
    # Portrait preset: checkpoint + LoRAs for consistent generated images.
    image: CharacterImage = Field(default_factory=CharacterImage)


# --------------------------------------------------------------------------- #
# Personas — who *you* are in the chat (the {{user}} side). A persona carries a
# long-form self-description the user writes, an optional generated short SUMMARY
# (the compact blurb a chat system prompt consumes), and APPEARANCE booru tags the
# image model renders a full-body self-portrait from. Server-side (one YAML per
# persona under configs/personas/, with a <key>.png avatar alongside), so a
# generated picture + description persist across sessions and machines.
# --------------------------------------------------------------------------- #
class Persona(BaseModel):
    name: str
    # The user-written long-form self-description — the source for generation.
    description: str = ""
    # A generated SHORT blurb (2-3 sentences) intended for the chat system prompt.
    summary: str = ""
    # Generated booru appearance tags (full-body identity for the image model).
    appearance: str = ""
    # Lossless extras (e.g. the model id that produced the summary/appearance).
    fields: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Scenarios — *the experience*: a setting a cast of characters is dropped into.
# This is the other half of the split: character cards (effectively "book
# experiences") conflate who-they-are with the-situation; a Scenario owns the
# situation (setting text, opening lines, world lorebook, the cast) so one
# character can be reused across many scenarios and vice versa.
# --------------------------------------------------------------------------- #
class CastMember(BaseModel):
    character: str               # key into the characters registry
    primary: bool = False        # the focal character (persona/portraits anchor)
    outfit: str | None = None    # optional: which portrait outfit they wear here


class Scenario(BaseModel):
    name: str
    # The situational framing — world state, premise, where/when. Composed into
    # the chat system prompt at run time (not baked into any character).
    setting: str = ""
    # Opening lines (the card's first_mes + alternate_greetings); the chat seeds a
    # fresh session from one of these.
    openings: list[str] = Field(default_factory=list)
    # The world lorebook (SillyTavern `character_book` shape, kept verbatim for
    # fidelity — entries + keyword routing).
    lorebook: dict[str, Any] = Field(default_factory=dict)
    # Who is in this scenario. One entry is usually `primary: true`.
    cast: list[CastMember] = Field(default_factory=list)
    # A background image asset for the experience (optional; future: music, tone).
    background: str | None = None
    # Lossless extras (creator notes, tags, source spec, V3 assets…).
    fields: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Stories — a "story experience" authored from a character card by the Story
# Builder. A Story is the top-level experience: a premise, a world, a cast
# (roster), and a set of LOCATIONS. Crucially the narrative is NOT scripted into
# scenes — locations are objective, neutral places (just backdrops). What happens
# in them, who is present, and when the setting changes are all emergent at run
# time (a "director" trigger detects the current location + present characters
# from the conversation). So there are no authored transitions and no per-scene
# cast — only the cast roster and the available places.
# --------------------------------------------------------------------------- #
class Location(BaseModel):
    id: str
    name: str
    description: str = ""                    # the place, objectively (no events/people)
    # A PURE background plate: the empty environment only — no characters/figures.
    background_prompt: str = ""
    background: str | None = None            # rendered background asset (later)


class Beat(BaseModel):
    """One CHAPTER of the storyboard — a distinct, self-contained episode (title +
    what it accomplishes), where it happens, and who's present. Chapters are the
    bounded creative spine; scenes (locations) and the cast are EXTRACTED from
    them. `location` is a place name (matched to a Location)."""
    title: str = ""
    summary: str
    location: str = ""
    characters: list[str] = Field(default_factory=list)


class Storyboard(BaseModel):
    logline: str = ""
    beats: list[Beat] = Field(default_factory=list)


class Story(BaseModel):
    name: str
    premise: str = ""                        # one-paragraph synopsis
    tone: str = ""
    themes: list[str] = Field(default_factory=list)
    # The bounded plot outline this experience was built from (kept as the spine
    # the runtime can loosely follow; scenes + cast are extracted from it).
    storyboard: Storyboard = Field(default_factory=Storyboard)
    cast: list[CastMember] = Field(default_factory=list)  # the roster (presence is dynamic)
    lorebook: dict[str, Any] = Field(default_factory=dict)
    locations: list[Location] = Field(default_factory=list)
    start: str | None = None                 # starting location id
    background: str | None = None            # cover / default background
    fields: dict[str, Any] = Field(default_factory=dict)  # source card key, creator…

    @model_validator(mode="after")
    def _check_start(self) -> "Story":
        ids = {l.id for l in self.locations}
        if self.locations and self.start and self.start not in ids:
            raise ValueError(f"story '{self.name}' start '{self.start}' is not a location id")
        return self


# --------------------------------------------------------------------------- #
# LoRA subsystem (self-contained: a typed library + named, routable stacks)
# --------------------------------------------------------------------------- #
class LibraryLora(BaseModel):
    """A LoRA classified once in the shared library, reusable across stacks. Its
    `type` decides how it activates:

    - ``detail``    — always-on (subject to `enabled`); a constant quality tail.
    - ``theme``     — a selectable look; on when picked.
    - ``character`` — a subject/identity/state LoRA; the pool stacks draw state
      candidates from (routed by tags), not applied as a global constant.
    """
    name: str
    type: Literal["detail", "theme", "character"] = "detail"
    weight: float = 1.0
    enabled: bool = True
    keys: list[str] = Field(default_factory=list)  # optional scene routing (theme)
    comment: str = ""


class StackMember(BaseModel):
    """A LoRA placed in a stack. It starts `uncategorized` (parked, ignored at
    render) until given a role:

    - ``identity`` — always-on base.
    - ``state``    — routes in automatically when the scene matches the tags it
      was trained on (rarity-weighted); `keys` optionally pins it, `threshold`
      overrides activation sensitivity.
    """
    name: str
    weight: float = 0.7
    role: Literal["uncategorized", "identity", "state"] = "uncategorized"
    keys: list[str] = Field(default_factory=list)
    threshold: float | None = None


class LoraStack(BaseModel):
    """A named, routable composition built by dragging/clicking LoRAs in. Each
    member carries its own role (identity = always-on, state = tag-routed,
    uncategorized = parked). Detail/theme LoRAs come from the library at resolve
    time. A character references a stack by `name`."""
    name: str
    checkpoint: str | None = None
    loras: list[StackMember] = Field(default_factory=list)


class LoraConfig(BaseModel):
    """The whole LoRA subsystem's data — loaded from configs/loras.yaml."""
    library: list[LibraryLora] = Field(default_factory=list)
    stacks: list[LoraStack] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pipelines
# --------------------------------------------------------------------------- #
class Step(BaseModel):
    id: str = Field(..., description="Unique within the pipeline; later steps/templates reference it by this id.")
    type: Literal["chat", "image"]
    model: str = Field(..., description="Key into the models registry.")
    # Optional role marker. "image_prompt" steps are driven by the Prompt Gen
    # config (its model + system prompt) at run time.
    role: str | None = None

    # Run this step only if the rendered `when` template is truthy
    # (non-empty and not 'false'/'no'/'0'). Omit to always run.
    when: str | None = None

    # --- chat-step fields ---
    system: str | None = None
    prompt: str | None = Field(None, description="The user turn for a chat step (Jinja2 template).")
    # If set, the chat model is asked to return JSON matching this JSON Schema,
    # and the parsed object is exposed to later steps as `{{ <step.id>.data.<field> }}`.
    # This is how a chat step can decide, in-band, whether an image is wanted.
    emits: dict[str, Any] | None = None

    # --- image-step fields ---
    # Jinja2 template producing the positive prompt for the image model.
    image_prompt: str | None = None
    negative_prompt: str | None = None

    @model_validator(mode="after")
    def _check_required_by_type(self) -> "Step":
        if self.type == "chat" and not self.prompt:
            raise ValueError(f"chat step '{self.id}' requires a 'prompt'")
        if self.type == "image" and not self.image_prompt:
            raise ValueError(f"image step '{self.id}' requires an 'image_prompt'")
        return self


class Pipeline(BaseModel):
    name: str
    description: str = ""
    steps: list[Step]

    @model_validator(mode="after")
    def _unique_step_ids(self) -> "Pipeline":
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id '{step.id}' in pipeline '{self.name}'")
            seen.add(step.id)
        return self


# --------------------------------------------------------------------------- #
# User configuration (user.yaml) — identity + local-tool/machine settings
# --------------------------------------------------------------------------- #
class ComfyUISettings(BaseModel):
    base_url: str = "http://127.0.0.1:8188"
    # ComfyUI's own models directory (the source of truth for weights).
    models_path: str | None = None
    # Whether Loom may launch ComfyUI headless when it isn't already running.
    managed: bool = False
    # Explicit launch overrides; left unset, a Desktop install is autodetected.
    python: str | None = None
    main_py: str | None = None
    base_directory: str | None = None
    startup_timeout_s: int = 180
    # When Loom launches ComfyUI, pop a visible console window instead of logging
    # to a file. Handy while debugging; off by default for headless operation.
    console: bool = False


class RunPodSettings(BaseModel):
    """RunPod GPU scaling configuration for dynamic image generation."""
    api_key: str = ""
    # Serverless endpoint id. When set, batch rendering fans jobs out to this
    # auto-scaling ComfyUI endpoint instead of managing whole GPU pods.
    serverless_endpoint_id: str = ""
    # Queue allocation: images per GPU instance before spinning up another
    images_per_instance: int = 10
    min_instances: int = 1
    max_instances: int = 10
    # Optional: specific RunPod template ID for new instances
    template_id: str | None = None


class UserConfig(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    comfyui: ComfyUISettings = Field(default_factory=ComfyUISettings)
    runpod: RunPodSettings = Field(default_factory=RunPodSettings)
    paths: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Top-level project config
# --------------------------------------------------------------------------- #
class Settings(BaseModel):
    """The fully-resolved project config, assembled by the loader."""

    models: dict[str, ModelDef]
    characters: dict[str, Character] = Field(default_factory=dict)
    # Who *you* are in the chat (the {{user}} side). One YAML per persona under
    # configs/personas/. SillyTavern-style: a picture + description used as you.
    personas: dict[str, Persona] = Field(default_factory=dict)
    scenarios: dict[str, Scenario] = Field(default_factory=dict)
    stories: dict[str, Story] = Field(default_factory=dict)
    pipelines: dict[str, Pipeline] = Field(default_factory=dict)
    # The self-contained LoRA subsystem: typed library + named, routable stacks.
    loras: LoraConfig = Field(default_factory=LoraConfig)

    @model_validator(mode="after")
    def _validate_references(self) -> "Settings":
        """Cross-check that every pipeline step points at a real model of the
        right kind. This is the payoff of decoupling: the engine can prove a
        chat step never accidentally targets an image model, and vice versa."""
        kind_for_type = {"chat": "text", "image": "image"}
        for pname, pipeline in self.pipelines.items():
            for step in pipeline.steps:
                model = self.models.get(step.model)
                if model is None:
                    raise ValueError(
                        f"pipeline '{pname}' step '{step.id}' references unknown model '{step.model}'"
                    )
                expected = kind_for_type[step.type]
                if model.kind != expected:
                    raise ValueError(
                        f"pipeline '{pname}' step '{step.id}' is a {step.type} step but model "
                        f"'{step.model}' is kind '{model.kind}' (expected '{expected}')"
                    )
        # A scenario's cast may only reference characters that exist (a dangling
        # reference would surface as an empty persona mid-chat — fail loudly here).
        for sname, scenario in self.scenarios.items():
            for member in scenario.cast:
                if member.character not in self.characters:
                    raise ValueError(
                        f"scenario '{sname}' casts unknown character '{member.character}'"
                    )
        # Same for stories: the cast roster must be real characters (presence is
        # dynamic at run time, so there's no per-location cast to check).
        for tname, story in self.stories.items():
            for member in story.cast:
                if member.character not in self.characters:
                    raise ValueError(f"story '{tname}' casts unknown character '{member.character}'")
        return self
