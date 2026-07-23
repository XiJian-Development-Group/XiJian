# XiJian Core MCP 功能说明

> 文档版本：v1.0
> 适用版本：XiJian Core API v0.1.0+
> 对应功能清单：Dev. Function List功能清单v2.md §A2 / §A5.2

---

## 1. 概览

XiJian Core 实现了完整的 **MCP（Model Context Protocol）** 服务端，让外部 MCP-aware Agent（以及内置的 Chat 工具调用管线）能够通过统一的 JSON-RPC 2.0 协议操作隙间的角色、世界、记忆、文件等能力。

MCP 功能分三部分：

| 部分 | 说明 | 状态 |
| ---- | ---- | ---- |
| **Part 1 — MCP Server** | JSON-RPC 2.0 协议端点，87 个工具 / 8 个资源 / 4 个提示 | ✅ 完整实现 |
| **Part 2 — Chat 工具调用** | 在 `/v1/chat/completions` 中注入 MCP 工具，模型可调用 | ✅ 完整实现 |
| **Part 3 — 桌面控制工具** | app_launch / browser / keyboard / mouse 等桌面动作 | ⚠️ 转发骨架（见 §8） |

**端点**：`POST /v1/mcp`（单请求或批量）
**协议版本**：`2025-06-18`
**服务名**：`xijian-core` / `1.0.0`
**鉴权**：与全局一致，`Authorization: Bearer <token>`

---

## 2. 协议

### 2.1 JSON-RPC 2.0 方法

| 方法 | 说明 |
| ---- | ---- |
| `initialize` | 握手，返回协议版本、服务信息、能力声明 |
| `ping` | 心跳，返回空 result |
| `tools/list` | 列出所有已注册工具 |
| `tools/call` | 调用工具（经 A5.2 门禁） |
| `resources/list` | 列出所有只读资源 |
| `resources/read` | 读取资源内容 |
| `prompts/list` | 列出所有提示模板 |
| `prompts/get` | 渲染提示模板 |

