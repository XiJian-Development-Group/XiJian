# 开发者工具中的问题

> 本文档记录实际代码实现与 [Dev. Function List 功能清单 v2.1](../docs/Dev.%20Function%20List%E5%8A%9F%E8%83%BD%E6%B8%85%E5%8D%95v2.md) 之间的差距。仅列出未实现/不完整的功能，不包含 Bug 或代码质量问题。

---

## A. 用户功能

### A1. 记忆系统

| # | 问题 | 详述 |
|---|------|------|
| A1-01 | **无 SQLite 持久化** | 功能清单定义了 `memory_entries`、`character_memory_config`、`protected_modules`、`character_protected_module`、`manual_backups`、`memory_citations` 六张 SQLite 表，实际所有数据存储在 `stubs/state.py` 的进程内 dict 中，进程重启即丢失。 |
| A1-02 | **A1.1 备份系统未实现** | 功能清单定义的 `POST /v1/backups`（触发手动备份）、`POST /v1/backups/{bid}/restore`（恢复，可选 `scope`）、`GET /v1/protected-modules`（列出受保护模块）三个端点不存在。`protected_modules`、`character_protected_module`、`manual_backups` 表无对应代码。 |
| A1-03 | **A1.1 自动备份策略未实现** | 功能清单要求的定时备份（每日凌晨 + 事件触发）、zstd 压缩、指数退避重试均未实现。 |
| A1-04 | **A1.2 memory_citations 未实现** | 功能清单定义的 `memory_citations` 表（用于幻觉审查，记录 response→entry 的引用关系）不存在。现有 `stubs/citations.py` 仅将审核结果追加到 `state.audits` 列表，无数据库表结构。 |
| A1-05 | **A1.2 embedding 列未填充** | `memory_entries.embedding` 和 `embedding_model` 字段在 `stubs/memory.py` 的 `_new_entry()` 中被注释掉，未实际生成/存储嵌入向量。`recall_search()` 使用基本的子字符串匹配，而非向量检索。 |
| A1-06 | **A1.2 强制调用规则在流式路径中未生效** | `chat.stream_chunks()` 直接委托给模型后端（`model_registry.complete_stream()`），未运行 A1.2 的强制召回管线（`loadContext` → memory citations → safety review）。非流式路径（`chat.complete()`）虽然通过 `stubs.chat` 调用了 `force_recall_pipeline()`，但流式路径完全跳过。 |
| A1-07 | **A1.2 遗忘算法未完整实现** | 短期记忆衰减分（`decay_score = decay_score(t0) * exp(-short_term_decay_rate * Δh)`）和自动升级为长期记忆候选的逻辑未实际运行。`stubs/memory.py` 虽有 `schedule_consolidate()` 端点但仅为模拟。 |

### A2. OpenAI 兼容的 AI 模块

| # | 问题 | 详述 |
|---|------|------|
| A2-01 | **角色上下文注入顺序未完整实现** | 功能清单定义的安全预检 → 加载人设 → 加载记忆 → 加载状态 → 加载世界 → 拼接 prompt 的完整链路，在路由层（`chat.py`）未实现。当前实现仅将用户消息透传到 stub/model backend，未自动注入角色上下文。 |
| A2-02 | **工具调用 (MCP) 未实现** | 功能清单要求 MCP 工具描述注入 → 模型决定调用 → 隙间执行 → 结果回灌的完整流程未实现。无 MCP 服务端代码。 |
| A2-03 | **多模态支持矩阵未定义** | 功能清单第 326 行的 `[TODO: 列出每个模型后端支持的模态]` 仍未完成，代码中无对应实现。 |
| A2-04 | **降级策略未实现** | 功能清单边界场景要求"模型不支持某模态时降级为占位描述"，未实现。 |

### A3. 角色与状态系统

