"""Desktop control forward tools — app launch / browser automation.
桌面控制转发工具 — 应用启动/浏览器自动化。

These tools represent actions that the Core API **cannot execute
directly** because they require desktop-level access (launching
applications, controlling a browser, simulating keyboard/mouse
input).  The Core records each requested action into a **pending
queue** in :mod:`xijian_api.stubs.state`; the desktop client is
expected to poll or subscribe to this queue and execute the actions
locally.
这些工具代表核心 API **无法直接执行**的操作，因为它们需要桌面级访问权限
（启动应用、控制浏览器、模拟键盘/鼠标输入）。核心将每个请求的操作记录到
:mod:`xijian_api.stubs.state` 的**待办队列**中；桌面客户端应轮询或订阅此队列并本地执行操作。

The A5.2 gate still runs before the action is enqueued — even
though Core doesn't execute it, the protection layer must still
approve the intent.  This matches the spec: "所有 MCP 工具调用进入
前必须过危险动作白名单/黑名单".
A5.2 门禁仍在操作入队前运行 — 即使核心不执行它，保护层仍需批准意图。

Action kinds / 操作类型
============

* ``app_launch``     → :data:`rules_stub.KIND_APP_LAUNCH`
* ``network``        → :data:`rules_stub.KIND_NETWORK` (browser fetch / 浏览器抓取)
* ``shell``          → :data:`rules_stub.KIND_SHELL` (keyboard/mouse / 键盘/鼠标)

Pending queue / 待办队列
=============

The queue lives at ``state.mcp_pending_actions`` (a dict keyed by
action id).  Each entry has::
队列位于 ``state.mcp_pending_actions``（按操作 ID 索引的字典）。每条记录格式：:

    {
        "id": "mcpact_<12 hex>",
        "kind": "app_launch" | "browser_open" | "browser_click" | ...,
        "action": { ... specific parameters ... },
        "status": "pending" | "claimed" | "executed" | "failed",
        "world_id": str | None,
        "created_at": float,
        "claimed_at": float | None,
        "result": dict | None,
    }

TODO: The desktop client integration (polling, WebSocket push,
result write-back) is not yet implemented.  These tools currently
only enqueue the action and return a structured "forwarded"
response.  A future desktop client will consume the queue.
TODO：桌面客户端集成（轮询、WebSocket 推送、结果回写）尚未实现。
这些工具当前仅将操作入队并返回结构化的"已转发"响应。将来的桌面客户端将消费此队列。
"""

from __future__ import annotations

import logging
from typing import Any

from xijian_api.mcp.registry import ToolError, register_tool
from xijian_api.stubs import mcp_rules as rules_stub
from xijian_api.stubs import state
from xijian_api.utils.ids import gen_id
from xijian_api.utils.time import now_ts


_LOGGER = logging.getLogger("xijian_api.mcp.tools.desktop")


# ---------------------------------------------------------------------------
# Pending queue helpers / 待办队列辅助函数
# ---------------------------------------------------------------------------


def _enqueue(
    kind: str,
    action: dict[str, Any],
    *,
    world_id: str | None = None,
) -> dict[str, Any]:
    """Enqueue a desktop action and return the record.
    将桌面操作入队并返回记录。

    The record is stored at ``state.mcp_pending_actions[action_id]``
    so the desktop client can discover and claim it.
    记录存储在 ``state.mcp_pending_actions[action_id]`` 中，
    以便桌面客户端发现并认领它。
    """
    # Lazy-init the state bucket so we don't mutate the stubs.state
    # module at import time.
    # 延迟初始化状态桶，避免在导入时修改 stubs.state 模块。
    if not hasattr(state, "mcp_pending_actions"):
        state.mcp_pending_actions = {}  # type: ignore[attr-defined]
    action_id = gen_id("mcpact")
    record: dict[str, Any] = {
        "id": action_id,
        "kind": kind,
        "action": action,
        "status": "pending",
        "world_id": world_id,
        "created_at": now_ts(),
        "claimed_at": None,
        "result": None,
    }
    state.mcp_pending_actions[action_id] = record  # type: ignore[attr-defined]
    _LOGGER.info("enqueued desktop action %s (kind=%s)", action_id, kind)
    return record


