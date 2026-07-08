"""Load and validate a Loom project from a configs/ directory.

Layout (all paths relative to the project root passed in):

    configs/
      models.yaml            # { models: { <key>: ModelDef, ... } }  or bare mapping
      characters/*.yaml      # one Character per file (filename stem = key)
      pipelines/*.yaml       # one Pipeline per file (filename stem = key)

Keeping characters and pipelines as one-file-per-resource keeps diffs clean and
makes a future visual editor trivial: each canvas/persona maps to a file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import Character, LoraConfig, ModelDef, Persona, Pipeline, Settings, Story, UserConfig


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return data


def load_settings(root: str | Path) -> Settings:
    root = Path(root)
    configs = root / "configs"
    if not configs.is_dir():
        raise FileNotFoundError(f"no configs/ directory under {root.resolve()}")

    # models.yaml — accept either {models: {...}} or a bare {<key>: {...}} mapping.
    models_doc = _read_yaml(configs / "models.yaml")
    raw_models = models_doc.get("models", models_doc)
    models = {key: ModelDef(**val) for key, val in raw_models.items()}

    characters: dict[str, Character] = {}
    char_dir = configs / "characters"
    if char_dir.is_dir():
        for path in sorted(char_dir.glob("*.yaml")):
            characters[path.stem] = Character(**_read_yaml(path))

    # personas/*.yaml — who *you* are in the chat (the {{user}} side). One Persona per file.
    personas: dict[str, Persona] = {}
    persona_dir = configs / "personas"
    if persona_dir.is_dir():
        for path in sorted(persona_dir.glob("*.yaml")):
            personas[path.stem] = Persona(**_read_yaml(path))

    # stories — ONE self-contained <key>.json per story. A story file EMBEDS its characters and is
    # AUTHORITATIVE for them: merge those into the character library, overriding any global file of
    # the same key. Any legacy <key>.db is auto-migrated to .json on load (see migrate_db_to_json).
    from ..stories import story_db as _SDB
    stories: dict[str, Story] = {}
    story_dir = configs / "stories"
    if story_dir.is_dir():
        # Lazy one-time migration: convert any legacy .db to .json before enumerating.
        from ..stories.migrate_db_to_json import migrate_dir as _migrate
        _migrate(story_dir)
        for path in sorted(story_dir.glob("*.json")):
            sdata, embedded = _SDB.load_story(path)
            stories[path.stem] = Story(**sdata)
            for ck, cdoc in embedded.items():
                try:
                    characters[ck] = Character(**cdoc)   # story file authoritative for its cast
                except Exception:  # noqa: BLE001 — a bad embedded card never sinks the load
                    pass

    pipelines: dict[str, Pipeline] = {}
    pipe_dir = configs / "pipelines"
    if pipe_dir.is_dir():
        for path in sorted(pipe_dir.glob("*.yaml")):
            pipelines[path.stem] = Pipeline(**_read_yaml(path))

    # loras.yaml — the self-contained LoRA subsystem (optional): { library, stacks }.
    loras = LoraConfig()
    loras_path = configs / "loras.yaml"
    if loras_path.is_file():
        doc = _read_yaml(loras_path)
        loras = LoraConfig(**doc)

    # Settings' validators cross-check every step→model reference here.
    return Settings(models=models, characters=characters, personas=personas,
                    stories=stories, pipelines=pipelines, loras=loras)


def load_user(root: str | Path) -> UserConfig:
    """Load <root>/user.yaml (identity + local-tool settings). Returns defaults
    if absent."""
    path = Path(root) / "user.yaml"
    if not path.is_file():
        return UserConfig()
    return UserConfig(**_read_yaml(path))
