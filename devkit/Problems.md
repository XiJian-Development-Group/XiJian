# 开发者工具中的问题

> 本文档记录实际代码实现与 [Dev. Function List 功能清单 v2.1](../docs/Dev.%20Function%20List%E5%8A%9F%E8%83%BD%E6%B8%85%E5%8D%95v2.md) 之间的差距。仅列出未实现/不完整的功能，不包含普通 Bug 或代码质量问题。
>
> 生成依据：2026-07-09 直接阅读 `core/` 与 `devkit/`（排除 `macapp/`）的代码后盘账。标注「代码确认」的项目有 `file:line` 佐证；标注「待复核」的项目涉及 `devkit/ui/` 的 HTML/JS，Python 层未实现，需结合 UI 复核。

---

## 核心架构差距（贯穿所有章节）

| # | 问题 | 详述 |
|---|------|------|
| ARCH-01 | **完全没有 SQLite 持久化** | 功能清单为几乎每个模块定义了 SQLite 表（附录 A 约 40 张）。实际所有数据存于 `core/xijian_api/stubs/state.py` 的进程内 dict（16 个 bucket），`grep` 全仓库无 `sqlite3`/`CREATE TABLE`/`sqlalchemy`，进程重启即丢失，并发安全未处理（`docs/notes.md` 自承）。 |
| ARCH-02 | **无 MCP 服务端** | 功能清单多处依赖 MCP（A2 工具调用、A5.2 桌面控制防护、A8 桌宠控制），但源码无 MCP 服务端实现（所有 `mcp` 命中均在 `core/.venv/` 第三方包内）。 |
| ARCH-03 | **AI 后端引擎与 v2.1 选型不符** | v2.1 锁定 MeloTTS（对话 TTS）、DiffSinger（歌声 TTS）、bge-m3（嵌入）、Qwen2.5-7B（主对话）。实际 `ai/backends/{mlx,gguf}` 是对 `mlx_lm`/`llama_cpp`/`mlx_audio`/`stable_diffusion_cpp` 等**可选外部库**的封装，库与模型权重均不随仓库发布；未安装时 `is_available()=False` 返回 503，唯一始终可用的是 `mock` 后端（脚本化测试桩）。视频后端探测的 `mlx_video`/`stable_diffusion_cpp_video` 库不存在，永远 503。 |
| ARCH-04 | **指定嵌入模型 bge-m3 未接入** | `memory_entries.embedding` 列在 `stubs/memory.py` 的 `_new_entry()` 中被注释掉；`recall_search()` 用子字符串匹配而非向量检索，与 A1.2 的 embedding 检索、A2.2 的 bge-m3 选型不符。 |

---

## A. 用户功能

### A1. 记忆系统

| # | 问题 | 详述 |
|---|------|------|
| A1-01 | **A1.1 受保护模块 / 备份系统完全未实现（代码确认）** | `protected_modules`、`character_protected_module`、`manual_backups` 三张表无任何代码；端点 `GET /v1/protected-modules`、`POST /v1/backups`、`POST /v1/backups/{bid}/restore` 全部缺失（无路由、无测试）。AC-1（受保护模块清单）、AC-3（备份命名 `{character_id}_{ISO8601}_v{n}.bak` + 版本上限）未满足。`docs/notes.md` 明确「A1.1 备份模块路由都缺」。 |
| A1-02 | **A1.1 自动备份策略未实现（代码确认）** | 定时（每日凌晨）+ 事件触发、zstd 压缩、指数退避重试 ×3 均无代码；全仓库无 `zstd`/`zstandard`。 |
| A1-03 | **记忆条目删除是硬删除，非软删除（代码确认）** | 功能清单 A1.1 边界场景要求 `DELETE` 写入 `deleted_at` 软删除（保留 7 天可恢复）。`stubs/memory.py:259` 的 `delete` 直接 `state.memory.pop`，`deleted_at` 列从不写入。 |
| A1-04 | **A1.2 的核心已实装，但引用审查仅告警不拦截（代码确认）** | `character_memory_config`、`memory_entries`、`load_context()`（`stubs/memory.py:637`）、衰减算法（`compute_decay_score`）、强制召回管线（`stubs/chat.py:_run_recall_pipeline`）均已实现并通过测试。**但** `stubs/citations.py` 的 `audit` 只返回 `pass`/`warn`，`VERDICT_BLOCK="block"` 已定义却从不返回。功能清单 AC-3/AC-4 要求的「模型凭空捏造过去 → 必须 block 并触发重生成（最多 2 次）」**未实现**，仅有 warning 记录。 |
| A1-05 | **A1.2 `memory_citations` 表未落库（代码确认）** | 表结构（id/response_id/entry_id/citation_kind）未持久化。`stubs/citations.py:120` 仅追加到通用 `state.audits` 列表，非结构化 `memory_citations` 表。 |
| A1-06 | **A1.2 自动升级长期记忆未接线（代码确认）** | `should_promote_to_long`/`promote_to_long`（`stubs/memory.py:322,341`）已定义但 `grep` 显示从未被任何循环/定时任务调用，是死代码，未集成进衰减流程。 |
| A1-07 | **A1.2 未遵循 per-character `force_recall_on_history` 开关（代码确认）** | `_should_enable_recall`（`stubs/chat.py:335`）只检查全局 `xijian.recall.enabled`，忽略 `character_memory_config.force_recall_on_history` 字段。 |
| A1-08 | **A1.2 流式路径跳过强制召回管线（代码确认）** | `chat.stream_chunks()` 直接委托 `model_registry.complete_stream()`，未跑 `loadContext`/`recall_memory`/安全审查。仅非流式 `complete()` 走 `force_recall_pipeline()`。 |

