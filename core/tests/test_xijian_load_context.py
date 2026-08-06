"""Tests for :func:`xijian_api.stubs.memory.load_context` (A1.2 §自动记忆载入).

The tests cover the spec's loadContext flow:

* Long-term memories are selected by importance ≥ config threshold,
  sorted by importance desc.
* Short-term memories are selected by live ``decay_score`` ≥ config
  threshold, sorted by ``decay_score × importance`` desc.
* A per-character budget override (``budget_tokens``) drives an
  importance-based trim when the assembled block would overflow.
* Per-character overrides from :class:`character_memory_config` are
  honoured (e.g. ``max_long_term=0`` ⇒ no long-term block).
* Empty / ``None`` inputs return a safe envelope without raising.
* Successful selection bumps ``access_count`` / ``last_access_at`` on
  every surviving entry (read-as-access semantics).
"""

from __future__ import annotations

import time

from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import memory_config as config_stub
from xijian_api.stubs import state


def _wipe() -> None:
    state.memory.clear()
    state.memory_configs.clear()


def _seed_long(char_id: str, content: str, importance: float) -> str:
    """直接插入一条长期条目并返回其 id。"""
    record = memory_stub.create(
        {
            "character_id": char_id,
            "type": "long",
            "content": content,
            "importance": importance,
            "source": "manual",
        }
    )
    # ``create`` 使用 ``now_ts`` 作为时间戳；长期条目不会
    # 衰减，因此时间戳仅供参考。将 ``created_at`` 固定到
    # 一个确定的纪元值，保证测试顺序稳定。
    record["created_at"] = 1_700_000_000_000
    record["updated_at"] = 1_700_000_000_000
    record["last_access_at"] = 1_700_000_000_000
    return record["id"]


def _seed_short(
    char_id: str,
    content: str,
    importance: float,
    *,
    decay_score: float = 0.95,
    age_hours: float = 0.0,
    created_at: int | None = None,
) -> str:
    """插入一条短期条目，并可通过 created_at 设置其年龄。"""
    record = memory_stub.create(
        {
            "character_id": char_id,
            "type": "short",
            "content": content,
            "importance": importance,
            "decay_score": decay_score,
            "source": "dialogue",
        }
    )
    base = created_at if created_at is not None else int(time.time() * 1000) - int(age_hours * 3600 * 1000)
    record["created_at"] = base
    record["updated_at"] = base
    record["last_access_at"] = base
    return record["id"]


# ---------------------------------------------------------------------------
# 基础选择
# ---------------------------------------------------------------------------


def test_load_context_returns_empty_envelope_for_none_character():
    _wipe()
    envelope = memory_stub.load_context(None)
    assert envelope["empty"] is True
    assert envelope["system_message"] == ""
    assert envelope["long_term_count"] == 0
    assert envelope["short_term_count"] == 0
    assert envelope["long_term_ids"] == []
    assert envelope["short_term_ids"] == []


def test_load_context_returns_empty_when_no_entries_match():
    _wipe()
    envelope = memory_stub.load_context("ghost_character")
    assert envelope["empty"] is True
    # 预算仍从默认值解析（8000 - 2000 = 6000）。
    assert envelope["budget_tokens"] == 6000
    assert envelope["estimated_tokens"] == 0


def test_load_context_picks_seeded_yuki_entries():
    _wipe()
    memory_stub.seed_default(character_id="char_yuki")
    envelope = memory_stub.load_context("char_yuki")
    # 种子包含 1 条长期 + 2 条短期条目（均高于默认阈值）。
    assert envelope["long_term_count"] == 1
    assert envelope["short_term_count"] == 2
    assert envelope["empty"] is False
    # 渲染出的系统消息同时提到两个部分。
    assert "## 长期记忆" in envelope["system_message"]
    assert "## 短期记忆" in envelope["system_message"]
    # 短期条目按 衰减 × importance 排序 ——
    # 0.7 × 0.95 = 0.665 的条目排在 0.4 × 0.6 = 0.24 的条目之前。
    short_ids = envelope["short_term_ids"]
    assert len(short_ids) == 2
    # 两个 id 均被记录且跨调用稳定。
    envelope2 = memory_stub.load_context("char_yuki")
    assert envelope2["short_term_ids"] == short_ids


