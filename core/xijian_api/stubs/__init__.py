"""Process-wide in-memory state stubs. 进程级内存存根。

Re-exports the per-resource modules so callers can write
``from xijian_api.stubs import characters, interactions, ...``.
重新导出各资源模块，以便调用者可以写
``from xijian_api.stubs import characters, interactions, ...``。
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
    mcp,
    mcp_rules,
    memory,
    snapshots,
    memory_config,
    manual_backups,
    npcs,
    overload,
    pois,
    resources,
    safety,
    safety_rules,
    scene_interactions,
    sessions,
    settings,
    tts_guard,
    transactions,
    travel_modes,
    video,
    multimodal,
    wallets,
    world_audit,
    world_compute_config,
    world_currencies,
    world_economy_state,
    world_environment,
    worlds,
    # A6 / A7 / A8 modules (added 2026-08-01).
    voice_calls,
    character_initiated_actions,
    desktop_pets,
)


def seed_all() -> None:
    """Populate the in-memory stores with their default data.

    Called once at app start-up (and again on demand) so endpoints
    that expect at least one record (``char_yuki``,
    ``world_modern_tokyo``) have something to return.
    在应用启动时调用一次（按需再次调用），用默认数据填充内存存储，
    以便期望至少有一条记录（``char_yuki``, ``world_modern_tokyo``）的
    端点有数据可返回。
    """
    characters.seed_default()
    # A3.1 startup scan — mark loaded every character with an
    # ``is_active=1`` model so they're available without a manual
    # load (spec §加载策略: 启动时仅加载 is_active=1 的模型).
    # A3.1 启动扫描 — 将有 ``is_active=1`` 模型的角色标记为已加载，
    # 无需手动加载即可使用 (规范 §加载策略：启动时仅加载 is_active=1 的模型)。
    characters.auto_load_active_models()
    interactions.seed_default()
    # Worlds are seeded *first* so the related per-world buckets
    # (environment, compute_config) can materialise their lazy
    # defaults against an existing world record.
    # 世界先被 *播种*，以便相关的每世界存储桶
    # (environment, compute_config) 能针对现有的世界记录实例化它们的惰性默认值。
    worlds.seed_default()
    # ``npcs.seed_default`` registers the A5.4 overload handler and
    # starts the background tick thread (if env allows).  It does
    # NOT seed any default NPCs — operators create them.
    # ``npcs.seed_default`` 注册 A5.4 过载处理器并启动后台 tick 线程
    # (如果环境允许)。它 *不* 播种任何默认 NPC —— 由运营创建。
    npcs.seed_default()
    # A1.1 manual backup — seeds the protected-module registry and
    # starts the daily scheduler (env-gated like the other schedulers).
    # A1.1 手动备份 — 播种受保护模块注册表并启动每日调度器
    # (与其他调度器一样受环境变量门控)。
    manual_backups.seed_default()
    memory.seed_default()
    memory_config.seed_default()  # type: ignore[attr-defined]
    # The merged safety module seeds both the A5.1 rulebook and
    # the legacy protection-state defaults (enabled / guard_level).
    # 合并后的 safety 模块同时播种 A5.1 规则书和
    # 旧版保护状态默认值 (enabled / guard_level)。
    safety.seed_default()
    settings.seed_default()
    overload.seed_default()
    character_state.seed_default()
    events.seed_default()
    # A4.3 scene system — no default POIs / travel modes / scene
    # interactions; the world library is operator-curated.  We still
    # call the seed hooks so future additions have a stable entry point.
    # A4.3 场景系统 — 无默认 POI / 旅行模式 / 场景交互；世界库由运营策展。
    # 我们仍然调用播种钩子，以便未来的添加有稳定的入口点。
    pois.seed_default()
    travel_modes.seed_default()
    scene_interactions.seed_default()
    # A4.4 economy — no default currencies / wallets / transactions;
    # operators define currencies per world and grant initial balances
    # through the route layer.  We still call the seed hooks so
    # future additions have a stable entry point.
    # A4.4 经济 — 无默认货币 / 钱包 / 交易；运营按世界定义货币并通过
    # 路由层授予初始余额。我们仍调用播种钩子，以便未来添加有稳定入口点。
    world_currencies.seed_default()
    world_economy_state.seed_default()
    wallets.seed_default()
    transactions.seed_default()
    economy.seed_default()
    # A5.1 output-safety — seeds the four legacy guard rules
    # (prompt-injection / system-prompt-probe) so the merged
    # safety layer catches them out of the box.  The per-world
    # rulebook is operator-curated beyond that.
    # A5.1 输出安全 — 播种四条遗留防护规则
    # (提示注入 / 系统提示探测)，以便合并后的安全层开箱即用即可捕获它们。
    # 每世界规则书在此基础上由运营策展。
    safety_rules.seed_default()
    # A5.2 MCP-protection — no default rules, freezes, or
    # snapshots (operator-curated).  Seed hooks are wired so
    # future rule-bundle imports have a stable entry point.
    # A5.2 MCP 防护 — 无默认规则、冻结或快照 (运营策展)。
    # 播种钩子已连接，以便未来规则包导入有稳定入口点。
    mcp_rules.seed_default()
    mcp.seed_default()
    # A5.3 automatic backup — seeds the policy record if
    # missing, registers the A5.4 ``emergency_dump`` handler, and
    # starts the hourly scheduled-backup thread (if env allows).
    # No default snapshots (operators trigger the first dump via the
    # route or a key event).
    # A5.3 自动备份 — 若缺失则播种策略记录，注册 A5.4 ``emergency_dump``
    # 处理器，并在环境允许时启动每小时定时备份线程；无默认快照
    # (运营通过路由或关键事件触发首次转储)。
    snapshots.seed_default()
    # A5.4 TTS-degradation guard — registers the ``degrade_tts``
    # overload handler so GPU/ANE pressure trips the TTS flag.
    # A5.4 TTS 降级守卫 — 注册 ``degrade_tts`` 过载处理器。
    tts_guard.seed_default()
    # citations module holds no state of its own but exposes its
    # helpers on the package for the chat pipeline to import via
    # ``from xijian_api.stubs import citations``.
    # citations 模块不持有自身状态，但在包上暴露其辅助函数，
    # 供聊天管道通过 ``from xijian_api.stubs import citations`` 导入。
    _ = citations
    # A6 / A7 / A8 seed hooks — no default records, but the A7 hook
    # starts the proactive-scan tick thread (env-gated).
    # A6 / A7 / A8 播种钩子 — 无默认记录，但 A7 钩子会启动
    # 主动扫描 tick 线程 (受环境变量门控)。
    voice_calls.seed_default()
    character_initiated_actions.seed_default()
    desktop_pets.seed_default()
    # ``models`` lives in the routes layer (it has an import-time seed
    # side effect that runs the first time the module is imported).
    # After ``state.reset_for_testing`` the bucket is empty, so re-seed
    # by calling the explicit helper exposed by the route module.
    # ``models`` 位于路由层 (它有导入时播种的副作用，首次导入模块时运行)。
    # ``state.reset_for_testing`` 后桶为空，所以通过调用路由模块暴露的显式
    # 辅助函数重新播种。
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
    "mcp",
    "mcp_rules",
    "memory",
    "memory_config",
    "manual_backups",
    "npcs",
    "overload",
    "pois",
    "resources",
    "safety",
    "safety_rules",
    "scene_interactions",
    "snapshots",
    "tts_guard",
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
    # A6 / A7 / A8 (added 2026-08-01).
    "voice_calls",
    "character_initiated_actions",
    "desktop_pets",
    "seed_all",
]