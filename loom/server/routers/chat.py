"""Main chat — the standalone roleplay surface.

The chat turn is driven by a PRESET (the unified primitive: chat model + image workflow
+ lorebooks), resolved Function→Lorebook→Preset or the active preset. The old parallel
chat_configs/SCRIPTS library was retired in the preset-unification overhaul.

  POST   /api/chat   → STREAM the next chat turn (SSE: delta + done)
"""
from __future__ import annotations

import json
import re

from fastapi.responses import JSONResponse

from ..services import config_files as _cf
from ..services import presets as _presets


def _compose_system(ctx, cfg: dict, character: str | None, persona: dict | None,
                    recent: str, lorebooks: list[str], *, mode: str = "roleplay",
                    artifact=None, artifact_label: str = "WORKING DOCUMENT",
                    context: dict | None = None) -> str:
    """Build the chat system prompt. Two ADDRESS modes:

    - roleplay: the model BECOMES the character — in-character dialogue (the standalone
      chat surface). "You are {name}", greet in voice, persona is who you ARE.
    - assist:   the model is a developmental COLLABORATOR working WITH the writer ON the
      {artifact_label}. It never inhabits a character; the character is reference SUBJECT
      matter and the document is the thing being built. This is what story-builder /
      script-bound flows use — it stops the consultant from roleplaying.

    A retrieved WORLD INFO block from the attached lorebooks (+ the character's book) is
    appended in both modes."""
    from ..services import lorebook_store as _LS
    from ..services.lorebook import format_lore_block

    context = context or {}
    ch = ctx.base_settings.characters.get(character) if character else None
    parts: list[str] = []

    if mode == "assist":
        # Consultant frame FIRST — establishes the model is not a character.
        parts.append(
            f"You are a developmental collaborator helping a writer build and refine the "
            f"{artifact_label} shown below. You are NOT a character in a story and you do "
            f"not roleplay or speak in any character's voice — you talk WITH the writer, as "
            f"a sharp, opinionated craft partner discussing the work itself. Reference "
            f"characters in the third person; never act as one. Keep replies conversational "
            f"and substantive (a few short paragraphs), not bullet dumps."
        )
        if cfg.get("system"):
            parts.append(cfg["system"].strip())   # the script's own rules
        if ch is not None and context.get("include_persona", True):
            persona_txt = (ch.system or "").strip()
            parts.append(f"The character this work centers on is {ch.name} — reference only, "
                         f"do not become them:" + (f"\n{persona_txt}" if persona_txt else ""))
        # The working document is the primary context in assist mode.
        if artifact not in (None, "", {}, []):
            parts.append(
                f"CURRENT {artifact_label} (the writer may have edited it; treat their edits "
                f"as authoritative and build on them). Structural changes are applied through "
                f"the available functions — discuss and propose them in prose, do not paste raw "
                f"JSON back:\n{json.dumps(artifact, ensure_ascii=False)}")
    else:
        if cfg.get("system"):
            parts.append(cfg["system"].strip())
        if ch is not None:
            persona_txt = (ch.system or "").strip()
            parts.append(f"You are {ch.name}." + (f"\n{persona_txt}" if persona_txt else ""))
            if ch.greeting:
                parts.append(f"Your established opening / voice: {ch.greeting.strip()}")
        if persona:
            nm = (persona.get("name") or "").strip()
            desc = (persona.get("description") or "").strip()
            if nm:
                parts.append(f"You are speaking with {nm}" + (f" — {desc}" if desc else "") + ".")

    fallback = ("You are a developmental collaborator helping a writer with their document."
                if mode == "assist" else "You are a helpful, engaging conversational partner.")
    system = "\n\n".join(p for p in parts if p) or fallback

    # Canon retrieval: the attached world/RPG books + the character's own book scope.
    top_k = int(context.get("lore_top_k") or 6)
    scopes = [re.sub(r"[^\w\-]+", "_", str(s)) for s in (lorebooks or []) if s]
    if character:
        scopes.append(re.sub(r"[^\w\-]+", "_", str(character)))
    if scopes and top_k > 0:
        hits = _LS.retrieve(ctx.root, recent, scopes, top_k=top_k, allow_nsfw=ctx.allow_nsfw())
        # Function-book entries are operations, not world facts — never inject their specs.
        from ...stories import graph_ops as _GO
        hits = [e for e in hits if not _GO.is_function_entry(e)]
        if hits:
            system = system + "\n\n" + format_lore_block(hits)
    return system


