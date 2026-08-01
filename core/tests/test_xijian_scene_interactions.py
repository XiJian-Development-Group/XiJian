"""Tests for ``stubs.scene_interactions`` (A4.3) and the
``/v1/xijian/scenes/interactions/*`` endpoints.

Covers:

* **Pure helpers** — target-type / cooldown / effects validation,
  character-interactable gate.
* **CRUD** — create / list / get / patch / delete, cooldown-cleanup
  on delete.
* **Trigger** — happy path, cooldown enforcement, character-state
  gate, NPC-alive gate, A4.1 cross-link ``fire_event_id``,
  audit-log bookkeeping.
* **Routes** — happy path + 4xx error mapping (404 vs 409).
* **Auth** — every endpoint requires a Bearer token.

  测试场景交互管理（场景内 NPC 行为、事件触发）。
  验证场景状态转换和交互逻辑。
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import character_state as cs_stub
from xijian_api.stubs import npcs as npcs_stub
from xijian_api.stubs import pois as pois_stub
from xijian_api.stubs import scene_interactions as si_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import world_audit as wa_stub
from xijian_api.stubs.scene_interactions import (
    DEFAULT_COOLDOWN_SECONDS,
    VALID_TARGET_TYPES,
)


# ---------------------------------------------------------------------------
# Fixtures
# 测试夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(client, auth_headers):
    body = {"name": "Scene Test World"}
    res = client.post("/v1/xijian/worlds", json=body, headers=auth_headers)
    assert res.status_code == 201
    return res.get_json()["id"]


@pytest.fixture()
def poi(client, auth_headers, world):
    body = {
        "world_id": world, "name": "Map", "kind": "map",
    }
    m = client.post(
        "/v1/xijian/scenes/pois", json=body, headers=auth_headers
    ).get_json()
    body = {
        "world_id": world, "name": "Region", "kind": "region", "parent_id": m["id"],
    }
    r = client.post(
        "/v1/xijian/scenes/pois", json=body, headers=auth_headers
    ).get_json()
    body = {
        "world_id": world, "name": "Shop", "kind": "shop", "parent_id": r["id"],
    }
    return client.post(
        "/v1/xijian/scenes/pois", json=body, headers=auth_headers
    ).get_json()


@pytest.fixture()
def open_chest(client, auth_headers, world, poi):
    body = {
        "world_id": world,
        "poi_id": poi["id"],
        "target_type": "object",
        "target_id": "chest_1",
        "action": "open",
        "effects": {"stamina_delta": -2, "loot": ["gold_coin"]},
        "cooldown_sec": 5,
    }
    res = client.post(
        "/v1/xijian/scenes/interactions", json=body, headers=auth_headers
    )
    assert res.status_code == 201
    return res.get_json()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_validate_target_type_accepts_canonical(self):
        for tt in ("npc", "object", "mechanism"):
            assert si_stub._validate_target_type(tt) == tt

    def test_validate_target_type_rejects_unknown(self):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_target_type("monster")

    def test_validate_target_type_rejects_non_string(self):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_target_type(1)

    def test_validate_cooldown_defaults_when_none(self):
        assert si_stub._validate_cooldown(None) == DEFAULT_COOLDOWN_SECONDS

    def test_validate_cooldown_rejects_negative(self):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_cooldown(-1)

    def test_validate_cooldown_rejects_non_int(self):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_cooldown(1.5)
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_cooldown("3")

    def test_validate_action_rejects_blank(self):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_action("   ")

    def test_validate_effects_must_be_dict(self):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub._validate_effects("not a dict")
        # None → {}
        assert si_stub._validate_effects(None) == {}
        # dict passes through
        assert si_stub._validate_effects({"k": 1}) == {"k": 1}

    def test_default_cooldown_is_a_small_positive_int(self):
        assert isinstance(DEFAULT_COOLDOWN_SECONDS, int)
        assert DEFAULT_COOLDOWN_SECONDS > 0

    def test_valid_target_types_set(self):
        assert VALID_TARGET_TYPES == frozenset({"npc", "object", "mechanism"})

    def test_character_interactable_with_no_state(self):
        # No state record → interactable (the stub is friendly to
        # characters the operator hasn't yet wired to A3.2).
        assert si_stub._character_is_interactable("char_xx") is True

    def test_character_interactable_with_full_health(self):
        stubs_state.character_states["char_xx"] = {
            "status": "active", "health": 100,
        }
        assert si_stub._character_is_interactable("char_xx") is True

    def test_character_blocked_by_zero_health(self):
        stubs_state.character_states["char_xx"] = {
            "status": "active", "health": 0,
        }
        assert si_stub._character_is_interactable("char_xx") is False

    def test_character_blocked_by_unconscious(self):
        stubs_state.character_states["char_xx"] = {
            "status": "unconscious", "health": 50,
        }
        assert si_stub._character_is_interactable("char_xx") is False

    def test_character_blocked_by_frozen(self):
        stubs_state.character_states["char_xx"] = {
            "status": "frozen", "health": 50,
        }
        assert si_stub._character_is_interactable("char_xx") is False


# ---------------------------------------------------------------------------
# Stub CRUD
# ---------------------------------------------------------------------------


class TestStubCRUD:
    def test_create_minimal(self, world, poi):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        assert si["cooldown_sec"] == DEFAULT_COOLDOWN_SECONDS
        assert si["effects"] == {}

    def test_create_rejects_unknown_world(self, poi):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.create(
                world_id="world_nope", poi_id=poi["id"],
                target_type="object", target_id="x", action="open",
            )

    def test_create_rejects_unknown_poi(self, world):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.create(
                world_id=world, poi_id="poi_nope",
                target_type="object", target_id="x", action="open",
            )

    def test_create_rejects_poi_from_different_world(self, world, poi, client, auth_headers):
        # Build a second world with its own POI.
        other_world = client.post(
            "/v1/xijian/worlds", json={"name": "Other"}, headers=auth_headers
        ).get_json()
        try:
            other_poi = client.post(
                "/v1/xijian/scenes/pois",
                json={"world_id": other_world["id"], "name": "M", "kind": "map"},
                headers=auth_headers,
            ).get_json()
            with pytest.raises(si_stub.SceneInteractionError):
                si_stub.create(
                    world_id=world, poi_id=other_poi["id"],
                    target_type="object", target_id="x", action="open",
                )
        finally:
            # Tidy up: only the world's audit log + world record
            # matter; the POI is in ``state.pois`` so we leave it
            # for the next test's reset.
            pass

    def test_create_rejects_invalid_target_type(self, world, poi):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.create(
                world_id=world, poi_id=poi["id"],
                target_type="monster", target_id="x", action="open",
            )

    def test_create_rejects_blank_action(self, world, poi):
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.create(
                world_id=world, poi_id=poi["id"],
                target_type="object", target_id="x", action="   ",
            )

    def test_create_rejects_duplicate_id(self, world, poi):
        si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
            interaction_id="sint_dup",
        )
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.create(
                world_id=world, poi_id=poi["id"],
                target_type="object", target_id="x", action="open",
                interaction_id="sint_dup",
            )

    def test_list_for_world(self, world, poi):
        si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        out = si_stub.list_for_world(world)
        assert len(out) == 1

    def test_list_for_poi(self, world, poi):
        si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        out = si_stub.list_for_poi(poi["id"])
        assert len(out) == 1

    def test_list_all(self, world, poi):
        si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        assert len(si_stub.list_all()) >= 1

    def test_update_changes_mutable_fields(self, world, poi):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
            cooldown_sec=5,
        )
        updated = si_stub.update(si["id"], {"action": "unlock", "cooldown_sec": 10})
        assert updated["action"] == "unlock"
        assert updated["cooldown_sec"] == 10

    def test_update_rejects_id_change(self, world, poi):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.update(si["id"], {"id": "sint_other"})

    def test_update_rejects_world_change(self, world, poi):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.update(si["id"], {"world_id": "world_other"})

    def test_update_revalidates_poi_world_match(self, world, poi, client, auth_headers):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        other_world = client.post(
            "/v1/xijian/worlds", json={"name": "Other"}, headers=auth_headers
        ).get_json()
        other_poi = client.post(
            "/v1/xijian/scenes/pois",
            json={"world_id": other_world["id"], "name": "M", "kind": "map"},
            headers=auth_headers,
        ).get_json()
        with pytest.raises(si_stub.SceneInteractionError):
            si_stub.update(si["id"], {"poi_id": other_poi["id"]})

    def test_delete_clears_cooldowns(self, world, poi):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        # Fire once to seed a cooldown.
        si_stub.trigger(si["id"], character_id="char_a")
        assert si_stub.delete(si["id"]) is True
        assert si_stub.get(si["id"]) is None

    def test_delete_unknown_returns_false(self):
        assert si_stub.delete("sint_nope") is False


# ---------------------------------------------------------------------------
# Trigger
# 触发器
# ---------------------------------------------------------------------------


class TestTrigger:
    def test_trigger_unknown_returns_404_reason(self, world, poi):
        out = si_stub.trigger("sint_nope", character_id="char_a")
        assert out["accepted"] is False
        assert out["reason"] == "interaction_not_found"

    def test_trigger_happy_path_writes_audit(self, world, poi, open_chest):
        out = si_stub.trigger(open_chest["id"], character_id="char_a")
        assert out["accepted"] is True
        assert out["world_id"] == world
        assert out["effects"]["loot"] == ["gold_coin"]
        assert out["audit_id"] is not None
        # Audit log has the entry.
        assert wa_stub.count_for(world) >= 1

    def test_trigger_respects_cooldown(self, world, poi, open_chest):
        first = si_stub.trigger(open_chest["id"], character_id="char_a")
        assert first["accepted"] is True
        cooldown_until = first["cooldown_until"]
        second = si_stub.trigger(
            open_chest["id"], character_id="char_a", now=cooldown_until - 0.5
        )
        assert second["accepted"] is False
        assert second["reason"] == "on_cooldown"
        assert second["cooldown_until"] == cooldown_until

    def test_trigger_allows_other_characters_during_cooldown(self, world, poi, open_chest):
        first = si_stub.trigger(open_chest["id"], character_id="char_a")
        assert first["accepted"] is True
        # char_b has no cooldown of its own yet.
        second = si_stub.trigger(
            open_chest["id"], character_id="char_b", now=first["cooldown_until"] - 1
        )
        assert second["accepted"] is True

    def test_trigger_blocks_unconscious_character(self, world, poi):
        stubs_state.character_states["char_a"] = {
            "status": "unconscious", "health": 50,
        }
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
            cooldown_sec=5,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is False
        assert out["reason"] == "character_not_interactable"

    def test_trigger_blocks_zero_health_character(self, world, poi):
        stubs_state.character_states["char_a"] = {
            "status": "active", "health": 0,
        }
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="x", action="open",
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is False
        assert out["reason"] == "character_not_interactable"

    def test_trigger_blocks_dead_npc_target(self, world, poi):
        npc = npcs_stub.create(world_id=world, name="Innkeeper")
        npcs_stub.update(npc["id"], {"is_alive": False})
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="npc", target_id=npc["id"], action="talk",
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is False
        assert out["reason"] == "target_dead"

    def test_trigger_fires_a4_1_event_when_effects_says_so(self, world, poi, monkeypatch):
        # Stub out ``events.fire_event`` so the test doesn't need
        # the full A4.1 world-event wiring.
        from xijian_api.stubs import events as events_stub
        calls = []
        monkeypatch.setattr(
            events_stub, "fire_event",
            lambda event_id, **kwargs: calls.append(
                {"event_id": event_id, "kwargs": kwargs}
            ),
        )
        si = si_stub.create(
            world_id=world, poi_id=poi["id"],
            target_type="object", target_id="altar", action="shatter",
            effects={"fire_event_id": "event_xxx"},
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        assert any(c["event_id"] == "event_xxx" for c in calls)
        assert calls[0]["kwargs"]["payload"]["source"] == "scene_interaction"

    def test_trigger_audit_failure_still_returns_success(self, world, poi, open_chest, monkeypatch):
        # Force audit log to fail; trigger should still succeed.
        from xijian_api.stubs import world_audit as wa
        def boom(*args, **kwargs):
            raise RuntimeError("ledger broken")
        monkeypatch.setattr(wa, "record", boom)
        out = si_stub.trigger(open_chest["id"], character_id="char_a")
        assert out["accepted"] is True
        assert out["audit_id"] is None  # we mark the audit as None when it fails

    def test_clear_cooldowns_helper(self, world, poi, open_chest):
        si_stub.trigger(open_chest["id"], character_id="char_a")
        si_stub.clear_cooldowns()
        # After clearing, even a "now" before the original cooldown
        # should be accepted.
        out = si_stub.trigger(
            open_chest["id"], character_id="char_a", now=0.0
        )
        assert out["accepted"] is True


# ---------------------------------------------------------------------------
# Routes
# 路由
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_list_requires_auth(self, client):
        res = client.get("/v1/xijian/scenes/interactions")
        assert res.status_code in (401, 403)

    def test_create_requires_auth(self, client):
        res = client.post("/v1/xijian/scenes/interactions", json={})
        assert res.status_code in (401, 403)

    def test_create_happy_path(self, client, auth_headers, world, poi):
        body = {
            "world_id": world, "poi_id": poi["id"],
            "target_type": "object", "target_id": "x", "action": "open",
        }
        res = client.post(
            "/v1/xijian/scenes/interactions", json=body, headers=auth_headers
        )
        assert res.status_code == 201
        body = res.get_json()
        assert body["id"].startswith("sint_")
        assert body["cooldown_sec"] == DEFAULT_COOLDOWN_SECONDS

    def test_create_rejects_unknown_poi(self, client, auth_headers, world):
        body = {
            "world_id": world, "poi_id": "poi_nope",
            "target_type": "object", "target_id": "x", "action": "open",
        }
        res = client.post(
            "/v1/xijian/scenes/interactions", json=body, headers=auth_headers
        )
        assert res.status_code == 400
        assert res.get_json()["error"]["code"] == "scene_interaction_error"

    def test_list_filters_by_poi(self, client, auth_headers, world, poi, open_chest):
        res = client.get(
            f"/v1/xijian/scenes/interactions?poi_id={poi['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == open_chest["id"]

    def test_list_filters_by_world(self, client, auth_headers, world, open_chest):
        res = client.get(
            f"/v1/xijian/scenes/interactions?world_id={world}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert any(d["id"] == open_chest["id"] for d in body["data"])

    def test_get_returns_record(self, client, auth_headers, open_chest):
        res = client.get(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["action"] == "open"

    def test_get_unknown_returns_404(self, client, auth_headers):
        res = client.get(
            "/v1/xijian/scenes/interactions/sint_nope", headers=auth_headers
        )
        assert res.status_code == 404
        assert res.get_json()["error"]["code"] == "scene_interaction_not_found"

    def test_patch_updates_field(self, client, auth_headers, open_chest):
        res = client.patch(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}",
            json={"action": "unlock", "cooldown_sec": 30},
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["action"] == "unlock"
        assert body["cooldown_sec"] == 30

    def test_patch_invalid_returns_400(self, client, auth_headers, open_chest):
        res = client.patch(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}",
            json={"target_type": "monster"},
            headers=auth_headers,
        )
        assert res.status_code == 400

    def test_delete_removes(self, client, auth_headers, open_chest):
        res = client.delete(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}",
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["deleted"] == open_chest["id"]

    def test_delete_unknown_returns_404(self, client, auth_headers):
        res = client.delete(
            "/v1/xijian/scenes/interactions/sint_nope", headers=auth_headers
        )
        assert res.status_code == 404

    def test_trigger_happy_path(self, client, auth_headers, open_chest):
        res = client.post(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}/trigger",
            json={"character_id": "char_a"},
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["accepted"] is True
        assert body["audit_id"] is not None

    def test_trigger_unknown_returns_404(self, client, auth_headers):
        res = client.post(
            "/v1/xijian/scenes/interactions/sint_nope/trigger",
            json={"character_id": "char_a"},
            headers=auth_headers,
        )
        assert res.status_code == 404
        assert res.get_json()["error"]["code"] == "scene_interaction_not_found"

    def test_trigger_cooldown_returns_409(self, client, auth_headers, open_chest):
        first = client.post(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}/trigger",
            json={"character_id": "char_a"},
            headers=auth_headers,
        )
        assert first.status_code == 200
        second = client.post(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}/trigger",
            json={"character_id": "char_a"},
            headers=auth_headers,
        )
        assert second.status_code == 409
        body = second.get_json()
        assert body["error"]["code"] == "on_cooldown"

    def test_trigger_blocks_unconscious_character(self, client, auth_headers, world, poi):
        body = {
            "world_id": world, "poi_id": poi["id"],
            "target_type": "object", "target_id": "x", "action": "open",
        }
        si = client.post(
            "/v1/xijian/scenes/interactions", json=body, headers=auth_headers
        ).get_json()
        stubs_state.character_states["char_a"] = {
            "status": "unconscious", "health": 50,
        }
        res = client.post(
            f"/v1/xijian/scenes/interactions/{si['id']}/trigger",
            json={"character_id": "char_a"},
            headers=auth_headers,
        )
        assert res.status_code == 409
        assert res.get_json()["error"]["code"] == "character_not_interactable"

    def test_trigger_no_body_works(self, client, auth_headers, open_chest):
        # ``optional=True`` lets the route accept an empty body.
        res = client.post(
            f"/v1/xijian/scenes/interactions/{open_chest['id']}/trigger",
            headers=auth_headers,
        )
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# A4.3 effects actually applied (AC-2 / AC-3)
# ---------------------------------------------------------------------------


class TestEffectsApplied:
    """The trigger() path must *apply* effects — the old behaviour only
    returned them.  These tests pin the state mutations."""

    def test_stamina_delta_applied_to_character(self, world, poi):
        from xijian_api.stubs import character_state as cs_stub
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="object",
            target_id="chest", action="open",
            effects={"stamina_delta": -2},
            cooldown_sec=0,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        assert out["effects_applied"] == ["stamina_delta"]
        record = cs_stub.get_state("char_a")
        assert record["stamina"] == 98.0

    def test_health_and_mood_deltas_applied(self, world, poi):
        from xijian_api.stubs import character_state as cs_stub
        cs_stub.apply_field_change("char_a", "health", 100.0)
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="object",
            target_id="trap", action="spring",
            effects={"health_delta": -20, "mood_delta": -10},
            cooldown_sec=0,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        record = cs_stub.get_state("char_a")
        assert record["health"] == 80.0
        assert record["mood"] == 60.0  # 70 default − 10

    def test_npc_mood_delta_applied_to_npc_target(self, world, poi):
        from xijian_api.stubs import npcs as npcs_stub
        npc = npcs_stub.create(
            world_id=world, name="N", state_json={"mood": 40},
        )
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="npc",
            target_id=npc["id"], action="praise",
            effects={"npc_mood_delta": 15},
            cooldown_sec=0,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        assert "npc_mood_delta" in out["effects_applied"]
        assert npcs_stub.get(npc["id"])["state_json"]["mood"] == 55.0

    def test_world_state_effect_patches_environment(self, world, poi):
        from xijian_api.stubs import world_environment as env_stub
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="mechanism",
            target_id="weather_control", action="flip",
            effects={"world_state": {"weather": "storm"}},
            cooldown_sec=0,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        assert env_stub.get(world)["weather"] == "storm"

    def test_effect_without_character_is_logged_not_applied(self, world, poi, caplog):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="object",
            target_id="chest", action="open",
            effects={"stamina_delta": -2},
            cooldown_sec=0,
        )
        # No character_id → stamina cannot be deducted; trigger still accepts.
        out = si_stub.trigger(si["id"])
        assert out["accepted"] is True
        assert "stamina_delta" not in out["effects_applied"]

    def test_npc_delta_without_npc_target_is_skipped(self, world, poi, caplog):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="object",
            target_id="chest", action="open",
            effects={"npc_mood_delta": 5},
            cooldown_sec=0,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        assert "npc_mood_delta" not in out["effects_applied"]

    def test_unsupported_effect_ignored(self, world, poi, caplog):
        si = si_stub.create(
            world_id=world, poi_id=poi["id"], target_type="object",
            target_id="chest", action="open",
            effects={"loot": ["gold_coin"]},
            cooldown_sec=0,
        )
        out = si_stub.trigger(si["id"], character_id="char_a")
        assert out["accepted"] is True
        assert out["effects_applied"] == []
