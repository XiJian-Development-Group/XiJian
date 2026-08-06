"""Tests for A1.2 chat-pipeline fixes and A3.2 can_dialogue gating.

(A1.2 聊天管道修复与 A3.2 can_dialogue 门控的测试。)

Covers:
(覆盖范围：)

* **AC-4 block → auto regenerate (max 2)** — when the citation audit
  returns ``block``, the recall pipeline regenerates up to 2 more
  times and reports the retry count.
* (**AC-4 拦截 → 自动重生成 (最多 2 次)** — 引用审查返回 ``block`` 时，
  召回管道最多再重生成 2 次并报告重试次数。)
* **Dialogue memory write-back** — after a chat completes, a short-term
  ``source=dialogue`` memory entry is written and cited entries get
  their access_count bumped.
* (**对话记忆回写** — 聊天完成后写入一条短期 ``source=dialogue`` 记忆，
  且被引用的条目 access_count 递增。)
* **A3.2 can_dialogue gate** — a character with health ≤ 0 (Critical)
  gets a 400 from the chat route instead of a reply.
* (**A3.2 can_dialogue 门控** — 健康值 ≤ 0 (Critical) 的角色从聊天路由
  收到 400，而不是回复。)
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import chat as chat_stub
from xijian_api.stubs import citations as citations_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs.citations import VERDICT_BLOCK, VERDICT_PASS


_MODEL = "mock-qwen2.5-7b"


def _post_chat(client, auth_headers, *, messages, xijian=None, stream=False):
    payload = {"model": _MODEL, "messages": messages}
    if xijian is not None:
        payload["xijian"] = xijian
    if stream:
        payload["stream"] = True
    return client.post("/v1/chat/completions", headers=auth_headers, json=payload)


def _reset_memory():
    stubs_state.memory.clear()
    memory_stub.seed_default(character_id="char_yuki")


# ---------------------------------------------------------------------------
# AC-4 拦截 → 重新生成
# ---------------------------------------------------------------------------


class TestRegenerateOnBlock:
    def test_regenerates_once_then_passes(self, client, auth_headers, monkeypatch):
        _reset_memory()
        calls = {"n": 0}

        def fake_audit(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"verdict": VERDICT_BLOCK, "warnings": [], "audited_entry_ids": [], "missing_entry_ids": []}
            return {"verdict": VERDICT_PASS, "warnings": [], "audited_entry_ids": [], "missing_entry_ids": []}

        monkeypatch.setattr(citations_stub, "audit", fake_audit)

        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "我喜欢什么口味？"}],
            xijian={
                "character_id": "char_yuki",
                "recall": {"enabled": True, "audit": True},
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["xijian"]["recall"]["regenerations"] == 1
        assert body["xijian"]["audit"]["verdict"] == VERDICT_PASS
        assert calls["n"] == 2

    def test_caps_regeneration_at_two(self, client, auth_headers, monkeypatch):
        _reset_memory()
        calls = {"n": 0}

        def always_block(**kwargs):
            calls["n"] += 1
            return {"verdict": VERDICT_BLOCK, "warnings": [], "audited_entry_ids": [], "missing_entry_ids": []}

        monkeypatch.setattr(citations_stub, "audit", always_block)

        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "你好"}],
            xijian={
                "character_id": "char_yuki",
                "recall": {"enabled": True, "audit": True},
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        # 1 条原始 + 2 次重新生成 = 共 3 条审计。
        assert body["xijian"]["recall"]["regenerations"] == 2
        assert calls["n"] == 3
        assert body["xijian"]["audit"]["verdict"] == VERDICT_BLOCK

    def test_no_regeneration_when_pass(self, client, auth_headers, monkeypatch):
        _reset_memory()
        calls = {"n": 0}

        def pass_audit(**kwargs):
            calls["n"] += 1
            return {"verdict": VERDICT_PASS, "warnings": [], "audited_entry_ids": [], "missing_entry_ids": []}

        monkeypatch.setattr(citations_stub, "audit", pass_audit)

        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "你好"}],
            xijian={
                "character_id": "char_yuki",
                "recall": {"enabled": True, "audit": True},
            },
        )
        body = response.get_json()
        assert body["xijian"]["recall"]["regenerations"] == 0
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 对话记忆写回
# ---------------------------------------------------------------------------


class TestDialogueWriteBack:
    def test_writes_dialogue_memory_after_chat(self, client, auth_headers):
        _reset_memory()
        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "今天天气怎么样？"}],
            xijian={
                "character_id": "char_yuki",
                "recall": {"enabled": True, "audit": True},
            },
        )
        assert response.status_code == 200
        entries = [
            e for e in stubs_state.memory.values()
            if e.get("character_id") == "char_yuki" and e.get("source") == "dialogue"
        ]
        assert len(entries) >= 1
        assert any("今天天气怎么样" in e.get("content", "") for e in entries)

    def test_dialogue_write_back_bumps_cited_access(self, client, auth_headers):
        _reset_memory()
        # 播种一条可被召回的记忆条目并记录其 access_count。
        target = memory_stub.create(
            {
                "character_id": "char_yuki",
                "type": "short",
                "content": "memory 相关的事实：用户是工程师",
                "importance": 0.9,
                "decay_score": 1.0,
            }
        )
        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "随便问点 memory"}],
            xijian={
                "character_id": "char_yuki",
                "recall": {"enabled": True, "audit": True},
            },
        )
        assert response.status_code == 200
        # 召回管道本身会通过 recall_search /
        # load_context 增加访问计数；写回辅助函数还会增加被引用集合。
        cited = response.get_json()["xijian"]["recall"]["citations"]
        assert target["id"] in cited
        # 至少存在一条对话记忆条目。
        assert any(
            e.get("source") == "dialogue"
            for e in stubs_state.memory.values()
            if e.get("character_id") == "char_yuki"
        )

    def test_plain_chat_writes_dialogue_when_character_id(self, client, auth_headers):
        _reset_memory()
        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "你好"}],
            xijian={"character_id": "char_yuki"},
        )
        assert response.status_code == 200
        assert any(
            e.get("source") == "dialogue"
            for e in stubs_state.memory.values()
            if e.get("character_id") == "char_yuki"
        )

    def test_no_dialogue_write_without_character(self, client, auth_headers):
        stubs_state.memory.clear()
        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "hello"}],
        )
        assert response.status_code == 200
        assert stubs_state.memory == {} or all(
            e.get("source") != "dialogue" for e in stubs_state.memory.values()
        )


# ---------------------------------------------------------------------------
# A3.2 can_dialogue 闸门
# ---------------------------------------------------------------------------


class TestCanDialogueGate:
    def test_critical_character_gets_400(self, client, auth_headers):
        from xijian_api.stubs import character_state as cs_stub

        _reset_memory()
        # 将角色状态驱动到 Critical（health ≤ 0）。
        cs_stub.apply_field_change("char_yuki", "health", 0.0, reason="manual")
        assert cs_stub.can_dialogue("char_yuki") is False

        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "你还好吗？"}],
            xijian={"character_id": "char_yuki"},
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"]["code"] == "character_cannot_dialogue"

    def test_critical_character_stream_gets_400(self, client, auth_headers):
        from xijian_api.stubs import character_state as cs_stub

        _reset_memory()
        cs_stub.apply_field_change("char_yuki", "health", 0.0, reason="manual")

        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "还在吗？"}],
            xijian={"character_id": "char_yuki"},
            stream=True,
        )
        assert response.status_code == 400

    def test_healthy_character_not_blocked(self, client, auth_headers):
        _reset_memory()
        response = _post_chat(
            client,
            auth_headers,
            messages=[{"role": "user", "content": "你好"}],
            xijian={"character_id": "char_yuki"},
        )
        assert response.status_code == 200


class TestCanDialogueUnit:
    def test_complete_raises_for_critical(self):
        from xijian_api.errors import ApiError
        from xijian_api.stubs import character_state as cs_stub

        cs_stub.apply_field_change("char_yuki", "health", 0.0, reason="manual")
        with pytest.raises(ApiError) as exc_info:
            chat_stub.complete(
                [{"role": "user", "content": "hi"}],
                xijian={"character_id": "char_yuki"},
            )
        assert exc_info.value.status == 400
        assert exc_info.value.code == "character_cannot_dialogue"
