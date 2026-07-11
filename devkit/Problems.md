# 依据功能清单发现的未完成功能（Problems）

> 本文档记录**实际代码实现**与 [Dev. Function List 功能清单 v2.2](../docs/Dev.%20Function%20List%E5%8A%9F%E8%83%BD%E6%B8%85%E5%8D%95v2.md) 之间的差距。
> 仅列出**未实现 / 不完整 / 占位**的功能，不含普通 Bug 或代码质量问题。
>
> - 盘账日期：2026-07-11
> - 范围：阅读 `core/`（含 `core/xijian_api`）与 `devkit/` 下全部 Python 代码、相关测试、以及 `docs/notes.md`，**排除 `macapp/`**。
> - 方法：逐节比对功能清单的验收标准（AC-*）、用户故事（US-*）、接口定义、数据模型表与功能清单，定位代码中缺失 / 仅存桩 / 仅内存 / TODO 的项。
> - 标注「代码确认」的项有 `file:line` 佐证；标注「notes 确认」的项来自 `docs/notes.md` 的自述。
> - 已实装且通过测试的项（如 A1.2 衰减/召回核心、A4.1 事件调度、A5.4 过载判定、C5 提交管线、C1.1/C1.2/C1.3/C2.3/C2.5/C2.7 编辑器校验）**不列入**。

---

## 0. 核心架构差距（贯穿所有章节）

| # | 问题 | 详述 |
|---|------|------|
| ARCH-01 | **完全没有 SQLite 持久化** | 功能清单为几乎每个模块定义了 SQLite 表（附录 A 约 40 张）。实际所有数据存于 `core/xijian_api/stubs/state.py` 的进程内 dict（内存态），全仓库无 `sqlite3`/`CREATE TABLE`/`sqlalchemy`，进程重启即丢失，并发安全未处理（`docs/notes.md` 自承）。 |
| ARCH-02 | **无 MCP 服务端** | 功能清单多处依赖 MCP（A2 工具调用、A5.2 桌面控制防护、A8 桌宠控制），但源码无 MCP 服务端实现（所有 `mcp` 命中均在 `core/.venv/` 第三方包内）。 |
| ARCH-03 | **AI 后端引擎与 v2.1 选型不符** | v2.1 锁定 MeloTTS / DiffSinger / bge-m3 / Qwen2.5-7B。实际 `ai/backends/{mlx,gguf}` 是对 `mlx_lm`/`llama_cpp`/`mlx_audio`/`stable_diffusion_cpp` 等**可选外部库**的封装，库与权重均不随仓库发布；未安装时 `is_available()=False` 返回 503，唯一始终可用的是 `mock` 后端（脚本化测试桩）。视频后端探测的 `mlx_video`/`stable_diffusion_cpp_video` 库不存在，永远 503。 |
| ARCH-04 | **指定嵌入模型 bge-m3 未接入** | `memory_entries.embedding` 列在 `stubs/memory.py` 的 `_new_entry()` 中被注释掉；`recall_search()` 用子字符串匹配而非向量检索，与 A1.2 的 embedding 检索、A2 的 bge-m3 选型不符。 |

---

## A. 用户功能

### A1. 记忆系统

