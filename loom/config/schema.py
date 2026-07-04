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
    system: str = ""
    # Optional first message / greeting to seed a fresh session.
    greeting: str | None = None
    # Free-form extra fields a pipeline template may reference
    # (appearance blurb for image prompts, speaking style, etc.).
    fields: dict[str, Any] = Field(default_factory=dict)
    # Portrait preset: checkpoint + LoRAs for consistent generated images.
    image: CharacterImage = Field(default_factory=CharacterImage)
    # PLAYABLE — when true this card is a *you* puppet: a character the human can EMBODY
    # in any story (portable, never story-bound). This folds the legacy thin Persona
    # (name + blurb) into the full character card, so an embodied player carries a real
    # backstory (`system`) AND a per-character lorebook that auto-attaches at play time.
    # The story doesn't store who you play — it's bound per-playthrough — so one puppet
    # ports across every story.
    playable: bool = False
    # OPTIONAL portable HOME scenes a playable persona carries with them — a character can
    # be at home in several places (apartment, parents' house, the studio). When embodied in
    # a story they become the player's own spots and stand in for a story 'persona_home' slot.
    # Empty = fall back to the story's persona_home scene (if any). `Scene` is defined below;
    # `from __future__ import annotations` makes this forward ref resolve lazily.
    home_scenes: list[Scene] = Field(default_factory=list)


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
# CastMember — shared by Stories (the authored experiences that actually play).
# A character is reusable across many stories and vice versa.
# --------------------------------------------------------------------------- #
class CastMember(BaseModel):
    character: str               # key into the characters registry
    primary: bool = False        # the focal character (persona/portraits anchor)
    outfit: str | None = None    # optional: which portrait outfit they wear here
    home: str = ""               # location id this character belongs to (roster grouping); "" = unplaced


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
    # Map grouping: the id of a parent location this one sits inside (an "area" is just a
    # location with children — a tree, no coordinates/adjacency). "" = top level.
    # Written by the set_location_area script; see [[persona-scene-roadmap]].
    parent: str = ""


# --------------------------------------------------------------------------- #
# Scenes & Places — a STORY-AUTHORED, character-anchored layer over bare locations.
# A Place is a first-class CONTAINER ("The House", "Main Street"); the Scenes inside
# it are the character spots that happen there — mom in the kitchen, the sister in her
# room, the baker behind the counter. Several scenes can share one Place, and a scene
# can be shared by several characters. A scene's `backstory` tells the director who is
# usually here + what they do; `role` == 'persona_home' marks the swappable "you" home
# that an embodied playable card can replace.  See [[persona-scene-roadmap]].
# --------------------------------------------------------------------------- #
class Scene(BaseModel):
    id: str
    name: str = ""                           # "Mom's kitchen", "Sister's room"
    character: str | None = None             # the anchor character key (None = shared/ambient)
    characters: list[str] = Field(default_factory=list)  # additional sharers of this spot
    backstory: str = ""                      # what this character does here (situational)
    background_prompt: str = ""              # a PURE background plate for this spot
    background: str | None = None            # rendered asset (later)
    role: str = ""                           # 'persona_home' = the swappable player-home slot


class Place(BaseModel):
    id: str
    name: str
    description: str = ""                     # the place, objectively
    background_prompt: str = ""               # establishing plate for the whole place
    background: str | None = None
    scenes: list[Scene] = Field(default_factory=list)


# Character.home_scenes forward-references Scene (defined above) — resolve it now.
Character.model_rebuild()


class Beat(BaseModel):
    """One CHAPTER of the storyboard — a distinct, self-contained episode (title +
    what it accomplishes), where it happens, and who's present. Chapters are the
    bounded creative spine; scenes (locations) and the cast are EXTRACTED from
    them. `location` is a place name (matched to a Location).

    New fields from the enriched 7-field storyboard format:
    - emotional_core: what shifts internally for the protagonist in this chapter
    - hook: the tension/question/seed pulling the reader into the next chapter
    - scene_prompt: a visual background prompt for this chapter (no characters);
      used directly by the scene renderer as a fallback / replacement for the
      s02 locations LLM pass when present.
    """
    title: str = ""
    summary: str
    location: str = ""
    characters: list[str] = Field(default_factory=list)
    emotional_core: str = ""
    hook: str = ""
    scene_prompt: str = ""
    # Beat-as-simulation (story graph): the state entering, the events, the state leaving.
    start: str = ""
    what_happened: str = ""
    end: str = ""