### A2. OpenAI 兼容的 AI 模块

| # | 问题 | 详述 |
|---|------|------|
| A2-01 | **角色上下文注入顺序未完整实现（代码确认）** | 功能清单定义的 安全预检 → 人设(C2.4) → 记忆(A1.2) → 状态(A3.2) → 世界(A4) → 拼 system+history 链路，在 `stubs/chat.py` 仅注入了记忆块 + recall 规则系统消息（`_inject_memory_context`/`_inject_recall_system`）。人设、角色状态、世界上下文均**未注入**。`docs/notes.md` 自承「chat pipeline 没注入 A3.2 summary」。 |
| A2-02 | **工具调用 (MCP) 未实现（代码确认）** | 仅 `recall_memory` 一个工具被注入，无通用 MCP 工具描述注入/执行/结果回灌机制，无桌面控制/文件/浏览器自动化。 |
| A2-03 | **多模态输入未实现（代码确认）** | 功能清单要求图片/音频/视频片段可作为输入消息，且模型不支持时降级为占位描述。当前 `ChatMessage` 仅消费字符串 `content`，无多模态 part 处理与降级占位。 |
| A2-04 | **多模态支持矩阵 `[TODO]` 未补（代码确认）** | 功能清单第 326 行 `[TODO: 列出每个模型后端支持的模态]` 仍未完成。 |

### A3. 角色与状态系统

| # | 问题 | 详述 |
|---|------|------|
| A3-01 | **A3.1 六张资源表未实现（代码确认）** | `character_models`、`character_motions`、`character_voices`、`character_handwritings`、`character_styles`、`character_asset_cache` 均不存在。`stubs/characters.py` 仅存基础字段。 |
| A3-02 | **A3.1 仍残留 Live2D 字段，违反 v2.1「彻底移除 Live2D」（代码确认）** | `stubs/characters.py:41,59` 仍保留 `live2d_model` 字段；无 VRM/three-vrm 加载、无贴图/动作/声音数据、无笔迹存档、无风格文档、无资产缓存。 |
| A3-03 | **A3.2 状态未注入对话 & `can_dialogue` 未强制（代码确认）** | `stubs/character_state.py`（1101 行）的状态机、衰减、tick 线程、日志实现良好（内存态）。但 `docs/notes.md` 确认：状态未注入 chat、健康 ≤0 时的 `can_dialogue=False` 未在对话路径强制。 |
| A3-04 | **A3.1 动作库 / 跨模态一致性 / LRU 缓存未实现（代码确认）** | 无 idle/happy/sad/angry/surprised 动作库，图像/视频生成时的 `pose_image`/`motion_clip` 注入缺失，`character_asset_cache` 与 LRU 上限未定义。 |

### A4. 模拟世界系统