| # | 问题 | 详述 |
|---|------|------|
| A3-01 | **A3.1 角色资源多表未实现** | `character_models`（多模型版本）、`character_motions`（动作库）、`character_voices`（声音数据）、`character_styles`（语言风格）、`character_handwritings`（笔迹）、`character_asset_cache`（缓存条目）六张 SQLite 表均未实现。`stubs/characters.py` 仅存储 `display_name`、`persona_doc`、`tags` 等基础字段。 |
| A3-02 | **A3.1 动作库未实现** | 功能清单要求动作库至少支持 idle/happy/sad/angry/surprised 六种 + 自定义，验收标准 AC-3。当前无任何动作库代码。A3.2 状态系统的 `active_behavior` 无法映射到实际 3D 动作。 |
| A3-03 | **A3.1 跨模态一致性未实现** | 功能清单要求在图像/视频生成时注入 `pose_image`/`motion_clip` + 贴图 + 声音，未实现。 |
| A3-04 | **A3.1 缓存策略未实现** | `character_asset_cache` 表不存在，LRU 缓存上限未定义。 |
| A3-05 | **A3.2 状态变更 UI 推送未实现** | 功能清单 AC-3 要求 UI 端到端更新延迟 < 500ms。当前状态变更虽写入 log 并通过 WS 广播，但 WS 广播为纯内存推送，无实际 UI 消费。 |

### A4. 模拟世界系统

| # | 问题 | 详述 |
|---|------|------|
| A4-01 | **A4.1 事件调度系统未实现** | `world_events`（事件定义）、`world_event_instances`（事件实例）表不存在。无定时器 tick 评估触发器，无冷却检查，无事件队列。`POST /v1/xijian/worlds/<id>/event` 仅为简单追加。 |
| A4-02 | **A4.1 高频事件节流未实现** | 功能清单要求默认 60s 内最多 1 个事件、排队与优先级丢弃，未实现。 |
| A4-03 | **A4.2 NPC 系统完全未实现** | `npcs`、`npc_scheduling_log`、`world_compute_config` 三张表不存在。无配角生成，无算力分配，无活动档位（high_active=3 / low_active=10），无思考间隔，无降级机制。 |
| A4-04 | **A4.2 并发世界架构未实现** | 功能清单要求每个世界独立进程/线程、资源隔离。当前无 World Manager，无多世界并发调度。 |
| A4-05 | **A4.2 环境模拟未实现** | `world_environment` 表不存在。无天气、时间、光照、环境音效模拟。 |
| A4-06 | **A4.2 世界审计日志未实现** | `world_audit_log` 表不存在。 |
| A4-07 | **A4.3 POI/交通/互动系统未完整实现** | `pois`、`travel_modes`、`interactions` 表不存在。`stubs/interactions.py` 仅存储预定义的互动列表，不支持 POI 系统与交通方式。 |
| A4-08 | **A4.4 经济系统完全未实现** | `world_currencies`、`wallets`、`transactions`、`world_economy_state` 四张表不存在。无货币系统，无交易，无非法手段，无通胀模拟。 |

### A5. 安全模块

| # | 问题 | 详述 |
|---|------|------|
| A5-01 | **A5.1 审查规则数据库未实现** | `safety_rules` 表不存在。当前保护系统的守卫规则（`stubs/protection.py`）仅为硬编码的 3 条注入检测正则 + 零宽字符检测，无可配置的规则表。 |
| A5-02 | **A5.1 OOC 检测未实现** | 功能清单要求"输出后审"实时扫描人设偏离度（OOC）。当前 `guard_preview()` 仅做基础的输入预审（注入检测），无输出后审、无人设一致性审查。 |
| A5-03 | **A5.1 审查模块降级未实现** | 功能清单边界场景要求"审查模块自身崩溃 → 降级为最严格档"。未实现。 |
| A5-04 | **A5.2 MCP 电脑控制防护未实现** | 功能清单要求 MCP 实时监控、黑名单/白名单、全局快捷键安全终止（默认 ⌃⌥⌘Q）。当前无 MCP 服务端，无防护实现。 |
| A5-05 | **A5.2 安全终止流程未实现** | 功能清单时序要求：按下安全终止键 → SIGFREEZE → dump_context → 弹窗确认 → sanitize → reload。未实现。 |
| A5-06 | **A5.3 自动快照/备份未实现** | `safety_snapshots`、`backup_policies` 表不存在。无定时/事件触发的快照生成，无 zstd 压缩，无空间上限管理。 |
| A5-07 | **A5.4 过载防护已实现 ✅** | 功能清单 v2.1 定义的严格/适中两档阈值表（CPU 93/95%、SoC 95°C、内存 90%、GPU 75/80%）、滑动窗口判定、20s 恢复等待、双重确认全部实现。 |

### A6. 实时通话

