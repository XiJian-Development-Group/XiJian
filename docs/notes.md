# 隙间(XiJian) 开发笔记

> 这是开发过程中**有意保留的口径笔记**：哪些做了、哪些没做、为什么、之后接谁做。
> 与 `Dev. Function List功能清单v2.md` 配套——v2 是规范（要做什么），notes 是工程现实（实际做了什么、留了什么）。

---

## 2026-07-20 · A5.2 MCP 防护实装（从零起）

### 任务来源
v2 spec §A5.2 列了 5 个产品故事（黑名单 100% 拦截 / 全局快捷键安全终止 / 白名单明示允许 / freeze→dump→confirm→sanitize→reload 终止后流程 / 受保护模块备份）+ 4 个验收标准（AC-1 黑名单 100% 拦截 / **AC-2 安全终止 < 200ms** / AC-3 恢复后 AI 从备份继续 / AC-4 备份存专用文件夹 + 受保护模块覆盖）+ 边界场景（多次连续安全终止 → 锁定模式冷重启 / MCP 进程僵死 → 强制 kill -9 重启）。spec 数据模型引用了 `mcp_action_blacklist` 表 + A5.3 的 `safety_snapshots`（A5.3 还没起）。spec 里有 1 个 `[TODO]`（全局快捷键默认 ⌃⌥⌘Q / Win+Alt+Shift+Q）。

盘点代码：state.py **完全没有** `mcp_rules` / `mcp_audit` / `mcp_freezes` / `mcp_snapshots` 这 4 个 bucket，没有 stub、没有路由、没有测试——**100% 从零起**。

### 已完成（实测一遍）

#### 范围划分（先想清楚再做）
A5.2 spec 同时包含两类东西：
1. **服务器侧权威性**（core stub 范畴）：rulebook / gate 决策 / freeze state machine / snapshot dump+restore+sanitize / 审计 / per-world policy
2. **桌面客户端侧**（Pywebview 范畴，不在 core）：全局快捷键监听 / UI 确认弹窗 / 进程 SIGFREEZE 实际发信号 / kill -9 MCP 进程

本轮只做 #1，#2 走"等 Pywebview 客户端起时调用 server 侧 API"的路径——server 是**真值源**，客户端观察 + 触发。这是和 A5.1 / A5.4 同一套分工。

#### 新建 stub
- `stubs/mcp_rules.py`（~350 行）：8 种 `action_kind`（`file_delete` / `file_write` / `file_read` / `shell` / `network` / `app_launch` / `settings_modify` / `system_cmd`），2 种 `mode`（`blacklist` / `whitelist`），`severity ∈ [1, 5]`，`is_active` A/B 开关，pattern 上限 4 KB，broken-regex 静默跳过，`match_action_rules` 热路径按 severity desc 排序 + **unknown kind 拒绝匹配**（spec 边界：unknown action_kind 不能 fallback 到别的 kind 的规则，必须 deny-by-default）
- `stubs/mcp.py`（~900 行，三大子系统）：
  - **Gate `check()` 决策树** 7 个分支按顺序短路：
    1. A5.4 overload recovery 窗口 → `allowed` + `blocked=overload_active`（不升级 deny；A5.1 / A4.4 同模式）
    2. world 在 lockout → `denied_lockout` + `blocked=world_lockout`
    3. world 有 pending freeze → `denied_frozen` + `blocked=world_frozen`（不返 freeze_id 因为客户端在 confirm 流程中，自己知道是哪个）
    4. blacklist hit → `denied` + `blocked=blacklist_hit` + `matched_rule`（severity 最高的）
    5. whitelist hit → `allowed` + `matched_rule`
    6. no match + policy.default=deny → `denied` + `blocked=default_deny_no_match`
    7. no match + policy.default=allow → `allowed`（黑名单兜底）
    - **Self-crash fallback**：scan 内部 try/except，任何异常 → `verdict=denied_crashed` + `blocked=check_crashed` + `reason=check_crashed: <ExceptionClass>`
  - **Safety-stop 状态机** `safety_stop` / `confirm_safety_stop` / `cancel_safety_stop`：
    - 6 状态：`frozen`（init 后初始） / `awaiting_confirm`（dump snapshot 之后） / `sanitizing`（restore 中间状态，目前合并在 confirm 内） / `restored`（confirm 成功） / `cancelled`（cancel 路径） / `lockout`（3-in-60s 触发）
    - **Lockout 触发**：60s 窗口内累积 3 次 safety_stop → 当前 freeze 状态转 `lockout` + per-world `lockout_until = now + 600s` 持久化 + 后续 `check()` 全返 `denied_lockout` + 后续 `safety_stop` 抛 `MCPLockoutError`（route 层 409）
    - **`clear_lockout(world_id)` 同时清空 `_FREEZE_HISTORY[world_id]`**——否则 cold restart 后下一个 safety_stop 仍会立刻再触发 lockout（4-in-60s 窗口内）。这是 spec "要求冷重启" 的字面解释：操作者主动 reset 必须重置历史
  - **Snapshots** `dump_snapshot` / `sanitize_snapshot` / `restore_snapshot`：
    - **`PROTECTED_BUCKETS = ("worlds", "characters", "memory", "sessions")`** 常量 = spec AC-4 "受保护模块" 集合。dump 时遍历，deep-copy 进 `payload`（改 payload 不影响 live state）
    - `file_path = "mcp_snapshots/<snap_id>.json"` — **服务端硬编码**，request body 里的 path 字段**不读不写**（防止路径逃逸出备份目录）
    - `sanitize_snapshot` 复用 A5.1 的 `state.safety_rules` 里所有 active 的 `forbidden_word` 规则做字段级 scrub（**只**走字符串叶子，dict key=`__meta` 跳过，循环引用防护）；scrub 出的字符串用 `[sanitized]` 替换
    - `restore_snapshot` 先调 `sanitize_snapshot` 兜底（即使 caller 跳过了显式 sanitize）——AC-3 "恢复后 AI 必须从备份的上下文继续" 的隐含要求
  - **Audit log** 每次 `check()` 写一条，5 个 verdict（`allowed` / `denied` / `denied_lockout` / `denied_frozen` / `denied_crashed`），3 个 module-level `_seq` 计数器（audit / freeze / snapshot）保证同秒插入的稳定排序，snippet 240 字符截断

#### 接入
- `state.py`：加 4 个 bucket（`mcp_rules` / `mcp_audit` / `mcp_freezes` / `mcp_snapshots`）+ `reset_for_testing` 清空
- `utils/ids.py`：加 `gen_mcp_rule_id` / `gen_mcp_audit_id` / `gen_mcp_freeze_id` / `gen_mcp_snapshot_id`（前缀 `mcpr_` / `mcpa_` / `mcpf_` / `mcpsnap_`）
- `stubs/__init__.py`：加 2 个新子模块到 import 列表 + `seed_all()` 调用 + `__all__`
- `routes/__init__.py`：`xijian_api.routes.xijian_mcp` 加进可选路由表
- `routes/xijian_mcp.py`（~400 行）：
  - `POST /v1/xijian/mcp/check` — gate 热路径
  - `GET/POST/GET/PATCH/DELETE /v1/xijian/mcp/rules[/:id]` — rules CRUD
  - `GET /v1/xijian/mcp/audit[/count]` — 审计查询
  - `GET/PUT/DELETE /v1/xijian/mcp/policy/:wid` — per-world policy（含 `clear_lockout` 走 `set_world_policy(clear_lockout=True)`）
  - `POST/GET /v1/xijian/mcp/safety_stop` + `GET /v1/xijian/mcp/safety_stop/:id` + `POST .../confirm` + `POST .../cancel` — 状态机
  - `GET /v1/xijian/mcp/snapshots` + `GET /:id` + `POST /` + `POST /:id/sanitize` + `POST /:id/restore` — 快照 CRUD
  - `POST /v1/xijian/mcp/dev/crash` — XIJIAN_DEV=1 演练 rulebook 自崩 fallback
- `tests/conftest.py`：加 2 个新 stub 的 `reset_for_testing`（reset 顺序：`mcp_rules.reset_for_testing()` 在前，再 `mcp.reset_for_testing()`；这样下一轮 test 的 sanitize 拿到的 active `forbidden_word` 是空的）

#### 测试（262 个新 case，0 flaky）
- `test_xijian_mcp_rules.py`（105 个 case）：纯函数（kind/mode/pattern/severity 验证 + regex compile + broken regex）/ CRUD 26 / list_active 按 severity desc + 按 kind+mode 过滤 4 / list_all + filter 5 / update（含 immutable id/created_at）10 / delete 2 / match_active_rules 10（unknown kind 不匹配 / empty payload / broken regex 跳过 / case-insensitive）/ HTTP CRUD 18 / 鉴权 5
- `test_xijian_mcp.py`（157 个 case）：纯函数（flatten_payload 含 dict/list/nested/depth-limit / truncate / seq 3 个计数器 / world policy / lockout 过期自动清）22 / audit 12 / gate `check` 全 7 分支（overload / lockout / frozen / blacklist / whitelist / default_deny / default_allow / self-crash 含 audit 写入验证）20 / safety-stop 状态机 18（init / pending-freeze 拒绝 / 3-in-60s lockout / lockout 跨世界隔离 / clear_lockout 重置 + 清历史 / list+filter+limit / confirm 跑 dump+sanitize+restore 全套 / cancel 释放世界 / 状态守卫）/ snapshot 26（dump+deep-copy 隔离 / protected buckets 全覆盖 / sanitize 含 A5.1 forbidden_word 联动 / sanitize 跳过 inactive 规则 / sanitize 跳过 __meta / sanitize 幂等 / restore auto-sanitize 兜底）/ lifecycle 2 / HTTP 47（check / audit list+count+filter / policy CRUD / safety_stop 全 11 端点含 409 lockout_active + 409 freeze_pending / snapshots 全 9 端点 / dev crash 含 403 gate）/ 鉴权 16

#### 真实启动验证
端到端跑通：world → 3 rules 创建（blacklist `rm` severity 5 / blacklist `shutdown` severity 5 / whitelist `^chrome$` severity 3）→ check `rm -rf /` → `denied` + matched_rule=blacklist / check `ls -la` → `denied` + `default_deny_no_match`（默认 deny 政策） / check `chrome` → `allowed` + matched_rule=whitelist / `safety_stop(world_id=w, reason='test')` → `frozen` / `confirm` → `restored` + snapshot_id 已写入 + restore_summary 4 个受保护 bucket 全恢复（`worlds` / `characters` / `memory` / `sessions`）/ audit 3 条全部可查。

### 没动的（与原因）

