"""Main chat — the standalone roleplay surface and its reusable *chat configs*.

A chat config bundles a text model + system prompt + default lorebooks + creativity
level. One is active at a time and the chat reads it as its default; the ⚙ config
modal (used at every chat surface) edits this library at the point of use rather than
on a settings page.

  GET    /api/chat-configs              → { active, configs:[...] }
  POST   /api/chat-configs              → upsert one config (by id) → full library
  DELETE /api/chat-configs/{id}         → delete a config (never the last one)
  POST   /api/chat-configs/{id}/activate→ make a config active
  POST   /api/chat                      → STREAM the next chat turn (SSE: delta + done)
"""
from __future__ import annotations

import json
import re

from fastapi.responses import JSONResponse

from ..services import config_files as _cf


def _compose_system(ctx, cfg: dict, character: str | None, persona: dict | None,
                    recent: str, lorebooks: list[str]) -> str:
    """Build the chat system prompt: config system + character persona + who-you-are +
    a retrieved WORLD INFO block from the attached lorebooks (and the character's book)."""
    from ..services import lorebook_store as _LS
    from ..services.lorebook import format_lore_block

    parts: list[str] = []
    if cfg.get("system"):
        parts.append(cfg["system"].strip())

    ch = ctx.base_settings.characters.get(character) if character else None
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

    system = "\n\n".join(p for p in parts if p) or "You are a helpful, engaging conversational partner."

    # Canon retrieval: the attached world/RPG books + the character's own book scope.
    scopes = [re.sub(r"[^\w\-]+", "_", str(s)) for s in (lorebooks or []) if s]
    if character:
        scopes.append(re.sub(r"[^\w\-]+", "_", str(character)))
    if scopes:
        hits = _LS.retrieve(ctx.root, recent, scopes, top_k=6)
        if hits:
            system = system + "\n\n" + format_lore_block(hits)
    return system


def register(app, ctx):
    # ── chat configs ─────────────────────────────────────────────────────────
    @app.get("/api/chat-scripts")
    def get_chat_scripts() -> dict:
        """The built-in pipeline scripts a config can bind to (story-builder stages,
        prompt configs, image roles) — drives the modal's Script picker."""
        return {"scripts": _cf.SCRIPTS}

    @app.get("/api/chat-configs")
    def get_chat_configs() -> dict:
        return _cf.load_chat_configs(ctx.root)

    @app.post("/api/chat-configs")
    def upsert_chat_config(body: dict):
        """Create or update one config (matched by `id`). A blank/absent id mints a new one."""
        body = body or {}
        lib = _cf.load_chat_configs(ctx.root)
        cid = (body.get("id") or "").strip()
        if not cid:
            existing = {c["id"] for c in lib["configs"]}
            base, n, cid = "config", 2, "config"
            while cid in existing:
                cid, n = f"{base}-{n}", n + 1
        body["id"] = cid
        clean = _cf._clean_chat_config(body)
        configs = [c for c in lib["configs"] if c["id"] != cid]
        configs.append(clean)
        out = _cf.save_chat_configs(ctx.root, {"active": lib["active"], "configs": configs})
        return {"ok": True, **out, "id": cid}

    @app.delete("/api/chat-configs/{config_id}")
    def delete_chat_config(config_id: str):
        lib = _cf.load_chat_configs(ctx.root)
        if len(lib["configs"]) <= 1:
            return JSONResponse({"error": "can't delete the last config"}, status_code=400)
        configs = [c for c in lib["configs"] if c["id"] != config_id]
        active = lib["active"] if lib["active"] != config_id else configs[0]["id"]
        out = _cf.save_chat_configs(ctx.root, {"active": active, "configs": configs})
        return {"ok": True, **out}

    @app.post("/api/chat-configs/{config_id}/activate")
    def activate_chat_config(config_id: str):
        lib = _cf.load_chat_configs(ctx.root)
        if config_id not in {c["id"] for c in lib["configs"]}:
            return JSONResponse({"error": "no such config"}, status_code=404)
        out = _cf.save_chat_configs(ctx.root, {"active": config_id, "configs": lib["configs"]})
        return {"ok": True, **out}

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
        cfg = _cf.active_chat_config(ctx.root, body.get("config"))
        model = (body.get("model") or "").strip() or cfg.get("model") or None
        provider = ctx.text_provider_for(model, cfg.get("params") or {})
        if provider is None or not hasattr(provider, "generate_text"):
            return JSONResponse({"error": "no chat connection — connect a chat model first"}, status_code=400)

        history = [m for m in (body.get("history") or []) if isinstance(m, dict)]
        lorebooks = body.get("lorebooks")
        if lorebooks is None:
            lorebooks = cfg.get("lorebooks") or []
        character = body.get("character")
        persona = body.get("persona") or {}

        # Name the two sides of the transcript so the model has a clear voice to continue.
        ch = ctx.base_settings.characters.get(character) if character else None
        bot_name = (ch.name if ch else None) or "Assistant"
        you_name = (persona.get("name") or "You").strip() or "You"

        recent = " ".join(str(m.get("content", "")) for m in history[-3:])
        system = _compose_system(ctx, cfg, character, persona, recent, lorebooks)

        if history:
            lines = [f"{(you_name if m.get('role') == 'user' else bot_name)}: {m.get('content', '')}"
                     for m in history]
            prompt = "\n".join(lines) + f"\n{bot_name}:"
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
