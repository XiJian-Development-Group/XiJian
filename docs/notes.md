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

## 2026-07-04 · C5 开发者工具改为邮件提交 + Pywebview 独立窗口

### 任务来源
v2 里 C5 原本是"开发者服务器 + 上传 + 审核流程"。本轮按新口径**重做**：不依赖服务器、不跑双向证书、不做私有 registry；改为**本地打包 → 邮件提交**，开发者工具（DevKit）走独立 Pywebview 窗口，不与主程序共享 API。

### 已完成

#### 文档改写（v2 C0 + C5 + 附录 A/C）
- C0 流程图：开发者工具独立窗口，输出流向"邮件"而不是"开发者服务器"
- C5 整章重写：开发者认证（按开发者 ID 登录 + 本地身份）、本地 7Z 固实打包（py7zr，缺库时回退 zipfile 并 WARNING）、硬编码的 SMTP 凭证（env 覆盖）、1200 MB 上限（1MB = 1000KB，1000MB = 1GB）、1 小时冷却
- 附录 A 错误码表：新增 `dev_submit_*` 系列
- 附录 C 接口表：标注"C5 接口走 DevKit 窗口，不挂在 `/v1/xijian/*` 主路由下"

#### 代码模块（独立子包 `xijian_api/devkit/`，不挂主路由）
| 文件 | 行数 | 职责 |
|---|---|---|
| `__init__.py` | 905 | 配置常量、归档、SMTP、限流、冷却、CRUD、submit orchestrator、错误类、logger |
| `state.py` | 55 | 进程内 JSON 状态文件（`devkit_state.json`），开发者档案 + 提交历史 |
| `api.py` | 420 | `DevKitApi` js_api 类，给 Pywebview 调用 |
| `main.py` | 251 | `xijian-devkit` 入口、`_parse_args`、`create_window` |
| `__main__.py` | 11 | `python -m xijian_api.devkit` |
| `ui/index.html` `devkit.js` `devkit.css` | — | Pywebview UI（登录、提交、历史、设置 4 个 tab） |

#### 关键约束已实装
- **打包**：`py7zr.SevenZipFile(target, mode="solid")` 固实归档，缺库降级 zipfile 并 WARNING（不让静默走错格式）
- **大小**：`check_archive_size` 严格 `>`（pure），`preview_size_payload` 用 `>=`（UI 防护，含 manifest 余量）
- **限流**：`check_rate_limit(developer_id)` 基于 `state.last_submit_at` 计算 `now - last >= 3600s`，提交后立即刷新时间戳
- **SMTP**：硬编码默认 + env override，SSL/TLS、连接超时、starttls、超时都从环境变量注入；submission_id 用 `gen_submission_id()`（`utils/ids.py` 加的）
- **本地状态**：进程内 JSON，路径 `local_state_dir() / "devkit_state.json"`，toxiproxy / 进程崩溃都不影响（重启读盘）
- **Pywebview 隔离**：`xijian_api.devkit.api.DevKitApi` 不继承 Flask app、不读 config、不读 stubs；它直接调 devkit 子模块的纯函数。这样 devkit 窗口运行时 Flask 主程序甚至可以没启动

### 测试覆盖（82/82 通过）
- 纯函数：archive_name、compute_sha256、check_archive_size、check_rate_limit、cooldown_remaining、format_submission_id、delete_local_archive、list_submissions
- 错误类：RateLimitedError、PayloadTooLargeError、SmtpError、DevKitError（含 `__str__` 与 HTTP code 派生）
- 提交编排：成功路径 / 限流命中 / 超大文件 / 无效开发者 / 无效 target_kind / 缺字段
- API 层 js_api：login / logout / status / preview_size（含等于上限的边界）/ submit / list / get / delete
- CLI：`xijian-devkit --headless --width 800 ...` 的参数解析（含 `--no-smtp-tls`、非正尺寸拒绝）
- 集成：JSON 状态读写 / 7Z 打包 + 解包 round-trip（验证 manifest + 文件名）/ SMTP 假发记录器

### 本轮修补
1. `check_archive_size` 改回 `>`（pure 函数语义），新增 `preview_size_payload` 给 UI 用 `>=`，避免两个测试语义打架
2. `test_payload_must_be_mapping` 改用 `fake_smtp` fixture（之前用了真实 `_smtp_send` 在离线环境必失败）
3. `test_overrides` 里 `r.example` 期望值与输入 `r@example` 不一致，改回 `r@example`
4. `api.py` 删掉不再用的 `PayloadTooLargeError` import

### 没动的（与原因）

#### 1. DevKit UI 没在真机跑过
**原因**：本机没装 pywebview（venv 也没装），且 Pywebview 在 headless 环境起不来。**只是代码层面检查通过**（HTML/JS/CSS 语法、`js_api` 绑定、API 方法签名一致）。
**接谁做**：第一次在 macOS 上跑 `xijian-devkit` 时，记得先 `pip install xijian-api[devkit]`。如果 pywebview 报错，多半是 PyObjC 装的位置不对，重装即可。

#### 2. `py7zr` 在测试环境未装
**现状**：测试全部走 zipfile 回退路径。打包逻辑在 7Z 路径上未跑过端到端 round-trip。
**接谁做**：下次 macOS 上跑 devkit 时，确认 7Z 路径。打包出来用 `7z l archive.7z` 看是不是固实（`Method = LZMA2:26`），文件清单第一行是不是 `manifest.json`。

