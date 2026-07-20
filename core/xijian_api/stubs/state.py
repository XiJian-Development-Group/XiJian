"""Process-wide in-memory state containers for the API stubs.

Each attribute below is a plain ``dict`` (or ``list`` for ``audits``)
mapping resource id → record.  Other tasks (oai-routes, xijian-routes,
websocket) will populate these dicts as their routes are exercised.
The container module itself is intentionally minimal — it only
guarantees that the names exist so ``stubs.state.characters`` etc.
always resolves.

Per ``DESIGN.md`` §10 the buckets are:

============ ===========================================
Key          Shape
============ ===========================================
characters   ``{character_id: dict}``
interactions ``{interaction_id: dict}``
worlds       ``{world_id: dict}``
memory       ``{memory_id: dict}``
protection   ``dict`` — single object (status / settings)
sessions     ``{session_id: dict}``
files        ``{file_id: dict}``
batches      ``{batch_id: dict}``
fine_tuning_jobs ``{job_id: dict}``
assistants   ``{assistant_id: dict}``
threads      ``{thread_id: dict}``
runs         ``{run_id: dict}``
messages     ``{message_id: dict}``
videos       ``{video_id: dict}``
models       ``{model_id: dict}``
audits       list — audit log is append-only
snapshots    ``{snapshot_id: dict}``
import_jobs  ``{job_id: dict}``
============ ===========================================

The API is single-user / local, so a simple container with no
external locking is sufficient — callers can grab the GIL when they
need to perform compound mutations.
"""

from __future__ import annotations

# XiJian extension resources.
characters: dict = {}
interactions: dict = {}
worlds: dict = {}
memory: dict = {}
memory_configs: dict = {}
protection: dict = {}
sessions: dict = {}
snapshots: dict = {}
import_jobs: dict = {}
# A3.2 character state system.  Three buckets mirror the SQL schema in
# the function list v2:
#   character_states      — {character_id: {hunger, thirst, health,
#                                            mood, max_*, last_updated,
#                                            status, status_changed_at}}
#   character_state_configs — {character_id: {*_decay_per_hour,
#                                             low_*_threshold,
#                                             transition_*_seconds,
#                                             behavior_bindings,
#                                             modifiers}}
#   character_state_log   — {log_id: {character_id, field, old_value,
#                                     new_value, reason, ref_id,
#                                     created_at}}
# Append-only log gets bounded inside the stub so the in-memory store
# never grows without bound.
character_states: dict = {}
character_state_configs: dict = {}
character_state_log: dict = {}
# Overload protection: persistent state only (event log, recovery
# state, last trigger info).  Sliding-window samples live inside the
# ``stubs.overload`` module because they are short-lived and not
# meaningful to persist between process restarts.
overload: dict = {}

# A4.1 world event system.  Three buckets mirror the SQL schema in the
# function list v2:
#   world_events             — {event_id: {world_id, kind, name,
#                                         description, trigger_config,
#                                         scene_ref_id, priority,
#                                         is_enabled, cooldown_until,
#                                         created_at}}
#   world_event_instances    — {instance_id: {event_id, world_id,
#                                            fired_at, resolved_at,
#                                            payload, affected_npcs,
#                                            affects_user}}
#   world_event_categories_disabled — {world_id: {category_str}}
#                                          per-world user-controlled
#                                          category toggles (战斗 /
#                                          日常 / 社交 / etc).
world_events: dict = {}
world_event_instances: dict = {}
world_event_categories_disabled: dict = {}

# A4.2 world-manager system.  Five buckets mirror the SQL schema in
# the function list v2:
#   npcs                       — {npc_id: {world_id, name, persona_doc,
#                                          state_json, compute_budget,
#                                          is_alive, activity_tier,
#                                          importance, last_think_at,
#                                          created_at}}
#   npc_scheduling_log         — {log_id: {npc_id, world_id, action,
#                                          from_tier, to_tier, reason,
#                                          created_at}}
#                                         Append-only audit of NPC
#                                         tier transitions for
#                                         compute-budget backtracking.
#   world_compute_config       — {world_id: {total_token_budget,
#                                          active_tier, max_npcs,
#                                          max_active_npcs, updated_at}}
#                                         Per-world compute tiers
#                                         (high_active=3 / low_active=10).
#   world_environment          — {world_id: {weather, time_of_day,
#                                          light_level, ambient_audio,
#                                          env_meta}}
#   world_audit_log            — {log_id: {world_id, action, actor,
#                                          payload, created_at}}
#                                         Append-only operator/system
#                                         event log (reset / patch /
#                                         transition / npc_create / etc).
npcs: dict = {}
npc_scheduling_log: dict = {}
world_compute_config: dict = {}
world_environment: dict = {}
world_audit_log: dict = {}

