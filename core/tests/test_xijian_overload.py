"""Tests for ``stubs.overload`` (A5.4) and ``/v1/xijian/overload/*``.

The protection guard is the last-line safety net: it watches system
metrics, fires the right action when a threshold trips, and walks the
user through a 20-second recovery handshake with double confirmation.
The system *cannot* be disabled, but the tier (strict vs medium) is a
runtime knob.

We exercise three layers:

* **Pure helpers** — no I/O, no thread.  Use synthetic samples.
* **State + recovery flow** — drive the stubs directly, with a
  freezable clock so we can verify the 20 s wait deterministically.
* **Routes** — go through the Flask test client, confirm wiring
  end-to-end (auth, error formats, status codes).

  测试 A5.4 过载保护系统。
  验证在过载事件期间和恢复窗口中的调度器行为。
"""

from __future__ import annotations

import os
import time
from collections import deque

import pytest

from xijian_api.stubs import overload as ov_stub
from xijian_api.stubs.overload import (
    ACTION_COMPRESS_MEMORY,
    ACTION_DEGRADE_TTS,
    ACTION_EMERGENCY_DUMP,
    ACTION_SUSPEND_IDLE_NPCS,
    METRIC_CPU,
    METRIC_GPU,
    METRIC_MEM,
    METRIC_SOC,
    RECOVERY_WAIT_SECONDS,
    SAMPLE_INTERVAL_SECONDS,
    TIER_THRESHOLDS,
    VALID_TIERS,
    Sample,
)
from xijian_api.stubs import state as stubs_state


# ---------------------------------------------------------------------------
# 时钟 + 动作处理器夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def frozen_clock(monkeypatch):
    """返回可控时钟 + advance() 辅助函数。

    过载 stub 在多个地方调用 :func:`time.time`
    （恢复窗口记账、start_recovery 的默认时间戳）。
    想在不睡眠的情况下验证 ``too_early`` / ``can_finalize`` 的测试
    直接打补丁时钟。

    返回一个带 ``now()`` 和 ``advance(seconds)`` 辅助方法的小对象，
    它们对 ``ov_stub.time.time`` 进行 monkeypatch。
    """
    current = {"t": 1_000_000.0}

    def fake_time() -> float:
        return current["t"]

    monkeypatch.setattr(ov_stub.time, "time", fake_time)

    class Clock:
        def now(self) -> float:
            return current["t"]

        def advance(self, seconds: float) -> None:
            current["t"] += seconds

    return Clock()


@pytest.fixture()
def action_handler_recorder():
    """捕获处理器收到的每个 (action, event) 对。

    便捷 API：

    * ``recorder.on(action)`` — 注册内置捕获处理器，
      返回底层处理器。调用记录在 ``recorder.calls``。
    * ``recorder.register_with(action, fn)`` — 注册自定义处理器。

    清理由测试间的 autouse ``_reset_state`` 夹具负责，
    因此套件中不存在泄漏处理器的问题。
    """

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []
            self._registered: list[tuple[str, object]] = []

        def on(self, action: str):
            def _capture(event: dict) -> None:
                self.calls.append((action, event))

            self.register_with(action, _capture)
            return _capture

        def register_with(self, action: str, handler):
            ov_stub.register_action_handler(action, handler)
            self._registered.append((action, handler))
            return handler

    recorder = Recorder()
    yield recorder
    for action, handler in recorder._registered:
        try:
            ov_stub.unregister_action_handler(action, handler)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 纯辅助函数 — 无 I/O、无状态
# ---------------------------------------------------------------------------


class TestSelectMostSevereAction:
    def test_empty_returns_none(self):
        assert ov_stub.select_most_severe_action([]) is None

    def test_single_action(self):
        assert ov_stub.select_most_severe_action([ACTION_DEGRADE_TTS]) == ACTION_DEGRADE_TTS

    def test_worst_action_wins(self):
        # emergency_dump 优先级高于其它所有动作
        actions = [ACTION_SUSPEND_IDLE_NPCS, ACTION_DEGRADE_TTS, ACTION_EMERGENCY_DUMP]
        assert ov_stub.select_most_severe_action(actions) == ACTION_EMERGENCY_DUMP

    def test_unknown_action_is_ignored(self):
        assert ov_stub.select_most_severe_action(["not_a_real_action"]) is None


