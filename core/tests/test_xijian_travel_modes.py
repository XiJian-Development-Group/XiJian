"""Tests for ``stubs.travel_modes`` (A4.3) and the
``/v1/xijian/scenes/travel-modes/*`` endpoints.

Covers:

* **Pure helpers** — speed / stamina / event-chance validation,
  ``estimate_trip`` shape and event-roll behaviour.
* **CRUD** — create / list / get / patch / delete.
* **Estimate endpoint** — cost preview with and without
  ``random_roll``.
* **Auth** — every endpoint requires a Bearer token.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import travel_modes as tm_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    body = {"name": "Travel Test World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


@pytest.fixture()
def walk_mode(client, auth_headers, world):
    body = {
        "world_id": world,
        "name": "walk",
        "speed_factor": 1.0,
        "stamina_cost": 5.0,
        "event_chance": 0.1,
    }
    res = client.post(
        "/v1/xijian/scenes/travel-modes", json=body, headers=auth_headers
    )
    assert res.status_code == 201
    return res.get_json()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_speed_factor_must_be_positive(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_speed_factor(0)
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_speed_factor(-1.0)

    def test_speed_factor_must_be_number(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_speed_factor("fast")
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_speed_factor(True)  # bool is rejected

    def test_stamina_cost_must_be_non_negative(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_stamina_cost(-0.1)
        assert tm_stub._validate_stamina_cost(0) == 0.0
        assert tm_stub._validate_stamina_cost(2.5) == 2.5

    def test_event_chance_bounded(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_event_chance(-0.1)
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub._validate_event_chance(1.1)
        assert tm_stub._validate_event_chance(0.0) == 0.0
        assert tm_stub._validate_event_chance(1.0) == 1.0

    def test_estimate_trip_speed_factor_doubles_time(self):
        mode = {"speed_factor": 0.5, "stamina_cost": 1, "event_chance": 0.0}
        out = tm_stub.estimate_trip(mode)
        # base 60 / 0.5 = 120
        assert out["duration_seconds"] == 120.0
        assert out["stamina_cost"] == 1
        assert out["event_chance"] == 0.0
        assert "event_triggered" not in out

    def test_estimate_trip_event_roll_below_chance_fires(self):
        mode = {"speed_factor": 1.0, "stamina_cost": 0, "event_chance": 0.5}
        out = tm_stub.estimate_trip(mode, random_roll=0.4)
        assert out["event_triggered"] is True

    def test_estimate_trip_event_roll_above_chance_skips(self):
        mode = {"speed_factor": 1.0, "stamina_cost": 0, "event_chance": 0.5}
        out = tm_stub.estimate_trip(mode, random_roll=0.6)
        assert out["event_triggered"] is False

    def test_estimate_trip_event_roll_must_be_bounded(self):
        mode = {"speed_factor": 1.0, "stamina_cost": 0, "event_chance": 0.5}
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.estimate_trip(mode, random_roll=1.5)

    def test_estimate_trip_requires_dict(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.estimate_trip("not a dict")

    def test_estimate_trip_uses_custom_base(self):
        mode = {"speed_factor": 1.0, "stamina_cost": 0, "event_chance": 0.0}
        out = tm_stub.estimate_trip(mode, base_seconds=30.0)
        assert out["duration_seconds"] == 30.0

    def test_default_base_is_60(self):
        assert tm_stub.DEFAULT_BASE_TRAVEL_SECONDS == 60.0


# ---------------------------------------------------------------------------
# Stub CRUD
# ---------------------------------------------------------------------------


class TestStubCRUD:
    def test_create_minimal(self, world):
        m = tm_stub.create(world_id=world, name="walk")
        assert m["speed_factor"] == 1.0
        assert m["stamina_cost"] == 0.0
        assert m["event_chance"] == 0.0

    def test_create_rejects_unknown_world(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.create(world_id="world_does_not_exist", name="walk")

    def test_create_rejects_blank_name(self, world):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.create(world_id=world, name="   ")

    def test_create_rejects_duplicate_id(self, world):
        tm_stub.create(world_id=world, name="walk", mode_id="tmode_dup")
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.create(world_id=world, name="walk", mode_id="tmode_dup")

    def test_create_rejects_zero_speed(self, world):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.create(world_id=world, name="walk", speed_factor=0)

    def test_create_rejects_overshoot_chance(self, world):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.create(world_id=world, name="walk", event_chance=1.5)

    def test_get_returns_record(self, world):
        m = tm_stub.create(world_id=world, name="walk")
        assert tm_stub.get(m["id"])["name"] == "walk"
        assert tm_stub.get_required(m["id"])["name"] == "walk"

    def test_get_required_raises_for_unknown(self):
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.get_required("tmode_nope")

    def test_list_for_world(self, world):
        tm_stub.create(world_id=world, name="walk")
        tm_stub.create(world_id=world, name="horse")
        out = tm_stub.list_for_world(world)
        assert sorted(m["name"] for m in out) == ["horse", "walk"]

    def test_list_all_includes_every_world(self, world):
        tm_stub.create(world_id=world, name="walk")
        assert any(m["name"] == "walk" for m in tm_stub.list_all())

    def test_update_changes_mutable_fields(self, world):
        m = tm_stub.create(world_id=world, name="walk", speed_factor=1.0)
        updated = tm_stub.update(m["id"], {"name": "sprint", "speed_factor": 0.5})
        assert updated["name"] == "sprint"
        assert updated["speed_factor"] == 0.5

    def test_update_rejects_id_change(self, world):
        m = tm_stub.create(world_id=world, name="walk")
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.update(m["id"], {"id": "tmode_other"})

    def test_update_rejects_world_change(self, world):
        m = tm_stub.create(world_id=world, name="walk")
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.update(m["id"], {"world_id": "world_other"})

    def test_update_rejects_bad_speed(self, world):
        m = tm_stub.create(world_id=world, name="walk")
        with pytest.raises(tm_stub.TravelModeError):
            tm_stub.update(m["id"], {"speed_factor": -1})

    def test_delete_removes_record(self, world):
        m = tm_stub.create(world_id=world, name="walk")
        assert tm_stub.delete(m["id"]) is True
        assert tm_stub.get(m["id"]) is None

    def test_delete_returns_false_for_unknown(self, world):
        assert tm_stub.delete("tmode_nope") is False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_list_requires_auth(self, client):
        res = client.get("/v1/xijian/scenes/travel-modes")
        assert res.status_code in (401, 403)

    def test_create_requires_auth(self, client):
        res = client.post("/v1/xijian/scenes/travel-modes", json={})
        assert res.status_code in (401, 403)

    def test_create_happy_path(self, client, auth_headers, world):
        res = client.post(
            "/v1/xijian/scenes/travel-modes",
            json={"world_id": world, "name": "walk"},
            headers=auth_headers,
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["id"].startswith("tmode_")

    def test_create_rejects_unknown_world(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/scenes/travel-modes",
            json={"world_id": "world_nope", "name": "walk"},
            headers=auth_headers,
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "travel_mode_error"

    def test_list_filters_by_world(self, client, auth_headers, world, walk_mode):
        res = client.get(
            f"/v1/xijian/scenes/travel-modes?world_id={world}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == walk_mode["id"]

    def test_get_returns_record(self, client, auth_headers, walk_mode):
        res = client.get(
            f"/v1/xijian/scenes/travel-modes/{walk_mode['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["name"] == "walk"

    def test_get_unknown_returns_404(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/scenes/travel-modes/tmode_nope", headers=auth_headers
        )
        assert res.status_code == 404
        assert res.get_json()["error"]["code"] == "travel_mode_not_found"

    def test_patch_updates_field(self, client, auth_headers, walk_mode):
        res = client.patch(
            f"/v1/xijian/scenes/travel-modes/{walk_mode['id']}",
            json={"speed_factor": 2.0},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["speed_factor"] == 2.0

    def test_delete_removes(self, client, auth_headers, walk_mode):
        res = client.delete(
            f"/v1/xijian/scenes/travel-modes/{walk_mode['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["deleted"] == walk_mode["id"]

    def test_delete_unknown_returns_404(self, client, auth_headers):
        res = client.delete(
            "/v1/xijian/scenes/travel-modes/tmode_nope", headers=auth_headers
        )
        assert res.status_code == 404

    def test_estimate_endpoint(self, client, auth_headers, walk_mode):
        res = client.post(
            f"/v1/xijian/scenes/travel-modes/{walk_mode['id']}/estimate",
            json={"random_roll": 0.05},
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["mode_id"] == walk_mode["id"]
        # 0.05 < 0.1 (event_chance) → event fires
        assert body["preview"]["event_triggered"] is True

    def test_estimate_endpoint_404(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/scenes/travel-modes/tmode_nope/estimate",
            json={},
            headers=auth_headers,
        )
        assert res.status_code == 404