class Storyboard(BaseModel):
    heart: str = ""    # the human truth at the center of this story (new enriched format)
    logline: str = ""
    beats: list[Beat] = Field(default_factory=list)


class ArcBeat(BaseModel):
    """A chapter node within an arc. `next` is a list of ArcBeat ids (enables branching/merging)."""
    id: str
    title: str = ""
    summary: str = ""
    emotional_core: str = ""
    hook: str = ""
    location: str = ""
    scene_prompt: str = ""
    characters: list[str] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)
    # Beat-as-simulation (story graph): the state entering, the events, the state leaving.
    start: str = ""
    what_happened: str = ""
    end: str = ""


class ArcTimeline(BaseModel):
    """One parallel path through an arc — a 'what if this persona expressed itself this way' thread."""
    id: str
    name: str = ""
    premise: str = ""             # what defines this path (e.g. "she chooses vulnerability")
    nodes: dict[str, ArcBeat] = Field(default_factory=dict)
    start: str = ""               # id of the first chapter node


class ArcTransition(BaseModel):
    """A crossover edge between two timeline chapters — where paths can shift."""
    from_timeline: str
    from_node: str
    to_timeline: str
    to_node: str
    condition: str = ""           # what decision/event triggers the shift
    direction: str = ""           # "up" (toward lighter path) | "down" | ""


class Arc(BaseModel):
    """One dramatic unit within a book — a self-contained mini-arc with its own ending."""
    id: str
    name: str
    mini_ending: str = ""         # what this arc leaves the protagonist with
    dramatic_function: str = ""   # e.g. "Introduction — You · Need · Go"
    themes: list[str] = Field(default_factory=list)  # themes this arc explores (arc-level, not story-level)
    premise: str = ""             # the dramatic situation/tension this arc puts the cast through
    cast: list[str] = Field(default_factory=list)   # character keys active in this arc
    # Relationship-first cross-refs (GENESIS.md §6): the arc↔character and arc↔graph links.
    owner: str = ""               # character key whose LIE this arc plots (the arc IS a lie over time)
    pressures: list[str] = Field(default_factory=list)  # relationship ids this arc rides on / strains
    nodes: dict[str, ArcBeat] = Field(default_factory=dict)  # legacy flat chain
    start: str = ""               # id of the first ArcBeat node (legacy)
    order: int = 0
    timelines: list[ArcTimeline] = Field(default_factory=list)      # parallel timeline tracks
    transitions: list[ArcTransition] = Field(default_factory=list)  # crossover edges
    divergence_axis: str = ""     # the persona dimension timelines diverge along


class Chapter(BaseModel):
    """A NOVEL chapter — a linear, baked unit (vs the VN's branching arc/timeline graph). The Author
    authors its harness (purpose / pov / beats); the Narrative agent drafts its prose; the Storymaster
    distills a `recap` that carries forward to the next chapter. Generation is serial + gated."""
    id: str
    title: str = ""
    purpose: str = ""        # what this chapter accomplishes (the harness)
    pov: str = ""            # character key whose point of view
    setting: str = ""        # where it happens
    beats: list[str] = Field(default_factory=list)   # the chapter's internal structure
    status: str = "outline"  # outline | drafting | drafted
    draft: str = ""          # generated prose (the manuscript)
    recap: str = ""          # carry-forward summary (Storymaster), feeds the next chapter


class OnStage(BaseModel):
    """A character present in a VN scene, with the goal + secret that drive how they're played."""
    char: str
    goal: str = ""
    secret: str = ""


class DivergenceTrigger(BaseModel):
    """A condition that can branch a VN scene. `choice` triggers fire on the player's pick;
    `emergent` triggers fire when a tracked feature crosses a threshold (`condition` like
    'suspicion >= 4'). A choice fires deterministically; an emergent trigger is only ARMED — the
    runtime then asks the tool whether it's time to switch (see /scene/{id}/evaluate)."""
    kind: str = "emergent"   # "choice" | "emergent"
    condition: str = ""      # choice: the option text; emergent: "<feature> <op> <number>"
    branch: str = ""         # target scene id
    intent: str = ""         # what this branch is about