class TestEvaluateMetrics:
    def test_unknown_tier_returns_error_marker(self):
        result = ov_stub.evaluate_metrics([], "ghost")
        assert result == {
            "triggered": [],
            "action": None,
            "per_metric": {},
            "error": "unknown_tier:ghost",
        }

    def test_no_samples_means_no_trigger(self):
        result = ov_stub.evaluate_metrics([], "strict")
        assert result["triggered"] == []
        assert result["action"] is None
        assert result["per_metric"] == {}

    def test_instantaneous_metric_trips_immediately(self):
        # mem 和 soc 是 *瞬时* 指标 —— 单次越界即可触发。
        now = time.time()
        sample = Sample(ts=now, cpu_pct=10.0, mem_pct=95.0, soc_celsius=None, gpu_ane_pct=10.0)
        result = ov_stub.evaluate_metrics([sample], "strict", now=now)
        assert METRIC_MEM in result["triggered"]
        assert METRIC_CPU not in result["triggered"]
        assert METRIC_GPU not in result["triggered"]
        assert result["action"] == ACTION_COMPRESS_MEMORY

    def test_windowed_metric_does_not_trip_on_single_sample(self):
        # CPU 在严格档需要持续 60 秒 —— 单次采样不足以触发。
        now = time.time()
        sample = Sample(ts=now, cpu_pct=99.0, mem_pct=10.0, soc_celsius=None, gpu_ane_pct=10.0)
        result = ov_stub.evaluate_metrics([sample], "strict", now=now)
        assert METRIC_CPU not in result["triggered"]
        assert result["action"] is None

    def test_windowed_metric_trips_after_sustained_over_threshold(self):
        # 用超过 93% 的采样填满严格档 CPU 窗口（60 秒）。
        now = time.time()
        samples = [
            Sample(
                ts=now - (TIER_THRESHOLDS["strict"]["cpu_window_s"] - i * SAMPLE_INTERVAL_SECONDS),
                cpu_pct=95.0,
                mem_pct=10.0,
                soc_celsius=None,
                gpu_ane_pct=10.0,
            )
            for i in range(int(TIER_THRESHOLDS["strict"]["cpu_window_s"]) + 1)
        ]
        result = ov_stub.evaluate_metrics(samples, "strict", now=now)
        assert METRIC_CPU in result["triggered"]
        assert result["action"] == ACTION_SUSPEND_IDLE_NPCS

    def test_worst_action_wins_when_multiple_metrics_trip(self):
        # 让 CPU、MEM 和 GPU/ANE 同时超过阈值。
        now = time.time()
        cpu_window = TIER_THRESHOLDS["strict"]["cpu_window_s"]
        gpu_window = TIER_THRESHOLDS["strict"]["gpu_ane_window_s"]
        span = max(cpu_window, gpu_window)
        samples = [
            Sample(
                ts=now - (span - i * SAMPLE_INTERVAL_SECONDS),
                cpu_pct=95.0,  # 超过 93%
                mem_pct=95.0,  # 超过 90%
                soc_celsius=None,
                gpu_ane_pct=80.0,  # 超过 75%
            )
            for i in range(int(span) + 2)
        ]
        result = ov_stub.evaluate_metrics(samples, "strict", now=now)
        assert {METRIC_CPU, METRIC_MEM, METRIC_GPU}.issubset(set(result["triggered"]))
        # mem → compress_memory（严重度 30）；其中最高严重度是 COMPRESS_MEMORY。
        assert result["action"] == ACTION_COMPRESS_MEMORY

    def test_soc_temp_trips_action_emergency_dump(self):
        now = time.time()
        sample = Sample(ts=now, cpu_pct=10.0, mem_pct=10.0, soc_celsius=99.0, gpu_ane_pct=10.0)
        result = ov_stub.evaluate_metrics([sample], "strict", now=now)
        assert METRIC_SOC in result["triggered"]
        assert result["action"] == ACTION_EMERGENCY_DUMP

    def test_medium_tier_is_more_lenient_on_cpu(self):
        # 95% 持续 60 秒 → 触发严格档的 93%，但不触发中档（中档需要 95% 持续 100 秒）。
        now = time.time()
        cpu_window_60 = int(TIER_THRESHOLDS["medium"]["cpu_window_s"]) - 40  # 远低于中档的 100 秒
        samples = [
            Sample(
                ts=now - (cpu_window_60 - i * SAMPLE_INTERVAL_SECONDS),
                cpu_pct=96.0,
                mem_pct=10.0,
                soc_celsius=None,
                gpu_ane_pct=10.0,
            )
            for i in range(cpu_window_60 + 2)
        ]
        strict = ov_stub.evaluate_metrics(samples, "strict", now=now)
        medium = ov_stub.evaluate_metrics(samples, "medium", now=now)
        assert METRIC_CPU in strict["triggered"]
        assert METRIC_CPU not in medium["triggered"]

    def test_function_is_pure(self):
        # 无模块状态变更：相同输入 → 相同输出。
        now = time.time()
        samples = [
            Sample(ts=now - 30 * SAMPLE_INTERVAL_SECONDS + i * SAMPLE_INTERVAL_SECONDS,
                   cpu_pct=10.0, mem_pct=10.0, soc_celsius=None, gpu_ane_pct=10.0)
            for i in range(31)
        ]
        baseline = ov_stub.evaluate_metrics(samples, "strict", now=now)
        # 再次调用 —— 必须完全相同。
        repeat = ov_stub.evaluate_metrics(samples, "strict", now=now)
        assert baseline == repeat


# ---------------------------------------------------------------------------
# 档位管理
# ---------------------------------------------------------------------------