| # | 问题 | 详述 |
|---|------|------|
| A6-01 | **通话系统完全未实现** | `voice_calls`、`call_events` 表不存在。无全双工语音流，无 STT→AI→TTS 管线，无 VRM 动作联动，无 DiffSinger 歌声合成集成。 |
| A6-02 | **MeloTTS/DiffSinger 未集成** | 功能清单 v2.1 选型锁定 MeloTTS（对话 TTS）和 DiffSinger（歌声 TTS），当前 GGUF/MLX 后端的 TTS 使用 Piper/Coqui/mlx_audio，与指定引擎不符。 |

### A7. 主动发起聊天或通话

| # | 问题 | 详述 |
|---|------|------|
| A7-01 | **主动发起系统未实现** | `character_initiated_actions` 表不存在。无后台保活进程，无角色主动决策逻辑，无系统通知集成。WebSocket 中的 `character.proactive_message` 事件仅为一个硬编码的测试消息。 |

### A8. 桌宠 / 动态壁纸

| # | 问题 | 详述 |
|---|------|------|
| A8-01 | **桌宠系统完全未实现** | `desktop_pets`、`dynamic_wallpapers`、`pet_action_log` 表不存在。无桌宠渲染，无桌面交互，无动态壁纸。WS 中的 `desktop_pet.*` 事件类型仅为占位。 |

---

## B. Apple TouchBar & Dynamic Island

| # | 问题 | 详述 |
|---|------|------|
| B-01 | **整章待补（文档层面）** | 功能清单本身标注 `[TODO: 本章待补]`。代码中无任何对应实现。 |

---

## C. Development Kit

### C1. 世界创建

| # | 问题 | 详述 |
|---|------|------|
| C1-01 | **C1.1 自定义事件 DSL 编辑器已实现 ✅** | `world_editor.py`：`list_world_events`/`save_world_event`/`delete_world_event`/`validate_event_trigger`。UI：事件列表 + 表单（名称/优先级/启用/触发条件 JSON/场景/影响）。DSL 支持 time/state/probability/composite 四种触发 kind。 |
| C1-02 | **C1.2 世界观 MD 编辑器已实现 ✅** | `world_editor.py`：`lint_world_doc` 检查必需标题（时间线/地理/主要势力），`get_world_doc_templates` 提供异世界/现代都市/校园/星际四套模板。UI：Markdown 预览按钮 + 渲染器 + lint 结果显示 + 模板选择器。多版本保存仍缺（存到本地 MD 文件）。 |
| C1-03 | **C1.3 时间/场景系统配置已实现 ✅** | `world_editor.py`：`get_world_config`/`save_world_config`/`validate_world_config`。UI：时间流速倍率、一天虚拟分钟、夜晚占比、天气概率表 JSON、光照预设、环境音库字段 + 检查配置按钮。 |

### C2. 角色创建

| # | 问题 | 详述 |
|---|------|------|
| C2-01 | **C2.1 声音设计未实现（需 ML 后端）** | `voice_cloner.py` 的 `generate_voice_from_text` 产生正弦波占位音频，`clone_voice_from_file` 仅为文件复制。真正的 MeloTTS/DiffSinger 集成需要 ML 库不可用。**无法独自完成**。 |
| C2-02 | **C2.5 初始记忆最少条数校验已实现 ✅** | `character_editor.py`：`check_initial_memory_minimum`/`enforce_initial_memory_minimum`（默认 10 条）。`save_character` 在 `assigned_memory_pack` 非空时强制执行。`api.check_initial_memory` 桥接。 |
| C2-03 | **C2.7 对话信息编辑器已实现 ✅** | `dialog_editor.py` 完整 CRUD + `check_dialog_minimum` 校验 ≥8 轮。UI：对话样本标签页 + 列表 + 编辑器 + 导出。 |
| C2-04 | **C2.8 3D 模型 AI 生成未实现（需外部服务）** | `model_viewer.py` 的 `generate_model_from_text` 返回占位 JSON。真正的 VRM 生成需要 Tripo/Meshy API 或自训练模型，DevKit 无法独立完成。VRM 1.0 规范校验未实现。**无法独自完成**。 |
| C2-05 | **C2.8 换装/BlendShape 未实现（需 three.js VRM）** | 换装切换和 BlendShape 表情需要在 three.js VRM 预览器中实现，超过纯 Python/HTML 能力范围。**无法独自完成**。 |
| C2-06 | **C2.9 动作设计已实现 ✅** | `motion_editor.py` 完整 CRUD + BVH/FBX 文件导入 + 参数 JSON 编辑 + 提交导出。UI：动作标签页 + 列表 + 编辑器。AI 推断动作需 ML 后端不可用。 |
| C2-07 | **C2.3 角色配置 JSON 编辑器已实现 ✅** | `character_editor.py`：`get_character_config_schema`/`validate_character_config`。UI：角色配置 JSON textarea + 校验按钮 + 自动填写按钮（调用 auto_suggest 从人设文档推断）。 |
| C2-08 | **C2.4 人设 MD 模板已实现 ✅** | `character_editor.py`：`get_persona_templates` 提供通用角色/主角型/配角型三套模板。UI：人设文档 textarea + 模板选择器。 |