#### 1. AC-2 "安全终止响应延迟 < 200ms" **没法在 server 侧验证**
**现状**：AC-2 测的是**端到端**快捷键 → MCP 进程冻结的延迟，server 侧的 `safety_stop` 调用本身是内存操作（us 级），但 spec 关心的是全局快捷键监听 → 跨进程 IPC → 服务端 → MCP 进程 SIGFREEZE 的总时长。本轮只给了 server 侧 API，**没有**全局快捷键监听。
**为什么留**：快捷键监听是 Pywebview 客户端（macOS 用 `pynput` / Windows 用 `keyboard`）的活儿，server 没法独立验。客户端起时需要 1) 在 `mavis` 主循环或 Pywebview 窗口里注册快捷键 2) 触发时直接调 `client.post("/v1/xijian/mcp/safety_stop", ...)` 3) 拿 freeze_id 后调客户端自己的"显示确认弹窗"逻辑 4) 用户点确认 → 调 `confirm_safety_stop`、点取消 → 调 `cancel_safety_stop`。
**接谁做**：Pywebview 客户端起时（约 30-50 行 JS + Python 桥接 + 端到端 timing 测试）。**注**：server 侧从快捷键 fire 到 `safety_stop` 入库 < 1ms，client 侧的 latency 主要在 IPC 通道，不是 server 能优化的。

#### 2. 全局快捷键默认 ⌃⌥⌘Q / Win+Alt+Shift+Q `[TODO]` **没摘**
**现状**：v2 spec 写"[TODO: 默认 ⌃⌥⌘Q / Win+Alt+Shift+Q]"。本轮 server 侧**没有**任何"快捷键"的概念（也没必要有）——server 接收的是已触发后的 `safety_stop` 调用。
**为什么留**：同 #1，桌面客户端域。server 可以接受"任何来源"（hotkey / 程序 / 调试命令）的 `safety_stop` 请求。
**接谁做**：Pywebview 客户端起时 1) 在 settings 加 "安全终止快捷键" 字段（默认 ⌃⌥⌘Q，Win 上 Win+Alt+Shift+Q）2) 启动时注册 3) 触发 → 调 `/v1/xijian/mcp/safety_stop`。v2 spec 这个 `[TODO]` 在客户端起来时一并摘除。

#### 3. `mcp_action_blacklist` 表 vs `mcp_rules` 表 **没分两张**
**现状**：spec §A5.2 数据模型写 `mcp_action_blacklist`（只有黑名单）。本轮 `mcp_rules` 是**单表双 mode**（`mode=blacklist` 或 `mode=whitelist`），没有分两张物理表。
**为什么留**：spec 写"黑名单：删除系统文件 / 关机 / 修改安全模块"+"白名单：明示允许的动作"——并列的两种 list 放一张表是规范化（mode 列控制）。SQL 层面分两张表的好处是索引更窄 / 写入并发可分；坏处是 CRUD 端点要写两份代码。本轮 stub 端代码放单表，**真实 SQL 落库时**可以拆成 `mcp_action_blacklist` + `mcp_action_whitelist` 两张表（同 `mode` 列约束 + 跨表 unique id），API 形态不变。
**接谁做**：SQL 落库章节（应该是 C2.x 数据层持久化）。

#### 4. 真实 `kill -9` + MCP 进程重启 **没接**
**现状**：spec 边界场景 "MCP 进程僵死 → 强制 kill -9 并按恢复流程重启"。本轮 server 侧**不拥有** MCP 进程——MCP 进程是桌宠客户端的子进程，server 通过 HTTP 协议交互。
**为什么留**：跨进程问题。客户端侧的 recovery 流程是：1) watch MCP 子进程的 PID 2) 进程无响应 > 5s → `os.kill(pid, SIGKILL)` 3) 用最近的 snapshot 重新拉起 MCP 4) 通知 server 调 `/v1/xijian/mcp/snapshots/:id/restore` 让 server 状态从备份恢复。
**接谁做**：Pywebview 客户端起时。**server 侧可以做的**：暴露一个 `POST /v1/xijian/mcp/recover` 端点接收"MCP 进程已重启 + 客户端要求重载"的信号，本轮**没加**（不在 A5.2 核心 scope；recovery 走 `restore_snapshot` 路径已经覆盖）。如果客户端起时觉得需要单独端点再加。

#### 5. 没有"安全模块受保护"的具体清单
**现状**：v2 §A5.2 写"修改安全模块"是黑名单动作之一，但没说哪些文件算"安全模块"。本轮 `KIND_SETTINGS_MODIFY` action_kind 给出来了，**没有**内置默认规则（operator-curated）。
**为什么留**：黑名单内容是 operator 决策（产品+合规层面），不属于 stub 默认值。`safety_rules` 同模式（无默认 forbidden_word / ooc_pattern）。operator 配的示例规则会在 Pywebview 客户端 first-run 时一次性 seed 进去（这一段也走客户端域）。

### 跨章节联动点（之后模块会碰 A5.2 的）

- **A1.1 / A1.2 记忆**：dump snapshot 时如果 `state.memory` 里有敏感条目（API key / 密码），sanitize 应该 scrub。**本轮** A5.1 `forbidden_word` 复用兜底；如果未来 operator 想区分"内容敏感"和"凭证敏感"，需要 A5.2 扩 `sanitize` 的策略源（既用 A5.1 又用专门的 credential scanner）。**本轮没接**
- **A2 chat pipeline**：模型做 tool call 之前必须过 `check()`。**本轮** `stubs/mcp.check` 已 ready，**没**接到 `stubs/chat.complete()`。A2 chat pipeline 章节起时在 tool_call dispatch 入口前插 `mcp_stub.check(action_kind=tool_kind, args=tool_args, world_id=wid)`——`verdict == 'allowed'` 才真发，否则返 fallback。**本轮没接**
- **A3.2 角色状态**：高 sick / 濒死状态的 NPC 更可能误触发危险 tool call——`check()` 应该在 `character_id` 已知 + 该角色状态危险时自动 +1 严格（参考 A5.1 的 `set_safety_threshold` 联动）。**本轮没接**
- **A4.1 事件调度**：fire 战斗类事件时 world `is_dangerous` 会被 A5.1 改成 true；A5.2 没有"dangerous world 自动收紧 MCP 政策"的联动（目前 `default=deny` 是稳态配置；操作者想严格化只需 PUT policy.default=deny）。**本轮没接**
- **A4.2 NPC 调度**：高 importance NPC 调用敏感 tool 时，决策应该更保守。**本轮没接**（gate 只看 action_kind + payload + world policy；character_id / npc_id 留作未来扩展）
- **A4.4 经济系统**：钱包 balance < 0 的 user 更可能铤而走险（黑名单模式？）；A5.2 没有联动。**本轮没接**
- **A5.1 输出审查**：本轮已经**双向联动**了：1) `sanitize_snapshot` 复用 A5.1 `forbidden_word` 规则 scrub 快照 2) A5.1 之前留的"A5.2 tool_call stage 没接"现在已 ready——A5.1 + A5.2 是"内容审核 + 行为审核"双闸。**已接（单向 A5.1 → A5.2）**
- **A5.3 自动备份**：A5.2 的 `dump_snapshot` 与 A5.3 的 `safety_snapshots(scope=safety_stop)` 数据模型是**同一张表的两个写入路径**。A5.3 起时需要决定 1) A5.2 snapshots 是否进 A5.3 总表 2) A5.3 的"压缩 / 空间上限"策略是否覆盖 A5.2。spec 没明说，本轮 A5.2 `mcpsnap_` 是独立 bucket，**等 A5.3 起时合并决策**。
- **A5.4 过载防护**：本轮已接——A5.4 recovery 窗口内 `check()` 短路放行（`reason=overload_active_short_circuit`）。见 `mcp.py:_is_overload_active`
- **A6 实时通话**：语音 call 期间 MCP tool call 概率低（口语化交互），但**安全终止**仍然需要工作——A6 客户端起时不要屏蔽全局快捷键。**本轮没接 / 不需要接**（server 侧无差别）
- **A7 主动发起**：AI 自己起的 chat 同样可以触发 tool call——A5.2 `check()` 对所有 source 一视同仁。**不需要单独接**
- **A8 桌宠 / 壁纸**：桌宠气泡框触发 tool call（如"帮我开 X 应用"）必须过 A5.2 gate。**本轮没接**（等 A8 客户端起时调）

### 文档里的 `[TODO]` 状态
v2 §A5.2 有 1 个 `[TODO]`："[TODO: 默认 ⌃⌥⌘Q / Win+Alt+Shift+Q]"——**本轮没摘**。server 侧无快捷键概念；客户端起时一并摘除。

### 测试覆盖情况
- `mcp_rules` stub 105 个 case：纯函数 30（kind 8/mode 4/pattern 6/severity 11/compile 3）/ CRUD 22 / list_active 4 / list_all 5 / update 10 / delete 2 / match_active_rules 10 / HTTP 18 / 鉴权 5
- `mcp` stub 157 个 case：纯函数 22（flatten 8 / truncate 4 / seq 4 / world policy 10 / lockout 过期 2）/ audit 12 / gate 7 分支 20 / safety-stop 状态机 18 / snapshot 26 / lifecycle 2 / HTTP 47 / 鉴权 16
- 总：262 个新 case，**1456** 总（1194 基线 + 262 新），0 回归

**缺口**：
- **AC-2 (latency) 没法在 server 侧验**——见"没动的 #1"
- **全局快捷键 + UI 确认弹窗 + SIGFREEZE 真实信号没接**——见"没动的 #1 / #2"，需要 Pywebview 客户端
- **真实 `kill -9` + MCP 进程重启没接**——见"没动的 #4"
- **`mcp_action_blacklist` SQL 落库**——见"没动的 #3"
- **A2 chat pipeline 集成**——最大的 server 侧联动缺口；A2 起时插 `check()` 即可

---

## 2026-07-19 · A5.1 输出审查实装（从零起）

### 任务来源
v2 spec §A5.1 列了 2 张表（`safety_audit_log` / `safety_rules`）+ 5 个产品故事（OOC / 危险场景 / 帕姆严格度 / 输出后审 / 输入预审 / 人设保护 / 例外机制 / 工具调用审计）+ 3 个验收标准（AC-1 OOC 触发率 < 1%（**带 [TODO] 评测集**）/ AC-2 危险场景例外必须显式记录 / AC-3 所有拦截事件可查询）+ 边界场景（审查模块自身崩溃 → 降级为最严格档）。盘点代码：state.py **完全没有** `safety_audit_log` / `safety_rules` 这 2 个 bucket，没有 stub、没有路由、没有测试——**100% 从零起**。

### 已完成（实测一遍）