| # | 问题 | 详述 |
|---|------|------|
| A1-01 | **A1.1 受保护模块 / 备份系统完全未实现（代码确认）** | `protected_modules`、`character_protected_module`、`manual_backups` 三张表无任何代码；端点 `GET /v1/protected-modules`、`POST /v1/backups`、`POST /v1/backups/{bid}/restore` 全部缺失（无路由、无测试）。AC-1（受保护模块清单）、AC-3（备份命名 `{character_id}_{ISO8601}_v{n}.bak` + 版本上限）未满足。 |
| A1-02 | **A1.1 自动备份策略未实现（代码确认）** | 定时（每日凌晨）+ 事件触发（≥50 条手动编辑 / 首次加载 / 安全终止后）、zstd 压缩、指数退避重试 ×3 均无代码；全仓库无 `zstd`/`zstandard`。 |
| A1-03 | **记忆条目删除是硬删除，非软删除（代码确认）** | 边界场景要求 `DELETE` 写入 `deleted_at` 软删除（保留 7 天可恢复）。`stubs/memory.py:259` 的 `delete` 直接 `state.memory.pop`，`deleted_at` 列从不写入，无级联/可恢复。 |
| A1-04 | **A1.2 引用审查仅告警不拦截（代码确认）** | `stubs/citations.py:148` 的 `audit` 最多返回 `VERDICT_WARN`，`VERDICT_BLOCK="block"` 已定义却从不返回。AC-3/AC-4 要求的「模型凭空捏造过去 → 必须 block 并触发重生成（最多 2 次）」**未实现**，仅有 warning 记录；`stubs/chat.py` 记录 audit 但从不 regenerate（grep "regenerat" 无命中）。 |
| A1-05 | **A1.2 `memory_citations` 表未落库（代码确认）** | 表结构（id/response_id/entry_id/citation_kind）未持久化。`stubs/citations.py:255` 仅追加到通用 `state.audits` 列表。 |
| A1-06 | **A1.2 自动升级长期记忆未接线（代码确认）** | `should_promote_to_long`/`promote_to_long`（`stubs/memory.py:322,341`）已定义，但 `grep` 显示从未被任何循环/定时任务调用，是死代码，未集成进衰减流程。 |
| A1-07 | **A1.2 未遵循 per-character `force_recall_on_history` 开关（代码确认）** | 强制召回只检查全局 `xijian.recall.enabled`，忽略 `character_memory_config.force_recall_on_history` 字段。 |
| A1-08 | **A1.2 流式路径跳过强制召回管线（代码确认）** | `chat.stream_chunks()` 直接委托 `model_registry.complete_stream()`，未跑 `loadContext`/`recall_memory`/安全审查；仅非流式 `complete()` 走 `force_recall_pipeline()`。 |
| A1-09 | **A1.1 记忆接口路径/参数与清单不符（代码确认）** | 清单 `GET /v1/characters/{cid}/memory/entries?type=&page=`，实际路由为 `GET /v1/xijian/memory/entries`（`routes/xijian_memory.py:26`），且 `list_entries` 未透传 `type` 过滤（stub 支持但路由不传）。`PATCH/DELETE /v1/entries/{eid}` 实际为 `/v1/xijian/memory/entries/<id>`。 |
| A1-10 | **A1.2 工具结果超窗未摘要回灌（代码确认）** | 边界场景「工具结果超过上下文窗口 → 摘要后回灌，原始结果存入 memory」无实现路径。 |

### A2. OpenAI 兼容的 AI 模块

| # | 问题 | 详述 |
|---|------|------|
| A2-01 | **角色上下文注入顺序未完整实现（代码确认）** | 清单定义 安全预检 → 人设(C2.4) → 记忆(A1.2) → 状态(A3.2) → 世界(A4) → 拼 system+history。`stubs/chat.py` 仅注入了记忆块 + recall 规则系统消息，人设 / 角色状态 / 世界上下文均**未注入**（notes 确认 E/F 步未接）。 |
| A2-02 | **工具调用 (MCP) 未实现（代码确认）** | 仅 `recall_memory` 一个工具被注入，无通用 MCP 工具描述注入 / 执行 / 结果回灌机制，无桌面控制 / 文件 / 应用启动 / 浏览器自动化。 |
| A2-03 | **多模态输入未实现（代码确认）** | 清单要求图片/音频/视频片段作输入消息，且模型不支持时降级为占位描述。`stubs/chat.py:448 _content_to_text` 把 list-content 扁平化为纯 `text`，丢弃 image/audio/video part，无降级占位路径。 |
| A2-04 | **多模态支持矩阵 `[TODO]` 未补（代码确认）** | 清单第 326 行 `[TODO: 列出每个模型后端支持的模态]` 仍未完成。 |
| A2-05 | **工具结果超窗未摘要回灌（代码确认）** | 同 A1-10 边界场景，未实现。 |

### A3. 角色与状态系统