### 2.2 请求示例

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "character_list",
    "arguments": {}
  }
}
```

### 2.3 批量请求

```json
[
  {"jsonrpc": "2.0", "id": 1, "method": "ping"},
  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
]
```

通知（无 `id`）返回 HTTP 202，不产生响应体。

### 2.4 错误码

| HTTP | JSON-RPC code | 含义 |
| ---- | ------------- | ---- |
| 400 | -32700 | 解析错误（非 JSON） |
| 400 | -32600 | 无效请求 |
| 404 | -32601 | 方法不存在 |
| 400 | -32602 | 参数无效 |
| 500 | -32603 | 内部错误 |
| 401/403 | — | 未鉴权 |

工具调用经 A5.2 门禁拒绝时返回 `isError: true` 的 result（非 JSON-RPC error），内含 `_gate` 字段。

---

## 3. 工具（87 个）

所有工具按域名模块注册，名称按字母序排列。

### 3.1 角色工具（9 个） — `characters.py`

| 工具 | 说明 |
| ---- | ---- |
| `character_create` | 创建角色 |
| `character_delete` | 删除角色 |
| `character_get` | 获取角色详情 |
| `character_list` | 列出所有角色 |
| `character_set_loaded` | 设置当前加载角色 |
| `character_state_get` | 获取角色状态（A3.2） |
| `character_state_summary` | 角色状态摘要 |
| `character_state_update` | 更新角色状态 |
| `character_update` | 更新角色信息 |

### 3.2 世界工具（11 个） — `worlds.py`

| 工具 | 说明 |
| ---- | ---- |
| `world_create` | 创建世界 |
| `world_delete` | 删除世界 |
| `world_get` | 获取世界详情 |
| `world_get_state` | 获取世界状态 |
| `world_list` | 列出所有世界 |
| `world_reset_confirm` | 确认重置世界 |
| `world_reset_preview` | 预览重置影响 |
| `world_summary` | 世界摘要 |
| `world_switch_active` | 切换当前活跃世界 |
| `world_transition` | 世界状态转换 |
| `world_update` | 更新世界信息 |

### 3.3 记忆工具（7 个） — `memory.py`

| 工具 | 说明 |
| ---- | ---- |
| `memory_create` | 创建记忆条目 |
| `memory_forget` | 遗忘记忆 |
| `memory_get` | 获取记忆详情 |
| `memory_list` | 列出记忆 |
| `memory_load_context` | 加载上下文记忆 |
| `memory_recall` | 召回记忆 |
| `memory_search` | 语义搜索记忆 |

### 3.4 会话工具（6 个） — `sessions.py`

| 工具 | 说明 |
| ---- | ---- |
| `session_append_message` | 追加消息 |
| `session_create` | 创建会话 |
| `session_delete` | 删除会话 |
| `session_get` | 获取会话 |
| `session_list` | 列出会话 |
| `session_list_messages` | 列出会话消息 |

### 3.5 NPC 工具（5 个） — `npcs.py`

| 工具 | 说明 |
| ---- | ---- |
| `npc_create` | 创建 NPC |
| `npc_get` | 获取 NPC |
| `npc_list` | 列出 NPC |
| `npc_set_tier` | 设置 NPC 层级 |
| `npc_tick_world` | 推进世界 NPC 调度 |

### 3.6 经济工具（6 个） — `economy.py`

| 工具 | 说明 |
| ---- | ---- |
| `economy_purchase` | 购买 |
| `economy_reward` | 奖励 |
| `economy_summary` | 经济摘要 |
| `transaction_list` | 交易列表 |
| `wallet_get` | 获取钱包 |
| `wallet_list` | 列出钱包 |

### 3.7 事件工具（5 个） — `events.py`

| 工具 | 说明 |
| ---- | ---- |
| `event_create` | 创建事件 |
| `event_get` | 获取事件 |
| `event_list` | 列出事件 |
| `event_list_instances` | 列出事件实例 |
| `event_trigger` | 触发事件 |

### 3.8 设置工具（3 个） — `settings.py`

| 工具 | 参数 | 说明 |
| ---- | ---- | ---- |
| `settings_get` | `key?` | 读取全部或单个设置 |
| `settings_update` | `patch` 或 `key`+`value` | 更新设置 |
| `settings_reset` | `key?` | 重置设置 |

### 3.9 保护模块工具（20 个） — `protection.py`

MCP 自身的安全管控工具（A5.2）：

- **规则**：`mcp_rule_create` / `mcp_rule_get` / `mcp_rule_list` / `mcp_rule_update` / `mcp_rule_delete`
- **策略**：`mcp_policy_get` / `mcp_policy_set` / `mcp_policy_reset`
- **审计**：`mcp_audit_list` / `mcp_audit_count`
- **安全停止**：`mcp_safety_stop_initiate` / `mcp_safety_stop_confirm` / `mcp_safety_stop_cancel` / `mcp_safety_stop_get` / `mcp_safety_stop_list`
- **快照**：`mcp_snapshot_create` / `mcp_snapshot_get` / `mcp_snapshot_list` / `mcp_snapshot_restore` / `mcp_snapshot_sanitize`

### 3.10 文件工具（5 个） — `files.py`（真实文件系统操作）

| 工具 | 说明 | A5.2 action_kind |
| ---- | ---- | ---------------- |
| `file_read` | 读文件（1 MB 上限，二进制返 base64） | `file_read` |
| `file_write` | 写文件（1 MB 上限，支持 append） | `file_write` |
| `file_list` | 列目录（500 条上限，支持 glob） | `file_read` |
| `file_delete` | 删除文件/目录（目录需 `recursive=true`） | `file_delete` |
| `file_stat` | 文件元数据 | `file_read` |

**路径范围**：限定在用户主目录 `~` 内。系统目录（`/etc`、`/var`、`/usr`、`/bin`、`/System`、`/Library` 等）一律拒绝。`..` 会被解析后检查。详见 §5。

### 3.11 桌面控制工具（10 个） — `desktop.py`（转发骨架）

| 工具 | 说明 |
| ---- | ---- |
| `app_launch` | 启动应用（参数 `app_name` 或 `app_path`） |
| `browser_open` | 打开 URL |
| `browser_click` | 点击元素 |
| `browser_type` | 输入文本 |
| `browser_screenshot` | 截图 |
| `keyboard_type` | 键盘输入 |
| `keyboard_key` | 按键 |
| `mouse_click` | 鼠标点击 |
| `desktop_pending_list` | 列出待办动作 |
| `desktop_pending_get` | 获取待办动作详情 |

**状态**：转发骨架。Core 将动作记录到待办队列（`state.mcp_pending_actions`），由桌面客户端拉取执行。详见 §8。

---

## 4. 资源（8 个） — `resources.py`

只读资源，URI scheme 为 `xijian://`。

