"""Tests for ``stubs.worlds`` (A4.2) and ``/v1/xijian/worlds/*``.

The A4.2 worlds surface covers:

* **CRUD** — create / list / get / patch / delete (operator-driven).
* **State & views** — combined view (world + env + compute config +
  NPC count), white-listed state-patch.
* **Lifecycle** — switch-active, two-step reset (AC-4 double-confirm).
* **Cross-module** — environment / compute-config / audit / NPC list
  routing from the world surface.
* **Legacy aliases** — pre-A4.2 transition / add_event endpoints
  are kept so old test fixtures still work.

Auth: every endpoint requires a Bearer token.  Auth coverage lives
in :class:`TestAuthCoverage` at the bottom.
"""

from __future__ import annotations

import os
import time

import pytest

from xijian_api.stubs import (
    npcs as npcs_stub,
)
from xijian_api.stubs import (
    state as stubs_state,
)
from xijian_api.stubs import (
    world_audit as audit_stub,
)
from xijian_api.stubs import (
    world_compute_config as wcc_stub,
)
from xijian_api.stubs import (
    world_environment as env_stub,
)
from xijian_api.stubs import (
    worlds as worlds_stub,
)
from xijian_api.stubs.worlds import (
    DEFAULT_WORLD_ID,
    RESET_TOKEN_TTL_SECONDS,
    WHITELISTED_STATE_FIELDS,
    WorldError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    """Create a world via the HTTP surface and return its id."""
    body = {
        "name": "Test World",
        "world_doc_path": "worlds/test/lore.md",
        "config_path": "worlds/test/config.json",
        "state_doc_path": "worlds/test/state.json",
    }
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestWhitelistedFields:
    def test_state_whitelist_is_frozen(self):
        # Spec Dev.md §4.3.3 — these are the canonical system dimensions.
        assert "economy" in WHITELISTED_STATE_FIELDS
        assert "health" in WHITELISTED_STATE_FIELDS
        assert "diet" in WHITELISTED_STATE_FIELDS
        assert "stamina" in WHITELISTED_STATE_FIELDS
        assert "mentality" in WHITELISTED_STATE_FIELDS

    def test_token_ttl_is_60_seconds(self):
        assert RESET_TOKEN_TTL_SECONDS == 60.0


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateWorld:
    def test_create_minimal(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/worlds",
            json={"name": "W1"},
            headers=auth_headers,
        )
        assert res.status_code == 201
        data = res.get_json()
        assert data["name"] == "W1"
        assert data["is_active"] is True
        assert data["world_doc_path"] == ""
        assert data["config_path"] == ""
        assert data["state_doc_path"] == ""
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_full(self, client, auth_headers):
        body = {
            "name": "W2",
            "world_doc_path": "worlds/w2/lore.md",
            "config_path": "worlds/w2/config.json",
            "state_doc_path": "worlds/w2/state.json",
            "world_id": "world_my_w2",
            "is_active": False,
        }
        res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
        assert res.status_code == 201
        data = res.get_json()
        assert data["id"] == "world_my_w2"
        assert data["is_active"] is False

    def test_create_missing_name_returns_400(self, client, auth_headers):
        res = client.post("/v1/xijian/worlds", json={}, headers=auth_headers)
        assert res.status_code == 400
        assert res.get_json().get("error", {}).get("code") == "missing_name"

    def test_create_invalid_body_returns_400(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/worlds",
            data="not json",
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json().get("error", {}).get("code") == "invalid_body"

    def test_create_duplicate_id_returns_400(self, client, auth_headers):
        body = {"name": "Dup", "world_id": "world_dup_test"}
        res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
        assert res.status_code == 201
        res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
        assert res.status_code == 400
        assert res.get_json().get("error", {}).get("code") == "world_error"

    def test_create_materializes_env_and_compute(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/worlds",
            json={"name": "Side Effect"},
            headers=auth_headers,
        )
        wid = res.get_json()["id"]
        assert wid in stubs_state.world_environment
        assert wid in stubs_state.world_compute_config
        # Audit log gets a "create" entry.
        entries = audit_stub.list_log(world_id=wid, action="create")
        assert any(e["action"] == "create" for e in entries)


class TestListWorlds:
    def test_list_includes_seeded_demo(self, client, auth_headers):
        res = client.get("/v1/xijian/worlds", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        ids = [w["id"] for w in data["data"]]
        assert DEFAULT_WORLD_ID in ids

    def test_list_orders_active_first(self, client, auth_headers):
        # Create an inactive one and an active one.
        client.post(
            "/v1/xijian/worlds",
            json={"name": "Inactive", "world_id": "world_inactive_test", "is_active": False},
            headers=auth_headers,
        )
        client.post(
            "/v1/xijian/worlds",
            json={"name": "Active", "world_id": "world_active_test"},
            headers=auth_headers,
        )
        res = client.get("/v1/xijian/worlds", headers=auth_headers)
        names = [w["name"] for w in res.get_json()["data"]]
        # Active entries come before inactive ones.
        active_idx = names.index("Active")
        inactive_idx = names.index("Inactive")
        assert active_idx < inactive_idx


class TestGetWorld:
    def test_get_returns_record(self, client, auth_headers, world):
        res = client.get(f"/v1/xijian/worlds/{world}", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert data["id"] == world

    def test_get_unknown_returns_404(self, client, auth_headers):
        res = client.get("/v1/xijian/worlds/world_phantom", headers=auth_headers)
        assert res.status_code == 404
        assert res.get_json().get("error", {}).get("code") == "world_not_found"


class TestPatchWorld:
    def test_patch_mutable_field(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/worlds/{world}",
            json={"name": "Renamed", "world_doc_path": "new/lore.md"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["name"] == "Renamed"
        assert data["world_doc_path"] == "new/lore.md"

    def test_patch_id_immutable(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/worlds/{world}",
            json={"id": "world_other"},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_patch_unknown_returns_404(self, client, auth_headers):
        res = client.patch(
            "/v1/xijian/worlds/world_phantom",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestDeleteWorld:
    def test_delete_succeeds(self, client, auth_headers, world):
        res = client.delete(f"/v1/xijian/worlds/{world}", headers=auth_headers)
        assert res.status_code == 200
        assert res.get_json()["deleted"] is True
        # Subsequent get → 404.
        res = client.get(f"/v1/xijian/worlds/{world}", headers=auth_headers)
        assert res.status_code == 404

    def test_delete_unknown_returns_404(self, client, auth_headers):
        res = client.delete("/v1/xijian/worlds/world_phantom", headers=auth_headers)
        assert res.status_code == 404

    def test_delete_writes_audit_before_removal(self, client, auth_headers, world):
        client.delete(f"/v1/xijian/worlds/{world}", headers=auth_headers)
        # The audit log still has the entry.
        entries = audit_stub.list_log(world_id=world, action="delete")
        assert any(e["action"] == "delete" for e in entries)


# ---------------------------------------------------------------------------
# State & views
# ---------------------------------------------------------------------------


class TestWorldState:
    def test_get_state_combined(self, client, auth_headers, world):
        res = client.get(f"/v1/xijian/worlds/{world}/state", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert data["world_id"] == world
        assert "environment" in data
        assert "compute_config" in data
        assert data["environment"]["weather"] == env_stub.DEFAULT_WEATHER
        assert data["compute_config"]["active_tier"] == wcc_stub.DEFAULT_ACTIVE_TIER
        assert data["npc_count"] == 0

    def test_get_state_unknown_returns_404(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/worlds/world_phantom/state", headers=auth_headers
        )
        assert res.status_code == 404

    def test_patch_state_whitelisted(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/worlds/{world}/state",
            json={"economy": 42, "health": 95},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["state"]["economy"] == 42
        assert data["state"]["health"] == 95

    def test_patch_state_unknown_field_is_silently_accepted(
        self, client, auth_headers, world
    ):
        # Forward-compat — unknown keys are stored but DEBUG-logged.
        res = client.patch(
            f"/v1/xijian/worlds/{world}/state",
            json={"custom_metric": 99},
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_patch_state_unknown_world(self, client, auth_headers):
        res = client.patch(
            "/v1/xijian/worlds/world_phantom/state",
            json={"economy": 10},
            headers=auth_headers,
        )
        assert res.status_code == 404

    def test_patch_state_doc(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/state/doc",
            json={"state_doc_path": "new/state.json"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["state_doc_path"] == "new/state.json"


# ---------------------------------------------------------------------------
# Lifecycle — switch / reset
# ---------------------------------------------------------------------------


class TestSwitchActive:
    def test_switch_marks_last_active(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/switch", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["last_active_at"] is not None
        assert data["last_active_at"] > 0

    def test_switch_inactive_world_returns_409(self, client, auth_headers):
        client.post(
            "/v1/xijian/worlds",
            json={"name": "Inactive", "world_id": "world_inactive_switch", "is_active": False},
            headers=auth_headers,
        )
        res = client.post(
            "/v1/xijian/worlds/world_inactive_switch/switch",
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert res.get_json().get("error", {}).get("code") == "world_inactive"

    def test_switch_unknown_returns_404(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/worlds/world_phantom/switch", headers=auth_headers
        )
        assert res.status_code == 404


class TestReset:
    def test_full_reset_flow(self, client, auth_headers, world):
        # Add an NPC so we can verify reset wipes it.
        client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "Test NPC"},
            headers=auth_headers,
        )
        # Patch some state to make sure reset clears it.
        client.patch(
            f"/v1/xijian/worlds/{world}/state",
            json={"economy": 99},
            headers=auth_headers,
        )
        # Preview.
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/preview", headers=auth_headers
        )
        assert res.status_code == 200
        token = res.get_json()["reset_token"]
        # Confirm.
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/confirm",
            json={"reset_token": token},
            headers=auth_headers,
        )
        assert res.status_code == 200
        # World still exists with the same id but defaults reapplied.
        res = client.get(f"/v1/xijian/worlds/{world}/state", headers=auth_headers)
        assert res.status_code == 200
        state = res.get_json()
        assert "economy" not in state["environment"]
        # NPC was wiped.
        assert state["npc_count"] == 0

    def test_confirm_without_token_returns_400(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/confirm",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json().get("error", {}).get("code") == "missing_reset_token"

    def test_confirm_with_bad_token_returns_403(self, client, auth_headers, world):
        client.post(
            f"/v1/xijian/worlds/{world}/reset/preview", headers=auth_headers
        )
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/confirm",
            json={"reset_token": "wrong-token"},
            headers=auth_headers,
        )
        assert res.status_code == 403
        assert res.get_json().get("error", {}).get("code") == "reset_token_mismatch"

    def test_confirm_without_preview_returns_409(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/confirm",
            json={"reset_token": "any"},
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert res.get_json().get("error", {}).get("code") == "no_pending_reset"

    def test_confirm_with_expired_token_returns_408(self, client, auth_headers, world, monkeypatch):
        # Issue a preview, then advance past the TTL.
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/preview", headers=auth_headers
        )
        token = res.get_json()["reset_token"]
        # Use a stub-level clock patch — the route doesn't expose `now`
        # so we directly mutate the token store.
        handle = worlds_stub._reset_tokens[world]
        handle["expires_at"] = time.time() - 1
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/confirm",
            json={"reset_token": token},
            headers=auth_headers,
        )
        assert res.status_code == 408
        assert res.get_json().get("error", {}).get("code") == "reset_token_expired"

    def test_cancel_reset_drops_token(self, client, auth_headers, world):
        client.post(
            f"/v1/xijian/worlds/{world}/reset/preview", headers=auth_headers
        )
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/cancel", headers=auth_headers
        )
        assert res.status_code == 200
        # Confirming now should fail with no_pending_reset.
        res = client.post(
            f"/v1/xijian/worlds/{world}/reset/confirm",
            json={"reset_token": "x"},
            headers=auth_headers,
        )
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# Cross-module — env / compute / audit / npcs
# ---------------------------------------------------------------------------


class TestEnvironment:
    def test_get_environment(self, client, auth_headers, world):
        res = client.get(
            f"/v1/xijian/worlds/{world}/environment", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["weather"] == env_stub.DEFAULT_WEATHER

    def test_patch_environment(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/worlds/{world}/environment",
            json={"weather": "rain", "time_of_day": 360},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["weather"] == "rain"
        assert data["time_of_day"] == 360
        # light_level auto-derives from time_of_day.
        assert 0.0 <= data["light_level"] <= 1.0

    def test_environment_unknown_world_returns_404(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/worlds/world_phantom/environment", headers=auth_headers
        )
        assert res.status_code == 404


class TestComputeView:
    def test_get_compute(self, client, auth_headers, world):
        res = client.get(
            f"/v1/xijian/worlds/{world}/compute", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["active_tier"] == wcc_stub.DEFAULT_ACTIVE_TIER
        assert data["total_token_budget"] == wcc_stub.DEFAULT_TOTAL_TOKEN_BUDGET

    def test_patch_compute(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/worlds/{world}/compute",
            json={"max_npcs": 30},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["max_npcs"] == 30

    def test_patch_compute_invalid_max_npcs(self, client, auth_headers, world):
        res = client.patch(
            f"/v1/xijian/worlds/{world}/compute",
            json={"max_npcs": 999},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_flip_tier_to_high(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/compute/tier",
            json={"active_tier": "high_active"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["active_tier"] == "high_active"

    def test_flip_tier_invalid_returns_400(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/compute/tier",
            json={"active_tier": "loose"},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_compute_unknown_world(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/worlds/world_phantom/compute", headers=auth_headers
        )
        assert res.status_code == 404


class TestAuditView:
    def test_audit_lists_entries(self, client, auth_headers, world):
        # Trigger an audit-eligible action.
        client.patch(
            f"/v1/xijian/worlds/{world}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/worlds/{world}/audit", headers=auth_headers
        )
        assert res.status_code == 200
        actions = [e["action"] for e in res.get_json()["entries"]]
        assert "update" in actions

    def test_audit_action_filter(self, client, auth_headers, world):
        client.patch(
            f"/v1/xijian/worlds/{world}",
            json={"name": "X"},
            headers=auth_headers,
        )
        client.post(
            f"/v1/xijian/worlds/{world}/compute/tier",
            json={"active_tier": "high_active"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/worlds/{world}/audit?action=tier_change",
            headers=auth_headers,
        )
        assert res.status_code == 200
        entries = res.get_json()["entries"]
        assert all(e["action"] == "tier_change" for e in entries)

    def test_audit_unknown_world(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/worlds/world_phantom/audit", headers=auth_headers
        )
        assert res.status_code == 404


class TestWorldNpcsView:
    def test_list_via_world(self, client, auth_headers, world):
        client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "A"},
            headers=auth_headers,
        )
        client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "B", "activity_tier": "high_active"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/worlds/{world}/npcs", headers=auth_headers
        )
        assert res.status_code == 200
        names = [n["name"] for n in res.get_json()["npcs"]]
        assert "A" in names
        assert "B" in names

    def test_filter_by_tier(self, client, auth_headers, world):
        client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "LowNPC"},
            headers=auth_headers,
        )
        client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "HighNPC", "activity_tier": "high_active"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/worlds/{world}/npcs?tier=high_active", headers=auth_headers
        )
        names = [n["name"] for n in res.get_json()["npcs"]]
        assert names == ["HighNPC"]

    def test_unknown_world_returns_404(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/worlds/world_phantom/npcs", headers=auth_headers
        )
        assert res.status_code == 404


class TestWorldComputeSummary:
    def test_summary(self, client, auth_headers, world):
        client.post(
            "/v1/xijian/npcs",
            json={"world_id": world, "name": "X"},
            headers=auth_headers,
        )
        res = client.get(
            f"/v1/xijian/worlds/{world}/compute/summary", headers=auth_headers
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["npc_count"] == 1


class TestWorldsGlobalSummary:
    def test_summary(self, client, auth_headers):
        res = client.get("/v1/xijian/worlds/summary", headers=auth_headers)
        assert res.status_code == 200
        data = res.get_json()
        assert "worlds_total" in data
        assert "worlds_active" in data
        assert isinstance(data["worlds"], list)


# ---------------------------------------------------------------------------
# Legacy aliases (pre-A4.2)
# ---------------------------------------------------------------------------


class TestLegacyTransition:
    def test_transition_writes_audit(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/transition",
            json={"to_location": "Shibuya", "transport": "walk"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        # Audit log has a "transition" entry.
        entries = audit_stub.list_log(world_id=world, action="transition")
        assert any(e["action"] == "transition" for e in entries)

    def test_transition_missing_to_location(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/transition",
            json={"transport": "walk"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json().get("error", {}).get("code") == "missing_to_location"

    def test_transition_unknown_world(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/worlds/world_phantom/transition",
            json={"to_location": "X", "transport": "walk"},
            headers=auth_headers,
        )
        assert res.status_code == 404


class TestLegacyAddEvent:
    def test_add_event_creates_custom_event(self, client, auth_headers, world):
        res = client.post(
            f"/v1/xijian/worlds/{world}/event",
            json={"name": "Custom Adventure", "description": "Story hook"},
            headers=auth_headers,
        )
        assert res.status_code == 201
        data = res.get_json()
        assert data["event_id"] is not None or "id" in data
        # World event_instances has a fired record.
        assert any(
            v.get("world_id") == world
            for v in stubs_state.world_event_instances.values()
        )


# ---------------------------------------------------------------------------
# Stub-level
# ---------------------------------------------------------------------------


class TestStubDirect:
    def test_create_and_get_round_trip(self):
        record = worlds_stub.create(name="Stub", world_doc_path="lore.md")
        wid = record["id"]
        try:
            assert worlds_stub.get(wid)["name"] == "Stub"
        finally:
            worlds_stub.delete(wid)

    def test_create_missing_name_raises(self):
        with pytest.raises(WorldError):
            worlds_stub.create(name="")

    def test_update_immutable_keys(self):
        record = worlds_stub.create(name="X")
        wid = record["id"]
        try:
            with pytest.raises(WorldError):
                worlds_stub.update(wid, {"id": "world_other"})
            with pytest.raises(WorldError):
                worlds_stub.update(wid, {"created_at": 0})
        finally:
            worlds_stub.delete(wid)

    def test_state_whitelist_via_stub(self):
        record = worlds_stub.create(name="X")
        wid = record["id"]
        try:
            state_blob, err = worlds_stub.update_state(
                wid, {"economy": 50, "health": 100}
            )
            assert err is None
            assert state_blob["economy"] == 50
            assert state_blob["health"] == 100
        finally:
            worlds_stub.delete(wid)

    def test_preview_reset_returns_token(self):
        record = worlds_stub.create(name="X")
        wid = record["id"]
        try:
            out = worlds_stub.preview_reset(wid)
            assert out is not None
            assert "reset_token" in out
            assert "expires_at" in out
        finally:
            worlds_stub.delete(wid)

    def test_seed_default_idempotent(self):
        # Already seeded by the autouse fixture — verify it's there.
        assert DEFAULT_WORLD_ID in stubs_state.worlds


# ---------------------------------------------------------------------------
# Auth coverage — every endpoint requires a Bearer
# ---------------------------------------------------------------------------


class TestAuthCoverage:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/v1/xijian/worlds"),
            ("GET", "/v1/xijian/worlds/summary"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo"),
            ("PATCH", "/v1/xijian/worlds/world_modern_tokyo"),
            ("DELETE", "/v1/xijian/worlds/world_modern_tokyo"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo/state"),
            ("PATCH", "/v1/xijian/worlds/world_modern_tokyo/state"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/switch"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/reset/preview"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/reset/confirm"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/reset/cancel"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo/environment"),
            ("PATCH", "/v1/xijian/worlds/world_modern_tokyo/environment"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo/compute"),
            ("PATCH", "/v1/xijian/worlds/world_modern_tokyo/compute"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/compute/tier"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo/audit"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo/npcs"),
            ("GET", "/v1/xijian/worlds/world_modern_tokyo/compute/summary"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/transition"),
            ("POST", "/v1/xijian/worlds/world_modern_tokyo/event"),
        ],
    )
    def test_requires_bearer(self, client, method, path):
        res = client.open(method=method, path=path)
        assert res.status_code in (401, 403), (
            f"{method} {path} should require auth, got {res.status_code}"
        )