class SceneHarness(BaseModel):
    """A VN scene as a HARNESS, not a script: its dramatic goal, who's on stage (+ their goals/
    secrets), tone, and the divergence triggers out of it. The runtime GENERATES the dialogue live
    within this; it is never authored line-by-line. See [[multiformat-story-engine]]."""
    id: str
    title: str = ""
    goal: str = ""
    setting: str = ""
    tone: str = ""
    on_stage: list[OnStage] = Field(default_factory=list)
    triggers: list[DivergenceTrigger] = Field(default_factory=list)


class FeatureVar(BaseModel):
    """A tracked play variable (trust, suspicion, a route flag) the director advances and that
    emergent divergence conditions test."""
    id: str
    label: str = ""
    initial: float = 0


class LoreEntry(BaseModel):
    id: str = ""
    title: str = ""
    keywords: list[str] = Field(default_factory=list)
    content: str = ""
    enabled: bool = True
    priority: int = 0   # higher = injected first when context is tight
    facet: str = ""     # retrieval groups by facet; at most one entry per facet per turn
    source: str = ""    # provenance: "" = authored, "auto" = written back by the state engine
    # An entry is a trigger→action rule. `trigger` is WHERE the keywords are matched:
    # "input" (the transcript, via BM25 retrieval) or "output" (the model's reply, scanned
    # by the guard layer). `script` is a named action from the registry ("" = none); with a
    # script and/or content set, a matched entry can run the action, inject the text, or both.
    trigger: str = "input"   # "input" | "output"
    script: str = ""         # "" | "fallback" | …  (see loom/stories/guards.py registry)


_STANCES = ("devoted", "warm", "neutral", "strained", "hostile")


class Relationship(BaseModel):
    """A directional bond between two cast members (source feels → target). The SOURCE OF TRUTH is
    `dynamic` — prose, in the characters' own terms, that the narrator/actors actually read and that
    DRIFT REWRITES during play (not a number). `stance` is a coarse CATEGORICAL label used only for
    graph colour — never the thing you reason from. (Numeric warmth was removed: a scalar doesn't
    steer an LLM and forcing emotional change through an integer delta produced arbitrary noise.)"""
    id: str = ""
    source: str = ""      # character key/name who holds the feeling
    target: str = ""      # who it's toward
    nature: str = ""      # the KIND of bond (SHARED): rival / mentor / lover / mother / debtor …
    # ── A bond READS BOTH WAYS — one edge, two sides. `dynamic`/`stance` = how SOURCE regards target;
    # `target_dynamic`/`target_stance` = how TARGET regards source. The two CAN DIFFER (unrequited love,
    # one trusts while the other exploits). Empty target_* → symmetric (mirror the source side).
    dynamic: str = ""     # 2-3 WORDS: how SOURCE feels toward target right now; drift edits this
    stance: str = "neutral"   # categorical, colour only (source side): devoted/warm/neutral/strained/hostile
    target_dynamic: str = ""  # 2-3 WORDS: how TARGET feels toward source ("" → mirror source)
    target_stance: str = ""   # categorical (target side); "" → mirror source's stance
    note: str = ""        # optional extra history
    # ── The POTENTIAL — the story SEED (relationship-first genesis): what could GROW between them.
    # `potential` = the hidden COMMON CORE beneath their surface-different backgrounds — the point they
    # can plausibly build on (works for love OR enmity). `trajectory` = where it could travel + how it
    # FEELS — a from→to with a tone ("wary strangers → a slow, unrushed first love, strawberry-milk
    # gentle"). derive_stories engineers the story FROM these. See loom/stories/GENESIS.md.
    potential: str = ""
    trajectory: str = ""
    value: int | None = None  # DEPRECATED legacy warmth; kept only so old stories load (migrated below)

    @model_validator(mode="after")
    def _migrate(self) -> "Relationship":
        # Old data carried a -3..+3 `value` and no stance/dynamic — derive them once so legacy
        # stories keep working, then the numeric field is ignored from here on.
        if self.stance not in _STANCES:
            self.stance = "neutral"
        if self.value is not None and self.stance == "neutral":
            v = max(-3, min(3, int(self.value)))
            self.stance = ("hostile" if v <= -2 else "strained" if v == -1
                           else "neutral" if v == 0 else "warm" if v == 1 else "devoted")
        if not self.dynamic:
            self.dynamic = self.note or self.nature
        self.dynamic = " ".join(self.dynamic.split()[:6])   # keep it to 2-3 words (backstop)
        # The reverse side reads both ways: validate its stance, trim its phrase. Empty = mirror source.
        if self.target_stance and self.target_stance not in _STANCES:
            self.target_stance = "neutral"
        if self.target_dynamic:
            self.target_dynamic = " ".join(self.target_dynamic.split()[:6])
        return self


