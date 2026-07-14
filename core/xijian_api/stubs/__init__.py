"""Process-wide in-memory state stubs.

Re-exports the per-resource modules so callers can write
``from xijian_api.stubs import characters, interactions, ...``.
"""

from xijian_api.stubs import state
from xijian_api.stubs import (
    assistants,
    audio,
    batches,
    character_state,
    characters,
    chat,
    citations,
    economy,
    embedding,
    events,
    files,
    fine_tuning,
    image,
    interactions,
    memory,
    memory_config,
    npcs,
    overload,
    pois,
    protection,
    resources,
    scene_interactions,
    sessions,
    settings,
    transactions,
    travel_modes,
    video,
    wallets,
    world_audit,
    world_compute_config,
    world_currencies,
    world_economy_state,
    world_environment,
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
    # Worlds are seeded *first* so the related per-world buckets
    # (environment, compute_config) can materialise their lazy
    # defaults against an existing world record.
    worlds.seed_default()
    # ``npcs.seed_default`` registers the A5.4 overload handler and
    # starts the background tick thread (if env allows).  It does
    # NOT seed any default NPCs — operators create them.
    npcs.seed_default()
    memory.seed_default()
    memory_config.seed_default()  # type: ignore[attr-defined]
    protection.seed_default()
    settings.seed_default()
    overload.seed_default()
    character_state.seed_default()
    events.seed_default()
    # A4.3 scene system — no default POIs / travel modes / scene
    # interactions; the world library is operator-curated.  We still
    # call the seed hooks so future additions have a stable entry point.
    pois.seed_default()
    travel_modes.seed_default()
    scene_interactions.seed_default()
    # A4.4 economy — no default currencies / wallets / transactions;
    # operators define currencies per world and grant initial balances
    # through the route layer.  We still call the seed hooks so
    # future additions have a stable entry point.
    world_currencies.seed_default()
    world_economy_state.seed_default()
    wallets.seed_default()
    transactions.seed_default()
    economy.seed_default()
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
    "character_state",
    "characters",
    "chat",
    "citations",
    "economy",
    "embedding",
    "events",
    "files",
    "fine_tuning",
    "image",
    "interactions",
    "memory",
    "memory_config",
    "npcs",
    "overload",
    "pois",
    "protection",
    "resources",
    "scene_interactions",
    "sessions",
    "settings",
    "transactions",
    "travel_modes",
    "video",
    "wallets",
    "world_audit",
    "world_compute_config",
    "world_currencies",
    "world_economy_state",
    "world_environment",
    "worlds",
    "seed_all",
]