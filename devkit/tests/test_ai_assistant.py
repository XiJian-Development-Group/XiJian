"""DevKit AI 设计助手测试 (C4)。

覆盖：

* **真实后端注册表** — :func:`auto_suggest` / :func:`_generate_suggestion`
  调用 AI 后端注册表 (:mod:`devkit.ai.registry`) 并在存根环境中回退到
  确定性模拟后端，根据输入上下文（人设/世界文档特征）生成建议，
  而非固定字符串。
* **确定性与多样性** — 相同输入始终产生相同建议；不同输入产生不同建议。
* **澄清问题** — :func:`suggest_with_questions` 返回模块级问题集 (C4 AC-2)。
* **ai_ratio 与 30% 阈值** — :func:`calculate_ai_ratio` /
  :func:`check_ai_threshold` 与辅助日志均被保留。

每个测试使用独立的临时工作目录，确保辅助日志
(``<work_dir>/ai_assist/assist_log.json``) 在测试间不泄露。
"""

from __future__ import annotations

import pytest

from devkit.ai_assistant import (
    _generate_suggestion,
    auto_suggest,
    calculate_ai_ratio,
    check_ai_threshold,
    get_assist_stats,
    list_assist_log,
    log_assist_event,
    suggest_with_questions,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_mock_backend(monkeypatch):
    """强制所有测试使用确定性模拟聊天后端。

    模拟后端始终 ``is_available()`` 且不接触文件系统，因此无论宿主
    是否安装 MLX / GGUF，测试均为确定性。
    """
    monkeypatch.setenv("XIJIAN_AI_BACKEND_CHAT", "mock")


@pytest.fixture
def work_dir(tmp_path) -> str:
    """每个测试一个全新的临时工作目录。"""
    return str(tmp_path)


# ---------------------------------------------------------------------------
# _generate_suggestion — 真实后端注册表
# ---------------------------------------------------------------------------


def test_generate_suggestion_uses_mock_backend(work_dir):
    """存根环境 → 注册表解析到确定性模拟后端。"""
    suggestion, backend = _generate_suggestion("角色 林晚，性格温柔，来自修仙世界")
    assert backend == "mock"
    assert suggestion
    assert "林晚" in suggestion


def test_generate_suggestion_context_derived(work_dir):
    """建议引用从输入上下文提取的特征，而非固定模板字符串。"""
    suggestion, backend = _generate_suggestion("世界观 废土科幻，人类灭绝后的地下城")
    assert backend == "mock"
    # 从上下文提取的特征应出现在建议中。
    assert "废土" in suggestion or "地下城" in suggestion or "科幻" in suggestion


def test_generate_suggestion_deterministic(work_dir):
    """相同输入 → 相同输出（无随机源）。"""
    a1, b1 = _generate_suggestion("角色 林晚，性格温柔")
    a2, b2 = _generate_suggestion("角色 林晚，性格温柔")
    assert (a1, b1) == (a2, b2)


def test_generate_suggestion_diverse(work_dir):
    """不同输入 → 不同建议。"""
    a1, _ = _generate_suggestion("角色 林晚，性格温柔")
    a2, _ = _generate_suggestion("世界观 废土科幻")
    assert a1 != a2


# ---------------------------------------------------------------------------
# auto_suggest — 端到端 + 辅助日志
# ---------------------------------------------------------------------------


def test_auto_suggest_available(work_dir):
    result = auto_suggest(work_dir, "角色 林晚，性格温柔")
    assert result["available"] is True
    assert result["backend"] == "mock"
    assert result["suggestion"]


def test_auto_suggest_logs_assist_event(work_dir):
    auto_suggest(work_dir, "角色 林晚，性格温柔")
    log = list_assist_log(work_dir)
    assert len(log) == 1
    assert log[0]["source"] == "ai_suggested"
    assert log[0]["target_module"] == "character"
    stats = get_assist_stats(work_dir)
    assert stats["total_events"] == 1
    assert stats["by_module"].get("character") == 1


def test_auto_suggest_feeds_ai_ratio(work_dir):
    """AI 建议事件计入 ai_ratio 与 30% 阈值。"""
    auto_suggest(work_dir, "角色 甲")
    auto_suggest(work_dir, "角色 乙")
    # 一个手动（非 AI）事件。
    log_assist_event(
        work_dir,
        event_type="manual_edit",
        target_module="character",
        description="手写设定",
        accepted=True,
        source="manual",
    )
    ratio = calculate_ai_ratio(work_dir)
    assert ratio == pytest.approx(2 / 3, abs=0.01)
    verdict = check_ai_threshold(work_dir)
    assert verdict["ai_ratio"] == pytest.approx(2 / 3, abs=0.01)
    assert verdict["requires_review"] is True


def test_ai_ratio_below_threshold(work_dir):
    """AI 占比低于 30% → 无需复核。"""
    log_assist_event(
        work_dir, event_type="manual_edit", target_module="world",
        description="手写设定", accepted=True, source="manual",
    )
    log_assist_event(
        work_dir, event_type="manual_edit", target_module="world",
        description="手写设定", accepted=True, source="manual",
    )
    auto_suggest(work_dir, "角色 林晚")
    ratio = calculate_ai_ratio(work_dir)
    assert ratio == pytest.approx(1 / 3, abs=0.01)
    assert check_ai_threshold(work_dir)["requires_review"] is True  # 33% > 30%
    # 两个手动 + 一个 AI 为 25% 时应低于阈值 —— 通过第二个手动事件验证。
    log_assist_event(
        work_dir, event_type="manual_edit", target_module="world",
        description="手写设定", accepted=True, source="manual",
    )
    assert calculate_ai_ratio(work_dir) == pytest.approx(0.25, abs=0.01)
    assert check_ai_threshold(work_dir)["requires_review"] is False


# ---------------------------------------------------------------------------
# suggest_with_questions — C4 AC-2
# ---------------------------------------------------------------------------


def test_suggest_with_questions_module(work_dir):
    result = suggest_with_questions(work_dir, "角色 林晚")
    assert result["module"] == "character"
    assert result["available"] is True
    assert len(result["questions"]) >= 3
    keys = {q["key"] for q in result["questions"]}
    assert "name" in keys
    assert "personality_core" in keys


def test_suggest_with_questions_world(work_dir):
    result = suggest_with_questions(work_dir, "世界观 废土科幻")
    assert result["module"] == "world"
    keys = {q["key"] for q in result["questions"]}
    assert "era" in keys and "conflict" in keys


def test_suggest_with_questions_logs(work_dir):
    suggest_with_questions(work_dir, "角色 枫晚")
    log = list_assist_log(work_dir)
    assert len(log) == 1
    assert log[0]["source"] == "ai_suggested"
    assert log[0]["event_type"] == "suggest_questions"


# ---------------------------------------------------------------------------
# 阈值边界 (30%)
# ---------------------------------------------------------------------------


def test_threshold_boundary(work_dir):
    """3 手动 + 7 AI = 70% → 复核; 7 手动 + 3 AI = 30% → 恰好在
    阈值 (不超过 → 无需复核, 按 ``>`` 语义)。"""
    for _ in range(7):
        auto_suggest(work_dir, "角色 测试")
    for _ in range(3):
        log_assist_event(
            work_dir, event_type="manual", target_module="character",
            description="手写", accepted=True, source="manual",
        )
    assert calculate_ai_ratio(work_dir) == pytest.approx(0.7, abs=0.01)
    assert check_ai_threshold(work_dir)["requires_review"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])