| URI | 说明 |
| --- | ---- |
| `xijian://server/info` | 服务版本、协议、状态计数 |
| `xijian://characters` | 所有角色 |
| `xijian://worlds` | 所有世界 |
| `xijian://memory?character_id=X` | 记忆条目（可按角色过滤） |
| `xijian://sessions` | 所有会话 |
| `xijian://mcp/rules` | MCP 保护规则 |
| `xijian://mcp/audit` | MCP 审计日志 |
| `xijian://mcp/policy/{world_id}` | 世界 MCP 策略 |

---

## 5. 提示（4 个） — `prompts.py`

| 名称 | 参数 | 说明 |
| ---- | ---- | ---- |
| `character_setup` | `name?`, `persona?` | 引导创建角色 |
| `world_setup` | `name?`, `theme?` | 引导创建世界 |
| `memory_recall` | `character_id?`, `topic?` | 引导检索记忆 |
| `npc_tick` | `world_id?` | 引导推进世界时间 |

---

## 6. A5.2 保护门禁

所有带 `action_kind` 的工具在执行前必须通过 A5.2 门禁（`mcp_stub.check`）。

### 6.1 action_kind

| action_kind | 说明 |
| ----------- | ---- |
| `file_delete` | 文件删除 |
| `file_write` | 文件写入 |
| `file_read` | 文件读取 |
| `shell` | Shell 执行 |
| `network` | 网络请求 |
| `app_launch` | 应用启动 |
| `settings_modify` | 设置修改 |
| `system_cmd` | 系统命令 |

### 6.2 判定流程

1. 系统锁定状态检查 → `denied_lockout`
2. 世界待处理冻结检查 → `denied_frozen`
3. 匹配规则（`match_action_rules`）：
   - 黑名单命中 → `denied`（`blocked=blacklist_hit`）
   - 白名单命中 + `default=deny` → `allowed`
   - 无白名单命中 + `default=deny` → `denied`（`blocked=default_deny_no_match`）
   - `default=allow` → `allowed`
4. 异常兜底 → `denied_crashed`

### 6.3 世界策略

```python
mcp_stub.set_world_policy("world_001", default="allow")  # or "deny"
```

- `default=deny`（推荐）：无规则匹配时拒绝，白名单放行
- `default=allow`：无规则匹配时允许，黑名单拦截

### 6.4 文件路径校验

文件工具的路径校验在 handler 内执行（门禁通过后）：

1. 展开 `~`，解析 `..` 和符号链接
2. 拒绝系统目录（`/etc`、`/var`、`/usr`、`/System`、`/Library` 等）
3. 拒绝主目录外的路径
4. 拒绝空路径

---

## 7. Chat 工具调用管线（A2）

在 `/v1/chat/completions` 中可通过两种方式触发 MCP 工具注入：

### 7.1 隙间扩展字段

```json
{
  "model": "qwen2.5-7b",
  "messages": [{"role": "user", "content": "列出我的角色"}],
  "xijian": {"tools": {"enabled": true}}
}
```