| # | 问题 | 详述 |
|---|------|------|
| A4-01 | **A4.1 事件引擎已实装，但场景生成未做（代码确认）** | `stubs/events.py`（978 行）+ `routes/xijian_events.py` 已实现：time/interval/probability/condition 触发器、每事件+全局风暴节流（60s）、类别禁用、后台调度线程、CRUD/实例/解决。`grep` 全仓库确认 `world_events`/`world_event_instances` 表存在于内存 bucket（`state.py:91-93`），与前版 Problems.md 称「完全未实现」不符——**已更正**。但 US-A4.1-03/AC-2 的「事件触发场景生成（图像/3D）」未实现：`events.py:73-78` 明确不调用图像/3D 管线，仅记录 `scene_ref_id`/`needs_scene` 标志，无生成队列、无占位降级。 |
| A4-02 | **A4.1 默认事件库未播种、事件类「工厂」/上限/冲突检测未实现（代码确认）** | 无内置常见事件库；`world_events` 内存态无种子。C1.1 侧的「一类事件批量实例化」「单世界上限 200 条」「触发条件冲突拒绝保存」均未实现（见 C1-01）。 |
| A4-03 | **A4.2 NPC 系统完全未实现（代码确认）** | `npcs`、`npc_scheduling_log`、`world_compute_config` 三张表不存在，无配角生成、算力分配、活动档位（high_active=3/low_active=10）、思考间隔、降级机制。 |
| A4-04 | **A4.2 无 `create_world` 路由 & 世界无法经 API 创建（代码确认）** | `stubs/worlds.py` 标头「empty by design」（82 行），无 `create_world`：`routes/xijian_worlds.py` 仅 list/get/transition/patch state。`xijian_events.py:92` 在 world 不存在时直接 404，即世界必须先存在——而创建途径缺失。`seed_default()` 为空操作（`worlds.py:12-14`）。 |
| A4-05 | **A4.2 并发世界架构 / 环境模拟 / 世界审计日志未实现（代码确认）** | 无 World Manager、无多世界并发/资源隔离；`world_environment`（天气/时间/光照/环境音）不存在；`world_audit_log` 不存在（仅通用 `state.audits`）。AC-5/AC-6（50 上限、3/10 活跃）未强制。 |
| A4-06 | **A4.3 POI/交通/互动系统未实现（代码确认）** | `pois`、`travel_modes`、规范定义的 `interactions` 表（poi_id/target_type/target_id/action/effects/cooldown）均不存在。现有 `stubs/interactions.py` 是**角色好感动作**（int_hug/int_kiss，含 nsfw_level），与功能清单的「世界内 POI 互动」是不同概念。AC-2（影响可回溯）、AC-3（体力真实扣减）未实现。 |
| A4-07 | **A4.4 经济系统完全未实现（代码确认）** | `world_currencies`、`wallets`、`transactions`、`world_economy_state` 全仓库零命中；无货币、交易、NPC 盗窃/诈骗、通胀模拟，NPC 主动盗窃/诈骗流程完全缺失。 |

### A5. 安全模块

| # | 问题 | 详述 |
|---|------|------|
| A5-01 | **A5.1 审查规则表与 OOC 检测未实现（代码确认）** | `safety_rules` 表不存在；`stubs/protection.py` 仅硬编码 3 条注入正则 + 零宽字符检测（元组 `_GUARD_RULES`），无可配置的 `rule_kind ∈ {ooc_pattern, injection_pattern, forbidden_word}` 表。无人设偏离度（OOC）审查，无「危险场景例外」机制（功能清单审查决策树未落地）。 |
| A5-02 | **A5.1 审查模块未接入对话管线（代码确认）** | `stubs/chat.py` 对 `protection`/`guard_preview` **零引用**；`guard_preview()` 仅能通过开发测试路由 `POST /v1/xijian/protection/guard/preview` 触达，真实流量不做输入预审/输出后审。AC-3（所有拦截可查询）、边界「审查崩溃降级最严格」未满足。`safety_audit_log` 表未实现。 |
| A5-03 | **A5.2 MCP 电脑控制防护完全未实现（代码确认）** | 无 `mcp_action_blacklist` 表、无 MCP 服务端、无全局快捷键安全终止（⌃⌥⌘Q）、无 SIGFREEZE→dump→confirm→sanitize→reload 流程。此能力归属 macapp，被排除在审计外。 |
| A5-04 | **A5.3 自动快照/备份未实现（代码确认）** | `safety_snapshots`、`backup_policies` 表不存在。现有 `protection.snapshot()` 是内存 dict（无 `file_path`/`reason`/磁盘持久化）。无定时快照调度、无 zstd、无空间上限提示。AC-1/AC-2/AC-3 未满足。 |
| A5-05 | **A5.4 过载防护已实现，但 4 个 action handler 未接线（代码确认）** | `stubs/overload.py`（1077 行）+ `routes/xijian_overload.py`（207 行）已实装严格/适中两档阈值、滑动窗口、20s 恢复等待（硬编码）、双重确认（425/409）、WS 广播、自动 `safety_snapshots(scope=overload)` 落盘（内存）。但 `register_action_handler`（`overload.py:215`）无任一处调用，`suspend_idle_npcs`/`degrade_tts`/`compress_memory`/`emergency_dump` 四个 handler 永远不触发实际副作用，需 A4.2/A6/A1.2/A1.1 起来后订阅。UI 弹窗/倒计时桥接缺失（UI 在 core 之外）。 |

