"""Process-wide in-memory state stubs.

Re-exports the per-resource modules so callers can write
``from xijian_api.stubs import characters, interactions, ...``.
"""

from xijian_api.stubs import state
from xijian_api.stubs import (
    assistants,
    audio,
    batches,
    characters,
    chat,
    citations,
    embedding,
    files,
    fine_tuning,
    image,
    interactions,
    memory,
    memory_config,
    protection,
    resources,
    sessions,
    settings,
    video,
    worlds,
)


def seed_all() -> None:
    """Populate the in-memory stores with their default data.

    Called once at app start-up (and again on demand) so endpoints
    that expect at least one record (``char_yuki``,
    ``world_modern_tokyo``) have something to return.
    """
    characters.seed_default()
    interactions.seed_default()
    worlds.seed_default()
    memory.seed_default()
    memory_config.seed_default()  # type: ignore[attr-defined]
    protection.seed_default()
    settings.seed_default()
    # citations module holds no state of its own but exposes its
    # helpers on the package for the chat pipeline to import via
    # ``from xijian_api.stubs import citations``.
    _ = citations
    # ``models`` lives in the routes layer (it has an import-time seed
    # side effect that runs the first time the module is imported).
    # After ``state.reset_for_testing`` the bucket is empty, so re-seed
    # by calling the explicit helper exposed by the route module.
    from xijian_api.routes.models import seed_default_models
    seed_default_models()


__all__ = [
    "state",
    "assistants",
    "audio",
    "batches",
    "characters",
    "chat",
    "citations",
    "embedding",
    "files",
    "fine_tuning",
    "image",
    "interactions",
    "memory",
    "memory_config",
    "protection",
    "resources",
    "sessions",
    "settings",
    "video",
    "worlds",
    "seed_all",
]