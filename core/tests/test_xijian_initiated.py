"""Tests for ``stubs.character_initiated_actions`` (A7) and routes.

Covers:

* **CRUD** — create / get / list actions (kind validation, filters).
* **Notification policy (AC-3)** — global switch, per-character switch,
  defaults materialisation.
* **Eligibility** — cooldown, hourly rate limit, mood threshold,
  global/character disabled.
* **scan_for_actions** — the trigger mechanism (creates actions for
  eligible characters only).
* **respond** — accepted / declined / ignored; **AC-2**: declined
  writes a "角色理解" memory entry via ``stubs.memory``.
* **Routes** — notifications, scan, respond HTTP smoke tests.
"""

from __future__ import annotations

import pytest

from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import character_initiated_actions as cia_stub
from xijian_api.stubs.characters import create as create_character


@pytest.fixture()
def character():
    return create_character({"name": "Proactive Char"})["id"]


@pytest.fixture()
def global_off(character):
    """Disable the global switch so later tests are deterministic."""
    cia_stub.set_global_settings({"enabled": False})
    yield


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_create_pending_action(self, character):
        record = cia_stub.create_action(character_id=character)
        assert record["id"].startswith("init_")
        assert record["character_id"] == character
        assert record["kind"] == cia_stub.KIND_MESSAGE
        assert record["status"] == cia_stub.STATUS_PENDING
        assert record["user_response"] is None

    def test_create_kind_validation(self, character):
        with pytest.raises(cia_stub.InitiatedActionError):
            cia_stub.create_action(character_id=character, kind="teleport")

    def test_create_voice_call_kind(self, character):
        record = cia_stub.create_action(
            character_id=character, kind=cia_stub.KIND_VOICE_CALL,
            payload={"offer": "voice_call"},
        )
        assert record["kind"] == cia_stub.KIND_VOICE_CALL

    def test_get_missing(self):
        assert cia_stub.get_action("init_nope") is None

    def test_list_filters(self, character):
        a1 = cia_stub.create_action(character_id=character, kind=cia_stub.KIND_MESSAGE)
        a2 = cia_stub.create_action(character_id=character, kind=cia_stub.KIND_VOICE_CALL)
        cia_stub.respond(a1["id"], cia_stub.RESPONSE_ACCEPTED)
        assert len(cia_stub.list_actions(character_id=character)) == 2
        accepted = cia_stub.list_actions(user_response=cia_stub.RESPONSE_ACCEPTED)
        assert a1["id"] in [a["id"] for a in accepted]
        calls = cia_stub.list_actions(kind=cia_stub.KIND_VOICE_CALL)
        assert a2["id"] in [a["id"] for a in calls]

    def test_create_bumps_last_triggered_at(self, character):
        cia_stub.create_action(character_id=character)
        cfg = cia_stub.get_character_config(character)
        assert cfg["last_triggered_at"] is not None


# ---------------------------------------------------------------------------
# Notification policy (AC-3)
# ---------------------------------------------------------------------------


class TestNotificationPolicy:
    def test_global_defaults(self):
        settings = cia_stub.get_global_settings()
        assert settings["enabled"] is True
        assert settings["default_cooldown_seconds"] == 3600

    def test_set_global_enabled(self):
        settings = cia_stub.set_global_settings({"enabled": False})
        assert settings["enabled"] is False

    def test_character_config_defaults(self, character):
        cfg = cia_stub.get_character_config(character)
        assert cfg["enabled"] is True
        assert cfg["kind"] == cia_stub.KIND_MESSAGE
        assert cfg["cooldown_seconds"] == 3600

    def test_disable_single_character(self, character):
        cia_stub.set_character_config(character, {"enabled": False})
        assert cia_stub.get_character_config(character)["enabled"] is False

    def test_kind_switch_validation(self, character):
        with pytest.raises(cia_stub.InitiatedActionError):
            cia_stub.set_character_config(character, {"kind": "pigeon"})

    def test_mood_threshold_none_disables_condition(self, character):
        cia_stub.set_character_config(character, {"mood_threshold": None})
        assert cia_stub.get_character_config(character)["mood_threshold"] is None