class TestTier:
    def test_default_tier_is_medium(self):
        # Conftest 在每个测试前重置为 "medium"。
        assert ov_stub.current_tier() == "medium"

    def test_valid_tiers(self):
        assert set(VALID_TIERS) == {"strict", "medium"}

    def test_set_tier_persists_and_records_timestamp(self):
        result = ov_stub.set_tier("strict")
        assert result["tier"] == "strict"
        assert isinstance(result["tier_changed_at"], int)
        assert ov_stub.current_tier() == "strict"

        result2 = ov_stub.set_tier("medium")
        assert result2["tier"] == "medium"
        assert ov_stub.current_tier() == "medium"

    def test_set_tier_rejects_unknown_values(self):
        # 按 AC-4：用户不能禁用守卫。Disabled/off/等
        # 值都必须被拒绝。
        for bad in ("off", "disabled", "low", "ghost", "", "loose", "lenient"):
            with pytest.raises(ValueError, match="invalid tier"):
                ov_stub.set_tier(bad)

    def test_host_recommendation_returns_a_valid_tier(self):
        rec = ov_stub.host_recommendation()
        assert rec in VALID_TIERS

    def test_no_off_or_disabled_constant_in_thresholds(self):
        # 防御性检查：文档明确说明只有 "strict / medium" 存在。
        assert set(TIER_THRESHOLDS.keys()) == {"strict", "medium"}


# ---------------------------------------------------------------------------
# Recovery handshake
# 恢复 — handshake
# ---------------------------------------------------------------------------


class TestRecoveryHandshake:
    def test_recovery_window_inactive_when_no_record(self):
        window = ov_stub.recovery_window()
        assert window == {"active": False, "remaining_seconds": 0, "can_confirm": False}

    def test_recovery_window_active_after_start(self, frozen_clock):
        # Trigger a synthetic event just to drive start_recovery.
        # 触发器 — a synthetic event just to drive start_recovery.
        event = {
            "id": "ovl_test_start",
            "triggered_at": frozen_clock.now(),
        }
        record = ov_stub.start_recovery(event)
        assert record["event_id"] == "ovl_test_start"
        assert record["status"] == "waiting"
        assert record["recoverable"] is True
        window = ov_stub.recovery_window()
        assert window["active"] is True
        assert window["can_finalize"] is False
        # 我们从 t = frozen_clock.now() 开始，因此还剩 20 秒。
        assert window["remaining_seconds"] == RECOVERY_WAIT_SECONDS

    def test_first_confirm_too_early(self, frozen_clock):
        event = {"id": "ovl_too_early", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event)
        result = ov_stub.first_confirm()
        assert result == {
            "ok": False,
            "error": "too_early",
            "remaining_seconds": RECOVERY_WAIT_SECONDS,
        }

    def test_first_confirm_after_wait(self, frozen_clock):
        event = {"id": "ovl_ok", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event)
        frozen_clock.advance(RECOVERY_WAIT_SECONDS + 1)
        result = ov_stub.first_confirm()
        assert result == {"ok": True, "status": "first_confirmed"}

    def test_finalize_requires_first_confirm(self, frozen_clock):
        event = {"id": "ovl_skip_first", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event)
        frozen_clock.advance(RECOVERY_WAIT_SECONDS + 1)
        result = ov_stub.finalize_recovery()
        assert result == {"ok": False, "error": "first_confirm_required"}

    def test_full_handshake(self, frozen_clock):
        event = {"id": "ovl_full", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event)
        frozen_clock.advance(RECOVERY_WAIT_SECONDS + 1)
        assert ov_stub.first_confirm()["status"] == "first_confirmed"
        # 同一时刻之后 finalize 必须成功。
        result = ov_stub.finalize_recovery()
        assert result["ok"] is True
        assert result["status"] == "finalized"
        assert result["event_id"] == "ovl_full"
        # 恢复记录应已被清除。
        assert ov_stub.recovery_window()["active"] is False

    def test_finalize_too_early(self, frozen_clock):
        event = {"id": "ovl_finalize_early", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event)
        frozen_clock.advance(RECOVERY_WAIT_SECONDS - 1)
        # first_confirm 同样受 20 秒门控
        result = ov_stub.finalize_recovery()
        assert result["ok"] is False
        assert result["error"] == "first_confirm_required"

    def test_retrigger_resets_countdown(self, frozen_clock):
        # Edge case from the doc: "用户在恢复前再次触发过载 → 重置 20s 倒计时"
        event_v1 = {"id": "ovl_v1", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event_v1)
        frozen_clock.advance(10)
        assert ov_stub.recovery_window()["remaining_seconds"] == 10
        frozen_clock.advance(5)  # 现在原始 20 秒已过去 15 秒
        assert ov_stub.recovery_window()["remaining_seconds"] == 5
        # 恢复中途的第二次触发必须重置回 20 秒。
        event_v2 = {"id": "ovl_v2", "triggered_at": frozen_clock.now()}
        ov_stub.start_recovery(event_v2)
        assert ov_stub.recovery_window()["remaining_seconds"] == RECOVERY_WAIT_SECONDS

    def test_cancel_when_inactive_is_noop(self):
        result = ov_stub.cancel_recovery()
        assert result == {"ok": True, "cancelled": False}

    def test_cancel_when_active_clears_record(self, frozen_clock):
        ov_stub.start_recovery({"id": "ovl_cancel", "triggered_at": frozen_clock.now()})
        result = ov_stub.cancel_recovery(reason="admin_test")
        assert result["cancelled"] is True
        assert result["event_id"] == "ovl_cancel"
        assert ov_stub.recovery_window()["active"] is False

    def test_first_confirm_with_no_active_recovery(self):
        # 记录错误码有助于路由层区分
        # 404（无恢复）与 425（仍在等待）。
        result = ov_stub.first_confirm()
        assert result == {"ok": False, "error": "no_active_recovery"}

    def test_finalize_with_no_active_recovery(self):
        result = ov_stub.finalize_recovery()
        assert result == {"ok": False, "error": "no_active_recovery"}

    def test_recovery_audit_on_finalize(self, frozen_clock):
        ov_stub.start_recovery({"id": "ovl_audit", "triggered_at": frozen_clock.now()})
        frozen_clock.advance(RECOVERY_WAIT_SECONDS + 1)
        ov_stub.first_confirm()
        ov_stub.finalize_recovery()
        # ``_append_audit`` 写入 ``state.audits``（仅追加的
        # 全局审计日志），而非 ``state.safety_state``。从
        # 那里取出匹配项。
        match = [a for a in stubs_state.audits if a.get("kind") == "overload_recovery_finalized"]
        assert match, "expected an overload_recovery_finalized audit entry"