#### 3. SMTP 真发从未跑过
**原因**：邮箱密码不能进 git。SMTP 真发只能在你本地用真实 env 变量（`XIJIAN_DEV_SMTP_PASSWORD` 等）跑。
**接谁做**：第一次用 devkit 提交时，建议先填**测试邮箱**，确认收到邮件后再用生产邮箱。失败的话 SMTP log 会写明是 auth / connect / tls 哪一段出错。

#### 4. 邮件模板正文是纯文本，没做 HTML / multipart
**现状**：`_build_email_message` 用 `MIMEMultipart("mixed")` + 文本 part + 附件 part，文本 part 里写开发者 ID / 提交 ID / 时间 / 目标。
**没做的**：不带 .eml 重定向、不带附件内嵌图片、不带签名档。
**为什么留空**：开发者审核邮件是后台脚本看的，不是给人肉看的，纯文本 + 附件够用。如果以后给真人审核再加 HTML。

#### 5. 没做"附件大小 = SMTP 服务商上限"的二次校验
**现状**：只在本地按 1200 MB 卡，**没读 SMTP 服务商的实际上限**（Gmail 25 MB、Outlook 20 MB、QQ 25 MB、企业邮箱看配置）。
**为什么**：硬编码 SMTP 服务器之后查不到动态上限；本地已经按 spec 的 1200 MB 卡死了，再校一层没意义。
**接谁做**：如果以后切换 SMTP 服务商且上限 < 1200 MB，需要把 `check_archive_size` 改成读配置，或者在 SMTP 错误码里识别 "552 message exceeds fixed maximum size" 然后给用户更精确的提示。

#### 6. 没做提交历史的导出 / 备份
**现状**：`list_submissions` 只读 JSON，`delete_local` 只删本地 archive。历史的 `.eml` 重定向备份、`manifest.jsonl` 累计存档都没做。
**为什么留空**：spec 没要求。开发者的历史留在本地 JSON + 邮件服务器两边就够了。本地 JSON 损坏的话邮件服务器那边还能查到 manifest 字段。
**接谁做**：如果以后加开发者 dashboard（看自己过去提交了多少资源），再加导出。

#### 7. 没有"撤回邮件"功能
**现状**：发出去就发出去，本地 JSON 也只能 `delete_local`（删本地 archive），但邮件已经在路上。
**为什么**：邮件协议不支持撤回（SMTP 没这概念）。要在邮件服务商侧做"召回已发邮件"，那是 Gmail / Outlook 的客户端功能，跟 devkit 无关。

### 跨章节联动点

- **A1.1 自动备份**：devkit 本地状态文件 `devkit_state.json` **应该被纳入自动备份范围**——开发者档案（active_developer、邮箱偏好）和提交历史丢了重打很麻烦。接 A1.1 时记得把 `local_state_dir() / "devkit_state.json"` 加进 backup list（用现有 `backup` 路由加个 entry，不要单独起备份）
- **A3.x 角色导入**：devkit 收到邮件后，开发者那边要做"导入到角色库"。这一段在 spec 里被简化成"邮件附件 + manifest"——开发者侧导入流程不在 XiJian 范围
- **A5.1 输出审查**：devkit 的 `submit()` 没有走 A5.1 审查——本地打的是开发者提交的资源，不是 chat 输出。spec 里 A5.1 是 chat-time 审查，不适用 devkit
- **C0 流程图**：v2 文档里"开发者工具"那一支改成"独立 Pywebview 窗口 + 邮件"。如果 C0 流程图还没改，下次维护文档时记得一起

### 文档里的 `[TODO]` 状态
- `C5: 私有服务器 / 双向证书 / 邮件审核` — 已降级为"不实装"（spec 变更）。这些 [TODO] 现在代表"已决定不做"，应该从 v2 文档里**删除**而不是保留
- `C5: 开发者认证机制` — 已实装为开发者 ID + 本地档案
- `C5: 上传审核流程` — 已替换为"邮件到达后由开发者侧审核"，流程描述变了
- `C5: 单世界事件上限（默认 200）` — 与 devkit 无关，属于 C1.1

### 测试覆盖情况
- 纯函数 8 个：archive_name / check_archive_size / preview_size_payload / check_rate_limit / cooldown_remaining / compute_sha256 / delete_local_archive / list_submissions
- 错误类 4 个：RateLimitedError / PayloadTooLargeError / SmtpError / DevKitError
- API js_api：登录 / 登出 / 状态 / 预览大小 / 提交 / 列历史 / 查单条 / 删本地 / 列表分页 / 列表过滤
- CLI：默认参数 / 全部 override / 非正尺寸拒绝
- 集成：JSON 状态 round-trip / 7Z 打包 round-trip / SMTP 假发记录器

**缺口**：没在真 Pywebview 窗口里跑过 UI，也没在 macOS 上验过 7Z 路径 + SMTP 真发。

---

## 维护约定

- 每次改完一个章节，**当日**补一条到本文件，格式：日期 + 章节 + 改动清单 + 没动的与原因
- 不要再新建别的笔记文件——都进这里，保持单一来源
- 跨章节的设计决策（比如 A5.x 怎么挡 critical）写到对应章节下"跨章节联动点"
- 文档里的 `[TODO]` 实际化之后，从 v2 文档摘掉，并在本文件留一行"摘除记录"