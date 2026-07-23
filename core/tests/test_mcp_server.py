"""Comprehensive tests for the MCP server (protocol + registry + tools + route).

Covers three layers:

* **Protocol** (:mod:`xijian_api.mcp.protocol`) — JSON-RPC 2.0 method
  dispatch (initialize, ping, tools/list, tools/call, resources/list,
  resources/read, prompts/list, prompts/get), error codes, batch
  handling, notifications.
* **Registry** (:mod:`xijian_api.mcp.registry`) — tool registration,
  dispatch, A5.2 gate routing (whitelist allow, default-deny, blacklist
  deny, ToolError / ToolGateError / ToolNotFoundError).
* **Tools** — representative tools across every module (characters,
  worlds, memory, npcs, economy, events, sessions, settings, files,
  desktop, protection).
* **Chat tools pipeline** (A2) — ``xijian.tools.enabled`` and OAI
  ``tools`` field both trigger the pipeline; tool calls are executed
  through the registry and results fed back.
* **Flask route** — ``POST /v1/mcp`` single / batch / notification /
  auth.
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


def _req(method: str, params: dict | None = None, *, req_id: int | str = 1):
    """Build a JSON-RPC 2.0 request dict."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }


def _notify(method: str, params: dict | None = None):
    """Build a JSON-RPC 2.0 notification (no id)."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }


# ===========================================================================
# Protocol layer
# ===========================================================================


class TestProtocolInitialize:
    """``initialize`` handshake."""

    def test_returns_server_info_and_capabilities(self):
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
        resp = handle_request(_req("initialize"))
        assert resp is not None
        assert "result" in resp


class TestProtocolPing:
    def test_ping_returns_empty(self):
        resp = handle_request(_req("ping"))
        assert resp is not None
        assert resp["result"] == {}


class TestProtocolToolsList:
    def test_returns_all_registered_tools(self):
        resp = handle_request(_req("tools/list"))
        assert resp is not None
        tools = resp["result"]["tools"]
        assert len(tools) >= 80  # 87 at time of writing
        # Every tool has the required fields.
        for t in tools:
            assert "name" in t
            assert "description" in t
            assert "inputSchema" in t

    def test_tools_are_sorted_by_name(self):
        resp = handle_request(_req("tools/list"))
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == sorted(names)


class TestProtocolToolsCall:
    def test_calls_internal_tool(self):
        """character_list is an internal domain tool (no gate)."""
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
        resp = handle_request(_req("tools/call", {
            "name": "nonexistent_tool",
            "arguments": {},
        }))
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    def test_missing_name_param(self):
        resp = handle_request(_req("tools/call", {"arguments": {}}))
        assert resp is not None
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    def test_arguments_defaults_to_empty_dict(self):
        resp = handle_request(_req("tools/call", {
            "name": "character_list",
        }))
        assert resp is not None
        assert "result" in resp

    def test_gate_denial_returns_iserror_result(self):
        """A gate denial is an isError result, not a JSON-RPC error."""
        # file_write has action_kind=file_write → gate runs.
        # With no world_id and default=deny, the gate denies.
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
        resp = handle_request(_req("resources/list"))
        assert resp is not None
        resources = resp["result"]["resources"]
        assert len(resources) >= 5
        for r in resources:
            assert "uri" in r
            assert "name" in r

    def test_resources_read_server_info(self):
        resp = handle_request(_req("resources/read", {
            "uri": "xijian://server/info",
        }))
        assert resp is not None
        result = resp["result"]
        assert "contents" in result

    def test_resources_read_invalid_uri(self):
        resp = handle_request(_req("resources/read", {
            "uri": "xijian://nonexistent/resource",
        }))
        assert resp is not None
        assert "error" in resp

    def test_resources_read_missing_uri(self):
        resp = handle_request(_req("resources/read", {}))
        assert resp is not None
        assert "error" in resp


class TestProtocolPrompts:
    def test_prompts_list(self):
        resp = handle_request(_req("prompts/list"))
        assert resp is not None
        prompts = resp["result"]["prompts"]
        assert len(prompts) >= 3
        for p in prompts:
            assert "name" in p
            assert "description" in p

    def test_prompts_get(self):
        resp = handle_request(_req("prompts/get", {
            "name": "character_setup",
            "arguments": {"character_id": "char_test"},
        }))
        assert resp is not None
        result = resp["result"]
        assert "messages" in result

    def test_prompts_get_unknown(self):
        resp = handle_request(_req("prompts/get", {
            "name": "nonexistent_prompt",
        }))
        assert resp is not None
        assert "error" in resp


class TestProtocolErrors:
    def test_invalid_jsonrpc_version(self):
        resp = handle_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_method_not_found(self):
        resp = handle_request(_req("nonexistent/method"))
        assert resp is not None
        assert resp["error"]["code"] == -32601

    def test_missing_method(self):
        resp = handle_request({"jsonrpc": "2.0", "id": 1})
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_non_dict_request(self):
        resp = handle_request("not a dict")
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_params_not_object(self):
        resp = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "ping", "params": "string",
        })
        assert resp is not None
        assert resp["error"]["code"] == -32602


class TestProtocolNotifications:
    def test_notification_returns_none(self):
        """Notifications (no id) return None — no response body."""
        resp = handle_request(_notify("ping"))
        assert resp is None

    def test_notification_unknown_method_returns_none(self):
        resp = handle_request(_notify("nonexistent/method"))
        assert resp is None


class TestProtocolBatch:
    def test_batch_multiple_requests(self):
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
        """Notifications in a batch produce no response entry."""
        batch = [
            _notify("ping"),
            _req("ping", req_id=1),
        ]
        resp = handle_batch(batch)
        assert isinstance(resp, list)
        assert len(resp) == 1
        assert resp[0]["id"] == 1

    def test_empty_batch_returns_error(self):
        resp = handle_batch([])
        assert resp is not None
        assert resp["error"]["code"] == -32600

    def test_single_dict_delegates_to_handle_request(self):
        resp = handle_batch(_req("ping"))
        assert resp is not None
        assert "result" in resp


# ===========================================================================
# Registry
# ===========================================================================


class TestRegistry:
    def test_list_tools_returns_specs_without_handler(self):
        tools = list_tools()
        for t in tools:
            assert "handler" not in t
            assert "action_kind" not in t

    def test_list_tool_names_sorted(self):
        names = list_tool_names()
        assert names == sorted(names)

    def test_call_unknown_tool_raises(self):
        with pytest.raises(ToolNotFoundError):
            call_tool("nonexistent_tool_xyz")

    def test_call_internal_tool_no_gate(self):
        """Internal tools (action_kind=None) skip the gate."""
        result = call_tool("character_list", {})
        assert result["isError"] is False
        assert "content" in result

    def test_call_tool_normalizes_bare_string(self):
        """A handler returning a bare string gets wrapped."""
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
    """A5.2 gate integration — tools with action_kind route through the gate."""

    def test_file_write_denied_by_default(self):
        """No world_id → default=deny → ToolGateError."""
        with pytest.raises(ToolGateError) as exc_info:
            call_tool("file_write", {
                "path": "~/xijian_gate_test.txt",
                "content": "test",
            })
        assert exc_info.value.data["verdict"] == mcp_stub.VERDICT_DENIED

    def test_file_write_allowed_with_whitelist_rule(self):
        """A whitelist rule matching the path allows the call."""
        rules_stub.create(
            action_kind=rules_stub.KIND_FILE_WRITE,
            pattern="xijian_gate_test",
            mode=rules_stub.MODE_WHITELIST,
        )
        # The gate should now allow the call (path matches whitelist).
        # Use a tempdir whose name contains the whitelist pattern so the
        # regex actually hits.
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
        """A blacklist rule matching the path denies the call."""
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
        """World policy default=allow lets unmatched calls through."""
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


class TestCharacterTools:
    def test_character_create_and_list(self):
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
        list_result = call_tool("character_list", {})
        list_text = list_result["content"][0]["text"]
        list_data = json.loads(list_text)
        # list_data might be a list or dict with items.
        if isinstance(list_data, dict):
            items = list_data.get("characters") or list_data.get("items") or []
        else:
            items = list_data
        names = [c.get("name", "") for c in items]
        assert "TestHero" in names

    def test_character_get(self):
        create = call_tool("character_create", {"name": "GetTestChar"})
        char_id = json.loads(create["content"][0]["text"]).get("id")
        result = call_tool("character_get", {"character_id": char_id})
        assert result["isError"] is False

    def test_character_get_not_found(self):
        with pytest.raises(ToolError):
            call_tool("character_get", {"character_id": "char_nonexistent"})


class TestWorldTools:
    def test_world_create_and_list(self):
        result = call_tool("world_create", {
            "name": "TestWorld",
            "description": "A test world",
        })
        assert result["isError"] is False

        list_result = call_tool("world_list", {})
        assert list_result["isError"] is False

    def test_world_summary(self):
        create = call_tool("world_create", {"name": "SummaryWorld"})
        data = json.loads(create["content"][0]["text"])
        world_id = data.get("id") or data.get("world_id")
        result = call_tool("world_summary", {"world_id": world_id})
        assert result["isError"] is False


class TestMemoryTools:
    def test_memory_create_and_list(self):
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
    def test_session_create_and_get(self):
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
        create = call_tool("session_create", {"character_id": "char_msg"})
        session_id = json.loads(create["content"][0]["text"]).get("id")
        result = call_tool("session_append_message", {
            "session_id": session_id,
            "role": "user",
            "content": "Hello!",
        })
        assert result["isError"] is False


class TestSettingsTools:
    def test_settings_get_and_update(self):
        get_result = call_tool("settings_get", {})
        assert get_result["isError"] is False

        update_result = call_tool("settings_update", {
            "patch": {"test_key": "test_value"},
        })
        assert update_result["isError"] is False


class TestProtectionTools:
    def test_mcp_rule_list(self):
        result = call_tool("mcp_rule_list", {})
        assert result["isError"] is False

    def test_mcp_policy_get_default(self):
        result = call_tool("mcp_policy_get", {"world_id": "world_no_rules"})
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert data["default"] in ("allow", "deny")

    def test_mcp_audit_list(self):
        result = call_tool("mcp_audit_list", {})
        assert result["isError"] is False

    def test_mcp_snapshot_create_and_list(self):
        create = call_tool("mcp_snapshot_create", {})
        assert create["isError"] is False

        list_result = call_tool("mcp_snapshot_list", {})
        assert list_result["isError"] is False


# ===========================================================================
# File tools — real filesystem operations
# ===========================================================================


class TestFileTools:
    """Real file operations scoped to the user's home directory.

    These tests set a whitelist rule (or world default=allow) so the
    A5.2 gate permits the operations.
    """

    @pytest.fixture(autouse=True)
    def _allow_file_ops(self):
        """Allow all file_read/file_write/file_delete for test paths."""
        mcp_stub.set_world_policy(
            "world_filetest", default=mcp_stub.POLICY_DEFAULT_ALLOW,
        )
        yield
        # Cleanup is per-test via tmpdir removal.

    def _tmpdir(self):
        return tempfile.mkdtemp(prefix="xijian_mcp_ftest_", dir=str(Path.home()))

    def test_file_write_and_read(self):
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

    The A5.2 gate runs *before* the handler's ``_validate_path``; with
    the default ``default=deny`` policy the gate would deny these calls
    before path validation ever runs.  We set a test world to
    ``default=allow`` so the gate passes and the handler's validation
    logic is exercised.
    """

    @pytest.fixture(autouse=True)
    def _allow_path_validation_tests(self):
        mcp_stub.set_world_policy(
            "world_pathval", default=mcp_stub.POLICY_DEFAULT_ALLOW,
        )
        yield

    def test_rejects_system_directory(self):
        with pytest.raises(ToolError, match="blocked system directory"):
            call_tool("file_read", {"path": "/etc/passwd"}, world_id="world_pathval")

    def test_rejects_path_outside_home(self):
        with pytest.raises(ToolError, match="outside the user home"):
            # /tmp is outside the home directory on macOS.
            call_tool("file_read", {"path": "/tmp/xijian_test_outside.txt"}, world_id="world_pathval")

    def test_rejects_empty_path(self):
        with pytest.raises(ToolError, match="path is required"):
            call_tool("file_read", {"path": ""}, world_id="world_pathval")

    def test_resolves_dotdot(self):
        """``~/../../etc/passwd`` should resolve to /etc/passwd and be blocked."""
        with pytest.raises(ToolError):
            call_tool("file_read", {"path": "~/../../etc/passwd"}, world_id="world_pathval")