### A6. 实时通话

| # | 问题 | 详述 |
|---|------|------|
| A6-01 | **通话系统完全未实现（代码确认）** | `voice_calls`、`call_events` 表不存在；无全双工 STT→AI→TTS 管线、无打断（barge-in）、无 VRM 动作联动、无 DiffSinger 歌声合成；无 `/v1/xijian/calls*` 路由与 WS 通话通道。`docs/notes.md` 自承 A6 是 A5.4 的「未来订阅者」。 |
| A6-02 | **指定引擎 MeloTTS/DiffSinger 未集成（代码确认）** | 见 ARCH-03 与 C2-01。 |

### A7. 主动发起聊天或通话

| # | 问题 | 详述 |
|---|------|------|
| A7-01 | **主动发起系统未实现（代码确认）** | `character_initiated_actions` 表不存在；无后台保活、无角色主动决策、无系统通知集成。此能力明确限定 macOS/iOS，归属 macapp，被排除在审计外。 |

### A8. 桌宠 / 动态壁纸

| # | 问题 | 详述 |
|---|------|------|
| A8-01 | **桌宠/壁纸系统完全未实现（代码确认）** | `desktop_pets`、`dynamic_wallpapers`、`pet_action_log` 表不存在；无渲染、无桌面交互、无动态壁纸、无 FPS 限制、无「捣乱」审计日志。此能力仅 macOS，归属 macapp，被排除。 |

---

## B. Apple TouchBar & Dynamic Island

| # | 问题 | 详述 |
|---|------|------|
| B-01 | **整章待补（文档层面，符合预期）** | 功能清单本身标注 `[TODO: 本章待补]`，无验收标准/数据模型/接口，代码中无任何对应实现。 |

---

## C. Development Kit

### C1. 世界创建

| # | 问题 | 详述 |
|---|------|------|
| C1-01 | **C1.1 无 DSL 解析器 / 事件类工厂 / 上限 / 冲突检测（代码确认）** | `world_editor.py:357,409` 的 `save_world_event`/`validate_event_trigger` 仅校验结构化 dict（time/state/probability/composite 四种 kind + AND/OR），**无文本 DSL 解析器**，不满足 AC-1「事件定义必须通过 DSL 校验」。US-C1.1-02 的「一类事件批量实例化」工厂未实现；单世界事件上限（AC-2 `[TODO] 200 条`）未强制；边界场景「触发条件冲突拒绝保存」未实现。 |
| C1-02 | **C1.2 多版本保存未实现（代码确认）** | `save_world`（`world_editor.py:81-85`）覆盖单一 `world_doc.md`，无版本历史。AC-1「文档可保存多版本」未满足。标题层级/关键词提取（供 A4 配角生成）未实现。 |
| C1-03 | **C1.3 时间/场景配置已实装（代码确认）** | `get_world_config`/`save_world_config`/`validate_world_config` + `WORLD_CONFIG_DEFAULT`（`world_editor.py:137`）含时间流速、昼夜比例、天气概率表（和=1 校验）、光照预设、环境音库，范围校验完善。DevKit 为独立进程，AC-2「生效到当前运行世界」不适用。 |

### C2. 角色创建