#### 新建 stub
- `stubs/safety_rules.py`（307 行）：3 种 `rule_kind`（`ooc_pattern` / `injection_pattern` / `forbidden_word`），`severity ∈ [1, 5]`，`is_active` A/B 开关，pattern 上限 4 KB，broken-regex 静默跳过（不 crash scan），`match_active_rules` 热路径按 severity desc 排序
- `stubs/safety.py`（567 行）：
  - **`scan_input`** 决策树：injection 必 block（不区分 severity）/ forbidden word 按 severity 分 `warn` / `block` / `hard_block`（threshold+2）/ clean → `pass`；A5.4 overload recovery 窗口内短路放行
  - **`scan_output`** 决策树：OOC 在 `world.is_dangerous=True` **且** `event_tags ⊇ {dangerous / danger / extreme / fatal / catastrophic}` 时转 `allow_with_exception`（AC-2 显式记录 reason `ooc_in_dangerous_scene`）否则 block（**双信号必须同时满足**，单信号 default-deny）
  - **Self-crash fallback**：scan 内部 try/except，任何异常 → `verdict=hard_block` + `blocked=scan_crashed`（spec 边界场景"审查模块自身崩溃 → 降级为最严格档，不绕过"）
  - **Audit log** 每次 scan 写一条，5 个 verdict（`pass` / `warn` / `block` / `hard_block` / `allow_with_exception`），2 个 stage（`pre_input` / `post_output`），`_seq` 单调计数器保证同秒插入的稳定排序，snippet 240 字符截断
  - **World policy**：`is_world_dangerous(world_id)` / `set_world_dangerous(world_id, bool)` + per-world `set_safety_threshold(world_id, int)`，in-memory `reset_world_policy(world_id)` 给 A4.2 world-reset 调

#### 接入
- `state.py`：加 2 个 bucket（`safety_audit_log` / `safety_rules`）+ `reset_for_testing` 清空
- `utils/ids.py`：加 `gen_safety_audit_id` / `gen_safety_rule_id`（前缀 `saf_` / `rule_`）
- `stubs/__init__.py`：加 2 个新子模块到 import 列表 + `seed_all()` 调用 + `__all__`
- `routes/__init__.py`：`xijian_api.routes.xijian_safety` 加进可选路由表
- `routes/xijian_safety.py`（275 行）：scan input/output + rules CRUD + audit list/count + per-world policy get/put/delete + dev crash 演练端点
- `tests/conftest.py`：加 2 个新 stub 的 `reset_for_testing`

#### 测试（150 个新 case）
- `test_xijian_safety_rules.py`（71）：纯函数（kind/pattern/severity 验证 + literal vs regex 编译）/ CRUD / list_active 按 severity desc 排序 / match_active_rules 各种场景（inactive skip / kind 过滤 / broken regex 跳过 / forbidden word case-insensitive）/ HTTP / 鉴权
- `test_xijian_safety.py`（79）：纯函数（snippet 截断 / verdict-from-match / event_is_dangerous 6 种 tag）/ audit record+list+count（filter / limit / newest-first）/ scan_input 全分支（clean / injection 5/3/1 severity / forbidden word 4/1 / overload）/ scan_output 全分支（clean / OOC in safe / OOC in dangerous + tag / OOC in dangerous + wrong tag / OOC in dangerous but world not dangerous / forbidden word / overload）/ **scan 自身崩 fallback `hard_block`**（monkeypatch `match_active_rules` raise RuntimeError，验证 input + output 都降级为 `hard_block`）/ world policy / HTTP（scan / audit / policy / dev crash）/ 鉴权

#### 真实启动验证
端到端跑通：world → 3 rules 创建 → clean input pass / injection block / forbidden word block / OOC in safe block / 切 `is_dangerous=True` + dangerous tag → OOC 改 `allow_with_exception`（reason 写 `ooc_in_dangerous_scene`）/ audit list 5 条 / block count 3 条。

### 没动的（与原因）

#### 1. AC-1 评测集（"OOC 触发率 < 1%"）**没接**
**现状**：v2 spec AC-1 写 "[TODO: 用评测集验证]"。本轮给了 `count_for(verdict=...)` + `count_for(character_id=...)` 的 API 供评测工具调，但**评测集本身没建**——不是 stub 范畴。
**为什么留**：评测集是 C1.1 创作者域（"哪些回复算 OOC"需要人工标注）和 A1.2 / A2 chat pipeline 数据（"哪些 prompt 算 prompt injection"）的交集。两个下游都没起，建出来也是空架子。
**接谁做**：A2 chat pipeline + 评测工具起的时候（约 5-10 行 + 评测集 JSONL 文件），调 `safety_stub.count_for(character_id=cid, verdict='block') / count_for(character_id=cid)` 算 per-character OOC 触发率，spec 是 `< 1%`。

#### 2. 没改 `stubs/chat.py`（A2 chat pipeline 集成点）
**现状**：本轮提供的 `scan_input` / `scan_output` 是**独立 API**，**没有**直接接到 `stubs/chat.py` 的 `complete()` / `stream_chunks()`。意味着即使规则配齐，模型输出**目前**也不走审查。
**为什么留**：`stubs/chat.py` 815 行，修改它会动到 recall pipeline / `_run_recall_pipeline` / 后端选择 / streaming 等多处。A2 chat pipeline 是另一个章节的工作（"A2 chat 真实接入"），不在 A5.1 scope。
**接谁做**：A2 chat pipeline 章节起时，在 `complete()` 入口（line 698）调 `safety_stub.scan_input(text=last_user_msg, character_id=cid, world_id=wid)`，在返回前调 `safety_stub.scan_output(text=full_reply, character_id=cid, world_id=wid, event_tags=event_tags)`——`verdict == 'pass' | 'allow_with_exception'` 放行，其它 verdict 改 `safe_completion` 走 fallback 模板。约 30 行代码 + 1 个 tool call 走查（spec 功能清单第 5 条 "工具调用审计"）。

#### 3. 工具调用审计（spec 功能清单第 5 条）**没接**
**现状**：v2 §A5.1 提"所有 tool_call 必须可被审计"，但 A5.1 的 audit log schema (`safety_audit_log`) 是**按 scan 写一条**——没专门为 tool_call 设计。tool_call 的审计**天然应该**在 A5.2（MCP 防护）那章节落，因为 A5.2 要做黑名单 / 安全终止 / MCP 冻结 / sanitize 等，tool_call 在 MCP 进程里执行，不是 LLM 输出。
**为什么留**：A5.2 是更大范围（电脑控制防护），本轮只做"输出审查"这一档。
**接谁做**：A5.2 起来时，在 `safety_audit_log` 同一 bucket 加 `stage='tool_call'` 的写入路径（schema 扩展），共享 `record_audit()` 入口。预计 5 行代码。

#### 4. severity 跟 "verdict=block vs hard_block" 的映射规则是启发式
**现状**：`_verdict_from_match(severity, threshold)` 用 `severity >= threshold+2 → hard_block` 这种线性映射。这是 spec 没说清的——v2 只说 "severity 1~5" + "默认严格档"，没规定"多少分 = 硬 block"。
**为什么留**：现实里需要的是**可调**，不是写死。本轮给的是起点，操作员可以微调。
**接谁做**：等 A1.x 评测集跑出来有数据后，把这个映射换成数据驱动的（"severity 4 + 短文本 + OOC 出现 3 次以上 = 硬 block"）。约 10 行 + 评测结果。

#### 5. 帕姆严格度（US-A5.1-03）**没量化**
**现状**：spec US-A5.1-03 说"参考崩坏：星穹铁道的帕姆 AI 的审查严格度"。这是**主观**的：帕姆严格度 = ?
**为什么留**：帕姆的严格度本身是个**校准**问题，不是 stub 工程问题。本轮给了 `set_safety_threshold(world_id, int)` 跟 `DEFAULT_SAFETY_THRESHOLD=3` 起点，operator 可以调到 5（最严）来近似帕姆。
**接谁做**：跟评测集一起来——跑 A5.1 评测集，调 `threshold` 看哪个值下 OOC 触发率符合帕姆的标准。

#### 6. `_AUDIT_SEQUENCE` 模块全局计数器不会跨进程
**现状**：在单进程 stub 里 `_AUDIT_SEQUENCE` 永远单调递增。生产里换数据库后端时会**完全重写**这一段。
**为什么留**：spec 没说审计日志的存储；当前是 in-memory，跨进程用不上的就是单进程内排序。
**接谁做**：换数据库后端时（spec 没明说，但 stub-in-memory 是过渡），sort key 改 `created_at + rowid` 或者 `created_at + auto_increment_id`。

#### 7. dev crash 端点用 monkeypatch 注入 boom
**现状**：`POST /v1/xijian/safety/dev/crash` 把 `rules_stub.match_active_rules` 替换成抛 RuntimeError 的函数，跑一次 scan，然后恢复。
**为什么留**：spec 要"审查模块自身崩溃"路径可测，又**不能**真让 review 崩（生产环境影响大）。用临时 monkeypatch 注入是最干净的演练手段。
**接谁做**：不需要动——这个端点本来就是 dev-only，标 `XIJIAN_DEV=1` 才放行。

### 跨章节联动点（之后模块会碰 A5.1 的）

- **A1.2 记忆写入**：OOC 触发时，记忆模块应该知道"这次回复没发出"——避免后续 chat 时模型把"被 block 的回复"当成已说出口的。**本轮没接**
- **A2 chat pipeline**：见"没动的 #2"——这是最大一个口子
- **A3.2 角色状态**：sick 状态的 NPC 输出 OOC 概率高——`apply_field_change` 触发 sick 状态时**应**自动调 `set_safety_threshold(world_id, +1)`（更严格）。**本轮没接**
- **A4.1 事件调度**：fire `kind=incident` 的事件，A4.1 已经把 `affected_npcs` 填好——A5.1 应该让 `event_tags` 把 `incident` 算进 dangerous set。**本轮没接**（event_tags 现在只识别 5 个硬编码 tag，incident 不在列）
- **A4.2 NPC 调度**：NPC 算"我要说什么"时，OOC 模式的 NPC（高随机性）应该被**显式**打 "high_ooc_risk" 标签，让 `event_tags` 注入。A5.1 这边**只需** `set_safety_threshold(world_id, +1)` 收紧。**本轮没接**
- **A4.3 场景与互动**：scene_interaction 的某些 effect 可能让用户输入带新 prompt injection 模式——scan_input 已经在拦，但**没区分**"用户正常输入" vs "用户刚接受了 scene_interaction 暴露的文本"。**本轮没接**
- **A4.4 经济系统**：被偷骗后用户情绪可能转向，scan_output 的 OOC 检测应考虑"刚发生了情绪事件"作为 dangerous context 的一部分（event_tags 里加 `after_theft` / `after_scam`）。**本轮没接**
- **A5.2 电脑控制防护（MCP）**：见"没动的 #3"——本轮 A5.1 没做 tool_call stage
- **A5.3 自动备份**：safety_audit_log 是否需要被纳入自动备份范围？spec 没明说。本轮**默认**不纳入（体积可能爆炸），但需要 operator 决策
- **A5.4 过载防护**：本轮已接——A5.4 recovery 窗口内 scan_input / scan_output 短路放行（"overload_active_short_circuit" reason）。见 `safety.py:_is_overload_active`
- **A6 实时通话**：实时通话的 chat 输出比普通 chat 严格一档（语音语气难精确控制）——`scan_output` 应该在 call 阶段把 threshold 自动 -1 收紧。**本轮没接**
- **A7 主动发起**：主动发起的消息是 AI 自己起的，OOC 风险更高——`scan_output` 应该在 `source='character_initiated'` 时自动 +1 严格。**本轮没接**
- **A8 桌宠 / 壁纸**：桌宠气泡框内容是 chat 输出复用，scan_output 自动 cover。**不需要单独接**