# ===========================================================================
# Desktop tools — forward skeleton (pending queue)
# ===========================================================================


class TestDesktopTools:
    """Desktop tools enqueue actions to the pending queue."""

    @pytest.fixture(autouse=True)
    def _allow_desktop_ops(self):
        mcp_stub.set_world_policy(
            "world_desktest", default=mcp_stub.POLICY_DEFAULT_ALLOW,
        )
        yield

    def test_app_launch_enqueues(self):
        result = call_tool("app_launch", {
            "app_name": "Safari",
        }, world_id="world_desktest")
        assert result["isError"] is False
        text = result["content"][0]["text"]
        # The response should mention "forwarded" or "pending".
        assert "forward" in text.lower() or "pending" in text.lower()

        # Verify the action was enqueued.
        pending = getattr(state, "mcp_pending_actions", {})
        assert len(pending) > 0

    def test_browser_open_enqueues(self):
        result = call_tool("browser_open", {
            "url": "https://example.com",
        }, world_id="world_desktest")
        assert result["isError"] is False

    def test_desktop_pending_list(self):
        call_tool("app_launch", {"app_name": "Calculator"}, world_id="world_desktest")
        result = call_tool("desktop_pending_list", {})
        assert result["isError"] is False

    def test_desktop_pending_get(self):
        launch = call_tool("app_launch", {"app_name": "Notes"}, world_id="world_desktest")
        # Extract the action id from the forwarded response.
        text = launch["content"][0]["text"]
        # The pending list should have the action.
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


