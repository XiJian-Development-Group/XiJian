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


#: Name of the probe tool registered for the gate tests.  Sorts first
#: so the mock backend picks it as the single available tool.
PROBE_TOOL = "aaa_gate_probe"
#: The chat model id registered in ``config.toml`` (mock backend).
CHAT_MODEL = "qwen2.5-7b-mlx-4bit"


def _probe_handler(args: dict, ctx: dict) -> dict:
    """Fixed tool handler — returns a distinguishable result so tests
    can assert the tool actually executed in the ``allowed`` branch."""
    return {
        "content": [{"type": "text", "text": "probe-ok"}],
        "isError": False,
    }


@pytest.fixture()
def gate_probe():
    """Register the gate probe tool for the duration of one test."""
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
    """POST a chat completion that forces the MCP tools pipeline to
    emit exactly one tool call (the probe tool)."""
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
    """Return the first logged tool call from the response body."""
    tools_block = body["xijian"]["tools"]
    assert tools_block["enabled"] is True
    assert len(tools_block["tool_calls"]) >= 1
    return tools_block["tool_calls"][0]


class TestChatMCPGateAllowed:
    """Verdict ``allowed`` → the tool executes normally."""

    def test_allowed_executes_tool(self, client, auth_headers, gate_probe):
        # World default=allow → no-match is allowed (no rules seeded).
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
    """Verdict ``denied`` (default=deny, no match) → refused + audited."""

    def test_denied_refuses_execution(self, client, auth_headers, gate_probe):
        # Fresh world with default=deny and no rules → denied.
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_deny"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["is_error"] is True
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied"
        assert mcp_stub.count_audit(verdict="denied", world_id="w_deny") >= 1

    def test_denied_without_world(self, client, auth_headers, gate_probe):
        # No world → default policy (default=deny) applies.
        resp = _post_chat(client, auth_headers, xijian={})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied"
        assert tc["gate"]["blocked"] == "default_deny_no_match"
        assert mcp_stub.count_audit(verdict="denied") >= 1


class TestChatMCPGateFrozen:
    """Verdict ``denied_frozen`` — pending safety-stop freeze."""

    def test_frozen_refuses_execution(self, client, auth_headers, gate_probe):
        # A pending safety-stop on the world short-circuits check().
        freeze = mcp_stub.safety_stop(world_id="w_frozen", reason="test")
        assert freeze["status"] == "frozen"
        resp = _post_chat(client, auth_headers, xijian={"world_id": "w_frozen"})
        assert resp.status_code == 200
        tc = _first_tool_call(resp.get_json())
        assert tc["error_type"] == "gate_denied"
        assert tc["gate"]["verdict"] == "denied_frozen"
        assert mcp_stub.count_audit(verdict="denied_frozen", world_id="w_frozen") >= 1


class TestChatMCPGateLockout:
    """Verdict ``denied_lockout`` — world in lockout."""

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
    """Verdict ``denied_crashed`` — rulebook matcher blows up."""

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