| # | 问题 | 详述 |
|---|------|------|
| C2-01 | **C2.1 声音设计未满足 v2.1 选型（代码确认）** | `tts_engine.py`/`voice_cloner.py` 有 TTS 抽象层并能产出音频，但：① 使用的是 `mlx_audio`（`MlxTTSEngine`, `tts_engine.py:142`），**未用功能清单锁定的 MeloTTS**；`FallbackTTSEngine` 是正弦波纯 Python 占位（非语音），`GgufTTSEngine.synthesize` 返回 `"not implemented"`（`tts_engine.py:297`）。② **歌声引擎 DiffSinger 完全缺失**，无任何 singing 路径。③ 声音克隆是假的：`clone_voice_from_file`（`voice_cloner.py:224`）仅复制音频文件并写元数据，不运行任何克隆模型；AC-1 版权确认（US-C2.1-01）未实现。 |
| C2-02 | **C2.3 `source='ai_suggested'` 标记未注入保存路径（代码确认）** | `save_character`（`character_editor.py:237`）持久化配置时不带来源标记；C4 的自动填写结果未写入 `source='ai_suggested'`，AC-2 未满足。 |
| C2-03 | **C2.4 人设关键特征抽取未实现（代码确认）** | `import_persona` 存在，但无「抽取关键性格特征 → 用于人设一致性审查（C2.7）」的解析器。 |
| C2-04 | **C2.5 初始记忆类型/强制逻辑有偏差（代码确认）** | `memory_editor.save_entry`（`memory_editor.py:103`）默认 `type="short"`，功能清单要求全部 `type='long', source='manual'`。`enforce_initial_memory_minimum`（`character_editor.py:310`）仅在绑定 memory pack 时触发，未绑定 pack 的角色可少于 10 条保存，AC-1 部分失效。 |
| C2-05 | **C2.6 `description` 字段缺失（代码确认）** | `save_character`（`character_editor.py:217-244`）写入 `name`/`display_name`/`persona_doc`/`language_style` 等，但**未写入 `description`**，而功能清单明确要求 `characters.display_name / description / language_style`。 |
| C2-06 | **C2.7 对话信息无人工 review 开关 & 无微调管线（代码确认）** | `dialog_editor.py` 有 CRUD + `check_dialog_minimum`（≥8 轮），但 AC-2「必须人工 review 才能启用」无启用/审核标志；AC-3 的 fine-tune / prompt 蒸馏路径未实现，样本仅存储，未被任何训练/导出流程消费。 |
| C2-07 | **C2.8 3D 模型 AI 生成/换装/表情/VRM 校验为占位（代码确认）** | `generate_model_from_text`（`model_viewer.py:159`）仅写占位 JSON，无真实 VRM 生成（无 MLX/AI 后端）。换装（VRM BlendShape）、表情绑定（US-C2.8-03/04）在 Python 层无实现；`read_model_bytes` 仅 base64 编码，**无 VRM 1.0 规范校验（AC-4）**；FBX 导入被拒绝（`model_viewer.py:55`），而功能清单允许 FBX/GLB。 |
| C2-08 | **C2.9 动作转换/AI 推断/关键帧编辑/骨骼校验未实现（代码确认）** | `import_motion_file`（`motion_editor.py:138`）仅复制文件，无 FBX→VRM / BVH→VRM 转换；US-C2.9-01 视频 AI 推断动作缺失；AC-1「关键帧参数可编辑」仅有自由 `parameters` dict 无关键帧编辑器；AC-3「骨骼命名匹配回放不失真」无骨骼名校验。 |
| C2-09 | **C2.2 笔迹设计暂不开放（符合预期）** | 功能清单明确暂不开放，`character_handwritings` 表保留、UI 隐藏，状态符合预期。 |

### C3. 剧情设计

| # | 问题 | 详述 |
|---|------|------|
| C3-01 | **C3 节点绑定关系未强制（代码确认）** | `plot_editor.py` 有节点/边 CRUD + `export_plot_for_submit`。但 AC-2「节点可绑定角色/世界/事件」无强制绑定 schema；AC-1「可被模拟世界读取并执行」在 DevKit 独立进程下不适用（A4 未实装）。 |

### C4. AI 设计辅助