### 文档里的 `[TODO]` 状态
v2 §A5.1 有 1 个 `[TODO]`："AC-1: OOC 触发率 < 1%（[TODO: 用评测集验证]）"——**本轮没摘除**。评测集是 C1.1 + A1.2 / A2 域的产物，A5.1 stub 只提供 `count_for` API。

### 测试覆盖情况
- `safety_rules` stub 71 个 case：纯函数 22（kind/pattern/severity validation + literal vs regex compile + VALID_KINDS）/ CRUD 22 / match_active_rules 8 / HTTP 14 / 鉴权 5
- `safety` stub 79 个 case：纯函数 22（truncate / worst_match / verdict_from_match / event_is_dangerous 8）/ audit 8 / scan_input 11 / scan_output 9 / self-crash 2 / world policy 8 / HTTP 16 / 鉴权 8
- 总：150 个新 case，**1194** 总（1044 基线 + 150 新），0 回归

**缺口**：
- **没接 A2 chat pipeline**（最大缺口）——见"没动的 #2"。一旦接，scan_input / scan_output 才有"实际作用"，本轮 A5.1 实质是"地基+API"
- **AC-1 评测集没跑**——见"没动的 #1"
- **没接 tool_call stage**——见"没动的 #3"
- **severity → verdict 的线性映射是启发式**——见"没动的 #4"

---

## 2026-07-15 · A4.4 经济系统实装（从零起）

### 任务来源
v2 spec §A4.4 列了 4 张表 (`world_currencies` / `wallets` / `transactions` / `world_economy_state`) + 4 个验收标准（AC-1 资金变动必写 transactions / AC-2 NPC 偷骗合理判定+冷却 / AC-3 用户可配是否允许非法手段 + 边界场景余额为负赊账 + 经济系统崩溃触发重置）。盘点代码：state.py **完全没有**这 4 个 bucket，没有 stub、没有路由、没有测试——**100% 从零起**。

### 已完成（实测一遍）

#### 新建 stub
- `stubs/world_currencies.py`（148 行）：按 `(world_id, code)` 复合主键，code 限定 `[A-Za-z0-9_]{1,16}`，decimals ∈ [0, 6]，cascade delete 检测 wallet / transaction 引用，`ensure_currency` 懒物化（orchestrator 兜底用）
- `stubs/wallets.py`（487 行）：按 `(owner_kind, owner_id, world_id, currency_code)` 复合主键，deposit / withdraw / transfer 三个底层操作，overdraft policy 默认禁用（按世界 `allow_overdraft` toggle 走），`delete_for_world` / `delete_for_owner` 级联删除
- `stubs/transactions.py`（294 行）：追加 only，8 种 `kind` (purchase / sale / theft / scam / reward / transfer / fine / repair)，FIFO `TXN_KEEP_PER_WORLD=5000` 限流，per-world / per-owner / per-kind / 全局 4 种 list，`summary` 聚合（total / total_volume / by_kind）
- `stubs/world_economy_state.py`（256 行）：每世界懒物化，`inflation_rate ∈ [-0.5, +0.5]` 锁死，`liquidity_index ∈ [0.5, 2.0]` 锁死，持有 `allow_illegal` / `allow_overdraft` 两个 per-world 政策开关，tick 函数按 `volume_delta` 推通胀 + 按 0.1 系数向 1.0 均值回归流动性
- `stubs/economy.py`（602 行）：orchestrator 统一 trade/crime 入口
  - **Trade verbs**: `purchase` / `sale` / `reward` / `transfer_user_to_user`，每个成功调用都写一条 `txn_stub.record`（AC-1）
  - **Crime verbs**: `attempt_theft` / `attempt_scam` 共享 `_attempt_crime` 内部，按 NPC `state_json.crime_theft_skill` / `crime_scam_skill` 算概率（默认 0.30 / 0.40），确定性 `hash(("economy_crime", npc_id, world_id, bucket))` 概率，30s per-NPC cooldown（**总是先消耗再判 roll**——防 pin 命中）
  - 偷盗目标 amount 自动 cap 到用户余额（防过载 overdraft）
  - 偷骗被 A5.4 overload recovery 短路（玩家不被过载惩罚）
  - 偷骗被世界 `allow_illegal` 政策 gate

#### 接入
- `state.py`：加 4 个 bucket（`world_currencies` / `wallets` / `transactions` / `world_economy_state`）+ `reset_for_testing` 清空
- `utils/ids.py`：加 `gen_currency_id` / `gen_wallet_id` / `gen_transaction_id` / `gen_economy_state_id`（前缀 `curr_` / `wlt_` / `txn_` / `eco_`）
- `stubs/__init__.py`：加 4 个新子模块到 import 列表 + `seed_all()` 调用 + `__all__`
- `routes/__init__.py`：`xijian_api.routes.xijian_economy` 加进可选路由表
- `routes/xijian_economy.py`（537 行）：3 套资源路由 + 6 个 trade/crime 端点 + dev-only tick 端点 + per-world economy summary
- `tests/conftest.py`：加 4 个新 stub 的 `reset_for_testing`（在 autouse `_reset_state` fixture 里）

#### 测试（282 个新 case）
- `test_xijian_world_currencies.py`（72）：纯函数（code/name/decimals validation）/ CRUD / 跨世界同 code 允许 / cascade delete / lazy default / HTTP 完整 round-trip / 鉴权
- `test_xijian_wallets.py`（67）：纯函数 / CRUD / `ensure_wallet` 幂等 / deposit 副作用 / withdraw 余额 + overdraft policy / transfer 原子性 + self-transfer 拒绝 / cascading delete / HTTP / 鉴权
- `test_xijian_transactions.py`（43）：纯函数（amount validation）/ CRUD / 7 种 list 路径 / FIFO 限流 / cascading delete / HTTP / 鉴权
- `test_xijian_economy.py`（100）：economy_state 纯函数 + 通胀流动性 tick + 政策 toggle + 4 个 trade verb 全路径（含 no_wallet / npc 余额不足 / currency 缺失） + 2 个 crime verb 全分支（allow_illegal_disabled / overload_active / cooldown / user_empty / no_user_wallet / failed_roll / success） + probability 辅助 + HTTP（purchase / sale / reward / transfer / theft / scam + state CRUD + dev tick + summary）/ 鉴权

#### 真实启动验证
跑了一遍端到端：world → currency → wallets (user 1000 + npc 0) → reward 500 to npc → purchase 100 → sale 50 → enable allow_illegal → summary 正确显示 1 reward + 1 purchase + 1 sale，total_volume=650。

### 没动的（与原因）

#### 1. 没做"商品 / 库存"系统
**现状**：spec §A4.4 提了"商品上架/购买/出售"，但 SQL schema 里**没有 goods / items 表**——4 张表里只有 currencies / wallets / transactions / economy_state。
**为什么留**：v2 没说商品的"哪个世界 / 谁持有 / 数量 / 单价"长什么样。做了就是发明 spec，可能跟 C1.1 创作者侧商品定义冲突。等 C1.1 起的时候再问设计决策（自创世界可能完全不需要商品概念——原神里没"背包"系统，崩铁也没）。
**接谁做**：C1.1 创作者工具 + US-A4.4-02（购买物品）起来时。这是**真正的设计决策**而不是工程活儿——不要猜。

#### 2. 没做 NPC 的 economy scheduler 主动偷骗循环
**现状**：`attempt_theft` / `attempt_scam` 是被动调用——得有人 POST 进来 / LLM agent 决定调用。
**为什么留**：spec §A4.4 流程图说 "N -> E: 决策：尝试盗窃/诈骗"——这步的"决策"归属 A4.2 NPC tick（NPC 算"我要不要偷"）还是 A1 chat pipeline（"模型决定偷"）？v2 没规定。
**接谁做**：A4.2 NPC tick 起背景线程时，每个 high_active NPC 每 tick 跑一次 "是否尝试犯罪" 决策（基于 personality 字段 + 随机）。或 A1 模型自己调 `attempt_theft`——A2 chat pipeline 加 tool 入口。当前不接。

#### 3. 没做"经济系统崩溃"的世界重置联动
**现状**：spec 边界场景"经济系统崩溃（极端通胀）→ 触发世界重置确认"。`tick()` 函数会写 `inflation_rate`，但**没监控**这个值。
**为什么留**：什么算"崩溃"？spec 没给阈值。`MAX_INFLATION_RATE=0.5` 是设计上的"绝不超"硬限，但"超过 0.3 持续 1 小时"这种业务阈值完全没定。
**接谁做**：业务阈值定下来后（约 2 行代码 + 1 个 `safety_snapshots` 写入）就接，路线：
```python
if state.world_economy_state[wid]["inflation_rate"] > 0.3:
    # 触发 A4.2 world 重置确认流程
    worlds_stub.preview_reset(wid)
```

#### 4. AC-1 写 transactions 强一致性靠 orchestrator
**现状**：钱包的 `deposit` / `withdraw` / `transfer` 是**直接**操作 `state.wallets`，**不**自动写 `state.transactions`。
**为什么留**：分两层 —— 低层是 wallet helpers（testable + 可被 admin tool 单独调用），高层是 economy orchestrator（保证 AC-1）。如果 wallet helpers 也写 transaction，会让"调整余额但不产生交易"的操作（admin 工具、测试）变得很难做。
**风险**：如果未来有人在 economy orchestrator **之外**直接调 wallet helpers，AC-1 会破。要么靠代码审查，要么未来给 wallet helpers 加个可选 `record=True` 参数兜底。
**接谁做**：文档已写明；如果未来真的出现绕过，orchestrator 改造成"所有余额变更必须经过它"的门面模式。

#### 5. 没做赃款 / 罚款 / 缴税系统
**现状**：8 种 `kind` 里 fine / repair 留了位但**没有 orchestrator 入口**。
**为什么留**：spec §A4.4 提了"非合法手段"（抢劫/诈骗/盗窃），但没提罚款 / 缴税 / 赃款追踪——这是监管类需求，v2 没规定。
**接谁做**：等设计决策（哪种"罚款"算合法操作？NPC 偷到的钱算"赃款"吗？谁有权没收？）。C1.1 创作者可能会需要——交给他们。