# ---------------------------------------------------------------------------
# Eligibility + scan (触发机制)
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_eligible_by_default(self, character):
        cfg = cia_stub.get_character_config(character)
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is True
        assert reason == "eligible"

    def test_global_disabled_blocks(self, character):
        cia_stub.set_global_settings({"enabled": False})
        cfg = cia_stub.get_character_config(character)
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is False
        assert reason == "global_disabled"

    def test_character_disabled_blocks(self, character):
        cia_stub.set_character_config(character, {"enabled": False})
        cfg = cia_stub.get_character_config(character)
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is False
        assert reason == "character_disabled"

    def test_cooldown_blocks(self, character):
        cia_stub.set_character_config(character, {"cooldown_seconds": 100})
        cfg = cia_stub.get_character_config(character)
        cfg["last_triggered_at"] = 950.0  # 50s ago < 100s cooldown
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is False
        assert reason == "cooldown"
        cfg["last_triggered_at"] = 800.0  # 200s ago > 100s cooldown
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is True

    def test_rate_limit_blocks(self, character):
        # Cooldown must not shadow the rate-limit check, so set it tiny.
        cia_stub.set_character_config(
            character, {"max_per_hour": 1, "cooldown_seconds": 1}
        )
        now = 1000.0
        cia_stub.create_action(character_id=character, now=now)
        cfg = cia_stub.get_character_config(character)
        ok, reason = cia_stub._character_eligible(character, cfg, now=now + 60)
        assert ok is False
        assert reason == "rate_limited"

    def test_mood_threshold_blocks_high_mood(self, character):
        from xijian_api.stubs import character_state as cs_stub
        cs_state = cs_stub.get_or_init_state(character)
        cs_state["mood"] = 95.0
        cia_stub.set_character_config(character, {"mood_threshold": 70.0})
        cfg = cia_stub.get_character_config(character)
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is False
        assert reason == "mood_too_high"
        cs_state["mood"] = 50.0
        ok, reason = cia_stub._character_eligible(character, cfg, now=1000.0)
        assert ok is True


class TestScan:
    def test_scan_creates_actions_for_eligible(self, character):
        created = cia_stub.scan_for_actions(now=1000.0)
        assert any(a["character_id"] == character for a in created)
        assert all(a["status"] == cia_stub.STATUS_PENDING for a in created)

    def test_scan_respects_global_switch(self, character):
        cia_stub.set_global_settings({"enabled": False})
        created = cia_stub.scan_for_actions(now=1000.0)
        assert all(a["character_id"] != character for a in created)

    def test_scan_respects_cooldown(self, character):
        cia_stub.create_action(character_id=character, now=1000.0)
        # Cooldown (3600s) not yet elapsed.
        created = cia_stub.scan_for_actions(now=1000.0 + 10)
        assert all(a["character_id"] != character for a in created)
        # After the cooldown elapses the character becomes eligible again.
        created = cia_stub.scan_for_actions(now=1000.0 + 3601)
        assert any(a["character_id"] == character for a in created)

    def test_scan_uses_character_config_kind(self, character):
        cia_stub.set_character_config(character, {"kind": cia_stub.KIND_VOICE_CALL})
        created = cia_stub.scan_for_actions(now=2000.0)
        mine = [a for a in created if a["character_id"] == character]
        assert mine and mine[0]["kind"] == cia_stub.KIND_VOICE_CALL


# ---------------------------------------------------------------------------
# Respond — AC-2 (declined → "理解" 记忆回写)
# ---------------------------------------------------------------------------