def _format_forwarded(record: dict[str, Any]) -> dict[str, Any]:
    """Format the "forwarded" response for the model.
    格式化供模型使用的"已转发"响应。"""
    import json
    summary = {
        "status": "forwarded",
        "action_id": record["id"],
        "kind": record["kind"],
        "message": (
            "此操作已记录到待办队列，需要桌面客户端执行。"
            "桌面客户端可通过 GET /v1/xijian/mcp/pending 拉取待办。"
            "action_id: %s" % record["id"]
        ),
    }
    return {
        "content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False, indent=2)}],
        "isError": False,
        "_meta": {"forwarded": True, "action_id": record["id"]},
    }


# ---------------------------------------------------------------------------
# Handlers / 处理器
# ---------------------------------------------------------------------------


def _app_launch_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    app_name = args.get("app_name", "")
    app_path = args.get("app_path")
    args_list = args.get("args", [])
    if not app_name and not app_path:
        raise ToolError("either app_name or app_path is required")

    action = {
        "app_name": app_name,
        "app_path": app_path,
        "args": args_list,
    }
    record = _enqueue("app_launch", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _browser_open_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url", "")
    if not url:
        raise ToolError("url is required")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ToolError("url must be a valid http(s) URL")

    action = {"url": url, "new_window": bool(args.get("new_window", False))}
    record = _enqueue("browser_open", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _browser_click_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    selector = args.get("selector", "")
    url = args.get("url", "")
    if not selector:
        raise ToolError("selector is required")

    action = {
        "selector": selector,
        "url": url,
        "click_type": args.get("click_type", "single"),
        "wait_ms": int(args.get("wait_ms", 0)),
    }
    record = _enqueue("browser_click", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _browser_type_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    selector = args.get("selector", "")
    text = args.get("text", "")
    url = args.get("url", "")
    if not selector:
        raise ToolError("selector is required")
    if not isinstance(text, str):
        raise ToolError("text must be a string")

    action = {
        "selector": selector,
        "text": text,
        "url": url,
        "clear_first": bool(args.get("clear_first", True)),
        "submit": bool(args.get("submit", False)),
    }
    record = _enqueue("browser_type", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _browser_screenshot_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url", "")
    action = {
        "url": url,
        "full_page": bool(args.get("full_page", False)),
        "format": args.get("format", "png"),
    }
    record = _enqueue("browser_screenshot", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _keyboard_type_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    text = args.get("text", "")
    if not text:
        raise ToolError("text is required")

    action = {"text": text, "delay_ms": int(args.get("delay_ms", 0))}
    record = _enqueue("keyboard_type", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _keyboard_key_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    key = args.get("key", "")
    if not key:
        raise ToolError("key is required")

    action = {"key": key, "modifiers": args.get("modifiers", []), "count": int(args.get("count", 1))}
    record = _enqueue("keyboard_key", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _mouse_click_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    x = args.get("x")
    y = args.get("y")
    if x is None or y is None:
        raise ToolError("x and y coordinates are required")

    action = {
        "x": int(x),
        "y": int(y),
        "button": args.get("button", "left"),
        "click_type": args.get("click_type", "single"),
        "double_click_interval_ms": int(args.get("double_click_interval_ms", 300)),
    }
    record = _enqueue("mouse_click", action, world_id=ctx.get("world_id"))
    return _format_forwarded(record)


def _pending_list_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    """List pending desktop actions (for the model to check status).
    列出待办桌面操作（供模型检查状态）。"""
    if not hasattr(state, "mcp_pending_actions"):
        state.mcp_pending_actions = {}  # type: ignore[attr-defined]
    status_filter = args.get("status")
    limit = int(args.get("limit", 50))
    entries = list(state.mcp_pending_actions.values())  # type: ignore[attr-defined]
    if status_filter:
        entries = [e for e in entries if e.get("status") == status_filter]
    entries.sort(key=lambda e: e.get("created_at", 0), reverse=True)
    entries = entries[:limit]
    import json
    return {
        "content": [{"type": "text", "text": json.dumps(entries, ensure_ascii=False, indent=2)}],
        "isError": False,
        "_meta": {"count": len(entries)},
    }


def _pending_get_handler(args: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    """Get a specific pending action by id.
    按 ID 获取特定待办操作。"""
    action_id = args.get("action_id", "")
    if not action_id:
        raise ToolError("action_id is required")
    if not hasattr(state, "mcp_pending_actions"):
        state.mcp_pending_actions = {}  # type: ignore[attr-defined]
    record = state.mcp_pending_actions.get(action_id)  # type: ignore[attr-defined]
    if record is None:
        raise ToolError("action not found: %s" % action_id)
    import json
    return {
        "content": [{"type": "text", "text": json.dumps(record, ensure_ascii=False, indent=2)}],
        "isError": False,
    }


# ---------------------------------------------------------------------------
# Registration / 注册
# ---------------------------------------------------------------------------

# TODO: Desktop client integration — implement a polling endpoint
# (GET /v1/xijian/mcp/pending) and a WebSocket push so the desktop
# client can claim and execute pending actions in real time.
# TODO: Implement result write-back (POST /v1/xijian/mcp/pending/<id>/result)
# so the desktop client can report execution results.
# TODO：桌面客户端集成 — 实现轮询端点 (GET /v1/xijian/mcp/pending) 和 WebSocket 推送，
# 使桌面客户端能实时认领和执行待办操作。
# TODO：实现结果回写 (POST /v1/xijian/mcp/pending/<id>/result)，
# 使桌面客户端可报告执行结果。

register_tool(
    "app_launch",
    "Launch an application on the user's desktop. The action is recorded to a pending "
    "queue for the desktop client to execute. Requires A5.2 gate approval. / "
    "在用户桌面上启动应用。操作记录到待办队列供桌面客户端执行。需要 A5.2 门禁批准。",
    {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "Application name (e.g. 'Safari', 'TextEdit') / 应用名称"},
            "app_path": {"type": "string", "description": "Absolute path to the app (alternative to app_name) / 应用的绝对路径（app_name 的替代）"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Command-line arguments / 命令行参数"},
        },
    },
    _app_launch_handler,
    action_kind=rules_stub.KIND_APP_LAUNCH,
    annotations={"openWorldHint": True},
)

register_tool(
    "browser_open",
    "Open a URL in the user's default browser. The action is recorded to a pending "
    "queue for the desktop client to execute. Requires A5.2 gate approval. / "
    "在用户默认浏览器中打开 URL。操作记录到待办队列供桌面客户端执行。需要 A5.2 门禁批准。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to open (must start with http:// or https://) / 要打开的 URL"},
            "new_window": {"type": "boolean", "description": "Open in a new window (default: false) / 在新窗口中打开", "default": False},
        },
        "required": ["url"],
    },
    _browser_open_handler,
    action_kind=rules_stub.KIND_NETWORK,
    annotations={"openWorldHint": True},
)

register_tool(
    "browser_click",
    "Click an element in the browser by CSS selector. The action is recorded to a "
    "pending queue for the desktop client to execute. / "
    "通过 CSS 选择器在浏览器中点击元素。操作记录到待办队列供桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the element to click / 要点击元素的 CSS 选择器"},
            "url": {"type": "string", "description": "URL of the page (optional, for context) / 页面 URL（可选，供参考）"},
            "click_type": {"type": "string", "enum": ["single", "double", "right"], "description": "Click type (default: single) / 点击类型", "default": "single"},
            "wait_ms": {"type": "integer", "description": "Wait time in ms before clicking (default: 0) / 点击前等待毫秒数", "default": 0},
        },
        "required": ["selector"],
    },
    _browser_click_handler,
    action_kind=rules_stub.KIND_APP_LAUNCH,
    annotations={"openWorldHint": True},
)

register_tool(
    "browser_type",
    "Type text into an input field in the browser by CSS selector. The action is "
    "recorded to a pending queue for the desktop client to execute. / "
    "通过 CSS 选择器在浏览器输入框中输入文本。操作记录到待办队列供桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the input field / 输入框的 CSS 选择器"},
            "text": {"type": "string", "description": "Text to type / 要输入的文本"},
            "url": {"type": "string", "description": "URL of the page (optional, for context) / 页面 URL（可选，供参考）"},
            "clear_first": {"type": "boolean", "description": "Clear field before typing (default: true) / 输入前清空字段", "default": True},
            "submit": {"type": "boolean", "description": "Submit the form after typing (default: false) / 输入后提交表单", "default": False},
        },
        "required": ["selector", "text"],
    },
    _browser_type_handler,
    action_kind=rules_stub.KIND_APP_LAUNCH,
    annotations={"openWorldHint": True},
)

