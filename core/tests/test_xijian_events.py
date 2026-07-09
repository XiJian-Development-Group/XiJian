"""Tests for ``stubs.events`` (A4.1) and ``/v1/xijian/events*``.

A4.1 says the world is allowed to "naturally produce common events"
without intervention; the user can also enable / disable categories,
and some events carry a scene reference that downstream code (UI
scene manager, A2 image routes) can pick up.

The module touches three concerns:

* **Trigger evaluation** — four flavours: time / interval / probability
  / condition.  Pure functions, no I/O.
* **Scheduling** — a 60 s background tick (default) that walks every
  world, runs the candidates through cooldowns, picks the highest
  priority winner subject to a per-world storm throttle.
* **Cross-links** — A5.4 overload protection.  When overload is in a
  recovery window, the scheduler drops every candidate outright
  rather than queueing or applying per-event cooldowns
  (per ``docs/notes.md`` A4.1 cross-link).

These tests exercise all three layers end-to-end via the Flask test
client and direct stub calls.  The clock is monkey-patched where the
scheduler relies on :func:`time.time` or :func:`time.gmtime`.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import pytest

from xijian_api.stubs import events as ev_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs.events import (
    DEFAULT_GLOBAL_COOLDOWN_SECONDS,
    DEFAULT_SCHEDULER_INTERVAL_SECONDS,
    KIND_COMMON,
    KIND_CUSTOM,
    KIND_INCIDENT,
    TRIGGER_CONDITION,
    TRIGGER_INTERVAL,
    TRIGGER_PROBABILITY,
    TRIGGER_TIME,
    EventError,
    _evaluate_interval_trigger,
    _evaluate_probability_trigger,
    _evaluate_time_trigger,
    _evaluate_trigger,
    _is_in_cooldown,
    _is_overload_active,
    _matches_disabled_categories,
    _pick_fire_payload,
    _safe_compare,
    _storm_throttle_pass,
    _validate_trigger,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world():
    """Return a fabricated world record id and register it on ``state``.

    Worlds are operator-created.  The stub has no public ``create`` —
    tests poke the bucket directly.  Each call gets its own world id so
    state doesn't leak between tests.
    """
    wid = f"world_test_{id(object())}"
    stubs_state.worlds[wid] = {
        "id": wid,
        "name": f"Test World {wid}",
        "state": {
            "weather": "sunny",
            "economy": 100,
            "festival": False,
        },
    }
    yield wid
    stubs_state.worlds.pop(wid, None)


@pytest.fixture()
def frozen_clock(monkeypatch):
    """Controllable clock injected into the events scheduler helpers."""
    current = {"t": 1_700_000_000.0}

    def fake_time() -> float:
        return current["t"]

    monkeypatch.setattr(ev_stub.time, "time", fake_time)

    class Clock:
        def now(self) -> float:
            return current["t"]

        def advance(self, seconds: float) -> None:
            current["t"] += seconds

    return Clock()


@pytest.fixture()
def fired_recorder(monkeypatch):
    """Capture ``event.fired`` WebSocket broadcasts issued by the stub."""
    from xijian_api.routes import ws_routes

    seen: list[tuple[str, dict[str, Any]]] = []

    def capture(event_type: str, data: dict | None = None) -> None:
        seen.append((event_type, data or {}))

    monkeypatch.setattr(ws_routes, "publish_event", capture)
    return seen


# ---------------------------------------------------------------------------
# Pure helpers — trigger validation
# ---------------------------------------------------------------------------


class TestValidateTrigger:
    """``_validate_trigger`` rejects malformed trigger configs."""

    def test_non_dict_raises(self):
        with pytest.raises(EventError, match="must be a JSON object"):
            _validate_trigger("not-a-dict")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "trigger",
        [
            {"type": "time", "hour": 9, "minute": 0, "frequency": "daily"},
            {"type": "time", "hour": 0, "minute": 0, "frequency": "hourly"},
            {"type": "interval", "seconds": 300},
            {"type": "probability", "per_tick": 0.1},
            {
                "type": "condition",
                "field": "weather",
                "op": "eq",
                "value": "rainy",
            },
        ],
    )
    def test_valid_triggers_pass(self, trigger):
        _validate_trigger(trigger)  # should not raise

    def test_unknown_type(self):
        with pytest.raises(EventError, match="must be one of"):
            _validate_trigger({"type": "magic"})

    def test_time_hour_out_of_range(self):
        with pytest.raises(EventError, match="hour must be int"):
            _validate_trigger(
                {"type": "time", "hour": 24, "minute": 0, "frequency": "daily"}
            )

    def test_time_minute_out_of_range(self):
        with pytest.raises(EventError, match="minute must be int"):
            _validate_trigger(
                {"type": "time", "hour": 0, "minute": 60, "frequency": "daily"}
            )

    def test_time_frequency_must_be_daily_or_hourly(self):
        with pytest.raises(EventError, match="frequency"):
            _validate_trigger(
                {"type": "time", "hour": 9, "minute": 0, "frequency": "weekly"}
            )

    def test_interval_must_be_positive(self):
        with pytest.raises(EventError, match="positive"):
            _validate_trigger({"type": "interval", "seconds": 0})

    def test_probability_out_of_range(self):
        with pytest.raises(EventError, match=r"\[0, 1\]"):
            _validate_trigger({"type": "probability", "per_tick": 1.5})

    def test_condition_missing_field(self):
        with pytest.raises(EventError, match="'field' is required"):
            _validate_trigger({"type": "condition", "op": "eq", "value": "x"})

    def test_condition_bad_op(self):
        with pytest.raises(EventError, match="op must be one of"):
            _validate_trigger(
                {"type": "condition", "field": "weather", "op": "weird", "value": 1}
            )

    def test_condition_missing_value(self):
        with pytest.raises(EventError, match="'value' is required"):
            _validate_trigger(
                {"type": "condition", "field": "weather", "op": "eq"}
            )


# ---------------------------------------------------------------------------
# Pure helpers — trigger evaluation
# ---------------------------------------------------------------------------


class TestEvaluateTimeTrigger:

    def test_daily_matches_when_minute_matches(self, monkeypatch):
        # 09:30 UTC
        fixed = time.struct_time((2026, 7, 10, 9, 30, 0, 0, 0, 0))
        monkeypatch.setattr(ev_stub.time, "gmtime", lambda _t: fixed)
        trigger = {
            "type": "time",
            "hour": 9,
            "minute": 30,
            "frequency": "daily",
        }
        assert _evaluate_time_trigger(trigger, 0.0) is True

    def test_daily_off_minute(self, monkeypatch):
        fixed = time.struct_time((2026, 7, 10, 9, 31, 0, 0, 0, 0))
        monkeypatch.setattr(ev_stub.time, "gmtime", lambda _t: fixed)
        trigger = {
            "type": "time",
            "hour": 9,
            "minute": 30,
            "frequency": "daily",
        }
        assert _evaluate_time_trigger(trigger, 0.0) is False

    def test_hourly_ignores_minute(self, monkeypatch):
        fixed = time.struct_time((2026, 7, 10, 9, 45, 0, 0, 0, 0))
        monkeypatch.setattr(ev_stub.time, "gmtime", lambda _t: fixed)
        trigger = {
            "type": "time",
            "hour": 9,
            "minute": 0,
            "frequency": "hourly",
        }
        assert _evaluate_time_trigger(trigger, 0.0) is True


class TestEvaluateIntervalTrigger:

    def test_first_eligible_always_fires(self):
        # No last-fire record → fires on first eligible tick.
        trigger = {"type": "interval", "seconds": 60}
        assert _evaluate_interval_trigger(trigger, "evt_x", 1_000_000.0) is True

    def test_within_window_does_not_fire(self):
        trigger = {"type": "interval", "seconds": 60}
        last = 1_000_000.0
        ev_stub._event_cooldowns["evt_x"] = last
        try:
            assert _evaluate_interval_trigger(
                trigger, "evt_x", last + 30.0
            ) is False
            assert _evaluate_interval_trigger(
                trigger, "evt_x", last + 60.0
            ) is True
            assert _evaluate_interval_trigger(
                trigger, "evt_x", last + 90.0
            ) is True
        finally:
            ev_stub._event_cooldowns.pop("evt_x", None)


class TestEvaluateProbabilityTrigger:

    def test_zero_per_tick_never_fires(self):
        trigger = {"type": "probability", "per_tick": 0.0}
        assert _evaluate_probability_trigger(trigger, 1_700_000_000.0) is False

    def test_one_per_tick_always_fires(self):
        trigger = {"type": "probability", "per_tick": 1.0}
        assert _evaluate_probability_trigger(trigger, 1_700_000_000.0) is True

    def test_deterministic_within_same_bucket(self):
        # Same second → same outcome (deterministic hash).
        trigger = {"type": "probability", "per_tick": 0.5}
        a = _evaluate_probability_trigger(trigger, 1_700_000_000.0)
        b = _evaluate_probability_trigger(trigger, 1_700_000_000.0)
        assert a == b

    def test_sweep_can_find_both_true_and_false(self):
        # Sweep through enough seconds to see both outcomes.
        trigger = {"type": "probability", "per_tick": 0.5}
        outcomes = {
            _evaluate_probability_trigger(trigger, float(s))
            for s in range(1_700_000_000, 1_700_000_000 + 50)
        }
        # With 50 samples and 50/50 odds, we *should* see both.  If
        # this ever flakes (extremely unlikely), the deterministic
        # hash distribution has degraded and we should investigate.
        assert {True, False}.issubset(outcomes)


class TestEvaluateConditionTrigger:

    def _world(self, **state):
        return {"state": state}

    def test_eq_match(self):
        trigger = {"type": "condition", "field": "weather", "op": "eq", "value": "rainy"}
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="rainy"))
            is True
        )
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="sunny"))
            is False
        )

    def test_ne(self):
        trigger = {"type": "condition", "field": "weather", "op": "ne", "value": "rainy"}
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="sunny"))
            is True
        )

    def test_numeric_gt_lt(self):
        trigger = {
            "type": "condition",
            "field": "economy",
            "op": "gt",
            "value": 100,
        }
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(economy=150))
            is True
        )
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(economy=50))
            is False
        )

    def test_in(self):
        trigger = {
            "type": "condition",
            "field": "weather",
            "op": "in",
            "value": ["rainy", "stormy"],
        }
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="stormy"))
            is True
        )
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="sunny"))
            is False
        )

    def test_not_in(self):
        trigger = {
            "type": "condition",
            "field": "weather",
            "op": "not_in",
            "value": ["rainy"],
        }
        assert (
            _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="sunny"))
            is True
        )

    def test_missing_state_field_returns_false(self):
        trigger = {"type": "condition", "field": "weather", "op": "eq", "value": "rainy"}
        assert _evaluate_trigger(trigger, "evt_x", 0.0, self._world()) is False

    def test_type_mismatch_in_compare_returns_false(self):
        # "sunny" > 5 returns False (no exception), per _safe_compare.
        trigger = {"type": "condition", "field": "weather", "op": "gt", "value": 5}
        assert _evaluate_trigger(trigger, "evt_x", 0.0, self._world(weather="sunny")) is False


class TestSafeCompare:

    def test_returns_op_result_on_success(self):
        assert _safe_compare(5, 3, lambda a, b: a > b) is True

    def test_swallows_type_error(self):
        # Comparing incompatible types should NOT raise — it should
        # return False.
        result = _safe_compare("abc", 3, lambda a, b: a > b)
        assert result is False


class TestEvaluateTriggerDispatch:

    def test_unknown_type_returns_false(self):
        assert _evaluate_trigger({"type": "magic"}, "evt_x", 0.0) is False

    def test_time_dispatch(self, monkeypatch):
        fixed = time.struct_time((2026, 7, 10, 9, 0, 0, 0, 0, 0))
        monkeypatch.setattr(ev_stub.time, "gmtime", lambda _t: fixed)
        trigger = {
            "type": "time",
            "hour": 9,
            "minute": 0,
            "frequency": "daily",
        }
        assert _evaluate_trigger(trigger, "evt_x", 0.0) is True


# ---------------------------------------------------------------------------
# Pure helpers — cooldowns + storm throttle + categories + payload
# ---------------------------------------------------------------------------


class TestIsInCooldown:

    def test_no_cooldown_set(self):
        assert _is_in_cooldown({"id": "e1"}, 1000.0) is False

    def test_future_cooldown_blocks(self):
        record = {"id": "e1", "cooldown_until": 1500.0}
        assert _is_in_cooldown(record, 1000.0) is True

    def test_past_cooldown_does_not_block(self):
        record = {"id": "e1", "cooldown_until": 500.0}
        assert _is_in_cooldown(record, 1000.0) is False

    def test_invalid_cooldown_treated_as_no_cooldown(self):
        record = {"id": "e1", "cooldown_until": "garbage"}
        assert _is_in_cooldown(record, 1000.0) is False


class TestStormThrottlePass:

    def test_first_fire_passes(self):
        assert _storm_throttle_pass("world_a", 1000.0) is True

    def test_recent_fire_throttles(self):
        ev_stub._world_cooldowns["world_a"] = 1000.0
        try:
            assert _storm_throttle_pass(
                "world_a",
                1000.0 + DEFAULT_GLOBAL_COOLDOWN_SECONDS - 1,
            ) is False
            assert _storm_throttle_pass(
                "world_a",
                1000.0 + DEFAULT_GLOBAL_COOLDOWN_SECONDS,
            ) is True
        finally:
            ev_stub._world_cooldowns.pop("world_a", None)


class TestMatchesDisabledCategories:

    def test_kind_in_disabled_set_blocks(self):
        stubs_state.world_event_categories_disabled.setdefault(
            "world_a", set()
        ).add("common")
        try:
            rec = {"kind": "common"}
            assert _matches_disabled_categories(rec, "world_a") is True
        finally:
            stubs_state.world_event_categories_disabled.pop("world_a", None)

    def test_unknown_category_does_not_match(self):
        rec = {"kind": "common"}
        assert _matches_disabled_categories(rec, "world_a") is False

    def test_disabled_set_per_world(self):
        stubs_state.world_event_categories_disabled.setdefault(
            "world_a", set()
        ).add("incident")
        stubs_state.world_event_categories_disabled.setdefault("world_b", set())
        try:
            assert (
                _matches_disabled_categories({"kind": "incident"}, "world_a") is True
            )
            assert (
                _matches_disabled_categories({"kind": "incident"}, "world_b")
                is False
            )
        finally:
            stubs_state.world_event_categories_disabled.pop("world_a", None)
            stubs_state.world_event_categories_disabled.pop("world_b", None)


class TestPickFirePayload:

    def test_merges_template_and_fired_at(self):
        record = {
            "trigger_config": {
                "type": "interval",
                "seconds": 60,
                "payload_template": {"weather": "stormy", "severity": 3},
            }
        }
        payload = _pick_fire_payload(record, 12345.0)
        assert payload["weather"] == "stormy"
        assert payload["severity"] == 3
        assert payload["fired_at"] == 12345.0

    def test_default_when_no_template(self):
        record = {"trigger_config": {"type": "interval", "seconds": 60}}
        payload = _pick_fire_payload(record, 12345.0)
        assert payload == {"fired_at": 12345.0}

    def test_non_dict_template_yields_empty(self):
        record = {"trigger_config": {"type": "interval", "seconds": 60,
                                       "payload_template": "invalid"}}
        payload = _pick_fire_payload(record, 12345.0)
        assert payload == {"fired_at": 12345.0}


# ---------------------------------------------------------------------------
# CRUD — event definitions
# ---------------------------------------------------------------------------


class TestEventCRUD:

    def test_create_persists_record(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="Market Day",
            description="A bustling market appears.",
            trigger_config={"type": "interval", "seconds": 3600},
        )
        assert rec["id"]
        assert rec["world_id"] == world
        assert rec["kind"] == KIND_COMMON
        assert rec["is_enabled"] is True
        # Stored under world_events bucket by id.
        assert stubs_state.world_events[rec["id"]] == rec

    def test_create_invalid_kind_raises(self, world):
        with pytest.raises(EventError, match="kind must be one of"):
            ev_stub.create_event(
                world_id=world,
                kind="legendary",
                name="x",
                trigger_config={"type": "interval", "seconds": 60},
            )

    def test_get_returns_record(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_CUSTOM,
            name="Custom",
            trigger_config={"type": "interval", "seconds": 60},
        )
        assert ev_stub.get_event(rec["id"]) == rec

    def test_get_returns_none_for_unknown(self):
        assert ev_stub.get_event("evt_does_not_exist") is None

    def test_list_filters(self, world):
        a = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="A",
            trigger_config={"type": "interval", "seconds": 60},
        )
        b = ev_stub.create_event(
            world_id=world,
            kind=KIND_INCIDENT,
            name="B",
            trigger_config={"type": "interval", "seconds": 60},
            priority=10,
            is_enabled=False,
        )
        # world filter
        only = ev_stub.list_events(world_id=world)
        assert {a["id"], b["id"]}.issubset({e["id"] for e in only})
        # kind filter
        kind_only = ev_stub.list_events(world_id=world, kind=KIND_COMMON)
        assert {e["id"] for e in kind_only} == {a["id"]}
        # enabled_only filter
        enabled = ev_stub.list_events(world_id=world, enabled_only=True)
        assert {e["id"] for e in enabled} == {a["id"]}
        # priority desc, created_at asc within same priority
        ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="C",
            trigger_config={"type": "interval", "seconds": 60},
            priority=10,
        )
        ordered = ev_stub.list_events(world_id=world, kind=KIND_COMMON)
        # a (prio 0) should come after C (prio 10) because of sort by -priority.
        assert ordered[0]["name"] == "C"

    def test_update_patches_mutable_fields(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        updated = ev_stub.update_event(
            rec["id"],
            {
                "name": "Renamed",
                "priority": 5,
                "trigger_config": {"type": "interval", "seconds": 120},
            },
        )
        assert updated["name"] == "Renamed"
        assert updated["priority"] == 5
        assert updated["trigger_config"]["seconds"] == 120

    def test_update_rejects_id_change(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        with pytest.raises(EventError, match="immutable"):
            ev_stub.update_event(rec["id"], {"id": "evt_other"})

    def test_update_rejects_world_id_change(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        with pytest.raises(EventError, match="immutable"):
            ev_stub.update_event(rec["id"], {"world_id": "world_b"})

    def test_update_rejects_invalid_kind(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        with pytest.raises(EventError, match="kind must be one of"):
            ev_stub.update_event(rec["id"], {"kind": "weird"})

    def test_update_invalid_trigger_rejected(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        with pytest.raises(EventError, match="trigger_config"):
            ev_stub.update_event(rec["id"],
                                  {"trigger_config": {"type": "magic"}})

    def test_update_unknown_returns_none(self, world):
        assert ev_stub.update_event("evt_phantom", {"name": "y"}) is None

    def test_delete_removes(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        assert ev_stub.delete_event(rec["id"]) is True
        assert stubs_state.world_events.get(rec["id"]) is None
        # Second call: idempotent False
        assert ev_stub.delete_event(rec["id"]) is False


# ---------------------------------------------------------------------------
# CRUD — fired instances
# ---------------------------------------------------------------------------


class TestInstanceCRUD:

    def test_fire_returns_instance(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="Rainstorm",
            trigger_config={
                "type": "interval",
                "seconds": 60,
                "payload_template": {"weather": "rainy"},
            },
            scene_ref_id="scene_forest_rain",
        )
        inst = ev_stub.fire_event(rec["id"])
        assert inst["id"]
        assert inst["event_id"] == rec["id"]
        assert inst["world_id"] == world
        assert inst["resolved_at"] is None
        assert inst["needs_scene"] is True
        assert inst["scene_ref_id"] == "scene_forest_rain"
        assert inst["payload"]["weather"] == "rainy"
        assert "fired_at" in inst["payload"]

    def test_fire_unknown_returns_none(self, world):
        assert ev_stub.fire_event("evt_phantom") is None

    def test_fire_merges_extra_payload(self, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60,
                             "payload_template": {"weather": "rainy"}},
        )
        inst = ev_stub.fire_event(
            rec["id"],
            payload={"additional": "data"},
            affected_npcs=["npc_1", "npc_2"],
            affects_user=True,
        )
        assert inst["payload"]["weather"] == "rainy"
        assert inst["payload"]["additional"] == "data"
        assert inst["affected_npcs"] == ["npc_1", "npc_2"]
        assert inst["affects_user"] is True

    def test_list_instances_filters(self, world):
        e1 = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="e1",
            trigger_config={"type": "interval", "seconds": 60},
        )
        e2 = ev_stub.create_event(
            world_id=world,
            kind=KIND_INCIDENT,
            name="e2",
            trigger_config={"type": "interval", "seconds": 60},
        )
        ev_stub.fire_event(e1["id"], now=1000.0)
        ev_stub.fire_event(e2["id"], now=1001.0)
        ev_stub.fire_event(e1["id"], now=1002.0)
        by_world = ev_stub.list_instances(world_id=world)
        assert len(by_world) == 3
        # Newest first
        assert by_world[0]["fired_at"] >= by_world[-1]["fired_at"]
        # Filter by event
        only_e1 = ev_stub.list_instances(event_id=e1["id"])
        assert len(only_e1) == 2

    def test_list_instances_limit(self, world):
        e = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="e",
            trigger_config={"type": "interval", "seconds": 60},
        )
        for i in range(5):
            ev_stub.fire_event(e["id"], now=1000.0 + i)
        assert len(ev_stub.list_instances(limit=3)) == 3
        assert len(ev_stub.list_instances(limit=0)) == 1  # floor at 1

    def test_resolve_marks_timestamp(self, world):
        e = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="e",
            trigger_config={"type": "interval", "seconds": 60},
        )
        inst = ev_stub.fire_event(e["id"])
        assert inst["resolved_at"] is None
        resolved = ev_stub.resolve_instance(inst["id"], now=2000.0)
        assert resolved["resolved_at"] == 2000.0

    def test_resolve_unknown_returns_none(self):
        assert ev_stub.resolve_instance("inst_phantom") is None

    def test_trim_caps_total(self, world, monkeypatch):
        # Save and override the cap to a small value for the test.
        from xijian_api.stubs import events as ev_module

        original_cap = ev_module.INSTANCE_KEEP_TOTAL
        monkeypatch.setattr(ev_module, "INSTANCE_KEEP_TOTAL", 3)
        try:
            e = ev_stub.create_event(
                world_id=world,
                kind=KIND_COMMON,
                name="e",
                trigger_config={"type": "interval", "seconds": 60},
            )
            for i in range(10):
                ev_stub.fire_event(e["id"], now=1000.0 + i)
            assert (
                len(stubs_state.world_event_instances) == 3
            )
            # The 3 newest should remain (fired_at 1007, 1008, 1009).
            timestamps = sorted(
                i["fired_at"]
                for i in stubs_state.world_event_instances.values()
            )
            assert timestamps == [1007.0, 1008.0, 1009.0]
        finally:
            monkeypatch.setattr(ev_module, "INSTANCE_KEEP_TOTAL", original_cap)


# ---------------------------------------------------------------------------
# Category toggles
# ---------------------------------------------------------------------------


class TestCategoryToggles:

    def test_set_disabled_adds(self, world):
        ev_stub.set_category_disabled(world, "incident", True)
        assert ev_stub.is_category_disabled(world, "incident") is True

    def test_set_disabled_removes_when_false(self, world):
        ev_stub.set_category_disabled(world, "incident", True)
        ev_stub.set_category_disabled(world, "incident", False)
        assert ev_stub.is_category_disabled(world, "incident") is False

    def test_list_disabled_sorted(self, world):
        ev_stub.set_category_disabled(world, "social", True)
        ev_stub.set_category_disabled(world, "battle", True)
        ev_stub.set_category_disabled(world, "daily", True)
        # No category named "weather" is part of the schema; we use the
        # kind-based toggles.  Sort order is alphabetical (str).
        result = ev_stub.list_disabled_categories(world)
        assert result == sorted(result)
        assert set(result) == {"social", "battle", "daily"}

    def test_invalid_category_raises(self, world):
        with pytest.raises(EventError, match="non-empty string"):
            ev_stub.set_category_disabled(world, "", True)

    def test_disabled_set_per_world(self):
        ev_stub.set_category_disabled("world_a", "incident", True)
        try:
            assert (
                ev_stub.is_category_disabled("world_a", "incident") is True
            )
            assert (
                ev_stub.is_category_disabled("world_b", "incident") is False
            )
        finally:
            ev_stub.set_category_disabled("world_a", "incident", False)
            stubs_state.world_event_categories_disabled.pop("world_a", None)


# ---------------------------------------------------------------------------
# Tick — single world
# ---------------------------------------------------------------------------


class TestTickWorld:

    def _create_event(self, world, **overrides):
        defaults = dict(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        defaults.update(overrides)
        return ev_stub.create_event(**defaults)

    def test_no_events_returns_empty(self, world):
        assert ev_stub.tick_world(world) == []

    def test_disabled_event_skipped(self, world):
        rec = self._create_event(world, is_enabled=False)
        ev_stub.set_category_disabled(world, KIND_COMMON, True)
        try:
            result = ev_stub.tick_world(world)
            assert result == []
        finally:
            ev_stub.set_category_disabled(world, KIND_COMMON, False)

    def test_per_event_cooldown_skips(self, world, frozen_clock):
        rec = self._create_event(
            world,
            cooldown_until=frozen_clock.now() + 60.0,
        )
        result = ev_stub.tick_world(world, now=frozen_clock.now())
        assert result == []

    def test_storm_throttle_blocks_subsequent_after_fire(
        self, world, frozen_clock
    ):
        first = self._create_event(world, priority=10)
        # First fire should succeed
        ev_stub.tick_world(world, now=frozen_clock.now())
        # Immediate retick is throttled (storm 60s)
        second_pass = ev_stub.tick_world(world, now=frozen_clock.now())
        assert len(second_pass) == 0  # or "lost_priority_race"

    def test_priority_race_winner_fires(self, world, frozen_clock):
        low = self._create_event(world, name="low", priority=0)
        high = self._create_event(world, name="high", priority=10)
        fired = ev_stub.tick_world(world, now=frozen_clock.now())
        # Only one event fires per tick due to storm throttle.
        assert len(fired) == 1
        assert fired[0]["event_id"] == high["id"]

    def test_trigger_evaluation_failure_logged_and_continues(
        self, world, frozen_clock, monkeypatch
    ):
        """A bad trigger shouldn't crash the whole tick."""
        good = self._create_event(world, name="good")
        # Patch only the good record's trigger to a corrupt dict via update.
        # We rely on the fact that ``_evaluate_trigger`` catches exceptions.
        called = []

        def boom(*a, **kw):
            called.append(1)
            raise RuntimeError("simulated")

        monkeypatch.setattr(ev_stub, "_evaluate_trigger", boom)
        # Stub returns empty candidates for all events → zero fires, no crash.
        result = ev_stub.tick_world(world, now=frozen_clock.now())
        assert result == []
        assert called, "_evaluate_trigger should have been called"

    def test_overload_active_drops_everything(self, world):
        # Seed two valid candidates.
        self._create_event(world, name="a", priority=0)
        self._create_event(world, name="b", priority=10)
        # Pretend overload is in a recovery window.
        stubs_state.overload.clear()
        stubs_state.overload["recovery"] = {
            "status": "waiting",
            "earliest_confirm_at": 1_000_000.0,
        }
        try:
            fired = ev_stub.tick_world(world)
            assert fired == []
            # Skipped list (debug log) is *not* exposed via the API, but
            # we can check it didn't accidentally instantiate cooldown
            # state.
            assert (
                ev_stub._world_cooldowns.get(world) is None
            ), "overload-shortcut must not consume cooldown slot"
        finally:
            stubs_state.overload.clear()

    def test_overload_recovery_finalised_unblocks(self, world):
        # Two candidates.
        self._create_event(world, name="a")
        # Set overload to finalized-then-cleared; recovery is None now.
        stubs_state.overload["recovery"] = {
            "status": "finalized",
            "earliest_confirm_at": 0.0,
        }
        # 'finalized' is not in {'waiting', 'first_confirmed'} so we
        # shouldn't drop.  But the storm throttle + first-pass also
        # needs to allow one fire.  Use a custom global cooldown that
        # already elapsed.
        try:
            fired = ev_stub.tick_world(world)
            assert len(fired) == 1
        finally:
            stubs_state.overload.clear()


