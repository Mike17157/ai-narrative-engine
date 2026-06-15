"""The pipeline runner — executes a Pipeline's steps in order.

This is the heart of the engine and it is intentionally small. Each step:

  1. Renders its templates against the accumulated context.
  2. Checks its `when` guard (skip if falsy).
  3. Dispatches to the model's provider — a chat step to a TextProvider, an
     image step to an ImageProvider.
  4. Writes its result back into the context under the step id.

Because models are resolved by name from the registry, a chat step and an image
step in the same pipeline run against entirely independent backends — the
decoupling holds all the way down to execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..config.schema import Character, Scenario, Settings, Step
from ..providers import ImageProvider, TextProvider, build_provider
from ..providers.base import ImageResult, TextResult
from .context import is_truthy, render


@dataclass
class StepOutcome:
    step_id: str
    type: str
    skipped: bool = False
    text: str | None = None
    images: list[bytes] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    pipeline: str
    outcomes: list[StepOutcome] = field(default_factory=list)

    @property
    def text(self) -> str | None:
        """The last chat step's text — the conversational reply, if any."""
        for outcome in reversed(self.outcomes):
            if outcome.type == "chat" and outcome.text is not None:
                return outcome.text
        return None

    @property
    def images(self) -> list[bytes]:
        return [img for o in self.outcomes for img in o.images]


class Runner:
    def __init__(self, settings: Settings):
        self.settings = settings
        # Providers are built once and reused across steps/runs.
        self._providers: dict[str, TextProvider | ImageProvider] = {}

    def _provider(self, model_key: str) -> TextProvider | ImageProvider:
        if model_key not in self._providers:
            self._providers[model_key] = build_provider(self.settings.models[model_key])
        return self._providers[model_key]

    def run(
        self,
        pipeline_name: str,
        *,
        user_message: str,
        character: str | None = None,
        scenario: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        chat_model: str | None = None,
        image_model: str | None = None,
        promptgen: dict[str, Any] | None = None,
        chat_system: str | None = None,
        init_image: bytes | None = None,
    ) -> RunResult:
        """Run a pipeline. `chat_model`/`image_model` override the model on every
        chat/image step respectively — this is the decoupled selection: the user
        picks a chat backend and an image backend independently, and the same
        pipeline runs against whichever they chose.

        `promptgen` ({model, system, enabled}) drives steps with role
        'image_prompt' — the configurable image-description generator.

        `chat_system` is a global system prompt prepended to every conversational
        chat step (role != 'image_prompt') — the user's standing instructions for
        the chat model, layered on top of the character's own system."""
        pipeline = self.settings.pipelines[pipeline_name]
        scen_obj: Scenario | None = self.settings.scenarios.get(scenario) if scenario else None
        # The focal character: an explicit one wins; otherwise the scenario's
        # primary cast member (then its first), so picking a scenario is enough.
        if character is None and scen_obj and scen_obj.cast:
            primary = next((m for m in scen_obj.cast if m.primary), scen_obj.cast[0])
            character = primary.character
        char_obj: Character | None = self.settings.characters.get(character) if character else None

        # Always provide full character + scenario shapes (empty when none is
        # selected) so templates like `{{ character.system }}` / `{{ scenario.setting }}`
        # never hit a missing key under strict templating. `cast` resolves each
        # member's character object so a pipeline can address the whole ensemble.
        cast = []
        if scen_obj:
            for m in scen_obj.cast:
                mc = self.settings.characters.get(m.character)
                cast.append({**m.model_dump(), "name": mc.name if mc else m.character,
                             "system": mc.system if mc else "",
                             "fields": mc.fields if mc else {}})
        context: dict[str, Any] = {
            "user_message": user_message,
            "character": (char_obj or Character(name="")).model_dump(),
            "scenario": {**(scen_obj or Scenario(name="")).model_dump(), "cast": cast},
        }

        result = RunResult(pipeline=pipeline_name)
        for step in pipeline.steps:
            if step.when is not None and not is_truthy(render(step.when, context)):
                result.outcomes.append(StepOutcome(step_id=step.id, type=step.type, skipped=True))
                context[step.id] = {"skipped": True}
                continue

            model_key = (chat_model if step.type == "chat" else image_model) or step.model
            system_override: str | None = None
            # The image-prompt generator step is driven by the Prompt Gen config.
            if step.role == "image_prompt" and promptgen and promptgen.get("enabled", True):
                if promptgen.get("model"):
                    model_key = promptgen["model"]
                if promptgen.get("system"):
                    system_override = promptgen["system"]

            # The global chat system prompt applies to conversational chat steps,
            # not to the image-prompt generator (which has its own system).
            prefix = chat_system if (step.type == "chat" and step.role != "image_prompt") else None
            outcome = self._run_step(step, context, on_delta, model_key=model_key,
                                     system_override=system_override, system_prefix=prefix,
                                     init_image=init_image)
            result.outcomes.append(outcome)
            # Expose this step to later templates as `{{ <id>.text|images|data >}}`.
            context[step.id] = {"text": outcome.text, "data": outcome.data, "images": outcome.images}

        return result

    def _run_step(
        self,
        step: Step,
        context: dict[str, Any],
        on_delta: Callable[[str], None] | None,
        *,
        model_key: str,
        system_override: str | None = None,
        system_prefix: str | None = None,
        init_image: bytes | None = None,
    ) -> StepOutcome:
        if model_key not in self.settings.models:
            raise ValueError(f"step '{step.id}' wants model '{model_key}' which isn't configured")
        provider = self._provider(model_key)

        if step.type == "chat":
            assert isinstance(provider, TextProvider)
            system_tmpl = system_override if system_override is not None else step.system
            system = render(system_tmpl, context) if system_tmpl else None
            # Layer the user's global chat system prompt on top of the step/character one.
            if system_prefix and system_prefix.strip():
                system = f"{system_prefix.strip()}\n\n{system}" if system else system_prefix.strip()
            prompt = render(step.prompt, context)
            res: TextResult = provider.generate_text(
                system=system, prompt=prompt, emits=step.emits, on_delta=on_delta
            )
            return StepOutcome(step_id=step.id, type="chat", text=res.text, data=res.data)

        # image step
        assert isinstance(provider, ImageProvider)
        image_prompt = render(step.image_prompt, context)
        negative = render(step.negative_prompt, context) if step.negative_prompt else None
        res_img: ImageResult = provider.generate_image(
            prompt=image_prompt, negative_prompt=negative, init_image=init_image)
        return StepOutcome(step_id=step.id, type="image", images=res_img.images, data=res_img.meta)
