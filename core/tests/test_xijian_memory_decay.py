"""Tests for the memory decay algorithm (A1.2 §遗忘算法).

These are pure unit tests against :mod:`xijian_api.stubs.memory` — they
exercise the decay math, the long-term promotion heuristic, and the
recall ranking without going through the chat route.
"""

from __future__ import annotations

import math

from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state


def _seed_now_offset(hours: float):
    """返回一个可调用对象，生成距纪元基准 ``hours`` 小时后的 'now'
    时间；该基准是种子条目所用的时间（其 ``created_at`` 为
    播种时 ``memory_stub.now_ts()`` 的值）。"""
    base = memory_stub.now_ts()
    return int(base + hours * 3600)


def test_decay_score_long_term_is_pinned_to_one():
    record = memory_stub.create(
        {"character_id": "char_decay", "type": "long", "importance": 0.9, "content": "x"}
    )
    # 100 小时后，长期条目的分值仍为 1.0。
    later = (record["created_at"] or 0) + 100 * 3600
    assert memory_stub.compute_decay_score(record, now=later) == 1.0


def test_decay_score_short_term_decays_exponentially():
    record = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "short",
            "importance": 0.5,
            "content": "x",
            "decay_score": 1.0,
        }
    )
    rate = memory_stub.DEFAULT_SHORT_TERM_DECAY_RATE
    # 1 小时后：score == exp(-rate * 1)。
    later_one = (record["created_at"] or 0) + 3600
    expected_one = math.exp(-rate * 1.0)
    assert math.isclose(
        memory_stub.compute_decay_score(record, now=later_one, rate=rate),
        expected_one,
        rel_tol=1e-9,
    )

    # 10 小时后：score == exp(-rate * 10)。
    later_ten = (record["created_at"] or 0) + 10 * 3600
    expected_ten = math.exp(-rate * 10.0)
    assert math.isclose(
        memory_stub.compute_decay_score(record, now=later_ten, rate=rate),
        expected_ten,
        rel_tol=1e-9,
    )


def test_decay_score_uses_last_access_at_when_present():
    record = memory_stub.create(
        {"character_id": "char_decay", "type": "short", "decay_score": 1.0, "content": "x"}
    )
    created = record["created_at"] or 0
    # 创建后 2 小时最后一次访问；再往后 1 小时查询，
    # 意味着 Δh = 1，而不是 3。
    record["last_access_at"] = created + 2 * 3600
    later = created + 3 * 3600
    score = memory_stub.compute_decay_score(record, now=later)
    expected = math.exp(-memory_stub.DEFAULT_SHORT_TERM_DECAY_RATE * 1.0)
    assert math.isclose(score, expected, rel_tol=1e-9)


def test_should_promote_to_long_respects_importance_and_access():
    # 符合条件：短期、importance ≥ 0.5、衰减低于阈值、访问次数 < 2。
    eligible = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "short",
            "importance": 0.8,
            "content": "x",
            "decay_score": 0.1,
            "access_count": 0,
        }
    )
    assert memory_stub.should_promote_to_long(eligible) is True

    # 不符合条件：已是长期。
    long_rec = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "long",
            "importance": 0.9,
            "content": "y",
            "decay_score": 0.1,
        }
    )
    assert memory_stub.should_promote_to_long(long_rec) is False

    # 不符合条件：访问次数过多（≥ 2）。
    too_popular = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "short",
            "importance": 0.8,
            "content": "z",
            "decay_score": 0.1,
            "access_count": 2,
        }
    )
    assert memory_stub.should_promote_to_long(too_popular) is False

    # 不符合条件：importance 过低。
    low_imp = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "short",
            "importance": 0.2,
            "content": "w",
            "decay_score": 0.1,
        }
    )
    assert memory_stub.should_promote_to_long(low_imp) is False


def test_promote_to_long_resets_decay_score():
    record = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "short",
            "importance": 0.8,
            "content": "x",
            "decay_score": 0.05,
        }
    )
    updated = memory_stub.promote_to_long(record["id"])
    assert updated is not None
    assert updated["type"] == "long"
    assert updated["decay_score"] == 1.0


def test_decay_score_does_not_go_negative_for_far_future():
    record = memory_stub.create(
        {
            "character_id": "char_decay",
            "type": "short",
            "importance": 0.5,
            "decay_score": 1.0,
            "content": "x",
        }
    )
    far_future = (record["created_at"] or 0) + 10_000 * 3600
    score = memory_stub.compute_decay_score(record, now=far_future)
    # 衰减严格为正（渐近趋近于 0）。
    assert 0.0 <= score < 1e-6


def test_recall_search_ranks_importance_and_decay():
    # 重置为干净的存储，避免其它测试的种子条目
    # 干扰排序。
    state.memory.clear()
    low = memory_stub.create(
        {
            "character_id": "char_recall",
            "type": "short",
            "importance": 0.3,
            "content": "冰淇淋口味一般",
            "decay_score": 0.3,
        }
    )
    high = memory_stub.create(
        {
            "character_id": "char_recall",
            "type": "short",
            "importance": 0.95,
            "content": "冰淇淋是用户最喜欢的食物之一",
            "decay_score": 0.9,
        }
    )
    hits = memory_stub.recall_search(
        character_id="char_recall", query="冰淇淋", top_k=5
    )
    assert len(hits) == 2
    # importance 高 / 衰减高的条目必须排在低 / 低的条目之前。
    assert hits[0]["entry"]["id"] == high["id"]
    assert hits[1]["entry"]["id"] == low["id"]


def test_recall_search_bumps_access_count_and_last_access():
    state.memory.clear()
    record = memory_stub.create(
        {
            "character_id": "char_recall",
            "type": "short",
            "importance": 0.6,
            "content": "测试召回",
            "decay_score": 0.7,
        }
    )
    assert record["access_count"] == 0
    memory_stub.recall_search(
        character_id="char_recall", query="测试召回", top_k=3
    )
    refreshed = memory_stub.get(record["id"])
    assert refreshed["access_count"] == 1
    assert refreshed["last_access_at"] is not None


def test_recall_search_no_match_returns_empty():
    state.memory.clear()
    memory_stub.create(
        {
            "character_id": "char_recall",
            "type": "short",
            "importance": 0.6,
            "content": "苹果",
        }
    )
    hits = memory_stub.recall_search(
        character_id="char_recall", query="完全不相关", top_k=3
    )
    assert hits == []


def test_recall_search_respects_character_filter():
    state.memory.clear()
    memory_stub.create(
        {
            "character_id": "char_a",
            "type": "short",
            "importance": 0.9,
            "content": "alpha 关键词",
        }
    )
    memory_stub.create(
        {
            "character_id": "char_b",
            "type": "short",
            "importance": 0.9,
            "content": "beta 关键词",
        }
    )
    hits = memory_stub.recall_search(character_id="char_a", query="关键词", top_k=5)
    assert len(hits) == 1
    assert hits[0]["entry"]["character_id"] == "char_a"
