"""Tests for A3.2 character state system (``stubs.character_state`` +
``/v1/xijian/characters/<id>/state*``).

Three layers, mirroring the overload test surface:

* **Pure helpers** — :func:`clamp`, :func:`decay_amount`,
  :func:`compute_target_status`, :func:`resolve_behavior_bindings`.
  No I/O, no thread, no global state.
* **State + status machine** — drive the stubs directly, with a
  freezable clock so we can verify the 5 min / 10 min dwell
  transitions deterministically.
* **Routes** — go through the Flask test client, confirm wiring
  end-to-end (auth, error formats, status codes).
"""

from __future__ import annotations

import time

import pytest

from xijian_api.stubs import character_state as cs_stub
from xijian_api.stubs.character_state import (
    DEFAULT_BEHAVIOR_BINDINGS,
    DEFAULT_DECAY_RATES,
    DEFAULT_LOW_THRESHOLDS,
    DEFAULT_MAX_HEALTH,
    DEFAULT_MAX_HUNGER,
    DEFAULT_MAX_MOOD,
    DEFAULT_MAX_THIRST,
    DEFAULT_RECOVERY_THRESHOLDS,
    DEFAULT_TICK_INTERVAL_SECONDS,
    DEFAULT_TRANSITION_DWELL_SECONDS,
    HIGH_MOOD_LOW_HUNGER_HUNGER,
    HIGH_MOOD_LOW_HUNGER_MOOD,
    LOG_MAX_ENTRIES,
    STATUS_CRITICAL,
    STATUS_HEALTHY,
    STATUS_HUNGRY,
    STATUS_RECOVERING,
    STATUS_SICK,
    STATUS_THIRSTY,
    VALUE_FIELDS,
)
from xijian_api.stubs import state as stubs_state


# ---------------------------------------------------------------------------
# Clock fixture — freezable, advance-able
# ---------------------------------------------------------------------------


@pytest.fixture()
def frozen_clock(monkeypatch):
    current = {"t": 1_000_000.0}

    def fake_time() -> float:
        return current["t"]

    monkeypatch.setattr(cs_stub.time, "time", fake_time)

    class Clock:
        def now(self) -> float:
            return current["t"]

        def advance(self, seconds: float) -> None:
            current["t"] += seconds

    return Clock()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestClamp:
    def test_within_range(self):
        assert cs_stub.clamp(50.0, 100.0) == 50.0

    def test_below_zero_clamped(self):
        assert cs_stub.clamp(-5.0, 100.0) == 0.0

    def test_above_max_clamped(self):
        assert cs_stub.clamp(150.0, 100.0) == 100.0

    def test_zero_passes_through(self):
        assert cs_stub.clamp(0.0, 100.0) == 0.0

    def test_max_passes_through(self):
        assert cs_stub.clamp(100.0, 100.0) == 100.0


class TestDecayAmount:
    def test_zero_dt_returns_zero(self):
        assert cs_stub.decay_amount(2.0, 0) == 0.0

    def test_negative_dt_returns_zero(self):
        # Backwards clock jump should not refill stats.
        assert cs_stub.decay_amount(2.0, -10) == 0.0

    def test_one_hour_decay(self):
        # 2 / hour × 1 hour = 2.0
        assert cs_stub.decay_amount(2.0, 3600) == 2.0

    def test_proportional_to_dt(self):
        # 30 min → 1.0
        assert cs_stub.decay_amount(2.0, 1800) == 1.0

    def test_modifiers_multiply(self):
        # 2 / hour × 1 hour × 0.5 × 1.2 = 1.2
        assert cs_stub.decay_amount(
            2.0, 3600, time_modifier=0.5, activity_modifier=1.0, world_modifier=1.2
        ) == pytest.approx(1.2)


