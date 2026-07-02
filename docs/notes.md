# 隙间(XiJian) 开发笔记

> 这是开发过程中**有意保留的口径笔记**：哪些做了、哪些没做、为什么、之后接谁做。
> 与 `Dev. Function List功能清单v2.md` 配套——v2 是规范（要做什么），notes 是工程现实（实际做了什么、留了什么）。

---

## 2026-07-02 · A3.2 角色状态系统收尾

### 任务来源
之前盘点（你贴过来的状态清单）把 A3.2 标成"部分实现，缺定时 tick 循环"。实际进代码一看，核心 stub + 路由 + 测试都已就位（1088 行 stub + 269 行路由 + 927 行测试）。本轮做的是**查漏 + 收尾**，不是从零起。

### 已完成（实测一遍）
- `stubs/character_state.py`（1088 行）：6 状态机、5 套默认值、纯函数、state/config CRUD、modifier、log、status handler 注册、WS 广播、apply_field_change、tick_character / tick_all、后台 tick 线程、summary
- `routes/xijian_characters.py`：10 个端点全挂上
  - `GET /v1/xijian/characters/<id>/state`（合并 v1 affection/mood + A3.2 numeric）
  - `POST /v1/xijian/characters/<id>/state`
  - `GET/PATCH /v1/xijian/characters/<id>/state/config`
  - `GET /v1/xijian/characters/<id>/state/log?limit=N`
  - `POST /v1/xijian/characters/<id>/state/tick`（dev only，需 `XIJIAN_DEV=1`）
  - `POST /v1/xijian/characters/<id>/state/recover`
  - `POST /v1/xijian/characters/<id>/state/recovering`
  - `PUT/DELETE /v1/xijian/characters/<id>/state/modifier`
  - `GET /v1/xijian/characters/<id>/state/behavior`
- 集成：`seed_all()` 在 `app.py:create_app` 里拉起；`conftest.py` 用 `XIJIAN_STATE_TICK=0` 关掉 tick 防测试抖动
- 测试：103/103 通过（core 全套 306 通过，0 回归）

### 本轮修补清单
1. `stubs/character_state.py:_default_state_record` — 删掉误导性的 `activity_modifier` 死字段（实际走 `cfg["modifiers"]`，不在 state record 上）
2. `stubs/character_state.py:_tick_loop` — `generation` 参数从"收了不用"改成"真检查"：reset 后老一代线程立刻退出，不再等下一个 interval
3. 新增测试：
   - `TestTickLifecycle::test_reset_bumps_generation` 锁住 #2 的行为
   - `TestTickStateMachineE2E`（4 个）覆盖 tick → decay → 状态机迁移 → log 全链路
   - `TestTickRouteE2E::test_tick_route_drives_status_via_decay` 端到端走 HTTP

### 没动的（与原因）

#### 1. chat pipeline 没注入 A3.2 summary 到 system message
**v2 流程图说**：`A[用户/工具调用请求] → B[安全预检 A5.1] → C[加载角色人设文档 C2.4] → D[加载记忆上下文 A1.2] → E[加载当前状态 A3.2] → F[加载世界上下文 A4] → ...`

**为什么没接**：spec 里 E 步描述很松（"加载当前状态"），没规定注入格式。stubs/chat.py 现在只调 `memory.load_context` 拼 memory_block。两条路可选：

- 路径 A（推荐后续做）：在 `chat.py` 里多调一行 `cs_stub.summary(character_id)`，把 `values / status / active_behavior` 拼成 Markdown 加到 system prompt 的末尾。需要补：
  - 拿 character_id 的统一入口（现在 `_run_recall_pipeline` 里 `default_character_id` 取自 tool call args，不是上游传入的）
  - 注入格式：建议用 `<!-- xijian:state -->\n{markdown}\n<!-- /xijian:state -->` 风格，与 memory block 的边界对齐

- 路径 B（保守）：UI 单独调 `GET /state` 渲染，不进 LLM。好处是 token 预算更紧；坏处是模型看不到角色当前状态，对话可能与状态不一致（比如角色已经 critical 但模型还在热情回应）。

**建议**：走路径 A，但**只注入 status + active_behavior.trigger，不注入 4 个数值**——4 个数值会撑爆上下文。让模型知道"角色现在疲惫 / 病了"就够了，具体数字 UI 渲染。

**接谁做**：下次动 chat pipeline 时顺带做。

#### 2. `_STATUS_HANDLERS` 没有任何模块订阅
状态机进入 Hungry / Thirsty / Sick / Recovering / Critical 时会调 `_publish_state_change`，但目前没人 `register_status_handler` 订阅。

**为什么留空**：handler 应该由下游模块来订：
- A5.1 安全审查 — Critical 触发通知用户
- A6 实时通话 — Critical 自动挂断
- A7 主动发起 — Critical 时不发主动聊天
- A8 桌宠 — Critical 时换低耗动画

**接谁做**：上述四个模块任一起的时候顺带注册。比如 A5.x 做 OOC 评测时，加一个订阅 Critical 的 handler，把"进入 critical"记到 audit log。

#### 3. `can_dialogue=False` 没有在 chat pipeline 阻断
**v2 边界场景**："健康 ≤ 0 → 角色不可对话"

**代码现状**：`stubs/character_state.can_dialogue()` 返回 False，但 `routes/chat.py` 不查这个标记。意味着 health=0 时模型仍然会被调用。