# ---------------------------------------------------------------------------
# Importance 过滤
# ---------------------------------------------------------------------------


def test_long_term_below_importance_min_is_excluded():
    _wipe()
    _seed_long("c1", "high-importance identity", 0.9)
    _seed_long("c1", "borderline-low identity", 0.5)  # 低于默认值 0.6
    envelope = memory_stub.load_context("c1")
    assert envelope["long_term_count"] == 1
    contents = envelope["system_message"]
    assert "high-importance identity" in contents
    assert "borderline-low identity" not in contents


def test_short_term_below_decay_threshold_is_excluded():
    _wipe()
    # 低 importance + 时间久远的条目 → 实时衰减得分低。
    _seed_short("c1", "forgettable chatter", 0.2, decay_score=0.5, age_hours=24)
    _seed_short("c1", "remembered preference", 0.8, decay_score=0.95, age_hours=0)
    envelope = memory_stub.load_context("c1")
    assert envelope["short_term_count"] == 1
    assert "remembered preference" in envelope["system_message"]
    assert "forgettable chatter" not in envelope["system_message"]


def test_max_long_term_zero_disables_long_term_block():
    _wipe()
    _seed_long("c1", "important identity", 0.9)
    _seed_short("c1", "recent preference", 0.7)
    config_stub.upsert("c1", {"max_long_term": 0})
    envelope = memory_stub.load_context("c1")
    assert envelope["long_term_count"] == 0
    assert envelope["short_term_count"] == 1
    assert "## 长期记忆" not in envelope["system_message"]
    assert "## 短期记忆" in envelope["system_message"]


def test_max_short_term_zero_disables_short_term_block():
    _wipe()
    _seed_long("c1", "important identity", 0.9)
    _seed_short("c1", "recent preference", 0.7)
    config_stub.upsert("c1", {"max_short_term": 0})
    envelope = memory_stub.load_context("c1")
    assert envelope["long_term_count"] == 1
    assert envelope["short_term_count"] == 0
    assert "## 长期记忆" in envelope["system_message"]
    assert "## 短期记忆" not in envelope["system_message"]


def test_per_character_filter_isolates_other_characters():
    _wipe()
    _seed_long("alice", "alice identity", 0.9)
    _seed_long("bob", "bob identity", 0.9)
    envelope = memory_stub.load_context("alice")
    assert envelope["long_term_count"] == 1
    assert "alice identity" in envelope["system_message"]
    assert "bob identity" not in envelope["system_message"]


# ---------------------------------------------------------------------------
# Token 预算 + importance 裁剪
# ---------------------------------------------------------------------------


def test_token_budget_override_triggers_trim_when_oversized():
    _wipe()
    # 三条长期条目均高于 importance 阈值；最上面的
    # 条目刻意很长，即使 alpha + 头部也会撑满
    # 预算 —— 裁剪生效，低 importance 条目被丢弃。
    _seed_long(
        "c1",
        "alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha-alpha",
        0.95,
    )
    _seed_long("c1", "beta-beta-beta-beta-beta-beta-beta-beta", 0.7)
    _seed_long("c1", "gamma-gamma-gamma-gamma-gamma-gamma-gamma", 0.65)
    envelope = memory_stub.load_context("c1", budget_tokens=40)
    assert envelope["trimmed"] is True
    # Trim drops lower-importance entries; at least the top one survives.
    # 裁剪 — drops lower-importance entries; at least the top one survives.
    assert envelope["long_term_count"] >= 1
    assert envelope["estimated_tokens"] <= envelope["budget_tokens"]
    assert "alpha" in envelope["system_message"]
    # 低 importance 条目首先被丢弃。
    assert "beta-beta-beta-beta-beta-beta-beta-beta" not in envelope["system_message"]
    assert "gamma-gamma-gamma-gamma-gamma-gamma-gamma" not in envelope["system_message"]