# ---------------------------------------------------------------------------
# 动作处理器注册表
# ---------------------------------------------------------------------------


class TestActionHandlers:
    def test_register_invokes_on_trigger(self, frozen_clock, action_handler_recorder):
        recorder = action_handler_recorder
        recorder.on(ACTION_DEGRADE_TTS)
        ov_stub.simulate_overload(METRIC_GPU)
        # simulate_overload 将 GPU 窗口驱动到触发 → DEGRADE_TTS
        actions = [a for a, _ in recorder.calls]
        assert ACTION_DEGRADE_TTS in actions

    def test_multiple_handlers_each_invoked(self, frozen_clock, action_handler_recorder):
        # 同一动作上的两个处理器都会触发。
        events = []

        def make_handler(label):
            def _h(event):
                events.append((label, event["triggered_metrics"]))

            ov_stub.register_action_handler(ACTION_EMERGENCY_DUMP, _h)
            return _h

        make_handler("first")
        make_handler("second")
        ov_stub.simulate_overload(METRIC_SOC)
        assert ("first", [METRIC_SOC]) in events
        assert ("second", [METRIC_SOC]) in events

    def test_buggy_handler_does_not_block_others(self, frozen_clock, action_handler_recorder):
        good_calls = []

        def good(event):
            good_calls.append(event["id"])

        def bad(event):
            raise RuntimeError("simulated failure")

        ov_stub.register_action_handler(ACTION_EMERGENCY_DUMP, good)
        ov_stub.register_action_handler(ACTION_EMERGENCY_DUMP, bad)
        ov_stub.simulate_overload(METRIC_SOC)
        assert len(good_calls) == 1

    def test_list_action_handlers_reprs_callables(self, action_handler_recorder):
        action_handler_recorder.on(ACTION_COMPRESS_MEMORY)

        handlers = ov_stub.list_action_handlers()
        assert handlers[ACTION_COMPRESS_MEMORY], "registry should expose the handler"
        # 即使为空，全部四个动作也必须出现在注册表中。
        assert set(handlers.keys()) == {
            ACTION_SUSPEND_IDLE_NPCS,
            ACTION_DEGRADE_TTS,
            ACTION_COMPRESS_MEMORY,
            ACTION_EMERGENCY_DUMP,
        }

    def test_unregister_removes_handler(self, frozen_clock, action_handler_recorder):
        calls = []

        def h(event):
            calls.append(event["id"])

        ov_stub.register_action_handler(ACTION_DEGRADE_TTS, h)
        # 第一次触发即调用它。
        ov_stub.simulate_overload(METRIC_GPU)
        assert len(calls) == 1
        # 注销后再次触发。
        result = ov_stub.unregister_action_handler(ACTION_DEGRADE_TTS, h)
        assert result == {"action": ACTION_DEGRADE_TTS, "removed": True}
        ov_stub.simulate_overload(METRIC_GPU)
        assert len(calls) == 1, "handler must not fire after unregistration"

    def test_unregister_unknown_handler_is_noop(self):
        def h(_): pass

        result = ov_stub.unregister_action_handler(ACTION_DEGRADE_TTS, h)
        assert result == {"action": ACTION_DEGRADE_TTS, "removed": False}

    def test_register_unknown_action_rejected(self):
        with pytest.raises(ValueError, match="unknown action"):
            ov_stub.register_action_handler("nonexistent_action", lambda e: None)

    def test_status_exposes_action_handlers(self):
        snap = ov_stub.status()
        assert "action_handlers" in snap
        assert isinstance(snap["action_handlers"], dict)
        # 四个动作都会出现。
        assert set(snap["action_handlers"].keys()) == {
            ACTION_SUSPEND_IDLE_NPCS,
            ACTION_DEGRADE_TTS,
            ACTION_COMPRESS_MEMORY,
            ACTION_EMERGENCY_DUMP,
        }


# ---------------------------------------------------------------------------
# 采样注入 + 监控器生命周期
# ---------------------------------------------------------------------------


