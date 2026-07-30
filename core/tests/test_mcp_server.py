"""Comprehensive tests for the MCP server (protocol + registry + tools + route).
(MCP 服务器的综合测试（协议 + 注册表 + 工具 + 路由）。)

Covers three layers:
(涵盖三个层次：)

* **Protocol** (:mod:`xijian_api.mcp.protocol`) — JSON-RPC 2.0 method
  dispatch (initialize, ping, tools/list, tools/call, resources/list,
  resources/read, prompts/list, prompts/get), error codes, batch
  handling, notifications.
* (**协议** (:mod:`xijian_api.mcp.protocol`) — JSON-RPC 2.0 方法
  分发 (initialize, ping, tools/list, tools/call, resources/list,
  resources/read, prompts/list, prompts/get)，错误码，批处理，
  通知。)
* **Registry** (:mod:`xijian_api.mcp.registry`) — tool registration,
  dispatch, A5.2 gate routing (whitelist allow, default-deny, blacklist
  deny, ToolError / ToolGateError / ToolNotFoundError).
* (**注册表** (:mod:`xijian_api.mcp.registry`) — 工具注册，
  分发，A5.2 门控路由 (白名单允许，默认拒绝，黑名单
  拒绝，ToolError / ToolGateError / ToolNotFoundError)。)
* **Tools** — representative tools across every module (characters,
  worlds, memory, npcs, economy, events, sessions, settings, files,
  desktop, protection).
* (**工具** — 每个模块的代表性工具 (characters,
  worlds, memory, npcs, economy, events, sessions, settings, files,
  desktop, protection)。)
* **Chat tools pipeline** (A2) — ``xijian.tools.enabled`` and OAI
  ``tools`` field both trigger the pipeline; tool calls are executed
  through the registry and results fed back.
* (**聊天工具管道** (A2) — ``xijian.tools.enabled`` 和 OAI
  ``tools`` 字段都触发该管道；工具调用通过注册表执行，
  结果被反馈。)
* **Flask route** — ``POST /v1/mcp`` single / batch / notification /
  auth.
* (**Flask 路由** — ``POST /v1/mcp`` 单条 / 批处理 / 通知 /
  认证。)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from xijian_api.mcp.protocol import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    handle_batch,
    handle_request,
)
from xijian_api.mcp.registry import (
    ToolError,
    ToolGateError,
    ToolNotFoundError,
    call_tool,
    list_tool_names,
    list_tools,
    register_tool,
)
from xijian_api.mcp.resources import list_resources, read_resource
from xijian_api.mcp.prompts import list_prompts, get_prompt
from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as rules_stub
from xijian_api.stubs import state


# ===========================================================================
# Helpers
# ===========================================================================
# (辅助函数)


def _req(method: str, params: dict | None = None, *, req_id: int | str = 1):
    """Build a JSON-RPC 2.0 request dict.
    (构建一个 JSON-RPC 2.0 请求字典。)
    """
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }


def _notify(method: str, params: dict | None = None):
    """Build a JSON-RPC 2.0 notification (no id).
    (构建一个 JSON-RPC 2.0 通知（无 id）。)
    """
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }


# ===========================================================================
# Protocol layer
# ===========================================================================
# (协议层)


class TestProtocolInitialize:
    """``initialize`` handshake.
    (``initialize`` 握手。)
    """

    def test_returns_server_info_and_capabilities(self):
        """Initialize returns server info and capabilities.
        (初始化返回服务器信息和能力声明。)
        """
        resp = handle_request(_req("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        }))
        assert resp is not None
        assert "result" in resp
        result = resp["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == SERVER_NAME
        assert result["serverInfo"]["version"] == SERVER_VERSION
        caps = result["capabilities"]
        assert "tools" in caps
        assert "resources" in caps
        assert "prompts" in caps

    def test_works_without_params(self):
        """Initialize works without params.
        (初始化无需参数也能工作。)
        """
        resp = handle_request(_req("initialize"))
        assert resp is not None
        assert "result" in resp


class TestProtocolPing:
    def test_ping_returns_empty(self):
        """Ping returns empty result.
        (Ping 返回空结果。)
        """
        resp = handle_request(_req("ping"))
        assert resp is not None
        assert resp["result"] == {}


class TestProtocolToolsList:
    def test_returns_all_registered_tools(self):
        """Lists all registered tools with required fields.
        (列出所有已注册工具及其必需字段。)
        """
        resp = handle_request(_req("tools/list"))
        assert resp is not None
        tools = resp["result"]["tools"]
        assert len(tools) >= 80  # 87 at time of writing (写作时为 87)
        # Every tool has the required fields.
        # (每个工具都有必需的字段。)
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "inputSchema" in t

    def test_tools_are_sorted_by_name(self):
        """Tools are sorted alphabetically by name.
        (工具按名称字母顺序排序。)
        """
        resp = handle_request(_req("tools/list"))
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == sorted(names)


class TestProtocolToolsCall:
    def test_calls_internal_tool(self):
        """character_list is an internal domain tool (no gate).
        (character_list 是内部域工具（无门控）。)
        """
        resp = handle_request(_req("tools/call", {
            "name": "character_list",
            "arguments": {},
        }))
        assert resp is not None
        result = resp["result"]
        assert result["isError"] is False
        assert len(result["content"]) > 0
        assert result["content"][0]["type"] == "text"

    def test_unknown_tool_returns_error(self):
        """Unknown tool name returns JSON-RPC error.
        (未知工具名称返回 JSON-RPC 错误。)
        """
        resp = handle_request(_req("tools/call", {
            "name": "nonexistent_tool",
            "arguments": {},
        }))
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    def test_missing_name_param(self):
        """Missing name parameter returns JSON-RPC error.
        (缺少 name 参数返回 JSON-RPC 错误。)
        """
        resp = handle_request(_req("tools/call", {"arguments": {}}))
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    def test_arguments_defaults_to_empty_dict(self):
        """Arguments defaults to empty dict when omitted.
        (省略时 arguments 默认为空字典。)
        """
        resp = handle_request(_req("tools/call", {
            "name": "character_list",
        }))
        assert resp is not None
        assert "result" in resp

    def test_gate_denial_returns_iserror_result(self):
        """A gate denial is an isError result, not a JSON-RPC error.
        (门控拒绝返回 isError 结果，而非 JSON-RPC 错误。)
        """
        # file_write has action_kind=file_write → gate runs.
        # With no world_id and default=deny, the gate denies.
        # (file_write 有 action_kind=file_write → 门控运行。
        # 没有 world_id 且 default=deny 时，门控拒绝。)
        resp = handle_request(_req("tools/call", {
            "name": "file_write",
            "arguments": {"path": "~/xijian_test_file.txt", "content": "test"},
        }))
        assert resp is not None
        result = resp["result"]
        assert result["isError"] is True
        assert "_gate" in result


class TestProtocolResources:
    def test_resources_list(self):
        """Lists available resources.
        (列出可用资源。)
        """
        resp = handle_request(_req("resources/list"))
        assert resp is not None
        resources = resp["result"]["resources"]
        assert len(resources) >= 5
        for r in resources:
            assert "uri" in r
            assert "name" in r

    def test_resources_read_server_info(self):
        """Reads server info resource.
        (读取服务器信息资源。)
        """
        resp = handle_request(_req("resources/read", {
            "uri": "xijian://server/info",
        }))
        assert resp is not None
        result = resp["result"]
        assert "contents" in result

    def test_resources_read_invalid_uri(self):
        """Read with invalid URI returns error.
        (使用无效 URI 读取返回错误。)
        """
        resp = handle_request(_req("resources/read", {
            "uri": "xijian://nonexistent/resource",
        }))
        assert resp is not None
        assert "error" in resp

    def test_resources_read_missing_uri(self):
        """Read without URI returns error.
        (没有 URI 的读取返回错误。)
        """
        resp = handle_request(_req("resources/read", {}))
        assert resp is not None
        assert "error" in resp


class TestProtocolPrompts:
    def test_prompts_list(self):
        """Lists available prompts.
        (列出可用提示模板。)
        """
        resp = handle_request(_req("prompts/list"))
        assert resp is not None
        prompts = resp["result"]["prompts"]
        assert len(prompts) >= 3
        for p in prompts:
            assert "name" in p
            assert "description" in p

    def test_prompts_get(self):
        """Gets a prompt with arguments.
        (获取带参数的提示模板。)
        """
        resp = handle_request(_req("prompts/get", {
            "name": "character_setup",
            "arguments": {"character_id": "char_test"},
        }))
        assert resp is not None
        result = resp["result"]
        assert "messages" in result

    def test_prompts_get_unknown(self):
        """Get unknown prompt returns error.
        (获取未知提示模板返回错误。)
        """
        resp = handle_request(_req("prompts/get", {
            "name": "nonexistent_prompt",
        }))
        assert resp is not None
        assert "error" in resp


class TestProtocolErrors:
    """JSON-RPC protocol error handling.
    (JSON-RPC 协议错误处理。)
    """

    def test_invalid_jsonrpc_version(self):
        """Invalid jsonrpc version returns error.
        (无效的 jsonrpc 版本返回错误。)
        """
        resp = handle_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_method_not_found(self):
        """Unknown method returns method-not-found error.
        (未知方法返回 method-not-found 错误。)
        """
        resp = handle_request(_req("nonexistent/method"))
        assert resp is not None
        assert resp["error"]["code"] == -32601

    def test_missing_method(self):
        """Missing method field returns error.
        (缺少 method 字段返回错误。)
        """
        resp = handle_request({"jsonrpc": "2.0", "id": 1})
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_non_dict_request(self):
        """Non-dict request returns error.
        (非字典请求返回错误。)
        """
        resp = handle_request("not a dict")
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_params_not_object(self):
        """Params with wrong type returns error.
        (参数类型错误返回错误。)
        """
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "ping", "params": "string",
        })
        assert resp is not None
        assert resp["error"]["code"] == -32602


class TestProtocolNotifications:
    def test_notification_returns_none(self):
        """Notifications (no id) return None — no response body.
        (通知（无 id）返回 None — 无响应体。)
        """
        resp = handle_request(_notify("ping"))
        assert resp is None

    def test_notification_unknown_method_returns_none(self):
        """Unknown method notification also returns None.
        (未知方法的通知也返回 None。)
        """
        resp = handle_request(_notify("nonexistent/method"))
        assert resp is None


class TestProtocolBatch:
    """JSON-RPC batch request handling.
    (JSON-RPC 批处理请求处理。)
    """

    def test_batch_multiple_requests(self):
        """Multiple requests in batch return array of responses.
        (批处理中的多个请求返回响应数组。)
        """
        batch = [
            _req("ping", req_id=1),
            _req("tools/list", req_id=2),
            _req("initialize", req_id=3),
        ]
        resp = handle_batch(batch)
        assert isinstance(resp, list)
        assert len(resp) == 3
        assert resp[0]["id"] == 1
        assert resp[1]["id"] == 2
        assert resp[2]["id"] == 3

    def test_batch_with_notification(self):
        """Notifications in a batch produce no response entry.
        (批处理中的通知不产生响应条目。)
        """
        batch = [
            _notify("ping"),
            _req("ping", req_id=1),
        ]
        resp = handle_batch(batch)
        assert isinstance(resp, list)
        assert len(resp) == 1
        assert resp[0]["id"] == 1

    def test_empty_batch_returns_error(self):
        """Empty batch returns error.
        (空批处理返回错误。)
        """
        resp = handle_batch([])
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_single_dict_delegates_to_handle_request(self):
        """Single dict batch delegates to handle_request.
        (单个字典的批处理委托给 handle_request。)
        """
        resp = handle_batch(_req("ping"))
        assert resp is not None
        assert "result" in resp


# ===========================================================================
# Registry
# ===========================================================================
# (注册表)


class TestRegistry:
    """MCP tool registry operations.
    (MCP 工具注册表操作。)
    """

    def test_list_tools_returns_specs_without_handler(self):
        """Tool specs returned without handler info.
        (返回的工具规格不含处理器信息。)
        """
        tools = list_tools()
        for t in tools:
            assert "handler" not in t
            assert "action_kind" not in t

    def test_list_tool_names_sorted(self):
        """Tool names are sorted alphabetically.
        (工具名称按字母顺序排序。)
        """
        names = list_tool_names()
        assert names == sorted(names)

    def test_call_unknown_tool_raises(self):
        """Calling unknown tool raises ToolNotFoundError.
        (调用未知工具抛出 ToolNotFoundError。)
        """
        with pytest.raises(ToolNotFoundError):
            call_tool("nonexistent_tool_xyz")

    def test_call_internal_tool_no_gate(self):
        """Internal tools (action_kind=None) skip the gate.
        (内部工具 (action_kind=None) 跳过门控。)
        """
        result = call_tool("character_list", {})
        assert result["isError"] is False
        assert "content" in result

    def test_call_tool_normalizes_bare_string(self):
        """A handler returning a bare string gets wrapped.
        (返回裸字符串的处理器会被包装。)
        """
        register_tool(
            "_test_bare_string",
            "test",
            {"type": "object", "properties": {}},
            lambda args, ctx: "hello world",
        )
        try:
            result = call_tool("_test_bare_string", {})
            assert result["content"][0]["text"] == "hello world"
            assert result["isError"] is False
        finally:
            from xijian_api.mcp.registry import unregister_tool
            unregister_tool("_test_bare_string")

    def test_call_tool_normalizes_bare_dict(self):
        """A handler returning a bare dict gets wrapped.
        (返回裸字典的处理器会被包装。)
        """
        register_tool(
            "_test_bare_dict",
            "test",
            {"type": "object", "properties": {}},
            lambda args, ctx: {"key": "value"},
        )
        try:
            result = call_tool("_test_bare_dict", {})
            text = result["content"][0]["text"]
            assert "key" in json.loads(text)
        finally:
            from xijian_api.mcp.registry import unregister_tool
            unregister_tool("_test_bare_dict")

    def test_tool_error_is_raised(self):
        """ToolError raised by handler propagates up.
        (处理器引发的 ToolError 向上传播。)
        """
        def _fail(args, ctx):
            raise ToolError("custom error", data={"code": 42})

        register_tool("_test_error", "test", {"type": "object"}, _fail)
        try:
            with pytest.raises(ToolError, match="custom error"):
                call_tool("_test_error", {})
        finally:
            from xijian_api.mcp.registry import unregister_tool
            unregister_tool("_test_error")

    def test_unexpected_exception_wrapped_as_tool_error(self):
        """Unexpected exception is wrapped as ToolError.
        (未预期的异常被包装为 ToolError。)
        """
        def _crash(args, ctx):
            raise RuntimeError("boom")

        register_tool("_test_crash", "test", {"type": "object"}, _crash)
        try:
            with pytest.raises(ToolError, match="failed"):
                call_tool("_test_crash", {})
        finally:
            from xijian_api.mcp.registry import unregister_tool
            unregister_tool("_test_crash")


class TestRegistryGateRouting:
    """A5.2 gate integration — tools with action_kind route through the gate.
    (A5.2 门控集成 — 带有 action_kind 的工具通过门控路由。)
    """

    def test_file_write_denied_by_default(self):
        """No world_id → default=deny → ToolGateError.
        (无 world_id → default=deny → ToolGateError。)
        """
        with pytest.raises(ToolGateError) as exc_info:
            call_tool("file_write", {
                "path": "~/xijian_gate_test.txt",
                "content": "test",
            })
        assert exc_info.value.data["verdict"] == mcp_stub.VERDICT_DENIED

    def test_file_write_allowed_with_whitelist_rule(self):
        """A whitelist rule matching the path allows the call.
        (匹配路径的白名单规则允许调用。)
        """
        rules_stub.create(
            action_kind=rules_stub.KIND_FILE_WRITE,
            pattern="xijian_gate_test",
            mode=rules_stub.MODE_WHITELIST,
        )
        # The gate should now allow the call (path matches whitelist).
        # Use a tempdir whose name contains the whitelist pattern so the
        # regex actually hits.
        # (门控现在应允许调用（路径匹配白名单）。
        # 使用名称包含白名单模式的临时目录，以便正则表达式实际命中。)
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="xijian_gate_test_", dir=str(Path.home()))
        try:
            result = call_tool("file_write", {
                "path": str(Path(tmpdir) / "test.txt"),
                "content": "hello",
            })
            assert result["isError"] is False
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_write_denied_by_blacklist_rule(self):
        """A blacklist rule matching the path denies the call.
        (匹配路径的黑名单规则拒绝调用。)
        """
        rules_stub.create(
            action_kind=rules_stub.KIND_FILE_WRITE,
            pattern="xijian_blacklisted",
            mode=rules_stub.MODE_BLACKLIST,
        )
        with pytest.raises(ToolGateError) as exc_info:
            call_tool("file_write", {
                "path": "~/xijian_blacklisted_file.txt",
                "content": "test",
            })
        assert exc_info.value.data["verdict"] == mcp_stub.VERDICT_DENIED
        assert exc_info.value.data["blocked"] == "blacklist_hit"

    def test_file_write_allowed_with_world_default_allow(self):
        """World policy default=allow lets unmatched calls through.
        (世界策略 default=allow 让不匹配的调用通过。)
        """
        mcp_stub.set_world_policy("world_test_allow", default=mcp_stub.POLICY_DEFAULT_ALLOW)
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="xijian_mcp_allow_", dir=str(Path.home()))
        try:
            result = call_tool("file_write", {
                "path": str(Path(tmpdir) / "test.txt"),
                "content": "hello",
            }, world_id="world_test_allow")
            assert result["isError"] is False
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ===========================================================================
# Representative tool tests
# ===========================================================================
# (代表性工具测试)


class TestCharacterTools:
    """Character-related MCP tools.
    (角色相关的 MCP 工具。)
    """

    def test_character_create_and_list(self):
        """Create and list characters.
        (创建和列出角色。)
        """
        result = call_tool("character_create", {
            "name": "TestHero",
            "display_name": "测试英雄",
            "description": "A test character",
        })
        assert result["isError"] is False
        text = result["content"][0]["text"]
        data = json.loads(text)
        char_id = data.get("id") or data.get("character_id")
        assert char_id

        # List should include the new character.
        # (列表应包含新角色。)
        list_result = call_tool("character_list", {})
        list_text = list_result["content"][0]["text"]
        list_data = json.loads(list_text)
        # list_data might be a list or dict with items.
        # (list_data 可能是列表或带有 items 的字典。)
        if isinstance(list_data, dict):
            items = list_data.get("characters") or list_data.get("items") or []
        else:
            items = list_data
        names = [c.get("name", "") for c in items]
        assert "TestHero" in names

    def test_character_get(self):
        """Get character by id.
        (按 id 获取角色。)
        """
        create = call_tool("character_create", {"name": "GetTestChar"})
        char_id = json.loads(create["content"][0]["text"]).get("id")
        result = call_tool("character_get", {"character_id": char_id})
        assert result["isError"] is False

    def test_character_get_not_found(self):
        """Get non-existent character raises ToolError.
        (获取不存在的角色抛出 ToolError。)
        """
        with pytest.raises(ToolError):
            call_tool("character_get", {"character_id": "char_nonexistent"})


class TestWorldTools:
    """World-related MCP tools.
    (世界相关的 MCP 工具。)
    """

    def test_world_create_and_list(self):
        """Create and list worlds.
        (创建和列出世界。)
        """
        result = call_tool("world_create", {
            "name": "TestWorld",
            "description": "A test world",
        })
        assert result["isError"] is False

        list_result = call_tool("world_list", {})
        assert list_result["isError"] is False

    def test_world_summary(self):
        """Get world summary.
        (获取世界摘要。)
        """
        create = call_tool("world_create", {"name": "SummaryWorld"})
        data = json.loads(create["content"][0]["text"])
        world_id = data.get("id") or data.get("world_id")
        result = call_tool("world_summary", {"world_id": world_id})
        assert result["isError"] is False


class TestMemoryTools:
    """Memory-related MCP tools.
    (记忆相关的 MCP 工具。)
    """

    def test_memory_create_and_list(self):
        """Create and list memories.
        (创建和列出记忆。)
        """
        create = call_tool("memory_create", {
            "character_id": "char_memtest",
            "type": "long_term",
            "content": "User likes ramen",
            "importance": 7,
        })
        assert create["isError"] is False

        list_result = call_tool("memory_list", {"character_id": "char_memtest"})
        assert list_result["isError"] is False

    def test_memory_search(self):
        """Search memories by query.
        (按查询搜索记忆。)
        """
        call_tool("memory_create", {
            "character_id": "char_search",
            "type": "long_term",
            "content": "User prefers Python",
        })
        result = call_tool("memory_search", {
            "character_id": "char_search",
            "query": "Python",
            "top_k": 5,
        })
        assert result["isError"] is False


class TestSessionTools:
    """Session-related MCP tools.
    (会话相关的 MCP 工具。)
    """

    def test_session_create_and_get(self):
        """Create and get session.
        (创建和获取会话。)
        """
        create = call_tool("session_create", {
            "character_id": "char_session",
            "title": "Test Session",
        })
        assert create["isError"] is False
        data = json.loads(create["content"][0]["text"])
        session_id = data.get("id") or data.get("session_id")

        get_result = call_tool("session_get", {"session_id": session_id})
        assert get_result["isError"] is False

    def test_session_append_message(self):
        """Append message to session.
        (向会话追加消息。)
        """
        create = call_tool("session_create", {"character_id": "char_msg"})
        session_id = json.loads(create["content"][0]["text"]).get("id")
        result = call_tool("session_append_message", {
            "session_id": session_id,
            "role": "user",
            "content": "Hello!",
        })
        assert result["isError"] is False


class TestSettingsTools:
    """Settings-related MCP tools.
    (设置相关的 MCP 工具。)
    """

    def test_settings_get_and_update(self):
        """Get and update settings.
        (获取和更新设置。)
        """
        get_result = call_tool("settings_get", {})
        assert get_result["isError"] is False

        update_result = call_tool("settings_update", {
            "patch": {"test_key": "test_value"},
        })
        assert update_result["isError"] is False


class TestProtectionTools:
    """Protection (MCP) tools.
    (保护 (MCP) 工具。)
    """

    def test_mcp_rule_list(self):
        """List MCP rules.
        (列出 MCP 规则。)
        """
        result = call_tool("mcp_rule_list", {})
        assert result["isError"] is False

    def test_mcp_policy_get_default(self):
        """Get default MCP policy for a world.
        (获取世界的默认 MCP 策略。)
        """
        result = call_tool("mcp_policy_get", {"world_id": "world_no_rules"})
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert data["default"] in ("allow", "deny")

    def test_mcp_audit_list(self):
        """List MCP audit log.
        (列出 MCP 审计日志。)
        """
        result = call_tool("mcp_audit_list", {})
        assert result["isError"] is False

    def test_mcp_snapshot_create_and_list(self):
        """Create and list MCP snapshots.
        (创建和列出 MCP 快照。)
        """
        create = call_tool("mcp_snapshot_create", {})
        assert create["isError"] is False

        list_result = call_tool("mcp_snapshot_list", {})
        assert list_result["isError"] is False


# ===========================================================================
# File tools — real filesystem operations
# ===========================================================================
# (文件工具 — 真实文件系统操作)


class TestFileTools:
    """Real file operations scoped to the user's home directory.
    (限定在用户主目录内的真实文件操作。)

    These tests set a whitelist rule (or world default=allow) so the
    A5.2 gate permits the operations.
    (这些测试设置白名单规则（或 world default=allow），以便 A5.2 门控允许操作。)
    """

    @pytest.fixture(autouse=True)
    def _allow_file_ops(self):
        """Allow all file_read/file_write/file_delete for test paths.
        (允许测试路径的所有 file_read/file_write/file_delete 操作。)
        """
        mcp_stub.set_world_policy(
            "world_filetest", default=mcp_stub.POLICY_DEFAULT_ALLOW,
        )
        yield
        # Cleanup is per-test via tmpdir removal.
        # (清理通过临时目录移除在每个测试中进行。)

    def _tmpdir(self):
        """Create a temporary directory under home.
        (在主目录下创建临时目录。)
        """
        return tempfile.mkdtemp(prefix="xijian_mcp_ftest_", dir=str(Path.home()))

    def test_file_write_and_read(self):
        """Write file then read it back.
        (写入文件然后读取回来。)
        """
        tmpdir = self._tmpdir()
        try:
            fpath = str(Path(tmpdir) / "test.txt")
            call_tool("file_write", {
                "path": fpath, "content": "hello world",
            }, world_id="world_filetest")

            result = call_tool("file_read", {"path": fpath}, world_id="world_filetest")
            assert result["isError"] is False
            assert result["content"][0]["text"] == "hello world"
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_write_append(self):
        """Append to an existing file.
        (追加到已有文件。)
        """
        tmpdir = self._tmpdir()
        try:
            fpath = str(Path(tmpdir) / "append.txt")
            call_tool("file_write", {
                "path": fpath, "content": "line1\n",
            }, world_id="world_filetest")
            call_tool("file_write", {
                "path": fpath, "content": "line2\n", "append": True,
            }, world_id="world_filetest")

            result = call_tool("file_read", {"path": fpath}, world_id="world_filetest")
            assert "line1" in result["content"][0]["text"]
            assert "line2" in result["content"][0]["text"]
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_list(self):
        """List directory contents.
        (列出目录内容。)
        """
        tmpdir = self._tmpdir()
        try:
            for name in ("a.txt", "b.txt", "c.log"):
                call_tool("file_write", {
                    "path": str(Path(tmpdir) / name), "content": "x",
                }, world_id="world_filetest")

            result = call_tool("file_list", {"path": tmpdir}, world_id="world_filetest")
            assert result["isError"] is False
            entries = json.loads(result["content"][0]["text"])
            names = [e["name"] for e in entries]
            assert "a.txt" in names
            assert "b.txt" in names
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_list_with_pattern(self):
        """List directory contents filtered by pattern.
        (按模式过滤的目录内容列表。)
        """
        tmpdir = self._tmpdir()
        try:
            for name in ("a.txt", "b.log", "c.txt"):
                call_tool("file_write", {
                    "path": str(Path(tmpdir) / name), "content": "x",
                }, world_id="world_filetest")

            result = call_tool("file_list", {
                "path": tmpdir, "pattern": "*.txt",
            }, world_id="world_filetest")
            entries = json.loads(result["content"][0]["text"])
            names = [e["name"] for e in entries]
            assert "a.txt" in names
            assert "c.txt" in names
            assert "b.log" not in names
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_stat(self):
        """Get file stat info.
        (获取文件状态信息。)
        """
        tmpdir = self._tmpdir()
        try:
            fpath = str(Path(tmpdir) / "stat.txt")
            call_tool("file_write", {
                "path": fpath, "content": "stat me",
            }, world_id="world_filetest")

            result = call_tool("file_stat", {"path": fpath}, world_id="world_filetest")
            assert result["isError"] is False
            info = json.loads(result["content"][0]["text"])
            assert info["name"] == "stat.txt"
            assert info["type"] == "file"
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_delete(self):
        """Delete a file.
        (删除文件。)
        """
        tmpdir = self._tmpdir()
        try:
            fpath = str(Path(tmpdir) / "delete_me.txt")
            call_tool("file_write", {
                "path": fpath, "content": "bye",
            }, world_id="world_filetest")

            result = call_tool("file_delete", {"path": fpath}, world_id="world_filetest")
            assert result["isError"] is False
            assert not Path(fpath).exists()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_file_read_not_found(self):
        """Read non-existent file raises ToolError.
        (读取不存在的文件抛出 ToolError。)
        """
        tmpdir = self._tmpdir()
        try:
            with pytest.raises(ToolError, match="not found"):
                call_tool("file_read", {
                    "path": str(Path(tmpdir) / "nope.txt"),
                }, world_id="world_filetest")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestFilePathValidation:
    """Path scoping — paths outside home and system dirs are rejected.
    (路径范围 — 主目录和系统目录之外的路径被拒绝。)

    The A5.2 gate runs *before* the handler's ``_validate_path``; with
    the default ``default=deny`` policy the gate would deny these calls
    before path validation ever runs.  We set a test world to
    ``default=allow`` so the gate passes and the handler's validation
    logic is exercised.
    (A5.2 门控在处理器 ``_validate_path`` 之前运行；
    使用默认 ``default=deny`` 策略时，门控会在路径验证之前拒绝这些调用。
    我们设置测试世界为 ``default=allow``，使门控通过并执行处理器的验证逻辑。)
    """

    @pytest.fixture(autouse=True)
    def _allow_path_validation_tests(self):
        mcp_stub.set_world_policy(
            "world_pathval", default=mcp_stub.POLICY_DEFAULT_ALLOW,
        )
        yield

    def test_rejects_system_directory(self):
        """System directory paths are rejected.
        (系统目录路径被拒绝。)
        """
        with pytest.raises(ToolError, match="blocked system directory"):
            call_tool("file_read", {"path": "/etc/passwd"}, world_id="world_pathval")

    def test_rejects_path_outside_home(self):
        """Paths outside user home are rejected.
        (用户主目录之外的路径被拒绝。)
        """
        with pytest.raises(ToolError, match="outside the user home"):
            # /tmp is outside the home directory on macOS.
            # (/tmp 在 macOS 上位于主目录之外。)
            call_tool("file_read", {"path": "/tmp/xijian_test_outside.txt"}, world_id="world_pathval")

    def test_rejects_empty_path(self):
        """Empty path is rejected.
        (空路径被拒绝。)
        """
        with pytest.raises(ToolError, match="path is required"):
            call_tool("file_read", {"path": ""}, world_id="world_pathval")

    def test_resolves_dotdot(self):
        """``~/../../etc/passwd`` should resolve to /etc/passwd and be blocked.
        (``~/../../etc/passwd`` 应解析为 /etc/passwd 并被阻止。)
        """
        with pytest.raises(ToolError):
            call_tool("file_read", {"path": "~/../../etc/passwd"}, world_id="world_pathval")


# ===========================================================================
# Desktop tools — forward skeleton (pending queue)
# ===========================================================================
# (桌面工具 — 转发骨架（待处理队列）)


class TestDesktopTools:
    """Desktop tools enqueue actions to the pending queue.
    (桌面工具将动作入队到待处理队列。)
    """

    @pytest.fixture(autouse=True)
    def _allow_desktop_ops(self):
        mcp_stub.set_world_policy(
            "world_desktest", default=mcp_stub.POLICY_DEFAULT_ALLOW,
        )
        yield

    def test_app_launch_enqueues(self):
        """Launching an app enqueues a pending action.
        (启动应用将动作入队到待处理队列。)
        """
        result = call_tool("app_launch", {
            "app_name": "Safari",
        }, world_id="world_desktest")
        assert result["isError"] is False
        text = result["content"][0]["text"]
        # The response should mention "forwarded" or "pending".
        # (响应应提及 "forwarded" 或 "pending"。)
        assert "forward" in text.lower() or "pending" in text.lower()

        # Verify the action was enqueued.
        # (验证动作已入队。)
        pending = getattr(state, "mcp_pending_actions", {})
        assert len(pending) > 0

    def test_browser_open_enqueues(self):
        """Opening a URL in browser enqueues a pending action.
        (在浏览器中打开 URL 将动作入队。)
        """
        result = call_tool("browser_open", {
            "url": "https://example.com",
        }, world_id="world_desktest")
        assert result["isError"] is False

    def test_desktop_pending_list(self):
        """List pending desktop actions.
        (列出待处理的桌面动作。)
        """
        call_tool("app_launch", {"app_name": "Calculator"}, world_id="world_desktest")
        result = call_tool("desktop_pending_list", {})
        assert result["isError"] is False

    def test_desktop_pending_get(self):
        """Get a specific pending desktop action.
        (获取特定的待处理桌面动作。)
        """
        launch = call_tool("app_launch", {"app_name": "Notes"}, world_id="world_desktest")
        # Extract the action id from the forwarded response.
        # (从转发响应中提取动作 id。)
        text = launch["content"][0]["text"]
        # The pending list should have the action.
        # (待处理列表应有该动作。)
        list_result = call_tool("desktop_pending_list", {})
        entries = json.loads(list_result["content"][0]["text"])
        if isinstance(entries, list) and entries:
            action_id = entries[0].get("id")
            if action_id:
                get_result = call_tool("desktop_pending_get", {"action_id": action_id})
                assert get_result["isError"] is False


# ===========================================================================
# Chat tools pipeline (A2)
# ===========================================================================
# (聊天工具管道 (A2))


_MODEL = "mock-qwen2.5-7b"


def _post_chat(client, auth_headers, *, messages, xijian=None, tools=None, tool_choice=None):
    """Helper to POST a chat completion request.
    (发送聊天补全请求的辅助函数。)
    """
    payload = {"model": _MODEL, "messages": messages}
    if xijian is not None:
        payload["xijian"] = xijian
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return client.post("/v1/chat/completions", headers=auth_headers, json=payload)


class TestChatToolsPipeline:
    """A2 — the MCP tools pipeline in the chat completion path.
    (A2 — 聊天补全路径中的 MCP 工具管道。)
    """

    def test_xijian_tools_enabled_triggers_pipeline(self, client, auth_headers):
        """xijian.tools.enabled=true → pipeline runs, xijian.tools block present.
        (xijian.tools.enabled=true → 管道运行，xijian.tools 块存在。)
        """
        resp = _post_chat(
            client, auth_headers,
            messages=[{"role": "user", "content": "list my characters"}],
            xijian={"tools": {"enabled": True}},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "xijian" in body
        assert "tools" in body["xijian"]
        assert body["xijian"]["tools"]["enabled"] is True

    def test_oai_tools_field_triggers_pipeline(self, client, auth_headers):
        """OAI tools array → pipeline runs.
        (OAI tools 数组 → 管道运行。)
        """
        resp = _post_chat(
            client, auth_headers,
            messages=[{"role": "user", "content": "hello"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "character_list",
                    "description": "List all characters",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "xijian" in body
        assert "tools" in body["xijian"]

    def test_no_tools_no_pipeline(self, client, auth_headers):
        """Without tools, the regular chat path runs (no xijian.tools block).
        (没有工具时，常规聊天路径运行（无 xijian.tools 块）。)
        """
        resp = _post_chat(
            client, auth_headers,
            messages=[{"role": "user", "content": "hello"}],
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "tools" not in body.get("xijian", {})

    def test_tool_call_executed_through_registry(self, client, auth_headers):
        """The mock emits a tool call → pipeline executes it via the registry.
        (模拟发出工具调用 → 管道通过注册表执行。)
        """
        resp = _post_chat(
            client, auth_headers,
            messages=[{"role": "user", "content": "show characters"}],
            xijian={"tools": {"enabled": True}},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        tools_block = body["xijian"]["tools"]
        # The mock calls the first available tool, so at least one
        # tool call should be logged.
        # (模拟调用第一个可用工具，因此至少应记录一个工具调用。)
        assert len(tools_block["tool_calls"]) >= 1
        tc = tools_block["tool_calls"][0]
        assert "name" in tc
        assert "result" in tc

    def test_tool_choice_required(self, client, auth_headers):
        """tool_choice=required adds a note to the system prompt.
        (tool_choice=required 向系统提示添加注释。)
        """
        resp = _post_chat(
            client, auth_headers,
            messages=[{"role": "user", "content": "hi"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "character_list",
                    "description": "List characters",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            tool_choice="required",
        )
        assert resp.status_code == 200


# ===========================================================================
# Flask route — POST /v1/mcp
# ===========================================================================
# (Flask 路由 — POST /v1/mcp)


class TestMcpRoute:
    """``POST /v1/mcp`` Flask endpoint.
    (``POST /v1/mcp`` Flask 端点。)
    """

    def test_initialize(self, client, auth_headers):
        """Initialize via Flask route.
        (通过 Flask 路由初始化。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("initialize"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["result"]["serverInfo"]["name"] == SERVER_NAME

    def test_tools_list(self, client, auth_headers):
        """List tools via Flask route.
        (通过 Flask 路由列出工具。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("tools/list"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["result"]["tools"]) >= 80

    def test_tools_call(self, client, auth_headers):
        """Call tool via Flask route.
        (通过 Flask 路由调用工具。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("tools/call", {
            "name": "character_list",
            "arguments": {},
        }))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["result"]["isError"] is False

    def test_notification_returns_202(self, client, auth_headers):
        """Notification via Flask route returns 202.
        (通过 Flask 路由的通知返回 202。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_notify("ping"))
        assert resp.status_code == 202

    def test_batch(self, client, auth_headers):
        """Batch request via Flask route.
        (通过 Flask 路由的批处理请求。)
        """
        batch = [_req("ping", req_id=1), _req("ping", req_id=2)]
        resp = client.post("/v1/mcp", headers=auth_headers, json=batch)
        assert resp.status_code == 200
        body = resp.get_json()
        assert isinstance(body, list)
        assert len(body) == 2

    def test_invalid_json_returns_400(self, client, auth_headers):
        """Invalid JSON input returns 400.
        (无效的 JSON 输入返回 400。)
        """
        resp = client.post(
            "/v1/mcp", headers=auth_headers, data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_method_not_found_returns_404(self, client, auth_headers):
        """Unknown method returns 404.
        (未知方法返回 404。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("unknown/method"))
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        """MCP endpoint requires authentication.
        (MCP 端点需要认证。)
        """
        resp = client.post("/v1/mcp", json=_req("initialize"))
        assert resp.status_code in (401, 403)

    def test_resources_list(self, client, auth_headers):
        """List resources via Flask route.
        (通过 Flask 路由列出资源。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("resources/list"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["result"]["resources"]) >= 5

    def test_prompts_list(self, client, auth_headers):
        """List prompts via Flask route.
        (通过 Flask 路由列出提示模板。)
        """
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("prompts/list"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["result"]["prompts"]) >= 3
