"""Tests for the citation audit module (A1.2 §引用审查).
(引用审计模块的测试 (A1.2 §引用审查)。)

Verifies the contract the chat pipeline relies on:
(验证聊天管道依赖的契约：)

* AC-3 — when a response references real entries that don't exist,
  audit warns.
* (AC-3 — 当响应引用不存在的真实条目时，审计发出警告。)
* AC-4 — when a response claims past knowledge without citing any
  real entry, audit warns.
* (AC-4 — 当响应声称过去知识但未引用任何真实条目时，审计发出警告。)
* Pass case — clean responses with matching citations pass.
* (通过情况 — 带有匹配引用的干净响应通过。)
* Audit events land in :data:`xijian_api.stubs.state.audits`.
* (审计事件落在 :data:`xijian_api.stubs.state.audits` 中。)
"""

from __future__ import annotations

from xijian_api.stubs import citations as citations_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state


def _real_entry(content: str = "用户喜欢草莓", importance: float = 0.7) -> dict:
    """Helper to create a real memory entry for citation testing.
    (创建真实记忆条目用于引用测试的辅助函数。)
    """
    return memory_stub.create(
        {
            "character_id": "char_cite",
            "type": "short",
            "importance": importance,
            "content": content,
        }
    )


def test_has_past_reference_detects_chinese_and_english():
    """Detects past references in both Chinese and English text.
    (检测中英文文本中的过去引用。)
    """
    assert citations_stub.has_past_reference("你上次说过喜欢猫") is True
    assert citations_stub.has_past_reference("记得你提过这件事") is True
    assert citations_stub.has_past_reference("you said you love cats") is True
    assert citations_stub.has_past_reference("Last time we talked about it") is True
    assert citations_stub.has_past_reference("今天的天气真好") is False
    assert citations_stub.has_past_reference("") is False


def test_audit_passes_when_no_past_reference_and_no_citations():
    """No past reference and no citations → pass verdict.
    (无过去引用且无引用 → 通过判定。)
    """
    state.memory.clear()
    result = citations_stub.audit(response_text="你好，今天天气不错。", candidate_entry_ids=[])
    assert result["verdict"] == "pass"
    assert result["warnings"] == []
    assert result["audited_entry_ids"] == []


def test_audit_passes_when_citations_resolve():
    """Citations resolve to real entries → pass verdict.
    (引用解析到真实条目 → 通过判定。)
    """
    state.memory.clear()
    entry = _real_entry()
    text = (
        f"我记得你说过 ({entry['id']})，你喜欢草莓。"
    )
    result = citations_stub.audit(response_text=text, candidate_entry_ids=[entry["id"]])
    assert result["verdict"] == "pass"
    assert result["warnings"] == []
    assert result["audited_entry_ids"] == [entry["id"]]
    assert result["missing_entry_ids"] == []


def test_audit_warns_when_cited_entry_missing():
    """Cited entry does not exist → warn verdict.
    (引用的条目不存在 → 警告判定。)
    """
    state.memory.clear()
    fake_id = "mem_deadbeef0000"
    result = citations_stub.audit(
        response_text="我记得这件事。",
        candidate_entry_ids=[fake_id],
    )
    assert result["verdict"] == "warn"
    assert any(w["kind"] == "missing_citation" for w in result["warnings"])
    assert result["missing_entry_ids"] == [fake_id]
    assert result["audited_entry_ids"] == []


def test_audit_warns_on_uncited_history_reference():
    """Past reference without citations → warn verdict.
    (过去引用无引用 → 警告判定。)
    """
    state.memory.clear()
    # No citations, but the response claims knowledge of the past.
    # (无引用，但响应声称过去知识。)
    result = citations_stub.audit(
        response_text="你上次说喜欢猫，今天还想聊聊吗？",
        candidate_entry_ids=[],
        escalate_uncited=False,
    )
    assert result["verdict"] == "warn"
    kinds = {w["kind"] for w in result["warnings"]}
    assert "uncited_history_reference" in kinds


def test_audit_dedups_duplicate_entry_ids():
    """Duplicate entry IDs in candidate list are deduplicated.
    (候选列表中的重复条目 ID 被去重。)
    """
    state.memory.clear()
    entry = _real_entry()
    result = citations_stub.audit(
        response_text="hello",
        candidate_entry_ids=[entry["id"], entry["id"], entry["id"]],
    )
    assert result["audited_entry_ids"] == [entry["id"]]


def test_audit_writes_to_state_audits_log():
    """Audit writes audit events to state.audits.
    (审计将审计事件写入 state.audits。)
    """
    state.memory.clear()
    state.audits.clear()
    entry = _real_entry()
    citations_stub.audit(
        response_text=f"({entry['id']}) something",
        candidate_entry_ids=[entry["id"]],
    )
    kinds = {a["kind"] for a in state.audits}
    assert "citation_audited" in kinds


def test_audit_emits_separate_event_for_each_missing_entry():
    """Each missing entry gets its own audit event.
    (每个缺失条目获得自己的审计事件。)
    """
    state.memory.clear()
    state.audits.clear()
    citations_stub.audit(
        response_text="x",
        candidate_entry_ids=["mem_aaaaaaaaaaaa", "mem_bbbbbbbbbbbb"],
    )
    missing_events = [a for a in state.audits if a["kind"] == "citation_missing_entry"]
    assert len(missing_events) == 2
    entry_ids = {a["details"].get("entry_id") for a in missing_events}
    assert entry_ids == {"mem_aaaaaaaaaaaa", "mem_bbbbbbbbbbbb"}
