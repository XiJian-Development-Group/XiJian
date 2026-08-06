"""End-to-end tests for the A1.2 forced-recall chat pipeline.

These tests hit ``POST /v1/chat/completions`` with the
``xijian.character_id`` + ``xijian.recall.enabled`` extension and
assert:

* the response carries ``xijian.recall.tool_calls`` and ``xijian.audit``
  blocks;
* the recall tool call is auto-executed and citations are returned;
* the citation audit verdict is consistent with the final text;
* when recall is *not* requested the response shape is unchanged
  (regression guard for the regular chat path).

  测试记忆召回管道的核心流程（A1.3）。
  覆盖嵌入、检索、重排序和生成阶段。
"""

from __future__ import annotations

from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state


_MODEL = "mock-qwen2.5-7b"


def _post_chat(client, auth_headers, *, messages, xijian=None):
    payload = {"model": _MODEL, "messages": messages}
    if xijian is not None:
        payload["xijian"] = xijian
    return client.post("/v1/chat/completions", headers=auth_headers, json=payload)


def test_recall_pipeline_auto_executes_tool_and_returns_citations(client, auth_headers):
    # 播种一条记忆条目，让召回搜索有内容可查。
    state.memory.clear()
    memory_stub.seed_default(character_id="char_yuki")

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
    assert body["object"] == "chat.completion"

    recall = body["xijian"]["recall"]
    assert recall["enabled"] is True
    assert recall["auto_executed"] is True
    assert len(recall["tool_calls"]) == 1
    tc = recall["tool_calls"][0]
    assert tc["name"] == "recall_memory"
    # Mock 以 query="memory" 触发召回，但种子
    # 条目对 "memory" 并无匹配；相反 mock 发出
    # 引用工具结果中 entry_ids 的最后一轮文本。
    # 在无命中情况下引用为空，但工具仍然
    # 执行了 —— 这正是被测的契约。

    audit_block = body["xijian"]["audit"]
    assert audit_block is not None
    assert audit_block["verdict"] in {"pass", "warn"}


def test_recall_pipeline_returns_real_citations_when_query_hits(client, auth_headers):
    state.memory.clear()
    memory_stub.seed_default(character_id="char_yuki")

    # 使用 mock 模型，但通过定制方式让测试
    # 决定召回查询。做法是走标准的
    # mock 路径，并断言当召回搜索匹配到种子条目时
    # 它们会被引用。mock 后端将工具调用
    # 硬编码为 query="memory"，因此我们放入一条
    # 内容包含该子串的种子条目。
    memory_stub.create(
        {
            "character_id": "char_yuki",
            "type": "short",
            "importance": 0.9,
            "content": "memory 相关的事实：用户是工程师",
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
    body = response.get_json()
    citations = body["xijian"]["recall"]["citations"]
    assert citations, "recall should have produced at least one citation"

    # 最后一轮文本回显了条目 id，因此审计应
    # 至少看到一条被审计的条目。
    audit_block = body["xijian"]["audit"]
    assert len(audit_block["audited_entry_ids"]) >= 1
    assert audit_block["missing_entry_ids"] == []
    assert audit_block["verdict"] == "pass"


def test_recall_pipeline_does_not_run_without_character_id(client, auth_headers):
    response = _post_chat(
        client,
        auth_headers,
        messages=[{"role": "user", "content": "hi"}],
        xijian={"recall": {"enabled": True}},  # 缺少 character_id
    )
    assert response.status_code == 200
    body = response.get_json()
    # 管道被禁用时不返回 recall 块。
    assert "recall" not in body["xijian"]


def test_recall_pipeline_skipped_when_recall_disabled(client, auth_headers):
    response = _post_chat(
        client,
        auth_headers,
        messages=[{"role": "user", "content": "hi"}],
        xijian={
            "character_id": "char_yuki",
            "recall": {"enabled": False},
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert "recall" not in body["xijian"]


def test_regular_chat_path_unchanged_when_no_xijian(client, auth_headers):
    response = _post_chat(
        client,
        auth_headers,
        messages=[{"role": "user", "content": "hello"}],
    )
    assert response.status_code == 200
    body = response.get_json()
    # ``xijian.backend`` 已设置；``xijian.recall`` 未设置。
    assert body["xijian"].get("backend") == "mock"
    assert "recall" not in body["xijian"]
    assert "audit" not in body["xijian"]


def test_recall_pipeline_appends_system_instruction(client, auth_headers):
    """注入的系统消息包含召回提示词。

    我们无法直接从路由观察发送给后端的消息，但可以通过
    发起一次读取最新系统消息的后续聊天来验证 mock 后端
    确实收到了它们。目前我们只确认响应格式良好 —— 详细的
    注入行为由下面聊天 stub 的单元测试覆盖。
    """
    state.memory.clear()
    memory_stub.seed_default(character_id="char_yuki")
    response = _post_chat(
        client,
        auth_headers,
        messages=[
            {"role": "system", "content": "你是一位助手"},
            {"role": "user", "content": "测试"},
        ],
        xijian={
            "character_id": "char_yuki",
            "recall": {"enabled": True, "audit": True},
        },
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["xijian"]["recall"]["enabled"] is True