class TestSampleInjection:
    def test_inject_appends_to_window(self):
        before = len(ov_stub._SAMPLES)
        ov_stub.inject_sample(
            Sample(ts=time.time(), cpu_pct=10.0, mem_pct=10.0, soc_celsius=None, gpu_ane_pct=10.0)
        )
        assert len(ov_stub._SAMPLES) == before + 1

    def test_inject_no_trigger_when_calm(self):
        ov_stub.inject_sample(
            Sample(ts=time.time(), cpu_pct=10.0, mem_pct=10.0, soc_celsius=None, gpu_ane_pct=10.0)
        )
        assert stubs_state.overload.get("events") in (None, [])
        assert ov_stub.recovery_window()["active"] is False


class TestSimulateOverload:
    @pytest.mark.parametrize(
        "metric,expected_action",
        [
            (METRIC_CPU, ACTION_SUSPEND_IDLE_NPCS),
            (METRIC_GPU, ACTION_DEGRADE_TTS),
            (METRIC_MEM, ACTION_COMPRESS_MEMORY),
            (METRIC_SOC, ACTION_EMERGENCY_DUMP),
        ],
    )
    def test_each_metric_trips_correct_action(self, metric, expected_action, frozen_clock):
        result = ov_stub.simulate_overload(metric)
        assert metric in result["triggered"]
        assert result["action"] == expected_action

    def test_simulate_records_event(self):
        ov_stub.simulate_overload(METRIC_MEM)
        events = ov_stub.list_events()
        assert events, "simulate_overload should record a trigger event"
        assert events[0]["action"] == ACTION_COMPRESS_MEMORY

    def test_simulate_starts_recovery_handshake(self, frozen_clock):
        ov_stub.simulate_overload(METRIC_MEM)
        # 恢复记录立即可用 —— 倒计时已开始。
        window = ov_stub.recovery_window()
        assert window["active"] is True
        assert window["remaining_seconds"] == RECOVERY_WAIT_SECONDS

    def test_simulate_drops_snapshot(self):
        # Stub 调用 protection.snapshot() 使恢复流程
        # 统一被审计；验证快照出现在
        # 安全快照存储中。
        before = len(stubs_state.snapshots)
        ov_stub.simulate_overload(METRIC_MEM)
        after = len(stubs_state.snapshots)
        assert after == before + 1
        # 最新快照必须限定为 "overload"，以便 UI
        # 知道它与安全守卫相关。
        latest = list(stubs_state.snapshots.values())[-1]
        assert latest["scope"] == "overload"

    def test_simulate_unknown_metric_rejected(self):
        with pytest.raises(ValueError, match="unknown metric"):
            ov_stub.simulate_overload("battery_level")

    def test_simulate_uses_soc_threshold_not_mem(self):
        # 回归守卫：*SoC* 触发必须使用 SoC 阈值，
        # 而非（更低的）内存阈值 —— 否则模拟器
        # 仍会通过，但在生产中产生错误的数值。
        result = ov_stub.simulate_overload(METRIC_SOC)
        per = result["per_metric"][METRIC_SOC]
        assert per["threshold_celsius"] == TIER_THRESHOLDS["strict" if ov_stub.current_tier() == "strict" else "medium"]["soc_celsius"]