register_tool(
    "browser_screenshot",
    "Take a screenshot of the browser. The action is recorded to a pending queue "
    "for the desktop client to execute. / "
    "截取浏览器屏幕截图。操作记录到待办队列供桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL of the page (optional) / 页面 URL（可选）"},
            "full_page": {"type": "boolean", "description": "Capture full page (default: false) / 截取整页", "default": False},
            "format": {"type": "string", "enum": ["png", "jpeg"], "description": "Image format (default: png) / 图片格式", "default": "png"},
        },
    },
    _browser_screenshot_handler,
    action_kind=rules_stub.KIND_NETWORK,
    annotations={"readOnlyHint": True, "openWorldHint": True},
)

register_tool(
    "keyboard_type",
    "Type text using the keyboard. The action is recorded to a pending queue for "
    "the desktop client to execute. / "
    "使用键盘输入文本。操作记录到待办队列供桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type / 要输入的文本"},
            "delay_ms": {"type": "integer", "description": "Delay between keystrokes in ms (default: 0) / 击键间延迟毫秒数", "default": 0},
        },
        "required": ["text"],
    },
    _keyboard_type_handler,
    action_kind=rules_stub.KIND_SHELL,
    annotations={"openWorldHint": True},
)

register_tool(
    "keyboard_key",
    "Press a keyboard key (with optional modifiers). The action is recorded to a "
    "pending queue for the desktop client to execute. / "
    "按下键盘键（带可选修饰键）。操作记录到待办队列供桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Key name (e.g. 'Enter', 'Escape', 'Tab', 'a') / 键名"},
            "modifiers": {"type": "array", "items": {"type": "string"}, "description": "Modifier keys (e.g. ['ctrl', 'shift']) / 修饰键"},
            "count": {"type": "integer", "description": "Number of times to press (default: 1) / 按下次数", "default": 1},
        },
        "required": ["key"],
    },
    _keyboard_key_handler,
    action_kind=rules_stub.KIND_SHELL,
    annotations={"openWorldHint": True},
)