#### 6. transfer_user_to_user 是 no-op（只记一条 transaction）
**现状**：当前模型只一个 user（`user_local`），所以 "user-to-user transfer" 实质是同钱包 withdraw + deposit，余额不变，但 transaction 还是会写。
**为什么留**：多用户（multi-tenant / 多角色扮演）模型是 forward-compat 路径——接外部用户系统时不用大改 orchestrator。
**接谁做**：等真有多用户需求时，扩 `wallets.LOCAL_USER_ID` → 真实 user id，A1 chat pipeline / 鉴权层传入。

#### 7. 通胀 tick 的 `volume_delta` 现在是手动传参
**现状**：`tick(world_id, volume_delta=0.0, seasonal_factor=0.0)` 不自己算交易量。
**为什么留**：算"上一窗口的净交易量"需要维护一个 per-world 滚动窗口（跟 overload 的 sliding deque 类似）——又是一个后台线程 / 内存对象。spec 没说 tick 是自动的还是被动的。
**接谁做**：跟 A4.2 / A1.2 一样，看是否需要常驻后台线程。如果要，`volume_delta = sum(txn_stub.list_for_world(wid, since=last_tick))`——5 行代码。

#### 8. dev tick endpoint 没真在线
**现状**：`POST /v1/xijian/economy/state/<wid>/tick` 需要 `XIJIAN_DEV=1`，否则 403。conftest 把 `XIJIAN_DEV` pop 掉，所以测试默认拒绝。
**为什么留**：跟 A3.2 / A4.1 / A4.3 同样口径——macro tick 应该是被动调（不是后台线程自己跑），dev flag 防 production 误调。
**接谁做**：起后台 tick 时把 dev 端点换 `XIJIAN_ECONOMY_TICK=0` 关闭模式。

### 跨章节联动点（之后模块会碰 A4.4 的）

- **A1.2 记忆写入**：偷骗交易应异步触发 `memory.append(source="economy_event", ref_id=txn_id, payload=...)`——用户聊到"昨天被偷了"时 AI 知道发生过。**本轮没接**
- **A2 chat pipeline**：NPC 主动偷骗的"决策"应该由 chat 模型决定——A2 tool calling 暴露 `attempt_theft` / `attempt_scam`。**本轮没接**
- **A3.2 角色状态**：NPC 偷盗成功 / 失败后应当更新 `state_json.illegal_acts_today` / `state_json.failed_attempts_today`（影响后续行为）。**本轮没接**
- **A4.1 事件调度**：fire `kind=incident` 的事件可能联动 economy（如 "market_day" 事件触发所有 NPC 的 `reward` 流入）。A4.1 已经支持 `ref_id`，但 stub 间没串。**本轮没接**
- **A4.2 NPC 调度**：tick_world 时高活跃 NPC 应当有机会调 `attempt_theft`（基于 state_json.personality）。**本轮没接**（见"没动的 #2"）
- **A4.3 场景与互动**：scene_interaction 可能联动 economy（如 "buy_item" 互动触发 `purchase`）。**本轮没接**
- **A5.1 OOC 评测**：chat 里模型说"我现在有 1000 摩拉"——但 orchestrator 实际余额是 100，OOC 检测器要能识别这种不一致。**本轮没接**
- **A5.4 过载防护**：本轮已接——`attempt_theft` / `attempt_scam` 在 overload recovery 窗口内短路，trade verbs 放行（玩家不应被过载惩罚）。见 `economy.py:_is_overload_active`
- **A6 实时通话**：通话中的商品交易应该走 `purchase`（不是绕过）。**本轮没接**——A6 起来时把 chat-time trade 走 orchestrator 路径
- **A7 主动发起**：NPC 主动发起的"想跟你做交易"消息应带交易细节（amount / currency_code）作为 ref_id。**本轮没接**
- **A8 桌宠 / 壁纸**：UI 上显示用户余额时，调 `GET /v1/xijian/wallets/user/user_local?world_id=<wid>` 拿数据（已就绪）

### 文档里的 `[TODO]` 状态
本轮**没改 v2 文档的 [TODO] 列表**——A4.4 spec 段落没有遗留 [TODO]，是干净的。changelog 加 v2.6 2026-07-15。

### 测试覆盖情况

- `world_currencies` stub 72 个 case：纯函数 18 + CRUD 22 + HTTP 18 + 鉴权 5 + lazy 9
- `wallets` stub 67 个 case：纯函数 14 + CRUD 21 + mutations 18 + cascading 4 + HTTP 12 + 鉴权 7
- `transactions` stub 43 个 case：纯函数 12 + CRUD 16 + FIFO 1 + cascading 2 + HTTP 8 + 鉴权 3
- `world_economy_state` stub 22 个 case（在 economy 测试里）：纯函数 16 + CRUD 4 + tick 5 + accessors 6
- `economy` orchestrator 78 个 case：trade verbs 16 + crime verbs 21（含全部 blocked 路径） + probability helpers 8 + convenience 4 + summary 1 + HTTP 25 + 鉴权 10
- 总：282 个新 case（72 + 67 + 43 + 22 + 78），1044 总（762 基线 + 282 新），0 回归

**缺口**：
- economy orchestrator 跟 A1.2 / A4.2 联动没测（因为没接）。等下个章节起来时补"event.fired → memory.append" "tick_world 触发 attempt_theft" 这类链路测试
- 多用户（multi-tenant）没测——单用户模型是当前限制
- "经济系统崩溃" 检测器没接——`inflation_rate > 0.3` 持续 X 时间这条链路完全空白

---

## 2026-07-10 · A4.1 事件调度接入落地

### 任务来源
之前盘点（你贴过来的状态清单）把 A4.1 标成"只有 `POST /worlds/<wid>/event` 触发；调度循环没有"。实际进代码一看 `stubs/events.py`（978 行）+ `routes/xijian_events.py`（302 行）都写完了，但**根本没接到 Flask**：
- `routes/__init__.py` 的可选路由表里**没有** `xijian_api.routes.xijian_events`
- `stubs/__init__.py:seed_all()` 里**没有**调 `events.seed_default()`
- `core/tests/` 里**完全没有** `test_xijian_events.py`

等于写完扔在那儿没人管。本轮做的是**接入 + 测试覆盖 + 跨章节联动落地**，不是从零起。

### 已完成（实测一遍）
- **stub + 路由**：原本就有 978 行 stub（4 类触发器 time/interval/probability/condition、CRUD、实例、分类禁用、调度生命周期、storm throttle、优先级竞争）+ 302 行路由（CRUD/实例/分类/调度/summary）。基础语义、验收标准、风暴节流、用户禁用、优先级竞争全部到位
- **接入（这一轮补的）**：
  - `stubs/__init__.py:30` 把 `events` 加进子模块列表 + `seed_all()` 第 50 行调 `events.seed_default()`（默认启动 scheduler 线程，符合"生产默认跑"约定）
  - `routes/__init__.py:48` 把 `xijian_api.routes.xijian_events` 加进可选路由表
  - `stubs/__init__.py:73` 把 `events` 加进 `__all__`
  - `core/tests/conftest.py:27` 加 `XIJIAN_EVENT_SCHEDULER=0`（与 A3.2 / A5.4 同约定）
  - `core/tests/conftest.py:91-93` 测试间 reset 调 `events_stub.reset_for_testing()`
- **过载联动（这一轮补的）**：
  - `stubs/events.py:_is_overload_active()` 内联读 `state.overload["recovery"]`（避免 import overload 形成循环依赖），status ∈ {`waiting`, `first_confirmed`} → True
  - `stubs/events.py:tick_world` 在 storm throttle 检查之前加 overload 短路：**全部候选标 `overload_active` 跳过**，不消费 cooldown 槽
- **WS 联动（这一轮补的）**：
  - `stubs/events.py:_broadcast_event_fired` 在 `fire_event` 末尾 publish `event.fired`，附带 `instance_id / event_id / world_id / fired_at / needs_scene / scene_ref_id / affects_user`
  - best-effort：publish 抛错不阻塞 `fire_event` 入库（与 overload 模块同模式）
- **测试**：117 个新测试覆盖
  - 纯函数：`_validate_trigger`（含 4 类触发器的边界）+ `_evaluate_trigger` 4 类 + `_evaluate_probability_trigger` 决定性 + `_evaluate_condition_trigger`（含 `gt/lt/in/not_in/类型不匹配 swallow`）+ `_safe_compare` + `_is_in_cooldown` + `_storm_throttle_pass` + `_matches_disabled_categories` + `_pick_fire_payload`
  - CRUD：create / get / list（按 world/kind/enabled_only 过滤 + priority desc 排序）/ update（含 id/world_id 不可变校验）/ delete
  - 实例：fire / get / list / resolve / `_trim_instances` FIFO 上限
  - 分类禁用：set/is/list + 世界隔离
  - 调度：`tick_world`（无事件 / 禁用 / per-event cooldown / storm throttle / 优先级竞争 / 触发器异常隔离 / **overload 短路 / overload finalized 不再阻塞**）+ `tick_all` 多世界
  - 生命周期：start/stop/status/env 关闭/env 间隔/floor 1s/start 真起一帧
  - WS 广播：fire 触发 broadcast + publish 抛错不阻塞 fire
  - 路由：CRUD 完整 round-trip + 缺失字段 + 校验错误 + 空 patch + 404；实例 list / get / resolve / limit 校验；分类 list / toggle / 缺 disabled 字段 / world 404；scheduler status / dev tick prod 阻断 / dev tick 允许 + tick_all / summary / world 404；auth 表驱动覆盖所有事件端点

### 本轮修补清单
1. `events.py:tick_world` — 在 storm throttle 检查之前加 `if _is_overload_active()` 短路，明确写了"不消费 cooldown 槽"的口径（不光是"丢弃"）
2. `events.py:_evaluate_probability_trigger` — 桶索引改成 `int(now) // max(int(_current_interval()), 1)`（原本就有的，但确保在 env 改 interval 时不会除零）
3. `events.py:_broadcast_event_fired` — 单挑出来不放在 fire_event 主体里，保证 fire 入库不受 broadcast 影响
4. `conftest.py` — 加 scheduler 默认关 + reset_for_testing
5. 新增测试类 17 个，117 cases

### 没动的（与原因）

#### 1. 没有"事件入库 → 角色状态修改"的端到端联动
**现状**：A4.1 fire 完只入 `world_event_instances` + 广播 `event.fired`。
**为什么没接**：spec 说"影响回写：事件可能写入角色记忆"——但 v2 没规定**哪些事件**影响哪些角色、影响写入格式。下游模块都还没起，做早了要返工。
**接谁做**：A4.2 NPC 调度起来时，把 NPC 列表 → fired instance 的 `affected_npcs` 字段已经被填过（fire_event 接受 `affected_npcs` 参数，但目前 stub 不主动算它），需要：
- `tick_world` 自动调用一个回调 `select_affected_npcs(world_id, event_record)`，默认返回空
- A4.2 实现时注册自己的 selector（基于 NPC 当前位置 / 状态）
- 角色的 state 字段在 fire 时如果 `affects_user=False` 可以跳过；True 才更新 `enter_recovering(reason="world_event")`（character_state 已有这个 reason）