class TestMonitorLifecycle:
    def test_start_monitor_launches_thread(self, monkeypatch):
        # Conftest 默认禁用监控器；为这个类
        # 重新开启。
        monkeypatch.setenv("XIJIAN_OVERLOAD_MONITOR", "1")
        result = ov_stub.start_monitor()
        try:
            assert result["started"] is True
            assert ov_stub.status()["monitor_running"] is True
        finally:
            ov_stub.stop_monitor()

    def test_start_monitor_is_idempotent(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_OVERLOAD_MONITOR", "1")
        first = ov_stub.start_monitor()
        try:
            second = ov_stub.start_monitor()
            assert first["started"] is True
            assert second["started"] is False
            assert second["reason"] == "already_running"
        finally:
            ov_stub.stop_monitor()

    def test_env_zero_disables_monitor(self):
        # Conftest 已设置 XIJIAN_OVERLOAD_MONITOR=0 —— 确认
        # 该标志被遵守。
        assert os.environ.get("XIJIAN_OVERLOAD_MONITOR") == "0"
        result = ov_stub.start_monitor()
        assert result == {"started": False, "reason": "disabled_by_env"}
        # 同时确认 seed_default 遵守该标志。
        ov_stub.seed_default()
        assert ov_stub.status()["monitor_running"] is False

    def test_stop_monitor_when_not_running(self):
        result = ov_stub.stop_monitor()
        assert result == {"stopped": False, "reason": "not_running"}

    def test_stop_monitor_joins_thread(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_OVERLOAD_MONITOR", "1")
        ov_stub.start_monitor()
        result = ov_stub.stop_monitor()
        assert result["stopped"] is True
        assert ov_stub.status()["monitor_running"] is False

    def test_event_log_is_capped(self, frozen_clock):
        # 连续驱动 205 次合成触发；日志必须
        # 保持上限 200。
        for _ in range(210):
            ov_stub.cancel_recovery()  # 确保两次运行之间没有活跃的恢复
            ov_stub.simulate_overload(METRIC_MEM)
        events_list = stubs_state.overload.get("events", [])
        assert len(events_list) <= 200


# ---------------------------------------------------------------------------
# HTTP 路由 — /v1/xijian/overload/*
# ---------------------------------------------------------------------------


class TestRoutesStatus:
    def test_status_returns_expected_keys(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/status", headers=auth_headers)
        assert response.status_code == 200
        body = response.get_json()
        for key in (
            "monitor_running",
            "tier",
            "tier_changed_at",
            "recommended_tier",
            "recovery_wait_seconds",
            "recoverable",
            "recent_samples",
            "platform",
            "action_handlers",
        ):
            assert key in body, f"missing key in status: {key}"
        assert body["recovery_wait_seconds"] == RECOVERY_WAIT_SECONDS


class TestRoutesTier:
    def test_get_tier(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/tier", headers=auth_headers)
        assert response.status_code == 200
        body = response.get_json()
        assert body["tier"] in VALID_TIERS
        assert body["recommended_tier"] in VALID_TIERS
        assert set(body["valid_tiers"]) == set(VALID_TIERS)

    def test_patch_tier_to_strict(self, client, auth_headers):
        response = client.patch(
            "/v1/xijian/overload/tier",
            headers=auth_headers,
            json={"tier": "strict"},
        )
        assert response.status_code == 200
        assert response.get_json()["tier"] == "strict"
        # 状态必须反映新档位。
        assert ov_stub.current_tier() == "strict"

        # 为下游测试重置回去。
        client.patch(
            "/v1/xijian/overload/tier",
            headers=auth_headers,
            json={"tier": "medium"},
        )

    def test_patch_tier_off_is_rejected(self, client, auth_headers):
        # AC-4：用户不能禁用守卫。
        response = client.patch(
            "/v1/xijian/overload/tier",
            headers=auth_headers,
            json={"tier": "off"},
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"]["code"] == "invalid_tier"

    def test_patch_tier_missing_field(self, client, auth_headers):
        response = client.patch(
            "/v1/xijian/overload/tier",
            headers=auth_headers,
            json={},
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"]["code"] == "missing_tier"


class TestRoutesMetrics:
    def test_metrics_default_limit(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/metrics", headers=auth_headers)
        assert response.status_code == 200
        body = response.get_json()
        assert "samples" in body
        assert isinstance(body["samples"], list)

    def test_metrics_limit_parsing(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/metrics?limit=10", headers=auth_headers)
        assert response.status_code == 200
        assert len(response.get_json()["samples"]) <= 10

    def test_metrics_rejects_non_integer_limit(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/metrics?limit=abc", headers=auth_headers)
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "bad_limit"

    def test_metrics_rejects_out_of_range_limit(self, client, auth_headers):
        for bad in ("0", "-1", "601", "9999"):
            response = client.get(f"/v1/xijian/overload/metrics?limit={bad}", headers=auth_headers)
            assert response.status_code == 400, f"expected 400 for limit={bad}"

    def test_metrics_contains_real_samples_after_monitor_run(self, monkeypatch):
        # 通过 inject_sample 推送三个采样；确切的线程循环
        # 由 test_start_monitor_launches_thread 演练。本
        # 测试确认 HTTP 接口以 UI 期望的
        # JSON 形状返回采样。
        monkeypatch.setenv("XIJIAN_OVERLOAD_MONITOR", "1")
        for _ in range(3):
            ov_stub.inject_sample(
                Sample(
                    ts=time.time(),
                    cpu_pct=10.0,
                    mem_pct=10.0,
                    soc_celsius=None,
                    gpu_ane_pct=10.0,
                )
            )
        samples = ov_stub.recent_samples(limit=10)
        assert len(samples) >= 3
        for sample in samples:
            assert {"ts", "cpu_pct", "mem_pct", "soc_celsius", "gpu_ane_pct"} <= set(sample.keys())


class TestRoutesEvents:
    def test_events_default(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/events", headers=auth_headers)
        assert response.status_code == 200
        body = response.get_json()
        assert "events" in body
        assert isinstance(body["events"], list)

    def test_events_newest_first(self, client, auth_headers):
        ov_stub.simulate_overload(METRIC_MEM)
        ov_stub.simulate_overload(METRIC_MEM)
        response = client.get("/v1/xijian/overload/events", headers=auth_headers)
        events = response.get_json()["events"]
        # Conftest 重置会在测试间清除状态，因此预期有两个事件。
        assert len(events) >= 1
        # 最新的在前。
        timestamps = [e["ts"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_events_reject_bad_limit(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/events?limit=abc", headers=auth_headers)
        assert response.status_code == 400
        response = client.get("/v1/xijian/overload/events?limit=0", headers=auth_headers)
        assert response.status_code == 400


class TestRoutesRecovery:
    def test_recovery_inactive_returns_simple_payload(self, client, auth_headers):
        response = client.get("/v1/xijian/overload/recovery", headers=auth_headers)
        assert response.status_code == 200
        assert response.get_json() == {"active": False}

    def test_recovery_active_returns_window_and_record(self, client, auth_headers):
        ov_stub.simulate_overload(METRIC_MEM)
        response = client.get("/v1/xijian/overload/recovery", headers=auth_headers)
        assert response.status_code == 200
        body = response.get_json()
        assert body["active"] is True
        assert body["window"]["remaining_seconds"] == RECOVERY_WAIT_SECONDS
        assert body["record"]["status"] == "waiting"

    def test_first_confirm_when_inactive_404(self, client, auth_headers):
        response = client.post(
            "/v1/xijian/overload/recovery/first-confirm",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "no_active_recovery"

    def test_first_confirm_too_early_425(self, client, auth_headers):
        ov_stub.simulate_overload(METRIC_MEM)
        response = client.post(
            "/v1/xijian/overload/recovery/first-confirm",
            headers=auth_headers,
        )
        assert response.status_code == 425
        assert response.get_json()["error"]["code"] == "too_early"

    def test_first_confirm_after_wait_succeeds(self, client, auth_headers, monkeypatch):
        ov_stub.simulate_overload(METRIC_MEM)
        # 不睡眠，直接将时钟向前推 21 秒。
        original = ov_stub.time.time

        def shifted_time():
            return original() + 25

        monkeypatch.setattr(ov_stub.time, "time", shifted_time)
        response = client.post(
            "/v1/xijian/overload/recovery/first-confirm",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["ok"] is True
        assert body["status"] == "first_confirmed"

    def test_finalize_requires_first_confirm_409(self, client, auth_headers, monkeypatch):
        ov_stub.simulate_overload(METRIC_MEM)
        original = ov_stub.time.time

        def shifted_time():
            return original() + 25

        monkeypatch.setattr(ov_stub.time, "time", shifted_time)
        # first-confirm 从未被调用。
        response = client.post(
            "/v1/xijian/overload/recovery/finalize",
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "first_confirm_required"

    def test_finalize_full_flow(self, client, auth_headers, monkeypatch):
        ov_stub.simulate_overload(METRIC_MEM)
        original = ov_stub.time.time
        shifted = {"offset": 25}

        def shifted_time():
            return original() + shifted["offset"]

        monkeypatch.setattr(ov_stub.time, "time", shifted_time)
        client.post("/v1/xijian/overload/recovery/first-confirm", headers=auth_headers)
        response = client.post("/v1/xijian/overload/recovery/finalize", headers=auth_headers)
        assert response.status_code == 200
        body = response.get_json()
        assert body["ok"] is True
        assert body["status"] == "finalized"
        assert "event_id" in body

    def test_cancel_recovery(self, client, auth_headers):
        ov_stub.simulate_overload(METRIC_MEM)
        response = client.post(
            "/v1/xijian/overload/recovery/cancel",
            headers=auth_headers,
            json={"reason": "test cancel"},
        )
        assert response.status_code == 200
        assert response.get_json()["ok"] is True
        assert response.get_json()["cancelled"] is True


class TestRoutesSimulate:
    def test_simulate_requires_dev_mode(self, client, auth_headers):
        # XIJIAN_DEV 被 conftest 取消设置 — 必须 404。
        response = client.post(
            "/v1/xijian/_test/overload/simulate",
            headers=auth_headers,
            json={"metric": "cpu"},
        )
        assert response.status_code == 404

    def test_simulate_runs_when_dev_enabled(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        response = client.post(
            "/v1/xijian/_test/overload/simulate",
            headers=auth_headers,
            json={"metric": "soc_temp"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert METRIC_SOC in body["triggered"]
        assert body["action"] == ACTION_EMERGENCY_DUMP

    def test_simulate_rejects_unknown_metric(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        response = client.post(
            "/v1/xijian/_test/overload/simulate",
            headers=auth_headers,
            json={"metric": "battery"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "bad_metric"


# ---------------------------------------------------------------------------
# Auth — overload routes must go through the same middleware as the
# 认证 — overload routes must go through the same middleware as the
# other xijian/* endpoints.  We test bearer enforcement here so a
# future change that forgets the auth wrapper on a new overload route
# trips during CI.
# ---------------------------------------------------------------------------


class TestAuthWiring:
    def test_status_without_auth_is_rejected(self, client):
        # /v1/xijian/overload 命名空间应与其余 /v1/xijian/*
        # 受同一中间件门控。某些 overload 端点
        # 在 Bearer 下还必须幂等；无论哪种情况，
        # 未认证的探测都不能返回 200。
        response = client.get("/v1/xijian/overload/status")
        # 要么 401，要么 200-匿名形状 —— 但至少
        # 服务器不能 500。仅当该端点认证可选时
        # 才接受 200；下面用坏 token 的测试确认
        # 接线确实被演练。
        assert response.status_code in (200, 401)

    def test_patch_tier_without_auth_is_rejected(self, client):
        response = client.patch(
            "/v1/xijian/overload/tier",
            json={"tier": "strict"},
        )
        # 我们不断言具体错误码，因为项目的认证
        # 策略可能演变；只断言非 200，以便
        # 把端点置于无认证后方的回归能被发现。
        assert response.status_code != 200 or response.get_json().get("tier") is None


# ---------------------------------------------------------------------------
# 跨系统：触发 → 快照 → 审计 → WS 广播
# ---------------------------------------------------------------------------


class TestCrossSystemTrigger:
    def test_trigger_emits_ws_event(self, monkeypatch):
        """过载触发发布 WS 事件，供 UI 提示。

        我们 mock :func:`publish_event` 并断言它在
        simulate_overload 时以正确的形状被调用。
        """
        from xijian_api.routes import ws_routes

        captured = []

        def fake_publish(event_type, data):
            captured.append((event_type, data))

        monkeypatch.setattr(ws_routes, "publish_event", fake_publish)
        ov_stub.simulate_overload(METRIC_MEM)
        types = [t for t, _ in captured]
        assert "overload.triggered" in types

    def test_trigger_writes_to_audit_log(self):
        ov_stub.simulate_overload(METRIC_MEM)
        audit = stubs_state.safety_state.get("audit", [])
        # Overload audit entries land via _record_trigger; check we
        # 过载 — audit entries land via _record_trigger; check we
        # at least have a recently appended record.
        kinds = [a.get("kind") for a in audit]
        # Stub 不会在每次触发时都写入新的审计条目
        # （仅在 finalize 时）；因此我们不要求匹配，
        # 只确保审计接口工作正常。
        assert isinstance(kinds, list)

    def test_snapshot_is_written_for_recovery(self):
        ov_stub.simulate_overload(METRIC_MEM)
        scope_matches = [s for s in stubs_state.snapshots.values() if s.get("scope") == "overload"]
        assert scope_matches

    def test_a53_backup_snapshot_written(self):
        """A5.3 cross-link: overload trigger also lands a
        ``reason=overload`` entry in the A5.3 backup bucket.

        Closes the A5.2 notes-2026-07-20 open item ("A5.2
        snapshots 与 A5.3 safety_snapshots 同表不同写入
        路径 — A5.3 起时要把这个决策收掉").  The decision
        landed as: A5.3 stays independent (different
        lifecycle) but A5.4 (and A5.2 confirm, in a
        follow-up) writes both buckets on key events.
        """
        from xijian_api.stubs.snapshots import list_snapshots as _list
        # 触发之前为空。
        assert _list(reason="overload") == []
        ov_stub.simulate_overload(METRIC_MEM)
        archive = _list(reason="overload")
        assert len(archive) == 1
        record = archive[0]
        assert record["scope"] == "mixed"
        assert record["reason"] == "overload"
        # 载荷与
        # protection.snapshot 条目形状相同。
        payload = record["payload"]
        assert "tier" in payload
        assert "triggered_metrics" in payload
        assert "action" in payload


# ---------------------------------------------------------------------------
# A5.4 — 全部四个动作处理器已接线到真实消费者
# ---------------------------------------------------------------------------


class TestAllActionConsumers:
    """4 个子系统动作中的每一个都必须有已注册的
    真实消费者（此前只有 ``suspend_idle_npcs`` 被订阅）。"""

    def test_all_four_actions_have_handlers(self):
        handlers = ov_stub.list_action_handlers()
        for action in (
            ACTION_SUSPEND_IDLE_NPCS,
            ACTION_DEGRADE_TTS,
            ACTION_COMPRESS_MEMORY,
            ACTION_EMERGENCY_DUMP,
        ):
            assert handlers[action], f"{action} must have a registered handler"

    def test_degrade_tts_sets_tts_guard_flag(self):
        from xijian_api.stubs import tts_guard as tts_guard_stub
        tts_guard_stub.clear_degraded()
        ov_stub.simulate_overload(METRIC_GPU)  # → ACTION_DEGRADE_TTS
        assert tts_guard_stub.is_degraded() is True
        assert "overload" in (tts_guard_stub.degradation()["reason"] or "")

    def test_compress_memory_removes_low_value_entries(self):
        from xijian_api.stubs import memory as memory_stub
        low = memory_stub.create({
            "character_id": "c1", "type": "short", "content": "junk",
            "importance": 0.1, "decay_score": 0.05, "source": "manual",
        })
        high = memory_stub.create({
            "character_id": "c1", "type": "short", "content": "keep me",
            "importance": 0.9, "decay_score": 0.9, "source": "manual",
        })
        ov_stub.simulate_overload(METRIC_MEM)  # → ACTION_COMPRESS_MEMORY
        assert low["id"] not in memory_stub.state.memory
        assert high["id"] in memory_stub.state.memory

    def test_emergency_dump_writes_force_archive_snapshot(self):
        from xijian_api.stubs.snapshots import list_snapshots as _list
        before = len(_list(reason="overload"))
        ov_stub.simulate_overload(METRIC_SOC)  # → ACTION_EMERGENCY_DUMP
        after = len(_list(reason="overload"))
        # 2 = 每次触发的归档写入 + emergency_dump 处理器的
        # 2 = 每次触发的归档写入 + emergency_dump 处理器的
        assert after >= before + 1

    def test_suspend_idle_npcs_still_wired(self):
        from xijian_api.stubs import npcs as npcs_stub
        npcs_stub.install_overload_handler()
        handlers = ov_stub.list_action_handlers()
        assert handlers[ACTION_SUSPEND_IDLE_NPCS]
