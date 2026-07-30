"""SQLite-backed state containers for the API stubs.

Every attribute below is a :class:`xijian_api.store.DictDB` instance
mapping resource id → record, persisted to ``~/.xijian/xijian.db``.
The API is identical to the previous in-memory dict interface, so
existing stubs work unchanged.
"""

from __future__ import annotations

from typing import Any

from xijian_api.store import DictDB, bucket


# Persisted key-value buckets (one SQLite table per bucket)
characters: DictDB = bucket("characters")
interactions: DictDB = bucket("interactions")
worlds: DictDB = bucket("worlds")
memory: DictDB = bucket("memory")
memory_configs: DictDB = bucket("memory_configs")
sessions: DictDB = bucket("sessions")
snapshots: DictDB = bucket("snapshots")
import_jobs: DictDB = bucket("import_jobs")
character_states: DictDB = bucket("character_states")
character_state_configs: DictDB = bucket("character_state_configs")
character_state_log: DictDB = bucket("character_state_log")
character_models: DictDB = bucket("character_models")
character_motions: DictDB = bucket("character_motions")
character_voices: DictDB = bucket("character_voices")
character_handwritings: DictDB = bucket("character_handwritings")
character_styles: DictDB = bucket("character_styles")
character_asset_cache: DictDB = bucket("character_asset_cache")
world_events: DictDB = bucket("world_events")
world_event_instances: DictDB = bucket("world_event_instances")
npcs: DictDB = bucket("npcs")
npc_scheduling_log: DictDB = bucket("npc_scheduling_log")
world_compute_config: DictDB = bucket("world_compute_config")
world_environment: DictDB = bucket("world_environment")
world_audit_log: DictDB = bucket("world_audit_log")
pois: DictDB = bucket("pois")
travel_modes: DictDB = bucket("travel_modes")
scene_interactions: DictDB = bucket("scene_interactions")
world_currencies: DictDB = bucket("world_currencies")
wallets: DictDB = bucket("wallets")
transactions: DictDB = bucket("transactions")
world_economy_state: DictDB = bucket("world_economy_state")
safety_audit_log: DictDB = bucket("safety_audit_log")
safety_rules: DictDB = bucket("safety_rules")
mcp_rules: DictDB = bucket("mcp_rules")
mcp_audit: DictDB = bucket("mcp_audit")
mcp_freezes: DictDB = bucket("mcp_freezes")
mcp_snapshots: DictDB = bucket("mcp_snapshots")
safety_snapshots: DictDB = bucket("safety_snapshots")
backup_policies: DictDB = bucket("backup_policies")
files: DictDB = bucket("files")
batches: DictDB = bucket("batches")
fine_tuning_jobs: DictDB = bucket("fine_tuning_jobs")
assistants: DictDB = bucket("assistants")
threads: DictDB = bucket("threads")
runs: DictDB = bucket("runs")
messages: DictDB = bucket("messages")
videos: DictDB = bucket("videos")
models: DictDB = bucket("models")


# In-memory special buckets (not suited for key-value SQL)
safety_state: dict[str, Any] = {}
overload: dict[str, Any] = {}
audits: list[dict[str, Any]] = []

# world_event_categories_disabled uses set values — stored via a
# dedicated DictDB bucket with list↔set conversion.
_world_event_categories_db: DictDB = bucket("world_event_categories_disabled")


def _load_categories() -> dict[str, set[str]]:
    d = {}
    for key in list(_world_event_categories_db.keys()):
        raw = _world_event_categories_db[key]
        d[key] = set(raw) if isinstance(raw, list) else set()
    return d


def _save_categories(d: dict[str, set[str]]) -> None:
    _world_event_categories_db.clear()
    for key, val in d.items():
        _world_event_categories_db[key] = list(val)


world_event_categories_disabled: dict[str, set[str]] = _load_categories()


# ---------------------------------------------------------------------------
# Reset & seed — called between tests
# ---------------------------------------------------------------------------


def reset_for_testing() -> None:
    """Wipe every bucket and re-seed defaults.

    DictDB buckets are cleared (cache + SQLite table truncated).
    In-memory buckets are cleared directly.
    """
    _all_dictdb = [
        characters, interactions, worlds, memory, memory_configs,
        sessions, snapshots, import_jobs,
        character_states, character_state_configs, character_state_log,
        character_models, character_motions, character_voices,
        character_handwritings, character_styles, character_asset_cache,
        world_events, world_event_instances,
        npcs, npc_scheduling_log, world_compute_config, world_environment,
        world_audit_log,
        pois, travel_modes, scene_interactions,
        world_currencies, wallets, transactions, world_economy_state,
        safety_audit_log, safety_rules,
        mcp_rules, mcp_audit, mcp_freezes, mcp_snapshots,
        safety_snapshots, backup_policies,
        files, batches, fine_tuning_jobs,
        assistants, threads, runs, messages, videos, models,
    ]
    for db in _all_dictdb:
        db.clear()

    safety_state.clear()
    overload.clear()
    audits.clear()
    _world_event_categories_db.clear()
    world_event_categories_disabled.clear()

    from xijian_api.stubs import seed_all
    seed_all()


# Reload categories after seed_all in case seed_all populated them
world_event_categories_disabled.update(_load_categories())


__all__ = [k for k, v in list(locals().items())
           if isinstance(v, (DictDB, dict, list)) and not k.startswith("_")] + [
    "reset_for_testing",
]