| # | 问题 | 详述 |
|---|------|------|
| C4-01 | **C4 `auto_suggest` 是模板，非真正 AI（代码确认）** | `ai_assistant.py:132-163` 的 `auto_suggest` 是关键词→中文模板拼接，无模型调用、无网络搜索、无「自助搜索并判断」。US-C4-02「优先询问用户、每个细节问清楚」的 `questions[]` 往返流程未实现。 |
| C4-02 | **C4 mlx 后端未接线（代码确认）** | `auto_suggest` 不调用 `get_chat_backend()`，即使安装 `mlx_audio` 也不会产出真实 AI 内容。 |
| C4-03 | **C4 `ai_ratio` 偏离字段级定义（代码确认）** | `calculate_ai_ratio`（`ai_assistant.py:98`）按 `source='ai_suggested'` 的日志占比估算，非功能清单定义的「ai_assisted_field_count / total_field_count」字段级比例，>30% 质量审核触发条件偏松；AC-1 的来源标记只在日志层，未落到实际内容字段。 |
| C4-04 | **C4 `dev_ai_assist_log` 非 SQLite 表（代码确认）** | `log_assist_event` 追加到 `assist_log.json`，非功能清单定义的结构化表。 |

### C5. 提交与上架（与功能清单存在硬性偏差）

| # | 问题 | 详述 |
|---|------|------|
| C5-01 | **体积上限与功能清单不符（代码确认，严重）** | 功能清单 AC-3 要求 **1200 MB**（`1_200_000_000`）。代码默认 `DEV_SUBMIT_MAX_ATTACHMENT_BYTES=512_000_000`（`__init__.py:155`）且 `config.py:33` 同为 512_000_000。需靠环境变量 `XIJIAN_DEV_MAX_BYTES` 覆盖，但发布默认不满足验收。 |
| C5-02 | **频次冷却与功能清单不符（代码确认，严重）** | 功能清单 AC-2 要求 **每小时 1 次（3600 s）**。代码默认 `DEV_SUBMIT_COOLDOWN_SECONDS=120`（`__init__.py:159`）且 `config.py:32` 为 120 秒（2 分钟），不满足验收。 |
| C5-03 | **提交真实 SMTP 凭据被硬编码进源码（代码确认，安全+合规问题）** | 功能清单 AC-5 要求占位符（`smtp.example.com`、587、STARTTLS、`REPLACE_BEFORE_DEPLOY`）。代码硬编码 `host="smtp.qq.com"`、`port=465`、`USE_TLS=False`（SSL 非 STARTTLS，`__init__.py:130`）、`USER="2500693887@qq.com"`、`PASSWORD="evcqxdqiiovtebie"`（`__init__.py:126-150`）、`RECIPIENT="panmofan@icloud.com"`，**明文密码入库**。 |
| C5-04 | **7Z 非「solid」模式（代码确认）** | 功能清单 AC-1 字面要求 `py7zr.SevenZipFile(mode='solid')`。代码用 `mode="w"`（`__init__.py:505`）。py7zr 默认即 solid，实际大概率 solid，但不满足字面规范。 |
| C5-05 | **`DevKitApi` 方法名与清单表不一致（代码确认）** | 清单表列 `get_status`、`last_submit_for`；代码暴露 `whoami()`（`api.py:291`）、`last_submit`（非 `last_submit_for`），名称偏离。 |
| C5-06 | **`docs/notes.md` 与代码对 C5 的描述互相矛盾（代码确认）** | `notes.md`（2026-07-04 C5 条目）称「1200 MB 上限」「1 小时冷却（≥3600s）」「mode='solid'」，与代码默认 512 MB / 120 s / `mode="w"` 全部不符——文档已过时/不准确。notes.md 亦自承 UI 从未在真实 Pywebview 窗口跑过、7Z 路径未端到端跑过、SMTP 从未真实发送。 |

---

## 重要提示：文档与实现不一致

- `docs/notes.md` 对 **C5** 的描述（1200 MB / 3600 s / solid）与 `devkit/` 代码默认（512 MB / 120 s / `mode="w"`）直接冲突，且 v2.4 日志称 A5.4 已实装但承认 4 个 action handler 未接线——前者会误导「已完成」的判断，建议以代码为准重新盘账。
- 前版 `devkit/Problems.md` 将 **C2.1（声音设计）、C2.5、C2.7、C3、C4** 标为「✅ 已实现」，但据本次代码审计：C2.1 未用 MeloTTS、DiffSinger 缺失、克隆为伪；C2.5 类型默认 short 且强制有条件；C2.7 无 review 开关；C4 非真实 AI。请以上述代码级证据为准复核。



## 记录一个BUG

DevKit设置页历史记录能清除，但是导出的可提交包怎么删都不行，刷新了依旧还在那。