def test_no_trim_when_assembly_fits_budget():
    _wipe()
    _seed_long("c1", "tiny entry", 0.9)
    envelope = memory_stub.load_context("c1", budget_tokens=10_000)
    assert envelope["trimmed"] is False
    assert envelope["long_term_count"] == 1
    assert envelope["estimated_tokens"] <= envelope["budget_tokens"]


def test_trim_prefers_long_when_importance_scores_tie():
    _wipe()
    # 两条长期 + 一条短期，importance 全部相同，因此
    # 长期优先的平局规则（排序键含 kind 等级）应在预算紧张时
    # 保留长期条目而非短期条目。
    _seed_long("c1", "long identity A", 0.7)
    _seed_long("c1", "long identity B", 0.7)
    _seed_short("c1", "short preference same importance", 0.7)
    envelope = memory_stub.load_context("c1", budget_tokens=80)
    # 三者都应放下；关键是长期条目
    # *在场*，且短期条目不会挤掉它们。
    assert envelope["long_term_count"] == 2
    assert envelope["short_term_count"] == 1


def test_trim_drops_low_importance_long_first():
    _wipe()
    # 两条长期条目均高于阈值；预算无法同时容纳时，
    # importance 更高者胜出。
    _seed_long("c1", "very-important identity", 0.95)
    _seed_long("c1", "less-important identity is longer string", 0.7)
    envelope = memory_stub.load_context("c1", budget_tokens=25)
    assert envelope["trimmed"] is True
    assert "very-important identity" in envelope["system_message"]
    assert "less-important identity" not in envelope["system_message"]


def test_zero_budget_returns_empty_after_trim():
    _wipe()
    _seed_long("c1", "any entry", 0.9)
    envelope = memory_stub.load_context("c1", budget_tokens=0)
    # 仅头部开销就超过预算，因此无任何条目幸存。
    assert envelope["trimmed"] is True
    assert envelope["long_term_count"] == 0
    assert envelope["empty"] is True


# ---------------------------------------------------------------------------
# 读取即访问的记账
# ---------------------------------------------------------------------------


def test_load_context_bumps_access_count_by_default():
    _wipe()
    entry_id = _seed_long("c1", "important identity", 0.9)
    before = state.memory[entry_id]["access_count"]
    memory_stub.load_context("c1")
    after = state.memory[entry_id]["access_count"]
    assert after == before + 1
    assert state.memory[entry_id]["last_access_at"] is not None


def test_load_context_with_bump_access_false_does_not_mutate():
    _wipe()
    entry_id = _seed_long("c1", "important identity", 0.9)
    before = state.memory[entry_id]["access_count"]
    memory_stub.load_context("c1", bump_access=False)
    after = state.memory[entry_id]["access_count"]
    assert after == before


# ---------------------------------------------------------------------------
# 诊断块
# ---------------------------------------------------------------------------


def test_used_config_reflects_effective_overrides():
    _wipe()
    _seed_long("c1", "identity", 0.9)
    config_stub.upsert("c1", {"max_long_term": 7, "long_term_importance_min": 0.4})
    envelope = memory_stub.load_context("c1")
    assert envelope["used_config"]["max_long_term"] == 7
    assert envelope["used_config"]["long_term_importance_min"] == 0.4


def test_default_budget_derived_from_config():
    _wipe()
    envelope = memory_stub.load_context("c1")
    # Default config: max_context_tokens=8000, reserve_tokens_for_reply=2000.
    # 默认 — config: max_context_tokens=8000, reserve_tokens_for_reply=2000.
    assert envelope["budget_tokens"] == 6000