register_tool(
    "mouse_click",
    "Click the mouse at a screen coordinate. The action is recorded to a pending "
    "queue for the desktop client to execute. / "
    "在屏幕坐标处点击鼠标。操作记录到待办队列供桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X coordinate / X 坐标"},
            "y": {"type": "integer", "description": "Y coordinate / Y 坐标"},
            "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default: left) / 鼠标按键", "default": "left"},
            "click_type": {"type": "string", "enum": ["single", "double"], "description": "Click type (default: single) / 点击类型", "default": "single"},
        },
        "required": ["x", "y"],
    },
    _mouse_click_handler,
    action_kind=rules_stub.KIND_SHELL,
    annotations={"openWorldHint": True},
)

register_tool(
    "desktop_pending_list",
    "List pending desktop actions and their status. Useful for checking whether a "
    "forwarded action has been executed by the desktop client. / "
    "列出待办桌面操作及其状态。用于检查已转发的操作是否已被桌面客户端执行。",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "claimed", "executed", "failed"], "description": "Filter by status / 按状态筛选"},
            "limit": {"type": "integer", "description": "Maximum entries to return (default: 50) / 最大返回条目数", "default": 50},
        },
    },
    _pending_list_handler,
    annotations={"readOnlyHint": True},
)

register_tool(
    "desktop_pending_get",
    "Get a specific pending desktop action by id, including its execution result "
    "if the desktop client has reported one. / "
    "按 ID 获取特定待办桌面操作，如果桌面客户端已报告执行结果则一并返回。",
    {
        "type": "object",
        "properties": {
            "action_id": {"type": "string", "description": "The pending action id / 待办操作 ID"},
        },
        "required": ["action_id"],
    },
    _pending_get_handler,
    annotations={"readOnlyHint": True},
)


__all__: list[str] = []