class TestRespond:
    def test_accept(self, character):
        action = cia_stub.create_action(character_id=character)
        record = cia_stub.respond(action["id"], cia_stub.RESPONSE_ACCEPTED)
        assert record["status"] == cia_stub.STATUS_ACCEPTED
        assert record["user_response"] == cia_stub.RESPONSE_ACCEPTED
        assert record["responded_at"] is not None

    def test_declined_writes_understanding_memory(self, character):
        """AC-2: 用户拒绝后，角色必须表现出"理解"（写回记忆）。"""
        action = cia_stub.create_action(character_id=character)
        cia_stub.respond(action["id"], cia_stub.RESPONSE_DECLINED)
        entries = [
            e for e in stubs_state.memory.values()
            if e.get("character_id") == character
        ]
        assert entries, "no memory entry written on decline"
        entry = entries[-1]
        assert entry["source"] == "character_initiated"
        assert "declined" in (entry.get("tags") or [])
        assert "理解" in entry["content"] or "婉拒" in entry["content"]
        assert entry["type"] == "short"

    def test_ignored(self, character):
        action = cia_stub.create_action(character_id=character)
        record = cia_stub.respond(action["id"], cia_stub.RESPONSE_IGNORED)
        assert record["status"] == cia_stub.STATUS_IGNORED

    def test_invalid_response(self, character):
        action = cia_stub.create_action(character_id=character)
        with pytest.raises(cia_stub.InitiatedActionError):
            cia_stub.respond(action["id"], "maybe")

    def test_respond_is_idempotent(self, character):
        action = cia_stub.create_action(character_id=character)
        first = cia_stub.respond(action["id"], cia_stub.RESPONSE_ACCEPTED)
        second = cia_stub.respond(action["id"], cia_stub.RESPONSE_DECLINED)
        assert second["user_response"] == cia_stub.RESPONSE_ACCEPTED
        assert second["responded_at"] == first["responded_at"]


# ---------------------------------------------------------------------------
# Tick thread
# ---------------------------------------------------------------------------


class TestTickThread:
    def test_env_disabled_by_default(self):
        # conftest sets XIJIAN_INITIATED_TICK=0 — the same posture as
        # the other background threads (character_state / npcs).
        status = cia_stub.tick_status()
        assert status["env_disabled"] is True
        assert cia_stub.start_tick()["reason"] == "disabled_by_env"
        assert cia_stub.stop_tick()["stopped"] is False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestInitiatedRoutes:
    def test_create_and_respond_over_http(self, client, auth_headers, character):
        created = client.post(
            "/v1/xijian/initiated-actions",
            json={"character_id": character, "kind": cia_stub.KIND_MESSAGE},
            headers=auth_headers,
        )
        assert created.status_code == 201, created.get_json()
        action_id = created.get_json()["id"]
        res = client.post(
            f"/v1/xijian/initiated-actions/{action_id}/respond",
            json={"user_response": cia_stub.RESPONSE_DECLINED},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["status"] == cia_stub.STATUS_DECLINED

    def test_notifications_global_patch(self, client, auth_headers):
        res = client.patch(
            "/v1/xijian/initiated-actions/notifications",
            json={"enabled": False},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["enabled"] is False
        summary = client.get(
            "/v1/xijian/initiated-actions/notifications", headers=auth_headers
        ).get_json()
        assert summary["global"]["enabled"] is False

    def test_character_notification_patch(self, client, auth_headers, character):
        res = client.patch(
            f"/v1/xijian/initiated-actions/notifications/{character}",
            json={"enabled": False, "kind": cia_stub.KIND_VOICE_CALL},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.get_json()["enabled"] is False
        got = client.get(
            f"/v1/xijian/initiated-actions/notifications/{character}",
            headers=auth_headers,
        ).get_json()
        assert got["kind"] == cia_stub.KIND_VOICE_CALL

    def test_scan_endpoint(self, client, auth_headers, character):
        res = client.post(
            "/v1/xijian/initiated-actions/scan", headers=auth_headers
        )
        assert res.status_code == 200
        body = res.get_json()
        assert body["scanned"] is True
        assert any(
            a["character_id"] == character for a in body["created"]
        )

    def test_requires_auth(self, client):
        assert client.get("/v1/xijian/initiated-actions").status_code in (401, 403)