class TestTickAll:

    def test_walks_every_world(self, world):
        wid_b = "world_test_b"
        stubs_state.worlds[wid_b] = {
            "id": wid_b,
            "name": "B",
            "state": {},
        }
        try:
            ev_stub.create_event(
                world_id=world,
                kind=KIND_COMMON,
                name="a",
                trigger_config={"type": "interval", "seconds": 60},
            )
            ev_stub.create_event(
                world_id=wid_b,
                kind=KIND_COMMON,
                name="b",
                trigger_config={"type": "interval", "seconds": 60},
            )
            result = ev_stub.tick_all()
            assert world in result
            assert wid_b in result
        finally:
            stubs_state.worlds.pop(wid_b, None)


# ---------------------------------------------------------------------------
# Background scheduler lifecycle
# ---------------------------------------------------------------------------


class TestSchedulerLifecycle:

    def test_start_stop(self, monkeypatch):
        # Conftest sets XIJIAN_EVENT_SCHEDULER=0; opt in for this test.
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER", "1")
        # Ensure clean.
        ev_stub.stop_scheduler()
        result = ev_stub.start_scheduler()
        assert result["started"] is True
        # Idempotent: second start should refuse.
        again = ev_stub.start_scheduler()
        assert again["started"] is False
        # Stop.
        stopped = ev_stub.stop_scheduler()
        assert stopped["stopped"] is True

    def test_status_reports_running(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER", "1")
        ev_stub.stop_scheduler()
        before = ev_stub.scheduler_status()
        ev_stub.start_scheduler()
        try:
            after = ev_stub.scheduler_status()
            assert before["running"] is False
            assert after["running"] is True
            assert (
                after["interval_s"] == DEFAULT_SCHEDULER_INTERVAL_SECONDS
                or after["interval_s"] >= 1.0
            )
            assert (
                after["global_cooldown_s"] == DEFAULT_GLOBAL_COOLDOWN_SECONDS
            )
        finally:
            ev_stub.stop_scheduler()

    def test_env_disable_blocks_start(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER", "0")
        ev_stub.stop_scheduler()
        result = ev_stub.start_scheduler()
        assert result["started"] is False
        assert result["reason"] == "disabled_by_env"

    def test_env_interval_override(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER_SECONDS", "5")
        assert ev_stub._current_interval() == 5.0

    def test_interval_floored_at_one_second(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER_SECONDS", "0.1")
        assert ev_stub._current_interval() == 1.0

    def test_start_runs_a_real_pass(self, world, monkeypatch):
        ev_stub.stop_scheduler()
        ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        # Conftest sets the scheduler env to "0"; opt in.
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER", "1")
        # We want a TINY interval so the test doesn't take 60s.
        monkeypatch.setenv("XIJIAN_EVENT_SCHEDULER_SECONDS", "1")
        result = ev_stub.start_scheduler()
        assert result["started"] is True
        try:
            # Give the thread time to fire at least one pass.
            time.sleep(2.5)
            status = ev_stub.scheduler_status()
            assert status["running"] is True
        finally:
            ev_stub.stop_scheduler()


class TestResetForTesting:

    def test_clears_state(self, world):
        ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        ev_stub.set_category_disabled(world, "incident", True)
        assert stubs_state.world_events
        assert stubs_state.world_event_categories_disabled
        ev_stub.reset_for_testing()
        assert stubs_state.world_events == {}
        assert stubs_state.world_event_instances == {}
        assert stubs_state.world_event_categories_disabled == {}
        assert ev_stub._world_cooldowns == {}
        assert ev_stub._event_cooldowns == {}


class TestSummary:

    def test_summary_returns_aggregate(self, world):
        e = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        ev_stub.fire_event(e["id"])
        s = ev_stub.summary(world)
        assert s["world_id"] == world
        assert s["events_total"] == 1
        assert s["events_enabled"] == 1
        assert s["instances_total"] == 1
        assert s["categories_disabled"] == []
        assert len(s["recent_instances"]) == 1


# ---------------------------------------------------------------------------
# WS broadcast
# ---------------------------------------------------------------------------


class TestBroadcastOnFire:

    def test_fire_publishes_event_fired(self, world, fired_recorder):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
            scene_ref_id="scene_x",
        )
        ev_stub.fire_event(rec["id"])
        assert len(fired_recorder) == 1
        event_type, payload = fired_recorder[0]
        assert event_type == "event.fired"
        assert payload["world_id"] == world
        assert payload["event_id"] == rec["id"]
        assert payload["needs_scene"] is True
        assert payload["scene_ref_id"] == "scene_x"

    def test_no_event_when_publish_fails(self, world, monkeypatch):
        # Failure of WS publish must not break the firing.
        from xijian_api.routes import ws_routes

        def boom(*a, **kw):
            raise RuntimeError("ws down")

        monkeypatch.setattr(ws_routes, "publish_event", boom)
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        # Must not raise.
        inst = ev_stub.fire_event(rec["id"])
        assert inst is not None


# ---------------------------------------------------------------------------
# Routes — CRUD
# ---------------------------------------------------------------------------


class TestRoutesEventCRUD:

    def test_create_requires_world(self, client, auth_headers):
        # 404 world_not_found when the world doesn't exist.
        resp = client.post(
            "/v1/xijian/events",
            json={
                "world_id": "world_phantom",
                "kind": KIND_COMMON,
                "name": "x",
                "trigger_config": {"type": "interval", "seconds": 60},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.get_json()["error"]["code"] == "world_not_found"

    def test_full_crud_roundtrip(self, client, auth_headers, world):
        # Create.
        create_resp = client.post(
            "/v1/xijian/events",
            json={
                "world_id": world,
                "kind": KIND_COMMON,
                "name": "Market Day",
                "trigger_config": {"type": "interval", "seconds": 60},
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        event_id = create_resp.get_json()["id"]

        # Read.
        get_resp = client.get(
            f"/v1/xijian/events/{event_id}", headers=auth_headers
        )
        assert get_resp.status_code == 200
        assert get_resp.get_json()["name"] == "Market Day"

        # List (by world).
        list_resp = client.get(
            f"/v1/xijian/events?world_id={world}",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        ids = [e["id"] for e in list_resp.get_json()["data"]]
        assert event_id in ids

        # Patch.
        patch_resp = client.patch(
            f"/v1/xijian/events/{event_id}",
            json={"name": "Festival Day", "priority": 99},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.get_json()["name"] == "Festival Day"

        # Delete.
        del_resp = client.delete(
            f"/v1/xijian/events/{event_id}", headers=auth_headers
        )
        assert del_resp.status_code == 204

    def test_create_missing_fields(self, client, auth_headers, world):
        resp = client.post(
            "/v1/xijian/events",
            json={"world_id": world, "name": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "missing_event_fields"

    def test_create_validation_error(self, client, auth_headers, world):
        resp = client.post(
            "/v1/xijian/events",
            json={
                "world_id": world,
                "kind": KIND_COMMON,
                "name": "x",
                "trigger_config": {"type": "magic"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_event"

    def test_patch_empty_400(self, client, auth_headers, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        resp = client.patch(
            f"/v1/xijian/events/{rec['id']}",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "empty_patch"

    def test_patch_unknown_404(self, client, auth_headers):
        resp = client.patch(
            "/v1/xijian/events/evt_phantom",
            json={"name": "y"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_unknown_404(self, client, auth_headers):
        resp = client.delete(
            "/v1/xijian/events/evt_phantom", headers=auth_headers
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Routes — instances
# ---------------------------------------------------------------------------


class TestRoutesInstances:

    def test_list_resolves_and_get(self, client, auth_headers, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        inst = ev_stub.fire_event(rec["id"])

        list_resp = client.get(
            f"/v1/xijian/events/instances?world_id={world}",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        ids = [i["id"] for i in list_resp.get_json()["data"]]
        assert inst["id"] in ids

        get_resp = client.get(
            f"/v1/xijian/events/instances/{inst['id']}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.get_json()["id"] == inst["id"]

        resolve_resp = client.post(
            f"/v1/xijian/events/instances/{inst['id']}/resolve",
            headers=auth_headers,
        )
        assert resolve_resp.status_code == 200
        assert resolve_resp.get_json()["resolved_at"] is not None

    def test_list_invalid_limit(self, client, auth_headers):
        resp = client.get(
            "/v1/xijian/events/instances?limit=abc",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "invalid_limit"

    def test_get_unknown_404(self, client, auth_headers):
        resp = client.get(
            "/v1/xijian/events/instances/inst_phantom",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_resolve_unknown_404(self, client, auth_headers):
        resp = client.post(
            "/v1/xijian/events/instances/inst_phantom/resolve",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Routes — categories
# ---------------------------------------------------------------------------


class TestRoutesCategoryToggles:

    def test_list_when_world_missing(self, client, auth_headers):
        resp = client.get(
            "/v1/xijian/worlds/world_phantom/event-categories",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_toggle_roundtrip(self, client, auth_headers, world):
        list_resp = client.get(
            f"/v1/xijian/worlds/{world}/event-categories",
            headers=auth_headers,
        )
        assert list_resp.status_code == 200
        assert list_resp.get_json()["disabled"] == []

        toggle_on = client.put(
            f"/v1/xijian/worlds/{world}/event-categories/incident",
            json={"disabled": True},
            headers=auth_headers,
        )
        assert toggle_on.status_code == 200
        assert toggle_on.get_json()["disabled"] is True
        assert "incident" in toggle_on.get_json()["all_disabled"]

        toggle_off = client.put(
            f"/v1/xijian/worlds/{world}/event-categories/incident",
            json={"disabled": False},
            headers=auth_headers,
        )
        assert toggle_off.status_code == 200
        assert toggle_off.get_json()["disabled"] is False
        assert toggle_off.get_json()["all_disabled"] == []

    def test_missing_disabled_field(self, client, auth_headers, world):
        resp = client.put(
            f"/v1/xijian/worlds/{world}/event-categories/incident",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"]["code"] == "missing_disabled_flag"

    def test_world_not_found_on_toggle(self, client, auth_headers):
        resp = client.put(
            "/v1/xijian/worlds/world_phantom/event-categories/incident",
            json={"disabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Routes — scheduler control + dev tick + summary
# ---------------------------------------------------------------------------


class TestRoutesScheduler:

    def test_status_public(self, client, auth_headers):
        resp = client.get(
            "/v1/xijian/events/scheduler", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert "running" in body
        assert "interval_s" in body

    def test_dev_tick_blocked_in_prod(self, client, auth_headers, world):
        # Default env: not dev, so the endpoint 404s.
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        resp = client.post(
            "/v1/xijian/events/scheduler/tick",
            json={"world_id": world},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_dev_tick_allowed_in_dev_mode(
        self, client, auth_headers, world, monkeypatch
    ):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        resp = client.post(
            "/v1/xijian/events/scheduler/tick",
            json={"world_id": world},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["world_id"] == world
        # Should fire the high-priority interval event on first tick.
        assert "fired" in body

    def test_dev_tick_all_worlds(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("XIJIAN_DEV", "1")
        wid = "world_tick_all"
        stubs_state.worlds[wid] = {"id": wid, "name": "x", "state": {}}
        try:
            ev_stub.create_event(
                world_id=wid,
                kind=KIND_COMMON,
                name="x",
                trigger_config={"type": "interval", "seconds": 60},
            )
            resp = client.post(
                "/v1/xijian/events/scheduler/tick",
                json={},
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert "fired_by_world" in resp.get_json()
        finally:
            stubs_state.worlds.pop(wid, None)

    def test_summary_roundtrip(self, client, auth_headers, world):
        rec = ev_stub.create_event(
            world_id=world,
            kind=KIND_COMMON,
            name="x",
            trigger_config={"type": "interval", "seconds": 60},
        )
        ev_stub.fire_event(rec["id"])
        resp = client.get(
            f"/v1/xijian/worlds/{world}/events/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["events_total"] == 1
        assert body["instances_total"] == 1

    def test_summary_unknown_world_404(self, client, auth_headers):
        resp = client.get(
            "/v1/xijian/worlds/world_phantom/events/summary",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Auth — every route requires Bearer
# ---------------------------------------------------------------------------


class TestRoutesAuth:

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/v1/xijian/events"),
            ("post", "/v1/xijian/events"),
            ("get", "/v1/xijian/events/instances"),
            ("get", "/v1/xijian/events/scheduler"),
            ("post", "/v1/xijian/events/scheduler/tick"),
        ],
    )
    def test_no_auth_blocked(self, client, method, path):
        resp = getattr(client, method)(path)
        assert resp.status_code in (401, 403)
