"""Tests for ``stubs.npcs`` (A4.2) and ``/v1/xijian/npcs/*``.

Covers the NPC life-cycle:

* **Pure helpers** — tier validation, budget validation, demotion
  order, affected-NPC selector.
* **CRUD** — create / list / get / patch / delete, 50-cap.
* **Tier transitions** — single + bulk, with scheduling-log
  bookkeeping.
* **Scheduling** — tick_world with budget enforcement, idle
  demotion, LLM-queue-pressure demotion.
* **Background tick** — start / stop / status, env-flag disable.
* **A5.4 overload** — suspend handler installed at seed time,
  fires when the overload module triggers, drops every active NPC.
* **A4.1 cross-link** — affected-NPC selector picks high_active
  NPCs by default; custom selector overrides.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import overload as ov_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import worlds as worlds_stub
from xijian_api.stubs.npcs import (
    DEFAULT_DEGRADE_P99_LATENCY_S,
    DEFAULT_NPC_COMPUTE_BUDGET,
    DEFAULT_TICK_INTERVAL_SECONDS,
    DEFAULT_TOTAL_TOKEN_BUDGET,
    HIGH_ACTIVE_INTERVAL_S,
    HIGH_ACTIVE_LIMIT,
    IDLE_INTERVAL_S,
    LOW_ACTIVE_INTERVAL_S,
    LOW_ACTIVE_LIMIT,
    MAX_NPCS_PER_WORLD,
    NPCError,
    TIER_HIGH_ACTIVE,
    TIER_IDLE,
    TIER_LOW_ACTIVE,
    VALID_TIERS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    """Create a fresh world for NPC tests."""
    body = {"name": "NPC Test World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


@pytest.fixture()
def npc(client, auth_headers, world):
    """Create a low_active NPC in the fixture world."""
    body = {"world_id": world, "name": "Test NPC"}
    res = client.post("/v1/xijian/npcs", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


# ---------------------------------------------------------------------------
# Constants — locked by v2.1
# ---------------------------------------------------------------------------


class TestConstants:
    def test_high_active_limit_is_3(self):
        assert HIGH_ACTIVE_LIMIT == 3

    def test_low_active_limit_is_10(self):
        assert LOW_ACTIVE_LIMIT == 10

    def test_total_budget_is_50000(self):
        assert DEFAULT_TOTAL_TOKEN_BUDGET == 50_000

    def test_max_npcs_per_world_is_50(self):
        assert MAX_NPCS_PER_WORLD == 50

    def test_think_intervals(self):
        assert HIGH_ACTIVE_INTERVAL_S == 5.0
        assert LOW_ACTIVE_INTERVAL_S == 15.0
        assert IDLE_INTERVAL_S == 60.0

    def test_valid_tiers(self):
        assert VALID_TIERS == frozenset({"high_active", "low_active", "idle"})

    def test_tick_interval_default_60s(self):
        assert DEFAULT_TICK_INTERVAL_SECONDS == 60.0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestValidateTier:
    def test_high_active(self):
        npcs_stub._validate_tier(TIER_HIGH_ACTIVE)

    def test_low_active(self):
        npcs_stub._validate_tier(TIER_LOW_ACTIVE)

    def test_idle(self):
        npcs_stub._validate_tier(TIER_IDLE)

    @pytest.mark.parametrize("bad", ["", "high", "LOW_ACTIVE", "ghost"])
    def test_invalid_raises(self, bad):
        with pytest.raises(NPCError):
            npcs_stub._validate_tier(bad)


class TestValidateComputeBudget:
    def test_zero(self):
        npcs_stub._validate_compute_budget(0)

    def test_positive(self):
        npcs_stub._validate_compute_budget(100)

    def test_exceeds_world_budget(self):
        with pytest.raises(NPCError):
            npcs_stub._validate_compute_budget(DEFAULT_TOTAL_TOKEN_BUDGET + 1)

    def test_negative(self):
        with pytest.raises(NPCError):
            npcs_stub._validate_compute_budget(-1)

    @pytest.mark.parametrize("bad", ["100", None, [], {}])
    def test_non_numeric(self, bad):
        with pytest.raises(NPCError):
            npcs_stub._validate_compute_budget(bad)


class TestCapForTier:
    def test_high(self):
        assert npcs_stub._cap_for_tier(TIER_HIGH_ACTIVE) == HIGH_ACTIVE_LIMIT

    def test_low(self):
        assert npcs_stub._cap_for_tier(TIER_LOW_ACTIVE) == LOW_ACTIVE_LIMIT

    def test_idle(self):
        assert npcs_stub._cap_for_tier(TIER_IDLE) == 0


class TestIntervalForTier:
    def test_high(self):
        assert npcs_stub._interval_for_tier(TIER_HIGH_ACTIVE) == HIGH_ACTIVE_INTERVAL_S

    def test_low(self):
        assert npcs_stub._interval_for_tier(TIER_LOW_ACTIVE) == LOW_ACTIVE_INTERVAL_S

    def test_idle(self):
        assert npcs_stub._interval_for_tier(TIER_IDLE) == IDLE_INTERVAL_S


class TestShouldDegrade:
    def test_idle_over_30s(self):
        assert npcs_stub._should_degrade(npc_idle_seconds=31.0) is True

    def test_recent_no_pressure(self):
        assert npcs_stub._should_degrade(npc_idle_seconds=10.0) is False

    def test_queue_p99_pressure(self):
        assert npcs_stub._should_degrade(
            npc_idle_seconds=10.0,
            queue_p99_latency_s=DEFAULT_DEGRADE_P99_LATENCY_S + 0.1,
        ) is True

    def test_no_p99_no_pressure(self):
        assert npcs_stub._should_degrade(
            npc_idle_seconds=10.0, queue_p99_latency_s=None
        ) is False


class TestPickDemoteCandidates:
    def test_pick_least_important_first(self):
        npcs = [
            {"id": "a", "importance": 0.5, "last_think_at": 0.0},
            {"id": "b", "importance": 0.9, "last_think_at": 0.0},
            {"id": "c", "importance": 0.1, "last_think_at": 0.0},
        ]
        picks = npcs_stub._pick_demote_candidates(
            npcs, world_total=DEFAULT_TOTAL_TOKEN_BUDGET, overage=2
        )
        assert picks == ["c", "a"]

    def test_no_overage_returns_empty(self):
        npcs = [{"id": "a", "importance": 1.0, "last_think_at": 0.0}]
        picks = npcs_stub._pick_demote_candidates(npcs, world_total=50000, overage=0)
        assert picks == []

    def test_empty_list(self):
        assert npcs_stub._pick_demote_candidates([], world_total=50000, overage=1) == []


# ---------------------------------------------------------------------------
# Affected-NPC selector — A4.1 cross-link
# ---------------------------------------------------------------------------


class TestAffectedSelector:
    def test_default_picks_high_active(self):
        world_record = {"id": "wid_test"}
        stubs_state.npcs["npc1"] = {
            "id": "npc1", "world_id": "wid_test", "name": "H",
            "activity_tier": TIER_HIGH_ACTIVE, "state_json": {},
        }
        stubs_state.npcs["npc2"] = {
            "id": "npc2", "world_id": "wid_test", "name": "L",
            "activity_tier": TIER_LOW_ACTIVE, "state_json": {},
        }
        try:
            out = npcs_stub.select_affected_npcs(
                world_record, {"id": "ev1", "payload": {}}
            )
            assert "npc1" in out
            assert "npc2" not in out
        finally:
            stubs_state.npcs.clear()

    def test_npc_kind_match(self):
        world_record = {"id": "wid_test"}
        stubs_state.npcs["npc_l1"] = {
            "id": "npc_l1", "world_id": "wid_test", "name": "L1",
            "activity_tier": TIER_LOW_ACTIVE,
            "state_json": {"npc_kind": "merchant"},
        }
        stubs_state.npcs["npc_l2"] = {
            "id": "npc_l2", "world_id": "wid_test", "name": "L2",
            "activity_tier": TIER_LOW_ACTIVE,
            "state_json": {"npc_kind": "guard"},
        }
        try:
            out = npcs_stub.select_affected_npcs(
                world_record,
                {"id": "ev1", "payload": {"npc_kind": "merchant"}},
            )
            assert "npc_l1" in out
            assert "npc_l2" not in out
        finally:
            stubs_state.npcs.clear()

    def test_custom_selector_override(self):
        world_record = {"id": "wid"}
        called = {"yes": False}

        def custom(world_rec, event_rec):
            called["yes"] = True
            return ["custom_npc"]

        npcs_stub.set_affected_npc_selector(custom)
        try:
            out = npcs_stub.select_affected_npcs(
                world_record, {"id": "ev1", "payload": {}}
            )
            assert called["yes"]
            assert out == ["custom_npc"]
        finally:
            npcs_stub.set_affected_npc_selector(None)

    def test_selector_exception_returns_empty(self):
        def bad(world_rec, event_rec):
            raise RuntimeError("boom")

        npcs_stub.set_affected_npc_selector(bad)
        try:
            out = npcs_stub.select_affected_npcs(
                {"id": "wid"}, {"id": "ev1", "payload": {}}
            )
            assert out == []
        finally:
            npcs_stub.set_affected_npc_selector(None)


# ---------------------------------------------------------------------------
# CRUD — stub-level
# ---------------------------------------------------------------------------


class TestCreateStub:
    def test_minimal(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="NPC")
            assert npc["name"] == "NPC"
            assert npc["activity_tier"] == TIER_LOW_ACTIVE
            assert npc["is_alive"] is True
            assert npc["compute_budget"] == DEFAULT_NPC_COMPUTE_BUDGET
        finally:
            worlds_stub.delete(world["id"])

    def test_unknown_world_raises(self):
        with pytest.raises(NPCError, match="does not exist"):
            npcs_stub.create(world_id="world_phantom", name="x")

    def test_50_cap_enforced(self):
        world = worlds_stub.create(name="W")
        try:
            # Create 50 NPCs with minimal budget.
            for i in range(MAX_NPCS_PER_WORLD):
                npcs_stub.create(
                    world_id=world["id"],
                    name=f"npc_{i}",
                    compute_budget=1,
                )
            with pytest.raises(NPCError, match="hard cap"):
                npcs_stub.create(
                    world_id=world["id"], name="overflow", compute_budget=1
                )
        finally:
            worlds_stub.delete(world["id"])

    def test_create_writes_spawn_log(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="NPC")
            entries = [
                e for e in stubs_state.npc_scheduling_log.values()
                if e.get("npc_id") == npc["id"]
            ]
            assert any(e["action"] == "spawn" for e in entries)
        finally:
            worlds_stub.delete(world["id"])


class TestGetListUpdateDelete:
    def test_get_round_trip(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            assert npcs_stub.get(npc["id"])["name"] == "x"
        finally:
            worlds_stub.delete(world["id"])

    def test_list_for_world_filters(self):
        world_a = worlds_stub.create(name="A")
        world_b = worlds_stub.create(name="B")
        try:
            npcs_stub.create(world_id=world_a["id"], name="a1")
            npcs_stub.create(world_id=world_a["id"], name="a2", activity_tier=TIER_HIGH_ACTIVE)
            npcs_stub.create(world_id=world_b["id"], name="b1")
            a_npcs = npcs_stub.list_for_world(world_a["id"])
            assert {n["name"] for n in a_npcs} == {"a1", "a2"}
            b_npcs = npcs_stub.list_for_world(world_b["id"])
            assert {n["name"] for n in b_npcs} == {"b1"}
            high_only = npcs_stub.list_for_world(world_a["id"], tier=TIER_HIGH_ACTIVE)
            assert {n["name"] for n in high_only} == {"a2"}
        finally:
            worlds_stub.delete(world_a["id"])
            worlds_stub.delete(world_b["id"])

    def test_update_immutable_keys(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            with pytest.raises(NPCError):
                npcs_stub.update(npc["id"], {"id": "other"})
            with pytest.raises(NPCError):
                npcs_stub.update(npc["id"], {"world_id": "world_other"})
        finally:
            worlds_stub.delete(world["id"])

    def test_update_via_tier_rejected_use_set_tier(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            with pytest.raises(NPCError, match="set_tier"):
                npcs_stub.update(npc["id"], {"activity_tier": TIER_IDLE})
        finally:
            worlds_stub.delete(world["id"])

    def test_update_mutable_fields(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            updated = npcs_stub.update(npc["id"], {"name": "new"})
            assert updated["name"] == "new"
        finally:
            worlds_stub.delete(world["id"])

    def test_delete_returns_true(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            assert npcs_stub.delete(npc["id"]) is True
            assert npcs_stub.get(npc["id"]) is None
            assert npcs_stub.delete(npc["id"]) is False
        finally:
            worlds_stub.delete(world["id"])


# ---------------------------------------------------------------------------
# Tier transitions
# ---------------------------------------------------------------------------


class TestSetTier:
    def test_set_tier_changes_and_logs(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            npcs_stub.set_tier(npc["id"], TIER_HIGH_ACTIVE)
            assert npcs_stub.get(npc["id"])["activity_tier"] == TIER_HIGH_ACTIVE
            log = [
                e for e in stubs_state.npc_scheduling_log.values()
                if e.get("npc_id") == npc["id"]
            ]
            assert any(
                e["action"] == "wake" and e["to_tier"] == TIER_HIGH_ACTIVE
                for e in log
            )
        finally:
            worlds_stub.delete(world["id"])

    def test_set_tier_to_idle_writes_sleep(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(
                world_id=world["id"], name="x", activity_tier=TIER_HIGH_ACTIVE
            )
            npcs_stub.set_tier(npc["id"], TIER_IDLE, reason="manual")
            log = [
                e for e in stubs_state.npc_scheduling_log.values()
                if e.get("npc_id") == npc["id"] and e["action"] == "sleep"
            ]
            assert len(log) == 1
            assert log[0]["reason"] == "manual"
        finally:
            worlds_stub.delete(world["id"])

    def test_set_tier_same_returns_noop(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            result = npcs_stub.set_tier(npc["id"], TIER_LOW_ACTIVE)
            assert result is not None
            # No new log entry.
            log = [
                e for e in stubs_state.npc_scheduling_log.values()
                if e.get("npc_id") == npc["id"] and e["action"] in {"sleep", "wake"}
            ]
            assert len(log) == 0
        finally:
            worlds_stub.delete(world["id"])

    def test_set_tier_unknown_npc_returns_none(self):
        assert npcs_stub.set_tier("npc_phantom", TIER_IDLE) is None

    def test_set_world_tier_bulk(self):
        world = worlds_stub.create(name="W")
        try:
            npcs_stub.create(world_id=world["id"], name="a1", activity_tier=TIER_HIGH_ACTIVE)
            npcs_stub.create(world_id=world["id"], name="a2", activity_tier=TIER_LOW_ACTIVE)
            npcs_stub.create(world_id=world["id"], name="a3", activity_tier=TIER_LOW_ACTIVE)
            out = npcs_stub.set_world_tier(world["id"], TIER_IDLE, reason="overload")
            assert out["updated"] == 3
            for n in npcs_stub.list_for_world(world["id"]):
                assert n["activity_tier"] == TIER_IDLE
        finally:
            worlds_stub.delete(world["id"])


# ---------------------------------------------------------------------------
# Budget + summary
# ---------------------------------------------------------------------------


class TestComputeBudget:
    def test_empty_world(self):
        world = worlds_stub.create(name="W")
        try:
            view = npcs_stub.compute_world_budget(world["id"])
            assert view["npc_count"] == 0
            assert view["over_budget"] is False
        finally:
            worlds_stub.delete(world["id"])

    def test_under_budget(self):
        world = worlds_stub.create(name="W")
        try:
            npcs_stub.create(world_id=world["id"], name="a", compute_budget=100)
            npcs_stub.create(world_id=world["id"], name="b", compute_budget=200)
            view = npcs_stub.compute_world_budget(world["id"])
            assert view["total_used"] == 300
            assert view["over_budget"] is False
        finally:
            worlds_stub.delete(world["id"])

    def test_over_budget_flag(self):
        world = worlds_stub.create(name="W")
        try:
            # Lower the world total to force overage.
            from xijian_api.stubs import world_compute_config as wcc_stub
            wcc_stub.update(world["id"], {"total_token_budget": 100})
            npcs_stub.create(world_id=world["id"], name="a", compute_budget=80)
            npcs_stub.create(world_id=world["id"], name="b", compute_budget=80)
            view = npcs_stub.compute_world_budget(world["id"])
            assert view["over_budget"] is True
        finally:
            worlds_stub.delete(world["id"])

    def test_tier_over(self):
        world = worlds_stub.create(name="W")
        try:
            # 4 high_active NPCs → 1 over the cap.
            for i in range(4):
                npcs_stub.create(
                    world_id=world["id"], name=f"n{i}",
                    activity_tier=TIER_HIGH_ACTIVE,
                )
            view = npcs_stub.compute_world_budget(world["id"])
            assert view["high_active_count"] == 4
            assert view["high_active_over"] == 1
        finally:
            worlds_stub.delete(world["id"])


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


class TestTickWorld:
    def test_empty_world(self):
        world = worlds_stub.create(name="W")
        try:
            out = npcs_stub.tick_world(world["id"])
            assert out["fired"] == 0
            assert out["demoted"] == 0
        finally:
            worlds_stub.delete(world["id"])

    def test_unknown_world_returns_empty(self):
        out = npcs_stub.tick_world("world_phantom")
        # World doesn't exist — no entries, no error.
        assert out["fired"] == 0

    def test_marks_last_think_at(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(world_id=world["id"], name="x")
            npcs_stub.tick_world(world["id"])
            refreshed = npcs_stub.get(npc["id"])
            assert refreshed["last_think_at"] is not None
        finally:
            worlds_stub.delete(world["id"])

    def test_demotes_over_budget(self):
        world = worlds_stub.create(name="W")
        try:
            from xijian_api.stubs import world_compute_config as wcc_stub
            wcc_stub.update(world["id"], {"total_token_budget": 100})
            npcs_stub.create(world_id=world["id"], name="a", compute_budget=80)
            npcs_stub.create(world_id=world["id"], name="b", compute_budget=80)
            out = npcs_stub.tick_world(world["id"])
            assert out["demoted"] >= 1
            # The lowest-importance NPC was demoted.
            tiers = [n["activity_tier"] for n in npcs_stub.list_for_world(world["id"])]
            assert TIER_IDLE in tiers
        finally:
            worlds_stub.delete(world["id"])

    def test_demotes_idle_high_active(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(
                world_id=world["id"], name="x", activity_tier=TIER_HIGH_ACTIVE
            )
            # Backdate last_think_at so the idle threshold trips.
            npcs_stub.update(npc["id"], {"last_think_at": 0.0})
            npcs_stub.tick_world(world["id"])
            refreshed = npcs_stub.get(npc["id"])
            assert refreshed["activity_tier"] == TIER_LOW_ACTIVE
            log = [
                e for e in stubs_state.npc_scheduling_log.values()
                if e.get("npc_id") == npc["id"] and e["action"] == "degrade"
            ]
            assert len(log) == 1
            assert log[0]["reason"] == "idle_timeout"
        finally:
            worlds_stub.delete(world["id"])

    def test_demotes_on_queue_pressure(self):
        world = worlds_stub.create(name="W")
        try:
            npc = npcs_stub.create(
                world_id=world["id"], name="x", activity_tier=TIER_HIGH_ACTIVE
            )
            npcs_stub.update(npc["id"], {"last_think_at": 0.0})
            npcs_stub.tick_world(
                world["id"], queue_p99_latency_s=DEFAULT_DEGRADE_P99_LATENCY_S + 1
            )
            refreshed = npcs_stub.get(npc["id"])
            # LLM-queue pressure is severe — directly to idle, not low.
            assert refreshed["activity_tier"] == TIER_LOW_ACTIVE
            log = [
                e for e in stubs_state.npc_scheduling_log.values()
                if e.get("npc_id") == npc["id"] and e["action"] == "degrade"
            ]
            assert any(e["reason"] == "overload" for e in log)
        finally:
            worlds_stub.delete(world["id"])


class TestTickAll:
    def test_walks_every_world(self):
        world_a = worlds_stub.create(name="A")
        world_b = worlds_stub.create(name="B")
        try:
            npcs_stub.create(world_id=world_a["id"], name="a")
            npcs_stub.create(world_id=world_b["id"], name="b")
            out = npcs_stub.tick_all()
            assert world_a["id"] in out["worlds"]
            assert world_b["id"] in out["worlds"]
        finally:
            worlds_stub.delete(world_a["id"])
            worlds_stub.delete(world_b["id"])


# ---------------------------------------------------------------------------
# Background tick thread
# ---------------------------------------------------------------------------


class TestTickLifecycle:
    def test_start_stop(self):
        npcs_stub.stop_tick()
        out = npcs_stub.start_tick()
        # Env default is "0" → disabled, so it should refuse.
        # To exercise the start path, flip the env flag temporarily.
        old = os.environ.get("XIJIAN_NPC_TICK")
        os.environ["XIJIAN_NPC_TICK"] = "1"
        try:
            npcs_stub.stop_tick()
            out = npcs_stub.start_tick()
            assert out["started"] is True
            npcs_stub.stop_tick()
        finally:
            os.environ["XIJIAN_NPC_TICK"] = old or "0"

    def test_status(self):
        out = npcs_stub.tick_status()
        assert "running" in out
        assert "interval_s" in out
        assert "suspended" in out

    def test_env_disabled_blocks_start(self, monkeypatch):
        monkeypatch.setenv("XIJIAN_NPC_TICK", "0")
        out = npcs_stub.start_tick()
        assert out["started"] is False
        assert out["reason"] == "disabled_by_env"


# ---------------------------------------------------------------------------
# A5.4 overload cross-link
# ---------------------------------------------------------------------------


class TestOverloadHandler:
    def test_handler_installed_at_seed(self):
        # The autouse fixture called seed_all() → install_overload_handler.
        handlers = ov_stub.list_action_handlers()
        registered = handlers.get(ov_stub.ACTION_SUSPEND_IDLE_NPCS, [])
        assert len(registered) >= 1
        # The registered handler is the module-level
        # ``_suspend_for_overload`` from npcs.py — its repr carries
        # the function name, so a substring check pins the contract.
        assert any("_suspend_for_overload" in h for h in registered)

    def test_suspend_drops_active_npcs(self):
        world = worlds_stub.create(name="W")
        try:
            npcs_stub.create(world_id=world["id"], name="a", activity_tier=TIER_HIGH_ACTIVE)
            npcs_stub.create(world_id=world["id"], name="b", activity_tier=TIER_LOW_ACTIVE)
            # CPU pressure → ACTION_SUSPEND_IDLE_NPCS (per overload
            # metric→action mapping).  This is the path that should
            # fan out to the NPC stub's _suspend_for_overload handler.
            ov_stub.simulate_overload(ov_stub.METRIC_CPU)
            tiers = [n["activity_tier"] for n in npcs_stub.list_for_world(world["id"])]
            assert all(t == TIER_IDLE for t in tiers), tiers
        finally:
            # Make sure we resume so other tests aren't affected.
            npcs_stub.resume_from_overload()
            worlds_stub.delete(world["id"])

    def test_suspended_skips_tick(self):
        # Force suspend state via CPU overload, then tick → should return suspended.
        world = worlds_stub.create(name="W")
        try:
            npcs_stub.create(world_id=world["id"], name="a")
            ov_stub.simulate_overload(ov_stub.METRIC_CPU)
            out = npcs_stub.tick_world(world["id"])
            assert out.get("suspended") is True, out
        finally:
            npcs_stub.resume_from_overload()
            worlds_stub.delete(world["id"])

    def test_resume_unblocks_tick(self):
        world = worlds_stub.create(name="W")
        try:
            npcs_stub.create(world_id=world["id"], name="a", activity_tier=TIER_IDLE)
            ov_stub.simulate_overload(ov_stub.METRIC_CPU)
            npcs_stub.resume_from_overload()
            out = npcs_stub.tick_world(world["id"])
            assert out.get("suspended") is False, out
        finally:
            worlds_stub.delete(world["id"])


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class TestHttpListCreate:
    def test_list_global(self, client, auth_headers, world, npc):
        res = client.get("/v1/xijian/npcs", headers=auth_headers)
        assert res.status_code == 200
        ids = [n["id"] for n in res.get_json()["data"]]
        assert npc in ids

    def test_list_by_world(self, client, auth_headers, world, npc):
        res = client.get(
            f"/v1/xijian/npcs?world_id={world}", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["world_id"] == world
        ids = [n["id"] for n in data["npcs"]]
        assert npc in ids

    def test_create_missing_world_id(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/npcs",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_world_id"

    def test_create_unknown_world_returns_404(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/npcs",
            json={"world_id": "world_phantom", "name": "x"},
            headers=auth_headers,
        )
        assert res.status_code == 404
        assert res.get_json()["error"]["code"] == "world_not_found"

    def test_create_50_cap_via_http(self, client, auth_headers, world):
        for i in range(MAX_NPCS_PER_WORLD):
            res = client.post(
                "/v1/xijian/npcs",
                json={"world_id": world, "name": f"n{i}", "compute_budget": 1},
                headers=auth_headers,
            )
            assert res.status_code == 201
        res = client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "overflow", "compute_budget": 1},
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert res.get_json()["error"]["code"] == "npc_cap_exceeded"


class TestHttpGetPatchDelete:
    def test_get(self, client, auth_headers, npc):
        res = client.get(f"/v1/xijian/npcs/{npc}", headers=auth_headers)
        assert res.status_code == 200

    def test_get_404(self, client, auth_headers):
        res = client.get("/v1/xijian/npcs/npc_phantom", headers=auth_headers)
        assert res.status_code == 404

    def test_patch(self, client, auth_headers, npc):
        res = client.patch(
            f"/v1/xijian/npcs/{npc}",
            json={"name": "renamed"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["name"] == "renamed"

    def test_patch_via_tier_rejected(self, client, auth_headers, npc):
        res = client.patch(
            f"/v1/xijian/npcs/{npc}",
            json={"activity_tier": TIER_IDLE},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_delete(self, client, auth_headers, npc):
        res = client.delete(f"/v1/xijian/npcs/{npc}", headers=auth_headers)
        assert res.status_code == 200
        res = client.get(f"/v1/xijian/npcs/{npc}", headers=auth_headers)
        assert res.status_code == 404


class TestHttpTier:
    def test_set_tier(self, client, auth_headers, npc):
        res = client.put(
            f"/v1/xijian/npcs/{npc}/tier",
            json={"activity_tier": TIER_HIGH_ACTIVE, "reason": "manual"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["activity_tier"] == TIER_HIGH_ACTIVE

    def test_set_tier_missing_field(self, client, auth_headers, npc):
        res = client.put(
            f"/v1/xijian/npcs/{npc}/tier",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "missing_activity_tier"

    def test_set_tier_invalid(self, client, auth_headers, npc):
        res = client.put(
            f"/v1/xijian/npcs/{npc}/tier",
            json={"activity_tier": "loose"},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_set_tier_unknown_npc(self, client, auth_headers):
        res = client.put(
            "/v1/xijian/npcs/npc_phantom/tier",
            json={"activity_tier": TIER_IDLE},
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_put_state(self, client, auth_headers, npc):
        res = client.put(
            f"/v1/xijian/npcs/{npc}/state",
            json={"state_json": {"hunger": 0.5}},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["state_json"]["hunger"] == 0.5

    def test_put_state_invalid_body(self, client, auth_headers, npc):
        res = client.put(
            f"/v1/xijian/npcs/{npc}/state",
            json={"state_json": "not a dict"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "invalid_state_json"


class TestHttpScheduling:
    def test_summary(self, client, auth_headers, world, npc):
        res = client.get(
            "/v1/xijian/npcs/scheduling/summary", headers=auth_headers
        )
        assert res.status_code == 200
        worlds = res.get_json()["worlds"]
        assert any(w["world_id"] == world for w in worlds)

    def test_log_filtered(self, client, auth_headers, world, npc):
        client.put(
            f"/v1/xijian/npcs/{npc}/tier",
            json={"activity_tier": TIER_HIGH_ACTIVE, "reason": "manual"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/npcs/scheduling/log?world_id={world}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        entries = res.get_json()["entries"]
        assert any(e["action"] == "wake" for e in entries)

    def test_per_npc_log(self, client, auth_headers, npc):
        client.put(
            f"/v1/xijian/npcs/{npc}/tier",
            json={"activity_tier": TIER_IDLE, "reason": "manual"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/npcs/{npc}/scheduling/log", headers=auth_headers
        )
        assert res.status_code == 200
        entries = res.get_json()["entries"]
        assert any(e["action"] == "sleep" for e in entries)

    def test_status(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/npcs/scheduling/status", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert "running" in data

    def test_resume(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/npcs/scheduling/resume", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.get_json()["resumed"] is True

    def test_dev_tick_blocked_without_dev_flag(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/npcs/scheduling/tick",
            json={"world_id": world},
            headers=auth_headers,
        )
        assert res.status_code == 403
        assert res.get_json()["error"]["code"] == "dev_only"

    def test_dev_tick_all_blocked(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/npcs/scheduling/tick/all", headers=auth_headers
        )
        assert res.status_code == 403

    def test_affected_preview(self, client, auth_headers, world, npc):
        res = client.post(
            "/v1/xijian/npcs/affected/preview",
            json={"world_id": world, "event": {"payload": {}}},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert npc not in res.get_json()["affected_npcs"]
        # Now flip to high_active and re-check.
        client.put(
            f"/v1/xijian/npcs/{npc}/tier",
            json={"activity_tier": TIER_HIGH_ACTIVE},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/npcs/affected/preview",
            json={"world_id": world, "event": {"payload": {}}},
            headers=auth_headers,
        )
        assert npc in res.get_json()["affected_npcs"]


# ---------------------------------------------------------------------------
# Auth coverage
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/npcs"),
            ("POST", "/v1/xijian/npcs"),
            ("GET", "/v1/xijian/npcs/npc_phantom"),
            ("PATCH", "/v1/xijian/npcs/npc_phantom"),
            ("DELETE", "/v1/xijian/npcs/npc_phantom"),
            ("PUT", "/v1/xijian/npcs/npc_phantom/tier"),
            ("PUT", "/v1/xijian/npcs/npc_phantom/state"),
            ("GET", "/v1/xijian/npcs/scheduling/log"),
            ("GET", "/v1/xijian/npcs/scheduling/summary"),
            ("GET", "/v1/xijian/npcs/scheduling/status"),
            ("POST", "/v1/xijian/npcs/scheduling/tick"),
            ("POST", "/v1/xijian/npcs/scheduling/tick/all"),
            ("POST", "/v1/xijian/npcs/scheduling/resume"),
            ("POST", "/v1/xijian/npcs/affected/preview"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            f"{method} {path} should require auth, got {res.status_code}"
        )
