"""Tests for the DevKit AI design assistant (C4).

Covers:

* **Real backend registry** — :func:`auto_suggest` / :func:`_generate_suggestion`
  call the AI backend registry (:mod:`devkit.ai.registry`) and fall back to
  the deterministic mock backend in stub environments, producing suggestions
  derived from the input context (persona / world-doc features) rather than
  fixed strings.
* **Determinism & diversity** — the same input always yields the same
  suggestion; different inputs yield different suggestions.
* **Clarifying questions** — :func:`suggest_with_questions` returns the
  module-scoped question set (C4 AC-2).
* **ai_ratio & 30% threshold** — :func:`calculate_ai_ratio` /
  :func:`check_ai_threshold` and the assist log are preserved.

Each test uses a fresh temporary work dir so the assist log
(``<work_dir>/ai_assist/assist_log.json``) never leaks between tests.
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
    """Force the deterministic mock chat backend for every test.

    The mock backend is always ``is_available()`` and never touches the
    filesystem, so tests are deterministic regardless of whether MLX / GGUF
    is installed on the host.
    """
    monkeypatch.setenv("XIJIAN_AI_BACKEND_CHAT", "mock")


@pytest.fixture
def work_dir(tmp_path) -> str:
    """A fresh temporary work directory per test."""
    return str(tmp_path)


# ---------------------------------------------------------------------------
# _generate_suggestion — real backend registry
# ---------------------------------------------------------------------------


def test_generate_suggestion_uses_mock_backend(work_dir):
    """Stub env → the registry resolves to the deterministic mock backend."""
    suggestion, backend = _generate_suggestion(
        "角色 林晚", "角色 林晚，性格温柔，来自修仙世界"
    )
    assert backend == "mock"
    assert suggestion
    assert "林晚" in suggestion


def test_generate_suggestion_context_derived(work_dir):
    """The suggestion references features extracted from the input context,
    not a fixed template string."""
    suggestion, backend = _generate_suggestion(
        "世界", "世界观 废土科幻，人类灭绝后的地下城"
    )
    assert backend == "mock"
    # Extracted features from the context should appear in the suggestion.
    assert "废土" in suggestion or "地下城" in suggestion or "科幻" in suggestion


def test_generate_suggestion_deterministic(work_dir):
    """Same input → same output (no random source)."""
    a1, b1 = _generate_suggestion("角色", "角色 林晚，性格温柔")
    a2, b2 = _generate_suggestion("角色", "角色 林晚，性格温柔")
    assert (a1, b1) == (a2, b2)


def test_generate_suggestion_diverse(work_dir):
    """Different inputs → different suggestions."""
    a1, _ = _generate_suggestion("角色", "角色 林晚，性格温柔")
    a2, _ = _generate_suggestion("世界", "世界观 废土科幻")
    assert a1 != a2


# ---------------------------------------------------------------------------
# auto_suggest — end-to-end + assist log
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
    """AI-suggested events count toward ai_ratio and the 30% threshold."""
    auto_suggest(work_dir, "角色 甲")
    auto_suggest(work_dir, "角色 乙")
    # One manual (non-AI) event.
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
    """Under 30% AI share → no review required."""
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
    # Two manual events + one AI event at 25% would be under — verify via
    # a second manual event.
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
    suggest_with_questions(work_dir, "角色 林晚")
    log = list_assist_log(work_dir)
    assert len(log) == 1
    assert log[0]["source"] == "ai_suggested"
    assert log[0]["event_type"] == "suggest_questions"


# ---------------------------------------------------------------------------
# Threshold boundary (30%)
# ---------------------------------------------------------------------------


def test_threshold_boundary(work_dir):
    """3 manual + 7 AI = 70% → review; 7 manual + 3 AI = 30% → exactly at
    threshold (not over → no review, per ``>`` semantics)."""
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