class SceneLink(BaseModel):
    """A directional connection in the scene/place map: you can move source → target."""
    id: str = ""
    source: str = ""      # scene/place/location id you move FROM
    target: str = ""      # where it leads TO
    label: str = ""       # how the move reads ("through the gate")


class Story(BaseModel):
    name: str
    # Format profile, FIXED at creation: "novel" (prose, baked chapter-by-chapter) or "vn" (a live,
    # AI-played harness of branching scenes). Tailors the overview lenses + generation pipeline.
    type: str = "novel"
    premise: str = ""                        # one-paragraph synopsis
    tone: str = ""
    themes: list[str] = Field(default_factory=list)
    # Layer 0 of every image this story renders (sprites AND location scenes): the art style,
    # decided on the Overview tab. Empty → the global broadcast anchor (configs/image_style.json).
    art_style: str = ""
    # The premise's core components as REAL editable fields (protagonist / lie / inciting /
    # opposition / stakes / texture) — structured overview-layer data narrative functions read
    # and mutate. Replaces the read-only LLM coverage checker.
    premise_parts: dict[str, str] = Field(default_factory=dict)
    # The bounded plot outline this experience was built from; scenes + cast are extracted from it.
    storyboard: Storyboard = Field(default_factory=Storyboard)
    cast: list[CastMember] = Field(default_factory=list)  # the roster (presence is dynamic)
    lorebook: dict[str, Any] = Field(default_factory=dict)
    locations: list[Location] = Field(default_factory=list)
    # Story-authored Places (containers) + their character-anchored Scenes. Additive over
    # `locations` — the world's "spots" (mom's kitchen, the baker's bakery). See Place/Scene.
    places: list[Place] = Field(default_factory=list)
    start: str | None = None                 # starting location id
    background: str | None = None            # cover / default background
    fields: dict[str, Any] = Field(default_factory=dict)  # source card key, creator…
    # NB: a story has NO single authored ending — that's a VN-ism. Endings live on ARCS (Arc.mini_ending,
    # the owner's lie resolved or not); the actual outcome EMERGES in play. See [[two-agent-model]].
    arcs: list[Arc] = Field(default_factory=list)
    # Novel profile: the linear chapter manuscript (authored harness → drafted prose → recap).
    # VNs use arcs/timelines instead. See Chapter; generation is serial + gated.
    chapters: list[Chapter] = Field(default_factory=list)
    # VN profile: scene HARNESSES (played live, not scripted), the tracked feature vars emergent
    # conditions test, and the opening scene. See SceneHarness; the runtime is detect→ask→act.
    scenes: list[SceneHarness] = Field(default_factory=list)
    features: list[FeatureVar] = Field(default_factory=list)
    start_scene: str = ""
    # Authored baselines (Character/Scene Agents build these); they also seed runtime state.
    relationships: list[Relationship] = Field(default_factory=list)  # the cast's bond web
    connections: list[SceneLink] = Field(default_factory=list)       # the scene/place map
    # Playable character keys this story SUGGESTS you embody — the "you" slots it ships
    # with. The player can adopt one or bring their own playable card (which then swaps in,
    # e.g. their home scene replaces the default persona's). Just hints; the play binding is
    # still per-playthrough (see Character.playable).
    default_personas: list[str] = Field(default_factory=list)
    # Sliding-window depth: how many most-recent play turns stay verbatim before older turns get
    # compressed into memory on consolidation. A per-thread world_state["recent_window"] overrides it.
    recent_window: int = 8

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
    # Launch the managed ComfyUI at server boot (in the background) instead of lazily on the
    # first render — so it's warm and ready. Only applies when `managed`. Turn off for dev if
    # frequent backend restarts make the cold relaunch annoying (it's killed on backend exit).
    warm_on_start: bool = True
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
    enabled: bool = True
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
        # The story cast roster must be real characters (presence is dynamic at
        # run time, so there's no per-location cast to check).
        for tname, story in self.stories.items():
            for member in story.cast:
                if member.character not in self.characters:
                    raise ValueError(f"story '{tname}' casts unknown character '{member.character}'")
        return self