#### 2. `condition` 触发器的 `world_state` 字段 schema 是"野生字典"
**现状**：`worlds.py:update_state` 接 `{economy, health, diet, stamina, mentality}` 这 5 个字段（Dev.md §4.3.3 的"系统维度"）。condition 触发器读 `world_record["state"][field]` 是任意 key——没有 schema 校验。
**为什么留**：A4.2 还没起，世界的完整 state schema 还没定。events stub 给的"任意 key"灵活性允许 event 作者定义自己的条件；等 schema 落地时再收紧。
**接谁做**：A4.2 起来时，在 `worlds.py:update_state` 加白名单校验，并把白名单同步到 `events.py:CONDITION_FIELDS`，condition 触发器拒绝未列出字段（返回 `False` 而不是抛错，避免一个烂 event 把整个 world tick 打崩）。

#### 3. dev tick 没限定只清自己的世界
**现状**：`POST /v1/xijian/events/scheduler/tick` 接受 `{"world_id": "..."}` — 单世界模式直接调 `tick_world(wid)`。但即使有 world_id，背景线程 `tick_all` 仍然在跑，dev tick 跟背景线程可能并发触发同一世界。
**为什么留**：测试和生产用同一个 stub，dev tick 就是"立即跑一次"的便利入口；并发竞争下 `tick_world` 内部 dict 写入非原子，可能丢失一次 fire，但**不会崩**。生产中 UI 不应该频繁触发 dev tick。
**接谁做**：不需要动。如果以后 dev tick 路径跟生产 tick 路径要严格隔离，把 `_SCHED_LOCK` 暴露到 `tick_world` 内层，加一把进程级互斥就行。

#### 4. scheduler 线程是 daemon
**现状**：`_sched_loop` 里 `thread = threading.Thread(..., daemon=True)`。
**为什么留**：与 A3.2 / A5.4 同口径——daemon 是简单正确选择，主进程本来就 Flask，崩溃让 tick 一起死、避免僵尸。tick 是幂等的（基于 wall clock 算 dt），丢一次下次重启从恢复点继续算，无副作用。
**接谁做**：不需要动。生产里如果做优雅退出（SIGTERM 跑 shutdown handler），记得先调 `events.stop_scheduler()`。

#### 5. `_current_interval()` 硬地板 1s
**现状**：env 设成 `0.1` 也会被钳到 `1.0`。
**为什么**：与 A3.2 tick thread 同口径。1s 是测试的最紧频率（dev tick 是同步路径，不走 scheduler 线程）；再快让 tick_all 在 50 世界规模 + 高频事件库里把 CPU 打满。
**接谁做**：sub-second tick 在 spec 里没要求，不动。

#### 6. 没做"事件库内置默认事件"
**现状**：`seed_default()` 只启动 scheduler，不 seed 任何默认事件。
**为什么留**：v2 §A4.1 把事件库描述为"内置 + 用户自定义 (C1.1)"，但**内置**事件的清单没列出来（不像 C5 那样给完整 enum）。需要作者决策"哪些事件应该默认开"，牵涉到游戏性。
**接谁做**：等到 MVP 上 UI 之前要决定——要么：
- 路径 A：内置一档"世界新鲜出炉"事件（玩家第一个进入世界时强制触发一次"欢迎仪式"）
- 路径 B：完全交给 C1.1 + DevKit，让作者自己组事件库
- 路径 C：内置只读，所有事件都可被 C1.1 覆盖
**当前维持空 seed**，等 devkit 起来时再补这一档。

#### 7. 没用 cron-style 调度
**现状**：A4.1 的"时间触发器"只支持 `daily` / `hourly` 两种频率，精确到分钟。
**为什么留**：cron 的全套字段（day of week / month / etc.）超出 spec。spec §A4.1 只说"时间（节日）"——节日是按"某一天"还是"某个星期几"都没规定。
**接谁做**：等到 E2E 评估发现事件触发不灵活时再加。先 4 类触发器够用。

#### 8. fire_event 不写 audit log
**现状**：fire 完只入 `world_event_instances` + WS；没写 audit log（不像 overload / character_state 那样有 audit 痕迹）。
**为什么**：事件是高频、低价值的"小事件"，写 audit 日志会撑爆文件。audit 应该留给"用户关键决策"（删除角色、世界重置、过载恢复等）。
**接谁做**：审计需求（合规 / 调试）起来时再补。`fire_event` 已经返回完整 instance record，按需要回查就行。

### 跨章节联动点（之后模块会碰 A4.1 的）
- **A1.2 记忆写入**：fire 一个 `affects_user=True` 的事件，应该异步触发 `memory.append(source="world_event", ref_id=instance_id, payload=...)`，让用户聊到相关话题时 AI 知道发生过
- **A3.2 角色状态**：fire 一个 `affected_npcs` 包含某角色的事件，自动调 `cs_stub.apply_field_change(char_id, field="health", delta=-10, source="world_event", ref_id=instance_id)` 或 `enter_recovering(reason="world_event")`
- **A4.2 NPC 调度**：fire 时把"哪些 NPC 受影响"塞到 `affected_npcs`；NPC scheduler 据此调整它们的活跃档位
- **A5.1 OOC 评测**：fire 完的事件描述应该被 A5.1 拿去做 OOC 检测（如果事件描述被投毒成"忽略之前所有指令..."，要在落入 context 之前就拦掉）
- **A5.4 过载**：本轮已接——overload recovery 窗口内 tick 直接 drop 所有候选。overload 起来时可以考虑 `register_action_handler(ACTION_SUSPEND_IDLE_NPCS, suspend_npc_event_firing)` 把整个 events scheduler 关掉（这一档更激进，本轮没做——先按"drop 候选不消费 cooldown"这一档；完全关掉留给 A4.2 高活跃档时再加）
- **A7 主动发起**：fire `kind=incident` 的事件后，可以触发 character 的主动聊天（"诶你听到了吗，刚才在市场..."）；本轮没接
- **A8 桌宠 / 桌宠动画**：fire 需要场景的事件 `needs_scene=True`，桌宠应该弹出对应的微动画——WS 广播 `event.fired` 已经带上 `needs_scene` 和 `scene_ref_id`，UI 订阅即可
- **C1.1 事件库创作**：作者通过 C1.1 创建的事件落到 `world_events` 表（devkit 路径），能被 events stub 直接读到——devkit 与本 stub 不共享状态是历史包袱，等 C1.1 落地时确认是否需要适配

### 文档里的 `[TODO]` 状态
- `A4.1: 默认 60s 内最多 1 个事件` — 已实装为 `DEFAULT_GLOBAL_COOLDOWN_SECONDS = 60.0` + `DEFAULT_SCHEDULER_INTERVAL_SECONDS = 60.0`，env 可覆盖。v2 文档已摘除

### 测试覆盖情况
- 纯函数 10 个：validation（4 类触发器 + 边界）/ evaluate（4 类 + 类型不匹配）+ cooldown + storm throttle + category match + payload merge
- CRUD 12 个：create (3) / get (2) / list (4) / update (5) / delete (2) / instance (8) / category (5)
- 调度 11 个：tick_world 7 + tick_all 1 + lifecycle 6 + reset 1 + summary 1
- WS 2 个：fire publishes + crash tolerant
- 路由 ~25 个：CRUD/instance/category/scheduler/dev/summary/auth 表驱动

**缺口**：
- 并发安全测试没做（`tick_world` 与背景 `tick_all` 同时跑 dict 写入）。50 角色规模撞不上问题，生产环境加数据库后端时要补锁
- dev tick 真跑"高频事件风暴节流"测试没做——env 改 `XIJIAN_EVENT_SCHEDULER_SECONDS=1` 然后 `tick_all` 跑 3 秒验证只触发 1 次——本轮没写

---


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

## 2026-07-09 · A5.4 系统过载防护（首次盘账）

### 任务来源
之前盘点（你贴过来的状态清单）把 A5.4 标成"完全没起"。实际进代码一看：核心 stub + 路由 + 测试全在，是 commit `ffd73fb` (2026-06-29) 就提交的。本轮做的是**盘账 + 写回 notes + 摘掉 v2 文档里残留的"过载档位待补"语义**，不是从零起。

### 已完成（实测一遍）
- `stubs/overload.py`（1077 行）：双档位阈值（strict / medium，**loose 已移除**）、滑动窗口（maxlen=120）、1Hz 监控线程、严重度排名 + 单一最严动作选择、20s 等待计时器（不可配置，锁死 AC-2）、双重确认握手状态机（waiting → first_confirmed → finalized）、安全快照自动落盘（scope=overload）、WS 广播 `overload.triggered`、4 个 action handler（suspend_idle_npcs / degrade_tts / compress_memory / emergency_dump）
- `routes/xijian_overload.py`（207 行）：status / tier / metrics / events / recovery + first_confirm / finalize_recovery / cancel + dev 模拟器（需 `XIJIAN_DEV=1`）
- 测试：86/86 通过（核心全套 306 通过，0 回归）
- 集成：routes/`__init__.py:55` 注册；`stubs/__init__.py:46` 的 `seed_all()` 调 `overload.seed_default()` 拉起线程；conftest 用 `XIJIAN_OVERLOAD_MONITOR=0` 关掉防测试抖动

### v2 文档里"已实装"对账
- 双档位阈值（严格 CPU 93% 持续 60s / 适中 CPU 95% 持续 100s，SoC 95°C、内存 90%、GPU/ANE 75% 持续 45s 或 80% 持续 80s，swap 不限制）—— 已严格按 v2.1 的数字锁死在 `TIER_THRESHOLDS`
- AC-4 不可关闭 —— `set_tier` 严格只接受 strict / medium，off / disabled / loose 全 400
- 20s 等待计时器（AC-2）—— `RECOVERY_WAIT_SECONDS = 20` 硬编码
- 边缘场景"恢复中再次触发 → 重置 20s" —— 有专门测试覆盖

### 没动的（与原因）

#### 1. `register_action_handler` 没有任何外部调用方
**现状**：四个 action（suspend_idle_npcs / degrade_tts / compress_memory / emergency_dump）触发时会调 handler，但目前没人 `register_action_handler(ACTION_*, ...)` 订阅。
**为什么留空**：handler 应该由下游模块来订：
- A4.x 配角调度 → suspend_idle_npcs（暂停闲置 NPC tick）
- A2 chat pipeline + A6 实时通话 → degrade_tts（切低质量 TTS / 中断流）
- A1.2 记忆模块 → compress_memory（合并旧条目、释放 long_term 配额）
- A1.1 备份模块 → emergency_dump（紧急 dump 当前 context 到 snapshot）