class TestComputeTargetStatus:
    def test_initial_state_is_healthy(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        cfg = cs_stub._default_config("c1")
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_HEALTHY

    def test_low_hunger_makes_hungry(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["hunger"] = 25.0
        record["status"] = STATUS_HEALTHY
        cfg = cs_stub._default_config("c1")
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_HUNGRY

    def test_low_thirst_makes_thirsty(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["thirst"] = 20.0
        cfg = cs_stub._default_config("c1")
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_THIRSTY

    def test_health_30_or_below_makes_sick(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["health"] = 25.0
        cfg = cs_stub._default_config("c1")
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_SICK

    def test_health_zero_or_below_is_critical(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["health"] = 0.0
        cfg = cs_stub._default_config("c1")
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_CRITICAL

    def test_critical_wins_over_hungry(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["health"] = 0.0
        record["hunger"] = 10.0
        record["status"] = STATUS_HUNGRY
        cfg = cs_stub._default_config("c1")
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_CRITICAL

    def test_hungry_to_healthy_requires_dwell(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["status"] = STATUS_HUNGRY
        record["hunger"] = 80.0
        record["status_changed_at"] = frozen_clock.now()
        cfg = cs_stub._default_config("c1")
        # Right after entering Hungry + hunger > 60: still Hungry (dwell not met).
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_HUNGRY
        # 4 minutes later — still under 5 min dwell.
        frozen_clock.advance(4 * 60)
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_HUNGRY
        # 5 min + 1s: dwell met → Healthy.
        frozen_clock.advance(60 + 1)
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_HEALTHY

    def test_recovering_to_healthy_requires_10_min_dwell(self, frozen_clock):
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["status"] = STATUS_RECOVERING
        record["health"] = 90.0
        record["status_changed_at"] = frozen_clock.now()
        cfg = cs_stub._default_config("c1")
        frozen_clock.advance(9 * 60)
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_RECOVERING
        frozen_clock.advance(60 + 1)
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_HEALTHY

    def test_sick_stays_sick_without_recover_event(self, frozen_clock):
        # Sick must not auto-recover; spec says "触发恢复事件".
        record = cs_stub._default_state_record("c1", now=frozen_clock.now())
        record["status"] = STATUS_SICK
        record["health"] = 80.0  # climbed back above 30
        record["status_changed_at"] = frozen_clock.now()
        cfg = cs_stub._default_config("c1")
        frozen_clock.advance(60 * 60)
        assert cs_stub.compute_target_status(record, cfg, frozen_clock.now()) == STATUS_SICK


class TestResolveBehaviorBindings:
    def test_healthy_no_special_binding(self):
        record = cs_stub._default_state_record("c1", now=1.0)
        record["status"] = STATUS_HEALTHY
        record["mood"] = 50.0
        cfg = cs_stub._default_config("c1")
        bindings = cs_stub.resolve_behavior_bindings(record, cfg)
        # Healthy has no binding; high_mood_low_hunger doesn't apply.
        assert bindings == []

    def test_hungry_returns_hungry_binding(self):
        record = cs_stub._default_state_record("c1", now=1.0)
        record["status"] = STATUS_HUNGRY
        record["mood"] = 50.0
        cfg = cs_stub._default_config("c1")
        bindings = cs_stub.resolve_behavior_bindings(record, cfg)
        assert len(bindings) == 1
        assert bindings[0]["name"] == STATUS_HUNGRY
        assert bindings[0]["trigger"] == DEFAULT_BEHAVIOR_BINDINGS["hungry"]["trigger"]

    def test_high_mood_low_hunger_edge_case(self):
        # Spec: "mood ≥ 95 且 hunger < 20 → 角色可能触发自定义台词/动作"
        record = cs_stub._default_state_record("c1", now=1.0)
        record["mood"] = HIGH_MOOD_LOW_HUNGER_MOOD
        record["hunger"] = HIGH_MOOD_LOW_HUNGER_HUNGER - 1
        record["status"] = STATUS_HEALTHY
        cfg = cs_stub._default_config("c1")
        bindings = cs_stub.resolve_behavior_bindings(record, cfg)
        names = [b["name"] for b in bindings]
        assert "high_mood_low_hunger" in names
        # It should come first so the more expressive animation wins ties.
        assert bindings[0]["name"] == "high_mood_low_hunger"

    def test_high_mood_with_full_hunger_does_not_trigger(self):
        # mood is high but hunger is fine — no special binding.
        record = cs_stub._default_state_record("c1", now=1.0)
        record["mood"] = 99.0
        record["hunger"] = 80.0
        record["status"] = STATUS_HEALTHY
        cfg = cs_stub._default_config("c1")
        bindings = cs_stub.resolve_behavior_bindings(record, cfg)
        assert bindings == []


# ---------------------------------------------------------------------------
# State record CRUD + config
# ---------------------------------------------------------------------------


class TestStateRecord:
    def test_get_or_init_creates_with_defaults(self):
        record = cs_stub.get_or_init_state("c1")
        assert record["character_id"] == "c1"
        assert record["hunger"] == 80.0
        assert record["thirst"] == 80.0
        assert record["health"] == 100.0
        assert record["mood"] == 70.0
        assert record["status"] == STATUS_HEALTHY

    def test_get_or_init_returns_same_record(self):
        r1 = cs_stub.get_or_init_state("c1")
        r2 = cs_stub.get_or_init_state("c1")
        assert r1 is r2

    def test_get_state_returns_none_for_unknown(self):
        assert cs_stub.get_state("ghost") is None

    def test_get_or_init_config_creates_with_defaults(self):
        cfg = cs_stub.get_or_init_config("c1")
        assert cfg["decay_per_hour"]["hunger"] == 2.0
        assert cfg["thresholds"]["hunger"] == 30.0
        assert cfg["behavior_bindings"]["hungry"]["trigger"] == "low_energy"


# ---------------------------------------------------------------------------
# apply_field_change
# ---------------------------------------------------------------------------


class TestApplyFieldChange:
    def test_clamps_negative_to_zero(self):
        record = cs_stub.apply_field_change("c1", "hunger", -10.0)
        assert record["hunger"] == 0.0

    def test_clamps_above_max(self):
        record = cs_stub.apply_field_change("c1", "health", 999.0)
        assert record["health"] == DEFAULT_MAX_HEALTH

    def test_writes_log_entry(self):
        cs_stub.apply_field_change("c1", "hunger", 50.0, reason="dialogue", ref_id="ref_1")
        entries = cs_stub.list_log("c1")
        assert len(entries) == 1
        assert entries[0]["field"] == "hunger"
        assert entries[0]["old_value"] == 80.0
        assert entries[0]["new_value"] == 50.0
        assert entries[0]["reason"] == "dialogue"
        assert entries[0]["ref_id"] == "ref_1"

    def test_transitions_to_hungry(self):
        record = cs_stub.apply_field_change("c1", "hunger", 20.0)
        assert record["status"] == STATUS_HUNGRY

    def test_transitions_to_sick_via_health(self):
        record = cs_stub.apply_field_change("c1", "health", 20.0)
        assert record["status"] == STATUS_SICK

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="unknown value field"):
            cs_stub.apply_field_change("c1", "battery", 50.0)

    def test_no_log_when_value_unchanged(self):
        # Apply the same value as the current — no log entry.
        cs_stub.apply_field_change("c1", "hunger", 80.0)
        assert cs_stub.list_log("c1") == []


class TestApplyPatch:
    def test_multi_field(self):
        record = cs_stub.apply_patch(
            "c1", {"hunger": 30.0, "thirst": 30.0, "health": 100.0}
        )
        assert record["hunger"] == 30.0
        assert record["thirst"] == 30.0

    def test_ignores_unknown_keys(self):
        record = cs_stub.apply_patch("c1", {"battery": 50.0, "hunger": 60.0})
        assert record["hunger"] == 60.0
        assert "battery" not in record

    def test_max_field_clamps_value(self):
        cs_stub.apply_patch("c1", {"max_hunger": 50.0, "hunger": 80.0})
        # 80 > 50 → clamp to 50.
        record = cs_stub.get_state("c1")
        assert record["max_hunger"] == 50.0
        assert record["hunger"] == 50.0


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------


class TestTickCharacter:
    def test_no_decay_when_dt_zero(self, frozen_clock):
        cs_stub.apply_field_change("c1", "hunger", 50.0, now=frozen_clock.now())
        before = cs_stub.get_state("c1")["hunger"]
        result = cs_stub.tick_character("c1", now=frozen_clock.now())
        assert result["dt_seconds"] == 0
        assert cs_stub.get_state("c1")["hunger"] == before

    def test_decay_after_one_hour(self, frozen_clock):
        # hunger decay 2 / hour, dt = 1h → 2.0
        cs_stub.apply_field_change("c1", "hunger", 80.0, now=frozen_clock.now())
        frozen_clock.advance(3600)
        result = cs_stub.tick_character("c1", now=frozen_clock.now())
        assert cs_stub.get_state("c1")["hunger"] == 78.0
        assert len(result["changes"]) >= 1

    def test_decay_writes_tick_log(self, frozen_clock):
        cs_stub.apply_field_change("c1", "hunger", 80.0, now=frozen_clock.now())
        frozen_clock.advance(3600)
        cs_stub.tick_character("c1", now=frozen_clock.now())
        log = cs_stub.list_log("c1")
        reasons = [e["reason"] for e in log]
        assert "tick" in reasons

    def test_modifier_zero_floors_decay(self, frozen_clock):
        # world_modifier = 0 → no decay.
        cs_stub.apply_field_change("c1", "hunger", 50.0, now=frozen_clock.now())
        cs_stub.set_modifier("c1", {"world_modifier": 0.0})
        # 0.0 is clamped to 0.01 inside set_modifier to keep the
        # math from dividing by zero, so we still see a tiny decay.
        frozen_clock.advance(3600)
        before = cs_stub.get_state("c1")["hunger"]
        cs_stub.tick_character("c1", now=frozen_clock.now())
        after = cs_stub.get_state("c1")["hunger"]
        # Should be slightly less, not the full 2.0 / hour.
        assert after < before
        assert (before - after) < 1.0

    def test_activity_modifier_doubles_decay(self, frozen_clock):
        cs_stub.apply_field_change("c1", "hunger", 80.0, now=frozen_clock.now())
        cs_stub.set_modifier("c1", {"activity_modifier": 2.0})
        frozen_clock.advance(3600)
        cs_stub.tick_character("c1", now=frozen_clock.now())
        # 2 / hour × 2.0 modifier × 1 hour = 4.0
        assert cs_stub.get_state("c1")["hunger"] == 76.0

    def test_tick_transitions_to_hungry_via_decay(self, frozen_clock):
        # Start with hunger = 32, decay for 1h → 30, transitions.
        cs_stub.apply_field_change("c1", "hunger", 32.0, now=frozen_clock.now())
        # Adjust decay to 4 / hour so 1h drops hunger by 4.
        cs_stub.get_or_init_config("c1")["decay_per_hour"]["hunger"] = 4.0
        frozen_clock.advance(3600)
        result = cs_stub.tick_character("c1", now=frozen_clock.now())
        assert cs_stub.get_state("c1")["hunger"] == 28.0
        assert cs_stub.get_state("c1")["status"] == STATUS_HUNGRY
        # The status change appears in ``changes``.
        change_fields = [c["field"] for c in result["changes"]]
        assert "status" in change_fields

    def test_tick_all_iterates_every_character(self, frozen_clock):
        for cid in ("c1", "c2", "c3"):
            cs_stub.apply_field_change(cid, "hunger", 80.0, now=frozen_clock.now())
        frozen_clock.advance(3600)
        results = cs_stub.tick_all(now=frozen_clock.now())
        assert len(results) == 3
        for r in results:
            assert r["dt_seconds"] == 3600


# ---------------------------------------------------------------------------
# can_dialogue + force_recover + enter_recovering
# ---------------------------------------------------------------------------


class TestCanDialogue:
    def test_healthy_can_dialogue(self):
        cs_stub.apply_field_change("c1", "health", 100.0)
        assert cs_stub.can_dialogue("c1") is True

    def test_sick_can_dialogue(self):
        # Per the spec only health <= 0 (Critical) blocks dialogue.
        cs_stub.apply_field_change("c1", "health", 20.0)
        assert cs_stub.can_dialogue("c1") is True

    def test_critical_cannot_dialogue(self):
        cs_stub.apply_field_change("c1", "health", 0.0)
        assert cs_stub.can_dialogue("c1") is False

    def test_unknown_character_can_dialogue(self):
        # Default-true: we never want a missing state record to
        # accidentally lock a character out.
        assert cs_stub.can_dialogue("ghost") is True


class TestForceRecover:
    def test_lifts_critical_to_healthy(self, frozen_clock):
        cs_stub.apply_field_change("c1", "health", 0.0, now=frozen_clock.now())
        assert cs_stub.get_state("c1")["status"] == STATUS_CRITICAL
        record = cs_stub.force_recover("c1", reason="admin")
        assert record["status"] == STATUS_HEALTHY
        assert record["health"] == DEFAULT_MAX_HEALTH

    def test_writes_log(self, frozen_clock):
        cs_stub.apply_field_change("c1", "health", 0.0, now=frozen_clock.now())
        cs_stub.force_recover("c1", reason="admin")
        log = cs_stub.list_log("c1")
        reasons = [e["reason"] for e in log]
        assert "admin" in reasons


class TestEnterRecovering:
    def test_sick_to_recovering(self, frozen_clock):
        cs_stub.apply_field_change("c1", "health", 20.0, now=frozen_clock.now())
        assert cs_stub.get_state("c1")["status"] == STATUS_SICK
        record = cs_stub.enter_recovering("c1", reason="world_event")
        assert record["status"] == STATUS_RECOVERING

    def test_no_op_on_healthy(self, frozen_clock):
        cs_stub.apply_field_change("c1", "health", 100.0, now=frozen_clock.now())
        before = cs_stub.get_state("c1")["status"]
        record = cs_stub.enter_recovering("c1", reason="world_event")
        assert record["status"] == before


# ---------------------------------------------------------------------------
# Modifiers
# ---------------------------------------------------------------------------


class TestModifiers:
    def test_set_modifier_returns_active(self):
        mods = cs_stub.set_modifier("c1", {"time_modifier": 1.5, "world_modifier": 0.5})
        assert mods["time_modifier"] == 1.5
        assert mods["world_modifier"] == 0.5
        assert mods["activity_modifier"] == 1.0  # default untouched

    def test_set_modifier_clamps_negative(self):
        mods = cs_stub.set_modifier("c1", {"time_modifier": -1.0})
        assert mods["time_modifier"] == 0.01

    def test_set_modifier_clamps_above_eight(self):
        mods = cs_stub.set_modifier("c1", {"time_modifier": 100.0})
        assert mods["time_modifier"] == 8.0

    def test_set_modifier_ignores_unknown(self):
        mods = cs_stub.set_modifier("c1", {"nonsense_modifier": 5.0})
        assert "nonsense_modifier" not in mods

    def test_clear_modifier_resets_to_one(self):
        cs_stub.set_modifier("c1", {"time_modifier": 2.0, "activity_modifier": 0.5})
        mods = cs_stub.clear_modifier("c1", "time_modifier")
        assert mods["time_modifier"] == 1.0
        assert mods["activity_modifier"] == 0.5  # untouched


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


class TestLog:
    def test_log_newest_first(self, frozen_clock):
        cs_stub.apply_field_change("c1", "hunger", 50.0, now=frozen_clock.now())
        frozen_clock.advance(1)
        cs_stub.apply_field_change("c1", "hunger", 40.0, now=frozen_clock.now())
        log = cs_stub.list_log("c1")
        assert log[0]["new_value"] == 40.0
        assert log[1]["new_value"] == 50.0

    def test_log_capped_at_max(self, frozen_clock):
        for _ in range(LOG_MAX_ENTRIES + 50):
            cs_stub.apply_field_change("c1", "hunger", 50.0, now=frozen_clock.now())
        log = cs_stub.list_log("c1", limit=10_000)
        assert len(log) <= LOG_MAX_ENTRIES

    def test_list_log_limit(self, frozen_clock):
        for i in range(20):
            cs_stub.apply_field_change("c1", "hunger", 50.0 - i, now=frozen_clock.now())
        assert len(cs_stub.list_log("c1", limit=5)) == 5


# ---------------------------------------------------------------------------
# Status handler registry
# ---------------------------------------------------------------------------


class TestStatusHandlers:
    def test_register_and_fire(self, frozen_clock):
        captured = []
        cs_stub.register_status_handler(STATUS_HUNGRY, lambda e: captured.append(e))
        cs_stub.apply_field_change("c1", "hunger", 20.0, now=frozen_clock.now())
        assert len(captured) == 1
        assert captured[0]["new_value"] == STATUS_HUNGRY
        # Cleanup
        handlers = cs_stub._STATUS_HANDLERS[STATUS_HUNGRY]
        cs_stub._STATUS_HANDLERS[STATUS_HUNGRY] = []

    def test_unregister_removes(self, frozen_clock):
        captured = []
        h = lambda e: captured.append(e)
        cs_stub.register_status_handler(STATUS_HUNGRY, h)
        result = cs_stub.unregister_status_handler(STATUS_HUNGRY, h)
        assert result == {"status": STATUS_HUNGRY, "removed": True}
        cs_stub.apply_field_change("c1", "hunger", 20.0, now=frozen_clock.now())
        assert captured == []

    def test_buggy_handler_does_not_break_apply(self, frozen_clock):
        def bad(_e):
            raise RuntimeError("boom")

        cs_stub.register_status_handler(STATUS_HUNGRY, bad)
        # Must not raise.
        record = cs_stub.apply_field_change("c1", "hunger", 20.0, now=frozen_clock.now())
        assert record["status"] == STATUS_HUNGRY
        cs_stub._STATUS_HANDLERS[STATUS_HUNGRY] = []


# ---------------------------------------------------------------------------
# Tick thread lifecycle
# ---------------------------------------------------------------------------


class TestTickLifecycle:
    def test_start_is_idempotent(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_STATE_TICK", "1")
        first = cs_stub.start_tick()
        try:
            second = cs_stub.start_tick()
            assert first["started"] is True
            assert second["started"] is False
            assert second["reason"] == "already_running"
        finally:
            cs_stub.stop_tick()

    def test_env_zero_disables(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_STATE_TICK", "0")
        result = cs_stub.start_tick()
        assert result == {"started": False, "reason": "disabled_by_env"}

    def test_stop_when_not_running(self):
        result = cs_stub.stop_tick()
        assert result == {"stopped": False, "reason": "not_running"}

    def test_tick_status_reflects_running(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_STATE_TICK", "1")
        cs_stub.start_tick()
        try:
            assert cs_stub.tick_status()["running"] is True
        finally:
            cs_stub.stop_tick()
        assert cs_stub.tick_status()["running"] is False

    def test_interval_env_override(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_STATE_TICK_SECONDS", "5")
        assert cs_stub._current_interval() == 5.0
        # Floor of 1 s.
        monkeypatch.setenv("XIJIAN_STATE_TICK_SECONDS", "0.1")
        assert cs_stub._current_interval() == 1.0

    def test_seed_default_starts_thread(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_STATE_TICK", "1")
        cs_stub.seed_default()
        try:
            assert cs_stub.tick_status()["running"] is True
        finally:
            cs_stub.stop_tick()


# ---------------------------------------------------------------------------
# WS broadcast
# ---------------------------------------------------------------------------


class TestWSBroadcast:
    def test_publish_state_change_calls_publish_event(self, monkeypatch, frozen_clock):
        from xijian_api.routes import ws_routes

        captured = []
        monkeypatch.setattr(ws_routes, "publish_event", lambda t, d: captured.append((t, d)))
        cs_stub.apply_field_change("c1", "hunger", 20.0, now=frozen_clock.now())
        types = [t for t, _ in captured]
        assert "character.state.changed" in types


# ---------------------------------------------------------------------------
# Summary view
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_for_unknown_character_is_none(self):
        assert cs_stub.summary("ghost") is None

    def test_summary_has_all_keys(self, frozen_clock):
        cs_stub.apply_field_change("c1", "hunger", 60.0, now=frozen_clock.now())
        s = cs_stub.summary("c1")
        for key in (
            "character_id", "values", "max", "status",
            "status_changed_at", "last_updated", "can_dialogue",
            "active_behavior", "modifiers",
        ):
            assert key in s
        assert s["values"]["hunger"] == 60.0
        assert s["max"]["hunger"] == DEFAULT_MAX_HUNGER


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_get_state_includes_a32_fields(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/char_yuki/state", headers=auth_headers
        )
        assert response.status_code == 200
        body = response.get_json()
        # Legacy fields still present.
        assert "affection" in body
        assert "mood" in body
        # A3.2 fields merged in (state was never touched, so a
        # summary won't exist and we fall back to v1 shape).
        # Touch the state and re-fetch.
        client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"hunger": 50.0},
        )
        response = client.get(
            "/v1/xijian/characters/char_yuki/state", headers=auth_headers
        )
        body = response.get_json()
        assert body.get("values", {}).get("hunger") == 50.0
        assert body.get("status") == STATUS_HEALTHY

    def test_post_state_passes_a32_fields(self, client, auth_headers):
        response = client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"hunger": 30.0, "thirst": 25.0, "health": 100.0},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["values"]["hunger"] == 30.0
        assert body["values"]["thirst"] == 25.0

    def test_post_state_via_mood_value(self, client, auth_headers):
        response = client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"mood_value": 50.0},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["values"]["mood"] == 50.0
        # Legacy ``mood`` text field untouched.
        assert body["mood"] == "neutral"

    def test_post_state_clamps(self, client, auth_headers):
        response = client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"hunger": 999.0, "health": -5.0},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["values"]["hunger"] == DEFAULT_MAX_HUNGER
        assert body["values"]["health"] == 0.0

    def test_get_state_unknown_404(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/ghost/state", headers=auth_headers
        )
        assert response.status_code == 404

    def test_post_state_unknown_404(self, client, auth_headers):
        response = client.post(
            "/v1/xijian/characters/ghost/state",
            headers=auth_headers,
            json={"hunger": 50.0},
        )
        assert response.status_code == 404

    def test_get_state_config(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/char_yuki/state/config", headers=auth_headers
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "decay_per_hour" in body
        assert body["decay_per_hour"]["hunger"] == DEFAULT_DECAY_RATES["hunger"]

    def test_patch_state_config(self, client, auth_headers):
        response = client.patch(
            "/v1/xijian/characters/char_yuki/state/config",
            headers=auth_headers,
            json={"decay_per_hour": {"hunger": 5.0}},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["decay_per_hour"]["hunger"] == 5.0

    def test_patch_state_config_merges_bindings(self, client, auth_headers):
        response = client.patch(
            "/v1/xijian/characters/char_yuki/state/config",
            headers=auth_headers,
            json={"behavior_bindings": {"hungry": {"motion": "stretch"}}},
        )
        assert response.status_code == 200
        body = response.get_json()
        # Original trigger preserved, motion replaced.
        assert body["behavior_bindings"]["hungry"]["trigger"] == "low_energy"
        assert body["behavior_bindings"]["hungry"]["motion"] == "stretch"

    def test_get_state_log(self, client, auth_headers):
        # Touch state to write a log entry.
        client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"hunger": 50.0},
        )
        response = client.get(
            "/v1/xijian/characters/char_yuki/state/log", headers=auth_headers
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "entries" in body
        assert len(body["entries"]) >= 1

    def test_get_state_log_rejects_bad_limit(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/char_yuki/state/log?limit=abc",
            headers=auth_headers,
        )
        assert response.status_code == 400
        response = client.get(
            "/v1/xijian/characters/char_yuki/state/log?limit=0",
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_post_state_tick_requires_dev_mode(self, client, auth_headers):
        response = client.post(
            "/v1/xijian/characters/char_yuki/state/tick",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_post_state_tick_runs_when_dev_enabled(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        response = client.post(
            "/v1/xijian/characters/char_yuki/state/tick",
            headers=auth_headers,
        )
        assert response.status_code == 200

    def test_post_state_tick_with_field(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        response = client.post(
            "/v1/xijian/characters/char_yuki/state/tick",
            headers=auth_headers,
            json={"field": "hunger", "value": 25.0},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["applied"]["hunger"] == 25.0

    def test_post_state_recover(self, client, auth_headers):
        # Drop health to 0 to enter Critical, then recover.
        client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"health": 0.0},
        )
        response = client.post(
            "/v1/xijian/characters/char_yuki/state/recover",
            headers=auth_headers,
            json={"reason": "admin"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == STATUS_HEALTHY
        assert body["health"] == DEFAULT_MAX_HEALTH

    def test_post_state_recovering(self, client, auth_headers):
        # Drop health to 20 to enter Sick, then advance to Recovering.
        client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"health": 20.0},
        )
        response = client.post(
            "/v1/xijian/characters/char_yuki/state/recovering",
            headers=auth_headers,
            json={"reason": "world"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == STATUS_RECOVERING

    def test_put_state_modifier(self, client, auth_headers):
        response = client.put(
            "/v1/xijian/characters/char_yuki/state/modifier",
            headers=auth_headers,
            json={"time_modifier": 2.0, "activity_modifier": 0.5},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["modifiers"]["time_modifier"] == 2.0
        assert body["modifiers"]["activity_modifier"] == 0.5

    def test_delete_state_modifier(self, client, auth_headers):
        client.put(
            "/v1/xijian/characters/char_yuki/state/modifier",
            headers=auth_headers,
            json={"time_modifier": 2.0},
        )
        response = client.delete(
            "/v1/xijian/characters/char_yuki/state/modifier?keys=time_modifier",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["modifiers"]["time_modifier"] == 1.0

    def test_get_state_behavior(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/char_yuki/state/behavior",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "behavior" in body


class TestRoutesUnknown:
    def test_get_state_config_unknown_404(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/ghost/state/config", headers=auth_headers
        )
        assert response.status_code == 404

    def test_patch_state_config_unknown_404(self, client, auth_headers):
        response = client.patch(
            "/v1/xijian/characters/ghost/state/config",
            headers=auth_headers,
            json={"decay_per_hour": {"hunger": 1.0}},
        )
        assert response.status_code == 404

    def test_get_state_log_unknown_404(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/ghost/state/log", headers=auth_headers
        )
        assert response.status_code == 404

    def test_get_state_behavior_unknown_404(self, client, auth_headers):
        response = client.get(
            "/v1/xijian/characters/ghost/state/behavior",
            headers=auth_headers,
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# End-to-end: state machine + decay + recover
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_health_drop_under_30_enters_sick(self, client, auth_headers):
        # Update health to 20.
        response = client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"health": 20.0},
        )
        body = response.get_json()
        assert body["status"] == STATUS_SICK

    def test_can_dialogue_false_when_critical(self, client, auth_headers):
        client.post(
            "/v1/xijian/characters/char_yuki/state",
            headers=auth_headers,
            json={"health": 0.0},
        )
        response = client.get(
            "/v1/xijian/characters/char_yuki/state", headers=auth_headers
        )
        assert response.get_json()["can_dialogue"] is False