### C3. 剧情设计

| # | 问题 | 详述 |
|---|------|------|
| C3-01 | **剧情设计已实现 ✅** | `plot_editor.py` 完整 CRUD（剧情/节点/边 + 提交导出）。UI：剧情标签页 + 列表 + 节点编辑器 + 边编辑器 + 导出。节点/边关系以文本列表呈现，缺少可视化图形编辑器。 |

### C4. AI 设计辅助

| # | 问题 | 详述 |
|---|------|------|
| C4-01 | **AI 辅助功能 API 已桥接 ⚠️** | `ai_assistant.py` 已重写：`auto_suggest`/`calculate_ai_ratio`/`check_ai_threshold`/`log_assist_event` 均符合 JS 调用约定。所有 6 个 API 方法已注册。但 `auto_suggest` 仍返回固定占位建议（无真正 LLM 调用）。 |
| C4-02 | **dev_ai_assist_log 表未实现 ⚠️** | `log_assist_event` 已实现（将事件追加到 `assist_log.json`），但非 SQLite 表结构。 |
| C4-03 | **AI 产出标记与 30% 阈值已桥接 ⚠️** | `calculate_ai_ratio` 和 `check_ai_threshold` 已实现并注册为 API。但 `source='ai_suggested'` 标记未在保存路径中实际注入。 |

### C5. 提交与上架

| # | 问题 | 详述 |
|---|------|------|
| C5-01 | **剧情提交已实现 ✅** | `plot` 已在 `TARGET_KINDS` 中，`export_plot_for_submit` 存在，`api.list_submit_packages` 返回剧情包。提交时可勾选剧情内容。 |
| C5-02 | **模型/声音样本提交已实现 ✅** | `model_viewer.py` 有 `export_model_for_submit`，`voice_cloner.py` 有 `export_voice_for_submit`。UI 中声音/模型包显示在提交列表。 |
| C5-03 | **状态已磁盘持久化 ✅** | `state.py` 新增 `load()`/`save()`，提交记录、冷却时间和归档路径在每次提交后持久化到 `devkit_state.json`，重启不丢失。 |
| C5-04 | **C2.2 笔迹设计暂不开放（符合预期）** | 功能清单明确笔迹设计功能暂不开放，`character_handwritings` 表保留但 UI 入口隐藏。当前状态符合预期。 |

---

## 核心架构差距

| # | 问题 | 详述 |
|---|------|------|
| ARCH-01 | **完全无 SQLite 持久化** | 功能清单定义了约 40 张 SQLite 表，实际所有数据存储在进程内内存 dict 中。 |  |
| ARCH-02 | **无 MCP 服务端** | 功能清单多处依赖 MCP（工具调用、桌宠桌面控制、安全防护），但无 MCP 服务端实现。 |
| ARCH-03 | **AI 后端引擎与选型不符** | v2.1 锁定 MeloTTS（对话 TTS）、DiffSinger（歌声 TTS）、bge-m3（嵌入）、Qwen2.5-7B（主对话），当前实现使用 Piper/Coqui/mlx_audio（TTS）、无指定嵌入模型、无指定对话模型。 |
| ARCH-04 | **gguf/video.py 存在死代码 Bug** | `load()` 中 `if self._attr and False` 永远为 False，导致 `__import__` 永远不执行。 |
| ARCH-05 | `fine_tuning.py` **和** `batches.py` **仅存根** | `stubs/fine_tuning.py` 仅返回初始事件，无 CRUD/训练循环。`stubs/batches.py` 仅模拟假时间线。 |