_MODEL = "mock-qwen2.5-7b"


def _post_chat(client, auth_headers, *, messages, xijian=None, tools=None, tool_choice=None):
    payload = {"model": _MODEL, "messages": messages}
    if xijian is not None:
        payload["xijian"] = xijian
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    return client.post("/v1/chat/completions", headers=auth_headers, json=payload)


class TestChatToolsPipeline:
    """A2 — the MCP tools pipeline in the chat completion path."""

    def test_xijian_tools_enabled_triggers_pipeline(self, client, auth_headers):
        """xijian.tools.enabled=true → pipeline runs, xijian.tools block present."""
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
        """OAI tools array → pipeline runs."""
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
        """Without tools, the regular chat path runs (no xijian.tools block)."""
        resp = _post_chat(
            client, auth_headers,
            messages=[{"role": "user", "content": "hello"}],
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "tools" not in body.get("xijian", {})

    def test_tool_call_executed_through_registry(self, client, auth_headers):
        """The mock emits a tool call → pipeline executes it via the registry."""
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
        assert len(tools_block["tool_calls"]) >= 1
        tc = tools_block["tool_calls"][0]
        assert "name" in tc
        assert "result" in tc

    def test_tool_choice_required(self, client, auth_headers):
        """tool_choice=required adds a note to the system prompt."""
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


class TestMcpRoute:
    """``POST /v1/mcp`` Flask endpoint."""

    def test_initialize(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("initialize"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["result"]["serverInfo"]["name"] == SERVER_NAME

    def test_tools_list(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("tools/list"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["result"]["tools"]) >= 80

    def test_tools_call(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("tools/call", {
            "name": "character_list",
            "arguments": {},
        }))
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["result"]["isError"] is False

    def test_notification_returns_202(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_notify("ping"))
        assert resp.status_code == 202

    def test_batch(self, client, auth_headers):
        batch = [_req("ping", req_id=1), _req("ping", req_id=2)]
        resp = client.post("/v1/mcp", headers=auth_headers, json=batch)
        assert resp.status_code == 200
        body = resp.get_json()
        assert isinstance(body, list)
        assert len(body) == 2

    def test_invalid_json_returns_400(self, client, auth_headers):
        resp = client.post(
            "/v1/mcp", headers=auth_headers, data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_method_not_found_returns_404(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("unknown/method"))
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.post("/v1/mcp", json=_req("initialize"))
        assert resp.status_code in (401, 403)

    def test_resources_list(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("resources/list"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["result"]["resources"]) >= 5

    def test_prompts_list(self, client, auth_headers):
        resp = client.post("/v1/mcp", headers=auth_headers, json=_req("prompts/list"))
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body["result"]["prompts"]) >= 3
