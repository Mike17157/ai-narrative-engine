"""Read-only projections of the mutable state of a story playthrough."""

from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app, ctx) -> None:
    """Attach endpoints that expose a play session's current state."""

    @app.get("/api/stories/{key}/state-card")
    def story_state_card(key: str, sid: str = ""):
        """Return the derived state card and the session's live card evolution."""
        from ...server.services.story_sessions import load_session
        from .. import state_engine as state
        from ..storymaster import state_card

        story = ctx.base_settings.stories.get(key)
        if story is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        live_sid = sid or f"play-{key}"
        session = load_session(ctx.root, live_sid) or {}
        world = state.world_of(session.get("state"))
        try:
            from ...server.services import story_store
            evolution = story_store.get_play_cards(ctx.root, key, live_sid)
        except Exception:  # noqa: BLE001 — a derived state card remains useful offline
            evolution = {}
        return {"card": state_card(world, story), "step": world.get("step"),
                "revision": world.get("revision"), "evolution": evolution}

    @app.get("/api/stories/{key}/live-cards")
    def story_live_cards(key: str, sid: str = "", kind: str = "", card_key: str = ""):
        """Return session-scoped mutable cards and optional evidence for one card."""
        from ...server.services import story_store

        if ctx.base_settings.stories.get(key) is None:
            return JSONResponse({"error": "no such story"}, status_code=404)
        live_sid = sid or f"play-{key}"
        history = (story_store.get_card_history(ctx.root, key, live_sid, kind, card_key)
                   if kind and card_key else [])
        return {"cards": story_store.get_play_cards(ctx.root, key, live_sid), "history": history}
