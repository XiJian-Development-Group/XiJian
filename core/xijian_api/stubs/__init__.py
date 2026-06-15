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
    embedding,
    files,
    fine_tuning,
    image,
    interactions,
    memory,
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
    protection.seed_default()
    settings.seed_default()


__all__ = [
    "state",
    "assistants",
    "audio",
    "batches",
    "characters",
    "chat",
    "embedding",
    "files",
    "fine_tuning",
    "image",
    "interactions",
    "memory",
    "protection",
    "resources",
    "sessions",
    "settings",
    "video",
    "worlds",
    "seed_all",
]