| # | 问题 | 详述 |
|---|------|------|
| A3-01 | **A3.1 六张资源表未实现（代码确认）** | `character_models`、`character_motions`、`character_voices`、`character_handwritings`、`character_styles`、`character_asset_cache` 均不存在。`stubs/characters.py` 仅存基础字段；无 VRM/FBX/GLB 多版本绑定与切换。 |
| A3-02 | **A3.1 残留 Live2D 字段，违反 v2.1「彻底移除 Live2D」（代码确认）** | `stubs/characters.py:41,59` 仍保留 `live2d_model` 字段；无 VRM/three-vrm 加载、无贴图/动作/声音数据、无笔迹存档、无风格文档、无资产缓存。 |
| A3-03 | **A3.2 状态未注入对话 & `can_dialogue` 未强制（代码确认）** | `stubs/character_state.py` 状态机/衰减/tick 线程/日志实现良好（内存态）。但状态未注入 chat 上下文，且健康 ≤0 时的 `can_dialogue=False`（`character_state.py:618`）未在对话路径强制（notes 确认）。 |
| A3-04 | **A3.1 动作库 / 跨模态一致性 / LRU 缓存未实现（代码确认）** | 无 idle/happy/sad/angry/surprised 动作库；图像/视频生成时的 `pose_image`/`motion_clip` 注入缺失；`character_asset_cache` 与 LRU 上限未定义；资源导入是假占位 zip（`stubs/resources.py:31-52`），不绑定上述模型/动作/声音。 |

### A4. 模拟世界系统

| # | 问题 | 详述 |
|---|------|------|
| A4-01 | **A4.1 场景生成未实现（代码确认）** | 事件引擎已实装（触发/冷却/lost_priority_race/节流），但 US-A4.1-03/AC-2「事件触发场景生成（图像/3D）」未做：`stubs/events.py:74-78` 明确不调用图像/3D 管线，仅记 `needs_scene`/`scene_ref_id`，无生成队列、无占位图+文字降级。 |
| A4-02 | **A4.1 内置事件库 / 事件类工厂 / 上限 / 冲突检测未实现（notes 确认）** | 无内置常见事件库；C1.1 侧「一类事件批量实例化」「单世界上限 200 条」「触发条件冲突拒绝保存」均未实现（见 C1-01）。 |
| A4-03 | **A4.1 触发器能力受限（notes 确认）** | 时间触发器仅 `daily`/`hourly`，无 cron/星期/月份级节日；condition 触发器字段白名单未校验，接受任意 key。 |
| A4-04 | **A4.1 事件 fire 后无记忆回写（notes 确认）** | US-A4.2-04「世界内发生的事自动影响记忆」未接：事件仅入 `world_event_instances` + 广播，不写角色记忆；`affected_npcs` 不主动推导。 |
| A4-05 | **A4.2 NPC 自动生成未实现（代码确认）** | `create()` 需手工填 name/persona；`seed_default()` 注释「We do not seed any default NPCs」（`stubs/npcs.py:979-996`），未基于世界观文档 + 模板自动生成。 |
| A4-06 | **A4.2 NPC 自我决策（LLM 推理）未实现（代码确认）** | 算力调度 / 档位 / 日志表已落地，但 `tick_world` 仅标记 `last_think_at`，无 LLM 调用、无 state 推进（注释明言留给 operator，`stubs/npcs.py:716-805`）；「思考产物=一句话意图→推进 state_json」未做。 |
| A4-07 | **A4.2 算力不足降级未端到端验证（notes 确认）** | `_should_degrade` 逻辑存在，但需 overload 模块提供 P99 延迟输入，跨模块实时联动无端到端链路测试。 |
| A4-08 | **A4.3 POI / 交通 / 世界互动系统完全缺失（代码确认）** | `pois`、`travel_modes` 表不存在（grep 无命中）；规范 `interactions` 表（poi_id/target_type/target_id/action/effects/cooldown_sec）未实现——现有 `stubs/interactions.py` 是角色 hug/kiss 亲密互动演示，非 A4.3 世界场景互动；AC-2 不可回溯（无 audit 写入）、AC-3 体力扣减、影响传播均未做。 |
| A4-09 | **A4.4 经济系统完全缺失（代码确认）** | `world_currencies`/`wallets`/`transactions`/`world_economy_state` 四表全部不存在（无 state 键、无 stub、无 route）；货币定义、余额、上架/购买/出售、NPC 主动偷窃/诈骗、`是否允许非法手段` 开关、经济总系统 tick（通胀/流动性/季节）全未实现。 |

### A5. 安全模块

