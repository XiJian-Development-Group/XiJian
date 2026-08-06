"""Tests for the chat → MCP tool-call safety gate (T0-1).

Every MCP tool call dispatched from :func:`xijian_api.stubs.chat.complete`
must pass :func:`xijian_api.stubs.mcp.check` **before** execution; a
verdict other than ``"allowed"`` refuses the call and leaves an audit
entry behind (written by ``check()`` itself).

Covers the five gate branches through the full HTTP chat pipeline:

* **allowed** — world policy ``default=allow`` → tool executes
* **denied**  — ``default=deny`` with no matching rule → refused
* **frozen**  — pending safety-stop freeze on the world → refused
* **lockout** — world in lockout → refused
* **crash**   — rulebook matcher crashes → refused (``denied_crashed``)

Each branch asserts the tool-call log carries ``error_type="gate_denied"``
with the gate verdict, and that ``mcp.count_audit`` sees the matching
verdict (AC-1: 黑名单动作 100% 拦截可审计).
"""

from __future__ import annotations

import pytest

from xijian_api.mcp.registry import register_tool, unregister_tool
from xijian_api.stubs import mcp as mcp_stub
from xijian_api.stubs import mcp_rules as mcp_rules_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs.mcp_rules import KIND_SHELL


#: 为闸门测试注册的探针工具名称。排序在前，
#: 以便 mock 后端将其作为唯一可用工具选中。
PROBE_TOOL = "aaa_gate_probe"
#: 在 ``config.toml`` 中注册的聊天模型 id（mock 后端）。
CHAT_MODEL = "qwen2.5-7b-mlx-4bit"


def _probe_handler(args: dict, ctx: dict) -> dict:
    """固定工具处理器 —— 返回可区分的结果，以便测试
    能在 ``allowed`` 分支中断言工具确实执行了。"""
    return {
        "content": [{"type": "text", "text": "probe-ok"}],
        "isError": False,
    }


@pytest.fixture()
def gate_probe():
    """在单个测试期间注册闸门探针工具。"""
    register_tool(
        PROBE_TOOL,
        description="gate test probe",
        input_schema={"type": "object", "properties": {}},
        handler=_probe_handler,
        action_kind=KIND_SHELL,
    )
    try:
        yield PROBE_TOOL
    finally:
        unregister_tool(PROBE_TOOL)


def _post_chat(client, auth_headers, *, xijian=None):
    """POST 一次聊天补全请求，强制 MCP 工具管道
    恰好发出一次工具调用（探针工具）。"""
    return client.post(
        "/v1/chat/completions",
        headers=auth_headers,
        json={
            "model": CHAT_MODEL,
            "messages": [{"role": "user", "content": "please run the probe"}],
            "xijian": xijian or {},
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": PROBE_TOOL,
                        "description": "probe",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        },
    )


def _first_tool_call(body: dict) -> dict:
    """从响应体中返回第一条被记录的工具调用。"""
    tools_block = body["xijian"]["tools"]
    assert tools_block["enabled"] is True
    assert len(tools_block["tool_calls"]) >= 1
    return tools_block["tool_calls"][0]


class TestChatMCPGateAllowed:
    """判定 ``allowed`` → 工具正常执行。"""

    def test_allowed_executes_tool(self, client, auth_headers, gate_probe):
        # 世界默认 allow → 无匹配规则时放行（未播种任何规则）。
        mcp_stub.set_world_policy("w_gate", default="allow")
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_gate"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["is_error"] is False
        assert tc["error_type"] is None
        assert tc["result"] == "probe-ok"
        # check() still wrote an ``allowed`` audit entry — and exactly
        # one: the chat pipeline ran the gate itself (T0-1) and told the
        # registry ``skip_gate=True``, so the inner dispatcher must NOT
        # re-check and double-audit the same allowed call (R1).
        # check() 仍写入一条 ``allowed`` 审计——且恰好一条：聊天管线
        # 自行执行了门禁（T0-1）并向注册表传递 ``skip_gate=True``，
        # 因此内层分发器不得重复检查、对同一次 allowed 调用写两条审计（R1）。
        assert mcp_stub.count_audit(verdict="allowed", world_id="w_gate") == 1


class TestChatMCPGateDenied:
    """判定 ``denied``（default=deny，无匹配）→ 拒绝并审计。"""

    def test_denied_refuses_execution(self, client, auth_headers, gate_probe):
        # 全新世界，默认 deny 且无规则 → 拒绝。
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_deny"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["is_error"] is True
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied"
        assert mcp_stub.count_audit(verdict="denied", world_id="w_deny") >= 1

    def test_denied_without_world(self, client, auth_headers, gate_probe):
        # 无世界 → 应用默认策略（default=deny）。
        resp = _post_chat(client, auth_headers, xijian={})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied"
        assert tc["gate"]["blocked"] == "default_deny_no_match"
        assert mcp_stub.count_audit(verdict="denied") >= 1


class TestChatMCPGateFrozen:
    """判定 ``denied_frozen`` — 待处理的安全停机冻结。"""

    def test_frozen_refuses_execution(self, client, auth_headers, gate_probe):
        # 世界存在待处理的安全停机 → 使 check() 短路。
        freeze = mcp_stub.safety_stop(world_id="w_frozen", reason="test")
        assert freeze["status"] == "frozen"
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_frozen"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied_frozen"
        assert mcp_stub.count_audit(verdict="denied_frozen", world_id="w_frozen") >= 1


class TestChatMCPGateLockout:
    """判定 ``denied_lockout`` — 世界处于锁定状态。"""

    def test_lockout_refuses_execution(self, client, auth_headers, gate_probe):
        from xijian_api.utils.time import now_ts

        mcp_stub.set_world_policy(
            "w_lock", lockout_until=float(now_ts()) + 600.0,
        )
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_lock"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied_lockout"
        assert mcp_stub.count_audit(verdict="denied_lockout", world_id="w_lock") >= 1


class TestChatMCPGateCrash:
    """判定 ``denied_crashed`` — 规则簿匹配器崩溃。"""

    def test_crash_refuses_execution(self, client, auth_headers, gate_probe, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("rulebook exploded")

        monkeypatch.setattr(
            "xijian_api.stubs.mcp_rules.match_action_rules", _boom,
        )
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_crash"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied_crashed"
        assert mcp_stub.count_audit(verdict="denied_crashed", world_id="w_crash") >= 1

    def test_crash_without_world_still_audited(self, client, auth_headers, gate_probe, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("rulebook exploded")

        monkeypatch.setattr(
            "xijian_api.stubs.mcp_rules.match_action_rules", _boom,
        )
        resp = _post_chat(client, auth_headers, xijian={})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["gate"]["verdict"] == "denied_crashed"
        assert mcp_stub.count_audit(verdict="denied_crashed") >= 1