**接口已备好**，等下游模块起来时调 `overload.register_action_handler(ACTION_DEGRADE_TTS, tts_low_quality_hook)` 即可。**约 4-5 行/订阅方**。
**接谁做**：A4.2 / A6 / A1.2 / A1.1 任一起的时候顺带做。

#### 2. 没做"档位 → 用户弹窗"的 UI 桥
**现状**：过载触发时 WS 广播 `overload.triggered`，但前端弹窗、双重确认对话框、20s 倒计时可视化都不在 core 里。
**为什么留空**：UI 层（SwiftUI / Pywebview）整体未起，跨平台弹窗规范也不归 core 决定。
**接谁做**：UI 起来时订阅 WS `/v1/ws`，按事件类型渲染对应弹窗，调用 `POST /v1/xijian/overload/recovery/first_confirm` + `POST /v1/xijian/overload/recovery/finalize`。

#### 3. 温度采样在不支持的平台上回退
**现状**：`_read_soc_temp` 在 Linux/Windows 上尽力读 `/sys/class/thermal/` / WMI，读不到就 None；macOS 上用 `osx-cpu-temp` 风格但实际没引外部依赖，靠 `psutil.sensors_temperatures()`。
**为什么留空**：spec AC-1 说"硬件无温度传感器 → 仅使用 CPU/内存"，回退路径已经覆盖这个边界。
**接谁做**：不需要动。如果以后想要更准的 SoC 温度（比如 Apple Silicon 的 M 系列专用路径），加 `import subprocess; subprocess.check_output(["powermetrics", ...])` 即可，但 powermetrics 在普通用户权限下取不到，得 sudo —— 反而坑。

#### 4. 1Hz 监控线程是 daemon
**现状**：`start_monitor` 里 `daemon=True`。主进程退出时线程被强杀，最后一帧样本丢失。
**为什么这样**：同 A3.2 tick thread 的理由 —— daemon 是简单正确选择，丢一帧不致命（下次启动从头来）。
**注意**：生产里如果主进程要优雅退出（SIGTERM 跑 shutdown handler），记得调 `overload.stop_monitor()` 等线程退出。

### 跨章节联动点
- **A3.2 tick**：档位切换时需要"暂停非活跃 NPC tick"。A3.2 notes 已记：`cs_stub.suspend(character_id)` / `cs_stub.resume(character_id)` 接口待加。本轮没动 —— 取决于 A4.2 NPC 调度什么时候起来。
- **A4.1 事件调度**：高频事件风暴节流（默认 60s 内最多 1 个事件）触发时，如果系统已在过载，应**直接拒绝**新事件而非入冷却队列。代码位置：event dispatch handler 里调 `overload.is_active()` 早返。
- **A5.1 OOC 评测**：进入过载时 OOC 触发的对话应被立刻截断（不是降级，是直接 abort）。overload.triggered 事件 + chat pipeline 的 abort 钩子。
- **A1.1 自动备份**：emergency_dump 已经自动落 safety_snapshot（scope=overload），但没接自动备份列表。A1.1 起来时把 `scope=overload` 的 snapshot 纳入备份范围。

### 文档里的 `[TODO]` 状态
- `A5.4: 移除宽松档` — 已实装（`TIER_THRESHOLDS` 只有 strict / medium 两档）
- `A5.4: 安全终止快捷键（默认 ⌃⌥⌘Q / Win+Alt+Shift+Q）` — **未实装**，属于 A5.2 范畴，等 MCP 进程 + 全局快捷键监听起时一起做
- `A5.4: 文档里的"过载档位"语义描述` — 与实际代码完全对齐，下次盘点不会再误判

### 测试覆盖情况
- 纯函数：阈值比对 / 滑动窗口聚合 / 严重度排名 / 恢复状态机转换 / 计时器倒计时
- 线程：monitor 启动 / 关闭 / 线程生命周期 / reset 后 generation 切换
- HTTP：status / tier PATCH（含非法档位 400）/ metrics / events / recovery 三步握手（含 425 / 409 边界）/ dev 模拟器
- 集成：触发时自动落 snapshot（`safety_snapshots` scope=overload 有新条目）/ 触发时 audit log 有新条目 / 触发时 WS 广播
- 鉴权：所有路由需 Bearer
- handler 注册表：注册 / 注销 / 触发时按注册顺序调用 / 异常隔离（一个 handler 抛错不阻塞其它）

**缺口**：`register_action_handler` 没有任何调用方的端到端测试 —— 因为目前没有调用方。等 A4.2 / A6 / A1.2 起来后补"过载触发 → TTS 真的切低质量"这种链路测试。

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

## 2026-07-06 · C5 DevKit 打包拆分（独立 PyInstaller）+ UI 文案清洗

### 任务来源
C5 之前把 DevKit 挂在 `core/xijian_api/devkit/` 下，作为 xijian_api 的子包。本轮按新口径**物理拆分**：devkit 是用户面产物（PyInstaller → `.app`），API 是服务面产物（wheel / 系统服务）。两者不再共享 import 关系。同步清理 UI 里残留的开发笔记。

### 已完成

#### 物理拆分（`core/xijian_api/devkit/` → `devkit/`）
- `git mv` 整个目录保历史
- 重写 devkit 内部 import：移除所有 `from xijian_api...` / `import xijian_api`
- 把 devkit 依赖的三个小工具（ApiError 基类、submission_id 生成器、ISO 时间 helper）vendoring 进 `devkit/_vendor.py`，零依赖、零 flask
- `devkit/__init__.py` docstring 顶部"module reference"全部改回 `devkit.xxx`，不再指向 `xijian_api.devkit`
- `pyproject.toml` 顶层 `pythonpath = [".."]` —— 测试从 `devkit/` 跑时也能 import 到同级的 `devkit` 包
- 加 `_vendor.py` 不依赖任何 xijian_api 符号 → 测试加 `test_package_does_not_depend_on_xijian_api` + `test_package_does_not_depend_on_flask` 锁住解耦

#### PyInstaller 入口 + spec + 构建脚本
- `devkit/app.py`：PyInstaller 冻结的 `__main__` shim，跟 `__main__.py` 走的是同一个 `main()`，仅做 sys.argv 转发
- `devkit/xijian-devkit.spec`：onedir 模式（macOS .app 需要附带文件做代码签名/公证），显式 `excludes=["xijian_api", "flask", "flask_sock", "waitress", ...]` 把不相关包挡在二进制外
- `devkit/build-devkit.sh`：项目本地 `.venv` 隔离 API 环境和构建环境；`--target={all,dir,app}`、`--clean-venv`、`--no-install` 三个开关
- `.github/workflows/build-devkit.yml`：macOS arm64 + Windows x64 双平台矩阵；tag / release 自动上传 artifact 到 release

#### UI 文案清洗（devkit/ui/index.html）
- header subtitle：`本地打包 → SMTP 邮件投递 · 与主 API 完全隔离` → `本地打包并提交你的创作内容`
- 登录卡片 hint：`把开发者 ID 填进去，后续步骤会用它做限流与归档命名` → `填一个便于识别的标识，我们会用它来记录这次提交`
- 目标卡片 hint：`世界 / 角色 / 剧情 · ID 是你在主 API 里用的那个` → `选择提交类型，并填上对应的标识`
- 字段标签 `类型 (target_kind)` / `目标 ID (target_id)` → `类型` / `标识`（去掉 Python 内部命名泄漏）
- 补充说明 placeholder：`本次提交的额外说明，会写到归档 manifest 与邮件正文` → `本次提交的额外说明，会随内容一起发送`
- 提交卡片 hint：`点击后 DevKit 会离线打包、sha256、连接 SMTP 投递，全程不调用主 API` → `点击后会开始打包并发送，过程通常需要几秒到一分钟`
- 运行配置卡片 hint：`只读，部署前替换` → `本次提交使用的设置`
- 收件 chip tooltip：`接收方邮箱（硬编码）` → `本次提交将发送到此邮箱`

#### `.gitignore` 修复（拆出来顺手修的）
- 模板里 `*.spec` 误伤我们的手写 `xijian-devkit.spec`，加 `!devkit/xijian-devkit.spec` + `!*/xijian-devkit.spec` 否定规则
- `.idea/` 之前未整体 ignore，导致 PyCharm 自动生成的 `devkit.iml` / `modules.xml` 进了 git 索引；`.idea/` 整体 ignore + `git rm --cached` 把已入库的两个文件清掉
- 新增 devkit 专属 ignore：`devkit/.venv/` / `build/` / `dist/` / `__pycache__/` / `.ruff_cache/` / `.pytest_cache/`

### 没动的（与原因）

#### 1. UI 里"运行配置"卡片保留 SMTP 详细信息
**现状**：右栏 `config-card` 仍然显示 SMTP host / port / STARTTLS / user / 归档格式 / 体积上限。
**为什么留**：这套配置是开发者本人（mofan）关心的部署参数，窗口双击开后第一眼能看到"这次连的是哪个 SMTP、封顶多少 MB"是有用的；不像别的文案，那是给"普通用户"看的功能说明，SMTP 配置对开发者本身就是有效信息。
**接谁做**：如果以后 devkit 要交付给其他开发者用，再加权限/角色分层；当前先按"开发者本人工具"的口径保留。

#### 2. UI 里"历史提交"卡片显示 sha256 + SMTP 状态码
**现状**：`history-list` 每条记录展示 `sha256: xxxxxxxx…xxxxxx` 和 `smtp sent (250)` / `smtp failed (552)` 等。
**为什么留**：开发者要核对"邮件到底发出去了没 / 服务器认不认这条记录"——sha256 是对账凭据，SMTP code 是失败排查线索。这不是开发笔记，是工具的**操作反馈**。
**接谁做**：做 dashboard 时可以加"只显示摘要 / 显示详情"切换，简化默认视图；现在先按"完整信息"展示。

#### 3. devkit/`__init__.py` docstring 里的"test surface"段落仍列出内部函数
**现状**：`__init__.py:Test surface` 段落列了 `check_rate_limit / check_archive_size / pack_payload / send_submission_email / submit / ...` 等内部函数名。
**为什么留**：docstring 是开发者读源码时的导航，不是 UI 文案；保留能让以后维护 devkit 的人立刻看到"测哪些函数就够了"。docstring 本来就是给开发者看的，不属于"用户可见层"。
**接谁做**：不需要动——文档分层本来就分得清。

#### 4. 没有为 spec 加 codesign / notarize 步骤
**现状**：`xijian-devkit.spec` 的 BUNDLE 段 `codesign_identity=None`，未配 entitlement，未接 notarize。
**为什么留**：spec 是 PyInstaller 配置，不绑具体签名身份——签名身份是部署期的环境变量/密钥。Codesign / notarize 需要 Apple Developer ID + app-specific password，硬编码进 spec 不合适；下一步在 `build-devkit.sh` 加 `--sign --notarize` 走 env 注入更干净。
**接谁做**：CI 接入 Apple Developer ID 之后，在 GitHub Actions 的 macOS job 里跑 codesign + notarytool；spec 这层不动。