| # | 问题 | 详述 |
|---|------|------|
| A5-01 | **A5.1 OOC（人设偏离）检测未实现（代码确认）** | `stubs/protection.py:23-28,118-154` 的 `guard_preview` 只匹配注入/探测关键词，无人设文档比对，无偏离度计算。AC-1（OOC 触发率 <1%）无法保证。 |
| A5-02 | **A5.1 危险场景例外机制缺失（代码确认）** | 决策树的「场景危险 → allow_with_exception」未做；`guard_preview(context=...)` 参数被接受但从未使用，无 world 危险等级 / 事件标签检查。AC-2（例外必须显式记录原因）未满足。 |
| A5-03 | **A5.1 输入预审 / 输出后审未接入 chat 管线（代码确认）** | 仅有一个手动 `POST /guard/preview` 端点（`routes/xijian_protection.py:57-63`），实时流式输出扫描、输入送模型前过滤均未接入 `stubs/chat.py`。 |
| A5-04 | **A5.1 数据模型 `safety_audit_log` / `safety_rules` 未实现（代码确认）** | 无这两张表；通用 `audits` 列表形状不同，verdict 为 `safe`/`blocked` 而非 `pass|warn|block|allow_with_exception`；规则硬编码，`safety_rules` 表缺失。 |
| A5-05 | **A5.1 工具调用审计未接入（代码确认）** | tool_calls 处理（`stubs/chat.py:420-684`）无 audit/guard 调用。 |
| A5-06 | **A5.1 审查模块崩溃降级为最严格档未实现（代码确认）** | 边界场景「审查崩溃 → 降级最严格档」无对应逻辑。 |
| A5-07 | **A5.2 电脑控制防护（MCP 防护）完全未实现（代码确认）** | 无 MCP 监控、无危险动作黑/白名单（`mcp_action_blacklist` 表缺失）；无全局快捷键安全终止（AC-2 <200ms）；无 freeze→dump_context→确认→sanitize→reload 流程；无专用备份文件夹 / 锁定模式 / `kill -9` 边界处理（notes 确认未实装）。 |
| A5-08 | **A5.3 自动快照部分缺失（代码确认）** | `safety_snapshots` 按清单 schema（scope/target_id/reason 枚举/expires_at）未实现，`snapshot()` 用 ad-hoc 形状（`stubs/protection.py:160-180`）；无 `backup_policies` 表、无 `max_total_bytes` 上限与超限提示；无 zstd 压缩（≥0.4）与用户同意压缩流程；无每小时定时自动快照（仅按需/过载时）。 |
| A5-09 | **A5.4 过载 action handler 仍有 3 个无外部调用方（代码确认）** | `degrade_tts` / `compress_memory` / `emergency_dump` 仅 `register_action_handler` 被 `ACTION_SUSPEND_IDLE_NPCS` 调用（`stubs/npcs.py:962-971`），另三个无调用方，等 A6/A1.2/A1.1 接入。 |

### A6. 实时通话

| # | 问题 | 详述 |
|---|------|------|
| A6-01 | **无实时全双工语音通话端点（代码确认）** | 仅一次性 REST `/v1/audio/speech` 与 `/v1/audio/transcriptions`（`audio.py:14,47`）；无双向 WS 语音路由、无 barge-in、无流式 STT/TTS（STT 后端为批处理）。 |
| A6-02 | **`voice_calls` / `call_events` 表未实现（代码确认）** | grep 全仓库（排除 macapp）无这两张表。 |
| A6-03 | **模型动作联动（VRM 骨骼/BlendShape）未实现（代码确认）** | 运行时按情感/语义触发 motion 的代码缺失；motion 仅在 `devkit/motion_editor.py` 离线编辑器存在，无运行时通话触发。 |
| A6-04 | **通话特效/动画未实现（代码确认）** | 角色配置触发的特效/简短动画无代码。 |
| A6-05 | **DiffSinger 歌声未接入通话（代码确认）** | `DiffSingerEngine`（`devkit/tts_engine.py:487`）仅为独立 dev TTS 引擎，未接入任何通话/唱歌管线。 |

### A7. 主动发起聊天或通话（仅 macOS/iOS）