**为什么留空**：这是 A1.2/A5.x 的范畴（"安全预检"）。spec 把 A5.1 的"输入预审"作为 chat 前的强制关卡——critical 应该在那里挡掉，不在 A3.2 里做。

**接谁做**：A5.1 输入预审链路上加一行 `if not cs_stub.can_dialogue(character_id): raise ApiError(409, "character is critical")`。这一行**大约 5 行**，A5.x 起来时一定记得加。

#### 4. `tick_thread` 是 daemon 线程
**代码**：`start_tick()` 里 `daemon=True`。意味着主进程退出时线程被强制杀，正在算的 tick 丢失。

**为什么这样**：daemon 是简单正确选择——主进程本来就是 Flask，崩溃时让 tick 一起死、避免僵尸线程。tick 是幂等的（基于 `last_updated` 算 dt），丢一次下次重启从恢复点继续算，无副作用。

**注意**：生产里如果主进程要优雅退出（SIGTERM 跑 shutdown handler），记得调 `cs_stub.stop_tick()` 等线程退出。否则 HTTP server 关闭后 tick thread 还在算，但 stub 状态已经被 reset（如果 seed_all 被重新调），dt 会爆。

#### 5. `_current_interval()` 硬地板 1s
**代码**：env 设成 `0.1` 也会被钳到 `1.0`。

**为什么**：spec 默认 60s，1s 是给测试用的最紧频率。再快会让 tick_character 在 50 角色规模下 O(N) 计算显著（虽然仍是 µs 级，但配合高 decay rate 容易把状态快速打崩测试夹具）。

**接谁做**：如果将来要支持 sub-second tick（比如实时模式），改这个地板 + 把 tick_character 内部加 batch 调度即可。

#### 6. `LOG_MAX_ENTRIES = 2000` 是软上限
**代码**：`_append_log` 每次 append 后检查，超过 2000 就把最老的删掉。

**为什么是 2000**：50 角色 × 60s tick × 4 字段 ≈ 1200 entries/hour（极端），2000 够覆盖 ~2 小时诊断窗口。超过就要打 archive 到磁盘，spec 里说"诊断窗口"足够。

**接谁做**：A1.1 自动备份（路由都缺）做起来时，把 archive 路径串起来——把 character_state_log 周期 dump 到 `backups/character_state_<ts>.jsonl`。

### 跨章节联动点（之后模块会碰 A3.2 的）
- **A4.2 配角算力调度**：高活跃 NPC 也应该有 A3.2 状态衰减（NPC 也会饿）。但 NPC 用的是 `npc.compute_budget`，不是 character state。要不要复用同一套 stub？建议**复用**——`cs_stub.tick_character(npc_id)`，配置里加一行 `character_type: 'npc'` 让 decay 慢一点。
- **A5.4 过载防护**：档位切换时，"暂停非活跃配角 tick"——这里暂停的是 A4.2 的 NPC tick，**不是** A3.2 的主角色 tick。主角色永远要算（用户在用）。代码位置：overload 的 handler 里调 `cs_stub.set_modifier(char_id, {"activity_modifier": 0.0})`？不对，应该是跳过整个 character_id——目前 API 没暴露 per-character 暂停。要加 `cs_stub.suspend(character_id)` / `cs_stub.resume(character_id)`。
- **A6 实时通话**：通话开始时 `register_status_handler(SICK, lambda e: end_call_if_sick(...))`。通话事件 `enter_recovering` / `force_recover` 通过 HTTP 调用 `POST /state/recover`。
- **A7 主动发起**：同上，订阅 Critical 阻止主动聊天。

### 文档里的 `[TODO]` 状态
- `A3.2: tick 间隔 N 默认 60s` — 已实装为 `DEFAULT_TICK_INTERVAL_SECONDS = 60.0`，env 可覆盖。可在文档里把 `[TODO]` 摘掉。
- `A3.2: 时间因子 / 世界/活动修饰因子` — 已实装为 `cfg["modifiers"]` 三件套。可在文档里把 `[TODO]` 摘掉。

### 测试覆盖情况
- 纯函数 5 个（clamp/decay_amount/compute_target_status/resolve_behavior_bindings）+ 状态机 9 个分支全过
- 状态 record CRUD / apply_field_change / apply_patch / tick_character / can_dialogue / force_recover / enter_recovering / Modifiers / Log / Status handlers / Tick lifecycle / WS / Summary 都有专门测试类
- HTTP 路由 15+ 测试（含 404 / clamp / dev tick / recover / recovering / modifier / behavior）
- 端到端 2 个（test_xijian_character_state.py:907 行起 + 新加的 5 个）

**缺口**：并发安全测试没做。tick_thread 与路由同时改 state record 时，dict 写入不是原子的。50 角色规模撞不上问题，但生产环境如果加数据库后端就要补一层锁。

---

## 维护约定

- 每次改完一个章节，**当日**补一条到本文件，格式：日期 + 章节 + 改动清单 + 没动的与原因
- 不要再新建别的笔记文件——都进这里，保持单一来源
- 跨章节的设计决策（比如 A5.x 怎么挡 critical）写到对应章节下"跨章节联动点"
- 文档里的 `[TODO]` 实际化之后，从 v2 文档摘掉，并在本文件留一行"摘除记录"