# A4.3 scene / interaction system.  Three buckets mirror the SQL schema
# in the function list v2:
#   pois                 — {poi_id: {world_id, parent_id, name, kind,
#                                  coords, description}}
#                                 Three-level hierarchy: parent_id=None
#                                 ⇒ map (e.g. "提瓦特大陆"), parent is
#                                 a map ⇒ region (e.g. "蒙德"), parent
#                                 is a region ⇒ POI (e.g. "天使的馈赠").
#   travel_modes         — {mode_id: {world_id, name, speed_factor,
#                                     stamina_cost, event_chance}}
#                                 Per-world transport options.
#                                 ``speed_factor`` multiplies a base
#                                 travel time; ``stamina_cost`` is the
#                                 flat deduction the character takes;
#                                 ``event_chance`` is the per-trip
#                                 random-event probability.
#   scene_interactions   — {interaction_id: {world_id, poi_id,
#                                            target_type, target_id,
#                                            action, effects, cooldown_sec}}
#                                 Scene-level interactions: the user
#                                 triggers an action against an NPC /
#                                 object / mechanism at a POI.  ``cooldown_sec``
#                                 blocks re-triggering for a window so
#                                 farming / exploitation is bounded.
pois: dict = {}
travel_modes: dict = {}
scene_interactions: dict = {}

# A4.4 economy system.  Four buckets mirror the SQL schema in the
# function list v2:
#   world_currencies        — {(world_id, code): {world_id, code,
#                                            name, symbol, decimals}}
#                                Per-world currency definitions.
#                                Composite key because spec says
#                                PRIMARY KEY(world_id, code).
#   wallets                — {(owner_kind, owner_id, world_id,
#                              currency_code): {..., balance}}
#                                Balance sheet: every user / NPC
#                                has one wallet per (world, currency).
#                                Composite key keeps lookups O(1).
#   transactions           — {transaction_id: {world_id, from_kind,
#                                              from_id, to_kind, to_id,
#                                              currency_code, amount,
#                                              kind, ref_id, created_at}}
#                                Append-only money-movement log.
#                                Every wallet mutation must write
#                                one of these (AC-1).
#   world_economy_state    — {world_id: {world_id, inflation_rate,
#                                        liquidity_index, last_tick_at,
#                                        allow_illegal, allow_overdraft,
#                                        updated_at}}
#                                Per-world macro state plus the
#                                per-world toggles AC-3 cares about
#                                (allow_illegal, allow_overdraft).
world_currencies: dict = {}
wallets: dict = {}
transactions: dict = {}
world_economy_state: dict = {}

# A5.1 output-safety system.  Two buckets mirror the SQL schema in
# the function list v2:
#   safety_audit_log    — {log_id: {id, character_id, world_id,
#                                     stage, verdict, reason,
#                                     snippet, created_at}}
#                                Append-only per-event audit.  Every
#                                scan (input pre-screen / output
#                                post-screen) lands one record so
#                                the operator can answer "what did
#                                the safety layer do?" without
#                                grepping through observability
#                                tooling.
#                                AC-3 ("所有拦截事件必须可查询")
#                                is satisfied by ``list_log``.
#   safety_rules        — {rule_id: {id, rule_kind, pattern,
#                                     severity, is_active}}
#                                The hot-path rulebook.  Each rule
#                                is one of three flavours
#                                (ooc_pattern / injection_pattern /
#                                forbidden_word) and carries a
#                                1..5 severity.  Inactive rules
#                                are skipped without being deleted
#                                so operators can A/B.
safety_audit_log: dict = {}
safety_rules: dict = {}