| # | 问题 | 详述 |
|---|------|------|
| A7-01 | **`character_initiated_actions` 表未实现（代码确认）** | grep 无命中；主动决策逻辑未实现（仅 `ws_routes.py:207-217` 一个硬编码 demo 在连接 3s 后推固定消息 `char_yuki`，无频率上限、无配置/状态驱动决策）。 |
| A7-02 | **用户拒绝后「理解」写回记忆未实现（代码确认）** | AC-2 未做。 |
| A7-03 | **全局 / 按角色开关未实现（代码确认）** | 非 macapp 代码中无开关；系统通知 / 来电接听 UI 属 macapp（排除）。 |

### A8. 桌宠 / 动态壁纸（仅 macOS）

| # | 问题 | 详述 |
|---|------|------|
| A8-01 | **`desktop_pets` / `dynamic_wallpapers` / `pet_action_log` 表未实现（代码确认）** | grep 无命中；渲染/自由活动/飞行/壁纸模拟属 macapp（排除），非 macapp 无实现。 |
| A8-02 | **桌宠可审计日志未实现（代码确认）** | `pet_action_log` 缺失；仅 `ws_routes.py:258` 一个 no-op echo（`desktop_pet.command.echo`）+ `:256` 的 `emergency_pause` ack，无持久化。 |
| A8-03 | **壁纸模式写操作禁用强制未实现（代码确认）** | AC-4 未做；权限矩阵无 macapp 外强制代码。 |

### B. Apple TouchBar & Dynamic Island

| # | 问题 | 详述 |
|---|------|------|
| B-01 | **整章为 `[TODO: 本章待补]`（清单自认）** | 清单第 1295 行标记待补；非 macapp 代码无任何 `touchbar`/`dynamic_island` 实现。 |

---

## C. Development Kit

> 整体独立性约束满足（`devkit/_vendor.py` 自带 errors/ids/time，不 import 主 API）。C5 提交管线核心（7z 固实 / SMTP / 限流 429 / 体积 413 / 错误分类 / sha256 / manifest）已实装。下列为缺口。