### 7.2 OAI 原生 tools 字段

```json
{
  "model": "qwen2.5-7b",
  "messages": [...],
  "tools": [{
    "type": "function",
    "function": {
      "name": "character_list",
      "description": "List all characters",
      "parameters": {"type": "object", "properties": {}}
    }
  }],
  "tool_choice": "auto"
}
```

两种方式均支持。`tool_choice` 支持 `"auto"` / `"required"` / `"none"` / `{"type":"function","function":{"name":"..."}}`。

### 7.3 工作原理

由于 AI backend 的 `chat()` 接口是低层文本生成契约（只接受 `stream` / `abort_signal`），工具描述以**文本形式注入 system prompt**，而非作为 backend kwargs。模型在回复中输出 OAI `tool_calls` 格式，管线解析后通过 registry 执行，结果以 `role=tool` 消息回填，进入下一轮（最多多轮）。

### 7.4 响应

响应体 `xijian.tools` 块包含：

```json
{
  "xijian": {
    "tools": {
      "enabled": true,
      "tool_calls": [
        {"name": "character_list", "arguments": {}, "result": {...}}
      ]
    }
  }
}
```

---

## 8. 桌面控制转发骨架（Part 3）

Core API 无法直接执行桌面级操作（启动应用、控制浏览器、模拟键鼠）。桌面控制工具采用**转发骨架**模式：

1. 模型调用 `app_launch` / `browser_open` 等工具
2. A5.2 门禁检查通过后，Core 将动作记录到待办队列 `state.mcp_pending_actions`
3. 返回 `status=forwarded` 响应，包含 `action_id`
4. **桌面客户端**（待实现）通过轮询或 WebSocket 拉取待办，本地执行，回写结果

### 8.1 待办队列结构

```json
{
  "id": "mcpact_<12 hex>",
  "kind": "app_launch",
  "action": {"app_name": "Safari", "args": []},
  "status": "pending",
  "world_id": "world_001",
  "created_at": 1784769600.0,
  "claimed_at": null,
  "result": null
}
```

### 8.2 待实现项（TODO）

- `GET /v1/xijian/mcp/pending` — 桌面客户端拉取待办端点
- WebSocket 推送 — 实时通知桌面客户端
- `POST /v1/xijian/mcp/pending/<id>/result` — 结果回写端点
- 桌面客户端集成（Electron / Tauri 侧）

当前 Core 侧能力：工具注册、门禁、入队、查询待办（`desktop_pending_list` / `desktop_pending_get`）已完整实现。

---

## 9. 测试

测试文件：`core/tests/test_mcp_server.py`（84 个测试，15 个测试类）

覆盖：协议层（initialize/ping/tools/resources/prompts/errors/notifications/batch）、registry（注册/分发/门禁路由）、各工具模块、文件路径校验、桌面转发、Chat 管线、Flask 路由。

```bash
conda activate xijianBase
python -m pytest core/tests/test_mcp_server.py -v
```

---

## 10. 架构文件索引

| 文件 | 说明 |
| ---- | ---- |
| `core/xijian_api/mcp/protocol.py` | JSON-RPC 2.0 协议处理 |
| `core/xijian_api/mcp/registry.py` | 工具注册与分发（含门禁路由） |
| `core/xijian_api/mcp/resources.py` | 只读资源 |
| `core/xijian_api/mcp/prompts.py` | 提示模板 |
| `core/xijian_api/mcp/tools/*.py` | 11 个工具模块 |
| `core/xijian_api/stubs/mcp.py` | A5.2 门禁实现 |
| `core/xijian_api/stubs/mcp_rules.py` | A5.2 规则簿 |
| `core/xijian_api/routes/mcp_server.py` | Flask 路由 `POST /v1/mcp` |
| `core/xijian_api/stubs/chat.py` | Chat 工具调用管线（A2） |
| `core/tests/test_mcp_server.py` | 测试套件 |