def register(app, ctx):
    # ── app-wide flags (global content gate, etc.) ───────────────────────────
    @app.get("/api/app-flags")
    def get_app_flags() -> dict:
        return _cf.load_app_flags(ctx.root)

    @app.put("/api/app-flags")
    def put_app_flags(body: dict):
        return {"ok": True, **_cf.save_app_flags(ctx.root, body or {})}

    # ── the chat turn ────────────────────────────────────────────────────────
    @app.post("/api/chat")
    async def chat_turn(body: dict):
        """Stream the assistant's next turn over SSE. Body:
          { history:[{role,content}], character?, persona?:{name,description},
            config?:<id>, model?:<override>, lorebooks?:[ids] }
        Resolves the active chat config for defaults; an explicit model/lorebooks override it."""
        import asyncio
        import threading

        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import StreamingResponse

        body = body or {}
        # Every chat turn is driven by a PRESET. Resolution order:
        #   1. a preset BOUND to an attached lorebook (Function → Lorebook → Preset) — function flows
        #   2. an explicit body.preset (a surface chose one)
        #   3. the ACTIVE preset (free chat's default)
        # No chat-config path anymore — presets are the single source of the model side.
        req_books = body.get("lorebooks")
        bound = _presets.preset_for_books(ctx.root, req_books) if req_books else None
        preset = bound \
            or _presets.get_preset(ctx.root, (body.get("preset") or "").strip() or None) \
            or _presets.active_preset(ctx.root)
        # A function-bound preset auto-resolves to ASSIST mode; a free/explicit one to ROLEPLAY
        # (script is only an auto-mode hint here — the preset's explicit mode always wins).
        cfg = {"id": "preset:" + preset["id"], "name": preset.get("name", ""),
               "system": preset.get("system", ""), "params": preset.get("params") or {},
               "mode": preset.get("mode", ""), "model": preset.get("model", ""),
               "connection": preset.get("connection", ""),
               "author_note": preset.get("author_note", ""), "author_depth": preset.get("author_depth", 4),
               "post_history": preset.get("post_history", ""), "stop": preset.get("stop") or [],
               "reasoning_effort": preset.get("reasoning_effort", ""),
               "lorebooks": req_books or [], "script": "preset" if bound else "", "context": {}}
        model = (body.get("model") or "").strip() or cfg.get("model") or None
        # Sampling params + the non-numeric provider options (stop / reasoning) ride together.
        opts = dict(cfg.get("params") or {})
        if cfg.get("stop"):
            opts["stop"] = cfg["stop"]
        if cfg.get("reasoning_effort"):
            opts["reasoning_effort"] = cfg["reasoning_effort"]
        provider = ctx.text_provider_for(model, opts, connection=cfg.get("connection") or None)
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no chat connection — connect a chat model first"}, status_code=400)

        history = [m for m in (body.get("history") or []) if isinstance(m, dict)]
        lorebooks = body.get("lorebooks")
        if lorebooks is None:
            lorebooks = cfg.get("lorebooks") or []
        character = body.get("character")
        persona = body.get("persona") or {}
        mode = _cf.address_mode(cfg)
        context = cfg.get("context") or {}
        artifact = body.get("artifact")
        artifact_label = (body.get("artifact_label") or "WORKING DOCUMENT").strip()

        # Trim history to the configured context window (0/blank = keep all).
        hist_turns = int(context.get("history_turns") or 0)
        if hist_turns > 0:
            history = history[-hist_turns:]

        recent = " ".join(str(m.get("content", "")) for m in history[-3:])
        system = _compose_system(ctx, cfg, character, persona, recent, lorebooks,
                                 mode=mode, artifact=artifact, artifact_label=artifact_label,
                                 context=context)

        if mode == "assist":
            # The two sides are the writer and the collaborator — NOT in-character voices.
            bot_name, you_name = "Assistant", "Writer"
        else:
            # Name the two sides of the transcript so the model has a clear voice to continue.
            ch = ctx.base_settings.characters.get(character) if character else None
            bot_name = (ch.name if ch else None) or "Assistant"
            you_name = (persona.get("name") or "You").strip() or "You"
            # Roleplay can still carry a working artifact in context (rare).
            if artifact not in (None, "", {}, []):
                system = system + f"\n\nCURRENT {artifact_label} (the writer may have edited it; treat " \
                    f"their edits as authoritative):\n{json.dumps(artifact, ensure_ascii=False)}"

        # Prompt-slot injections (positions in the assembled prompt):
        #   author_note  → inserted into the history `author_depth` turns from the end (close to
        #                  the generation point → strong steer)
        #   post_history → appended AFTER the whole history, the last thing before the reply
        author_note = (cfg.get("author_note") or "").strip()
        author_depth = int(cfg.get("author_depth") or 0)
        post_history = (cfg.get("post_history") or "").strip()

        if history:
            lines = [f"{(you_name if m.get('role') == 'user' else bot_name)}: {m.get('content', '')}"
                     for m in history]
            if author_note:
                lines.insert(max(0, len(lines) - author_depth), f"[{author_note}]")
            prompt = "\n".join(lines)
            if post_history:
                prompt += f"\n[{post_history}]"
            prompt += f"\n{bot_name}:"
        elif mode == "assist":
            # Opening turn — give the writer a developmental read of the document, no greeting.
            prompt = (f"Open the working session: give the writer your sharp first read of the "
                      f"current {artifact_label} and where it can go. Do not greet in character.\n{bot_name}:")
        else:
            # Opening turn — let the assistant greet in character.
            prompt = f"Begin the conversation. Greet {you_name} in character.\n{bot_name}:"

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        cancel_evt = threading.Event()

        def on_delta(t: str):
            loop.call_soon_threadsafe(q.put_nowait, {"type": "delta", "text": t})

        async def run():
            try:
                await run_in_threadpool(lambda: provider.generate_text(
                    system=system, prompt=prompt, on_delta=on_delta, cancel=cancel_evt.is_set))
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(exc)})
            loop.call_soon_threadsafe(q.put_nowait, None)

        asyncio.create_task(run())

        async def events():
            try:
                while True:
                    ev = await q.get()
                    if ev is None:
                        break
                    yield f"data: {json.dumps(ev)}\n\n"
            finally:
                cancel_evt.set()
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")