| # | 问题 | 详述 |
|---|------|------|
| C0-01 | **DevKit 包位置与清单不符（代码确认）** | 清单要求位于 `core/xijian_api/devkit/`；实际为顶层 `devkit/` 包，`api.py`/`__init__.py` 在其内。独立性满足，但位置偏离（硬编码 SMTP 常量也在 `devkit/__init__.py` 顶部，非清单所述 `core/xijian_api/devkit/__init__.py`）。 |
| C1-01 | **C1.1 事件类工厂 / 上限 / 冲突检测未实现（代码确认）** | DSL 编辑器与校验已做，但「一类事件批量实例化」「单世界上限 200 条」「触发条件互相冲突 → 拒绝保存」未实现（关联 A4-02）。 |
| C2-01 | **C2.1 文本生成声音为桩（代码确认）** | `generate_voice_from_text`（`voice_cloner.py:201-205`）直接 HTTP 501「语音合成（MeloTTS）功能仍在制作中」；`MeloTTSEngine` 存在却未接入此路径，AC-2（可试听可调）不满足。 |
| C2-02 | **C2.1 声音克隆不克隆且跳过版权确认（代码确认）** | `clone_voice_from_file`（`voice_cloner.py:228-283`）仅 `save_voice` 存参考文件，无真实说话人适配，且 AC-1（克隆前版权确认）未做。 |
| C2-03 | **C2.1 TTSManager 引用未定义属性（代码确认/疑似 Bug）** | `get_engine`/`list_all_voices` 引用 `self._engines`，但仅定义 `self._tts_engines`/`self._singing_engines`（`tts_engine.py:694,697,704`），任意 `synthesize()` 抛 `AttributeError`。 |
| C2-04 | **C2.7 微调 / prompt 蒸馏为桩（代码确认）** | `start_finetuning_job`/`export_finetuning_dataset`（`dialog_editor.py:201-248`）返回占位 dict，无真实训练/导出。 |
| C2-05 | **C2.8 AI 生成 VRM 非真实（代码确认）** | `generate_model_from_text`（`model_viewer.py:264-327`）要么下载整个任意 HF repo，要么写占位 `.glb` JSON（"Placeholder — full AI VRM generation requires MLX backend"）；无文本+参考图→VRM 1.0 生成或骨骼绑定。 |
| C2-06 | **C2.8 FBX 导入不支持（代码确认）** | `register_model` 直接拒绝 `.fbx`（`fbx_conversion_unavailable`，`model_viewer.py:55-64`）；转换仅能调外部 `UNITY_PATH`/`BLENDER_PATH` CLI，清单将 `.fbx` 列为支持导入格式。 |
| C2-07 | **C2.8 VRM 1.0 校验仅部分（代码确认）** | `validate_model_format`（`model_viewer.py:140-185`）只查 glTF JSON 中 `VRM`/`VRMC_vrm` 扩展标记，未校验完整 VRM 1.0 规范；AC-3 模型大小上限（<50MB/<20MB）保存时不强制，仅记录 `size_bytes`。 |
| C2-08 | **C2.9 关键帧编辑 / 回放未实现（代码确认）** | 关键帧仅存 dict（`motion_editor.py:396-424`），无真实动画曲线编辑；回放未实现，正确性仅依赖骨骼名比对（`validate_motion_skeleton`）与外部 `bvh2vrm` CLI。 |
| C3-01 | **C3 剧情无执行/模拟引擎（代码确认）** | `plot_editor` 仅持久化 `nodes.json`/`edges.json`（`plot_editor.py:96-149`），无「被模拟世界读取并执行」的 simulate/execute，AC-1/AC-2 绑定强制仅为数据层、未校验运行。 |
| C4-01 | **C4 AI 设计辅助整体不可用（代码确认）** | `auto_suggest`/`suggest_with_questions`（`ai_assistant.py:133,144-148,207-213`）返回「暂不开放，请耐心等待」，真实 LLM 调用回退静态模板；US-C4-01/02、AC-2（AI 必须优先询问用户）未满足。 |
| C4-02 | **C4 `dev_ai_assist_log` 表未实现（代码确认）** | 用 JSON `assist_log.json` 近似，`source='ai_suggested'` 标记未跨 AI 产出字段强制；`ai_ratio` 为每事件代理值或直读调用方 payload，非清单的「字段计数」算法（`ai_assistant.py:98-111`,`devkit/__init__.py:832`）。 |
| C5-01 | **C5 7z 调用形式与清单字面不符（代码确认）** | 清单写 `py7zr.SevenZipFile(mode='solid')`；实际 `pack_payload` 用 `SevenZipFile(target, mode="w", solid=True)`（`devkit/__init__.py:505`）。功能等价固实，但非文档 API 形式。 |

---

## 缺口统计（按章节）

| 章节 | 缺口数 | 主要未实现区域 |
|------|--------|----------------|
| 架构 | 4 | 无 SQLite 持久化 / 无 MCP / 引擎选型未落地 / bge-m3 未接入 |
| A1 | 10 | A1.1 备份与受保护模块全缺、软删除缺失、引用审查不拦截 |
| A2 | 5 | 上下文注入不全、MCP 工具、多模态输入、支持矩阵 |
| A3 | 4 | 六张资源表缺失、残留 Live2D、状态未注入、动作库/缓存 |
| A4 | 9 | A4.3 POI/交通/互动全缺、A4.4 经济全缺、NPC 自动生成与 LLM 决策、场景生成、内置事件库 |
| A5 | 9 | A5.1 OOC/例外/接入缺失、A5.2 完全未做、A5.3 快照不全、A5.4 三 handler 无调用方 |
| A6 | 5 | 无全双工通话、数据表缺失、动作联动/特效/歌声未接 |
| A7 | 3 | 数据表缺失、决策逻辑/开关缺失 |
| A8 | 3 | 数据表缺失、可审计日志/写禁用未做 |
| B | 1 | 整章 TODO |
| C | 14 | C2.1/C2.8 真实生成桩、C3 无执行、C4 AI 不可用、C1.1 工厂、C5 形式偏离、包位置偏离 |
| **合计** | **~67** | |

> 说明：A4.1 事件调度、A5.4 过载判定、C5 提交管线、C1.1/C1.2/C1.3/C2.3/C2.5/C2.7 编辑器校验、A1.2 衰减/召回核心、A3.2 状态机/tick、A4.2 世界/算力/环境/审计/档位基础设施 等已实装，**不计入**上表，故实际完成度高于表面缺口数。