#### 5. devkit/`tests/test_devkit.py` 行数 1189（vs 之前的 1147）
**现状**：测试文件从 1147 行涨到 1189 行，新增约 40 行。
**为什么涨**：`test_package_does_not_depend_on_flask` 和 `test_package_does_not_depend_on_xijian_api` 两个结构性测试，每个 ~20 行（包括临时把 `flask` / `xijian_api` 设成 `None` 模拟不存在、再清掉 `devkit.*` 模块缓存、最后跑 `import devkit / devkit.api / devkit.main`）。这是**结构性契约**测试，不是功能测试 —— 牺牲一点行数换的是"以后谁都不会不小心把 flask / xijian_api 重新引入 devkit"。
**接谁做**：不需要拆 — 结构性测试就该集中在这一个文件里，不分散。

### 测试覆盖情况（更新后）
- 原有 83 个 devkit 测试全部通过
- 结构性契约测试 2 个新加（不依赖 flask / 不依赖 xijian_api）
- `python -c "import devkit; import devkit.api; import devkit.main"` 在 sys.modules 把 `xijian_api` 设成 None 时仍可正常 import —— 拆分落地

### 跨章节联动点
- **C0 流程图**：上轮（2026-07-04）已经把"开发者工具"指向"独立 Pywebview 窗口 + 邮件"。本轮补一条"开发者工具产物 = 独立 PyInstaller .app，不再挂在 xijian_api 包下"
- **A1.1 自动备份**：上轮留的 `devkit_state.json` 入备份清单的承诺，本轮没有新内容；维持原样
- **C5 接口表**：附录 C 已经写"C5 接口走 DevKit 窗口，不挂在 `/v1/xijian/*` 主路由下"——本轮物理拆分后这条更准确了，不用改

---

## 2026-07-11 · A4.3 场景与互动（首次实装 + 顺手修 A4.2 残留 flaky）

### 任务来源
接 A4.2（已 commit `7f90666`）的下一章节。A4.3 spec 列出 3 张表（`pois` / `travel_modes` / `interactions`），其中 `interactions` 与现有 `xijian_api.stubs.interactions`（chat-level 拥抱/接吻模板）**重名但不重意**——A4.3 spec 把 `interactions` 表明确划在场景侧，命名空间归 `/v1/xijian/scenes/*`，与 chat-level 的 `/v1/xijian/interactions` 隔离。

### 完成清单
- **stubs**（3 个新文件，全新写）
  - `stubs/pois.py`（490 行）—— CRUD + 三级父子约束（map → region → leaf）+ tree 渲染 + ancestor chain + descendants + children
  - `stubs/travel_modes.py`（330 行）—— CRUD + `estimate_trip` 预演（speed_factor 反比、stamina/event_chance 透传、可选 random_roll）
  - `stubs/scene_interactions.py`（680 行）—— CRUD + `trigger()` 完整流程（per-character cooldown 锁 / character-state interactable gate / NPC 死亡 gate / audit 写入 / A4.1 `fire_event` 联动）
- **state.py** —— 3 个新 bucket + reset + `__all__`
- **utils/ids.py** —— `gen_poi_id` / `gen_travel_mode_id` / `gen_scene_interaction_id`，前缀 `poi_` / `tmode_` / `sint_`
- **routes** —— `xijian_scenes.py` 单 blueprint 管 3 套资源，统一挂 `/v1/xijian/scenes/*`；注册到 `routes/__init__.py` 可选列表
- **conftest** —— autouse reset 链追加 3 个 stub 的 `reset_for_testing`
- **测试** —— 148 个新 case
  - `test_xijian_pois.py`（51 个）：纯函数 9 / CRUD 18 / 树查询 6 / 路由 17 / auth 1
  - `test_xijian_travel_modes.py`（39 个）：纯函数 11 / CRUD 15 / 路由 13
  - `test_xijian_scene_interactions.py`（58 个）：纯函数 14 / CRUD 16 / trigger 11 / 路由 17

### 顺手修 A4.2 移交时残留的 4 个 flaky
- **`conftest.py:130`** —— `_reset_state` 先调 `stubs_state.reset_for_testing()`（re-seed → `npcs.install_overload_handler()`），再调 `ov_stub.reset_for_testing()`（**清空 action-handler 寄存器**），handler 被后者干掉了。在 overload reset 之后**显式 reinstall** 一次 handler。
- **`stubs/npcs.py:tick_world`** —— 非 suspended 路径缺 `"suspended": False` 键，route 层 `out.get("suspended")` 永远 None。补字段。
- **`stubs/events.py:_evaluate_probability_trigger`** —— 原本用 `hash(("probability", bucket))`，被 `PYTHONHASHSEED` 随机化。换成 `_stable_hash_unit` 走 `hashlib.sha256(repr(key))`，**完全确定**。
- **`tests/test_xijian_events.py:test_sweep_can_find_both_true_and_false`** —— sweep 50 个连续 unix second，但默认 scheduler interval=60s，50 sample 折叠到 1 个 bucket，hash 是 `<0.5` 的话**所有 50 个都是 True**。改成 sweep `range(start, start+3600*2, 60)`，覆盖 120 个独立 bucket，3/3 稳定。

### 没动的与原因
1. **没重命名 `xijian_interactions` (chat-level 拥抱/接吻)** —— spec 把 `interactions` 划给 A4.3（scene-level），但现有 chat-level blueprint + 测试 + 文档都用 `int_` 前缀和 `/v1/xijian/interactions` 路径；改名是 7+ 文件跨多个 commit 的大改。本轮让两套并存：A4.3 走 `sint_` 前缀 + `/v1/xijian/scenes/interactions` 路径，**spec 实际行为与命名空间都对齐**。后续在 A3.2 验收期间统一收编（要么把 chat-level 改名 `xijian_social_actions`，要么给 A4.3 改名 `xijian_scene_actions`），那一轮单独评估。
2. **没 seed 任何内置 POI / travel_mode / scene_interaction** —— v2 spec 全部写"内置 + 用户自定义"但**没列内置清单**；A4.2 同款问题。等 v2.2/2.3 把内置清单明确下来再 seed。
3. **场景切换的画面/音效过渡（AC-1 < 2s）** —— 完全在客户端（DevKit / 桌宠）层，Core API 端不沾，**不是这个章节的事**。
4. **互动结果 → 角色状态 / 记忆** —— spec US-A4.3-03 写"自然反映到后续对话和记忆"，但 A3.2 / A1.2 都还没就绪（character_state 有但 A1.2 memory backfill 没接）。本轮 trigger 只写 audit + A4.1 fire_event，**不**主动改 character_state / memory。等 A1.2 起来时统一接，最自然。
5. **scene_interaction 没有版本号 / 草稿** —— chat-level interactions 有，scene-level 没加。spec 没说，**v2.1 spec 默认所有 CRUD 资源可被运营任意编辑**，多版本是 Over-engineering。
6. **effects payload schema 没卡死** —— A4.3 spec 不像 A4.1 有 trigger_config schema 那种结构。`effects` 留作 free-form dict，只约定 4 个 reserved key（`fire_event_id` / `stamina_delta` / `mood_delta` / `world_state`），客户端/运营怎么用随便。**等具体使用方长出来再收紧**。
7. **travel event 真触发的 dispatcher 没接** —— `estimate_trip` 算 `event_triggered` bool，但本轮没有 `trip_plan` API 把"事件真的发生"接进 A4.1 world_events。属于 A4.3 + A4.1 进一步联动，留给后续 trip-plan 章节。
8. **NPC target_type 没限制"必须存在"** —— 只在 NPC target 已死时拒绝；target_id 不存在时（运营手抖）不校验（保持 operator-friendly）。等真的被误用触发时再补强校验。

### 跨章节联动点
- **A4.1** —— `fire_event_id` 走 `events.fire_event` 真触发 world event（best-effort，单测 monkeypatch 验证）；失败不阻塞 trigger 主流程
- **A3.2** —— character-state read-only 查询 `_character_is_interactable`（health<=0 / status=unconscious/frozen/dead 全挡），**不**改写 character_state
- **A4.2** —— NPC `is_alive=False` 时 target_type=npc 的 trigger 被拒（`reason=target_dead`）
- **A2** —— 共用同一个 `world_audit.record` 入口（actor 限制在 ACTORS 集合里，本轮走 `user`/`system`），所有互动结果可回溯
- **C5** —— DevKit 走的是独立 Pywebview 窗口，**不**经过 `/v1/xijian/scenes/*` 路由；场景侧蓝图对 DevKit 是"只读 + 写入配置"两个 use case，目前没有 DevKit 专用 blueprint

### 真实启动验证
- 三套端到端（world → POI 三级树 → travel estimate → scene interaction trigger → cooldown 拒绝）走 test_client 全绿
- 触发 audit 写入 1 条 record
- cooldown 第二次返回 409 + `error.code=on_cooldown`，与 spec 行为一致

### 改动文件清单
```
modified:   core/xijian_api/stubs/state.py                (+26 行：3 bucket + reset + __all__)
modified:   core/xijian_api/stubs/__init__.py             (+3 import + 3 seed hook + 3 __all__)
modified:   core/xijian_api/stubs/npcs.py                 (+1 行：tick_world suspended 字段)
modified:   core/xijian_api/stubs/events.py               (+15 行：_stable_hash_unit helper)
modified:   core/xijian_api/utils/ids.py                  (+30 行：3 个 gen_*_id)
modified:   core/xijian_api/routes/__init__.py            (+1 行：xijian_scenes 蓝图注册)
modified:   core/tests/conftest.py                        (+8 行：npcs handler reinstall + 3 个 reset)
modified:   core/tests/test_xijian_events.py             (sweep 范围 50→120 bucket)
modified:   docs/Dev. Function List功能清单v2.md          (changelog 加 v2.5 行)
new file:   core/xijian_api/stubs/pois.py                 (490 行)
new file:   core/xijian_api/stubs/travel_modes.py         (330 行)
new file:   core/xijian_api/stubs/scene_interactions.py   (680 行)
new file:   core/xijian_api/routes/xijian_scenes.py       (510 行)
new file:   core/tests/test_xijian_pois.py                (590 行)
new file:   core/tests/test_xijian_travel_modes.py        (430 行)
new file:   core/tests/test_xijian_scene_interactions.py  (810 行)
```

数字：**762 = 614 (基线) + 148 (新增)，3/3 稳定 0 回归**。

---

## 维护约定

- 每次改完一个章节，**当日**补一条到本文件，格式：日期 + 章节 + 改动清单 + 没动的与原因
- 不要再新建别的笔记文件——都进这里，保持单一来源
- 跨章节的设计决策（比如 A5.x 怎么挡 critical）写到对应章节下"跨章节联动点"
- 文档里的 `[TODO]` 实际化之后，从 v2 文档摘掉，并在本文件留一行"摘除记录"