# A5.2 computer-control protection (MCP 防护).  Four buckets mirror
# the SQL schema in the function list v2:
#   mcp_rules        — {rule_id: {id, action_kind, pattern, mode,
#                                  severity, is_active, created_at,
#                                  updated_at}}
#                                The MCP rulebook — operator-curated
#                                blacklist / whitelist that the
#                                ``check()`` gate consults before any
#                                desktop-control action is taken.
#                                action_kind is one of file_delete /
#                                file_write / file_read / shell /
#                                network / app_launch /
#                                settings_modify / system_cmd.
#                                mode=blacklist says "block on hit";
#                                mode=whitelist says "only allow on
#                                hit" (the per-world policy picks
#                                the overall default when no rule
#                                matches).
#   mcp_audit        — {log_id: {id, action_kind, args_summary,
#                                  verdict, rule_id, world_id,
#                                  created_at}}
#                                Append-only per-call audit.  Every
#                                ``check()`` lands one entry so
#                                AC-1 ("黑名单动作 100% 拦截")
#                                is observable: blocked actions
#                                show up here with verdict=denied.
#   mcp_freezes      — {freeze_id: {id, reason, requested_at,
#                                    confirmed_at, cancelled_at,
#                                    snapshot_id, status, lockout_at,
#                                    lockout_count, source,
#                                    restore_summary}}
#                                The safety-stop state machine.
#                                A5.2 US-A5.2-02 path: the global
#                                hotkey triggers a freeze, the
#                                server-side orchestrator (this
#                                bucket) tracks the lifecycle
#                                (frozen → awaiting_confirm →
#                                sanitizing → restored / cancelled).
#                                Three safety_stops within 60 s
#                                flip the world to status=lockout
#                                and refuse further freezes until
#                                a cold restart reset.
#   mcp_snapshots    — {snap_id: {id, world_id, scope, file_path,
#                                  size_bytes, reason, includes_protected,
#                                  sanitized, created_at, payload}}
#                                The "专用备份文件夹" payloads.
#                                file_path is server-controlled
#                                (operator cannot inject paths via
#                                the request); payload is the
#                                in-memory world/character/memory/
#                                session bundle at dump time.  AC-4
#                                ("受保护模块覆盖") is satisfied by
#                                the dump always including the
#                                state.{worlds,characters,memory,
#                                sessions} dicts.
mcp_rules: dict = {}
mcp_audit: dict = {}
mcp_freezes: dict = {}
mcp_snapshots: dict = {}

# Developer Kit (C5) state lives in ``xijian_api.devkit.state`` — the
# DevKit is a stand-alone Pywebview application that does not share a
# Flask server with the main API, so its buckets are intentionally
# kept out of this module.

# OAI-compatible resources.
files: dict = {}
batches: dict = {}
fine_tuning_jobs: dict = {}
assistants: dict = {}
threads: dict = {}
runs: dict = {}
messages: dict = {}
videos: dict = {}
models: dict = {}

# Audit log is append-only.
audits: list = []


def reset_for_testing() -> None:
    """Wipe every bucket (used by tests).

    Each call clears the underlying objects in-place so that other
    modules that imported references (e.g. ``from xijian_api.stubs
    import state``) keep pointing at the same (now empty) containers.

    The reset is also followed by a re-seed via :func:`xijian_api.stubs.seed_all`
    so tests that depend on ``char_yuki`` / ``world_modern_tokyo`` /
    default memory entries have them in place.  Lazy import to avoid
    a circular import at module-load time.
    """
    characters.clear()
    interactions.clear()
    worlds.clear()
    memory.clear()
    memory_configs.clear()
    protection.clear()
    sessions.clear()
    snapshots.clear()
    import_jobs.clear()
    character_states.clear()
    character_state_configs.clear()
    character_state_log.clear()
    overload.clear()
    world_events.clear()
    world_event_instances.clear()
    world_event_categories_disabled.clear()
    # A4.2 buckets.
    npcs.clear()
    npc_scheduling_log.clear()
    world_compute_config.clear()
    world_environment.clear()
    world_audit_log.clear()
    # A4.3 buckets.
    pois.clear()
    travel_modes.clear()
    scene_interactions.clear()
    # A4.4 buckets.
    world_currencies.clear()
    wallets.clear()
    transactions.clear()
    world_economy_state.clear()
    # A5.1 buckets.
    safety_audit_log.clear()
    safety_rules.clear()
    # A5.2 MCP-protection buckets.
    mcp_rules.clear()
    mcp_audit.clear()
    mcp_freezes.clear()
    mcp_snapshots.clear()
    files.clear()
    batches.clear()
    fine_tuning_jobs.clear()
    assistants.clear()
    threads.clear()
    runs.clear()
    messages.clear()
    videos.clear()
    models.clear()
    audits.clear()

    # Re-seed defaults so each test starts from a known state.
    from xijian_api.stubs import seed_all
    seed_all()


__all__ = [
    "characters",
    "interactions",
    "worlds",
    "memory",
    "memory_configs",
    "protection",
    "sessions",
    "snapshots",
    "import_jobs",
    "character_states",
    "character_state_configs",
    "character_state_log",
    "overload",
    "world_events",
    "world_event_instances",
    "world_event_categories_disabled",
    "npcs",
    "npc_scheduling_log",
    "world_compute_config",
    "world_environment",
    "world_audit_log",
    "pois",
    "travel_modes",
    "scene_interactions",
    "world_currencies",
    "wallets",
    "transactions",
    "world_economy_state",
    "safety_audit_log",
    "safety_rules",
    "mcp_rules",
    "mcp_audit",
    "mcp_freezes",
    "mcp_snapshots",
    "files",
    "batches",
    "fine_tuning_jobs",
    "assistants",
    "threads",
    "runs",
    "messages",
    "videos",
    "models",
    "audits",
    "reset_for_testing",
]