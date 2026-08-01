"""Tests for ``stubs.desktop_pets`` (A8) + ``/v1/xijian/desktop/*`` +
``/v1/xijian/mcp/pending*``.

Covers:

* **Pets CRUD** — create / list / get / patch / delete, activate.
* **Wallpapers CRUD** — create / list / activate; **exclusivity**
  (one active wallpaper per character; activating a wallpaper
  deactivates that character's pets).
* **AC-4** — ``write_ops_allowed`` is False while a wallpaper is
  active; pending write actions get blocked on result write-back.
* **AC-2 audit log** — pet action log append + query.
* **Execution loop (the A5.2-flagged gap)** — enqueue → poll
  (``list_pending``) → claim → result write-back (executed/failed),
  plus the pet-log write on executed actions.
* **Routes** — pets / wallpapers / actions / mcp-pending HTTP tests.
"""

from __future__ import annotations

import pytest

from xijian_api.mcp.tools import desktop as desktop_tools
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import desktop_pets as pets_stub
from xijian_api.stubs.characters import create as create_character


@pytest.fixture()
def character():
    return create_character({"name": "Pet Char"})["id"]


@pytest.fixture()
def pet(character):
    return pets_stub.create_pet(character_id=character)["id"]


# ---------------------------------------------------------------------------
# Pets CRUD
# ---------------------------------------------------------------------------


class TestPets:
    def test_create_pet(self, character):
        record = pets_stub.create_pet(
            character_id=character, can_fly=True, can_interact=True,
            spawn_x=12.5, spawn_y=30.0,
        )
        assert record["id"].startswith("pet_")
        assert record["character_id"] == character
        assert record["can_fly"] is True
        assert record["can_interact"] is True
        assert record["spawn_x"] == 12.5
        assert record["is_active"] is True
        assert record["fps_cap"] == pets_stub.DEFAULT_FPS_CAP

    def test_create_requires_character(self):
        with pytest.raises(pets_stub.DesktopPetError):
            pets_stub.create_pet(character_id="")

    def test_list_filter(self, character):
        p1 = pets_stub.create_pet(character_id=character)["id"]
        p2 = pets_stub.create_pet(character_id=character)["id"]
        pets_stub.set_pet_active(p1, False)
        assert len(pets_stub.list_pets(character_id=character)) == 2
        assert len(pets_stub.list_pets(is_active=True)) == 1
        assert p2 in [p["id"] for p in pets_stub.list_pets(is_active=True)]

    def test_update_immutable_fields(self, pet):
        with pytest.raises(pets_stub.DesktopPetError):
            pets_stub.update_pet(pet, {"character_id": "someone-else"})

    def test_update_fps_cap_clamped(self, pet):
        pets_stub.update_pet(pet, {"fps_cap": 9999})
        assert pets_stub.get_pet(pet)["fps_cap"] == 120

    def test_delete(self, pet):
        assert pets_stub.delete_pet(pet) is True
        assert pets_stub.get_pet(pet) is None

    def test_activate_deactivate(self, pet):
        pets_stub.set_pet_active(pet, False)
        assert pets_stub.get_pet(pet)["is_active"] is False
        pets_stub.set_pet_active(pet, True)
        assert pets_stub.get_pet(pet)["is_active"] is True


# ---------------------------------------------------------------------------
# Wallpapers + AC-4
# ---------------------------------------------------------------------------


class TestWallpapers:
    def test_create_wallpaper(self, character):
        record = pets_stub.create_wallpaper(
            character_id=character,
            world_id="world_modern_tokyo",
            env_settings={"time_of_day": "dusk"},
        )
        assert record["id"].startswith("wall_")
        assert record["world_id"] == "world_modern_tokyo"
        assert record["env_settings"]["time_of_day"] == "dusk"
        assert record["can_layout"] is True

    def test_activate_enforces_exclusivity(self, character):
        w1 = pets_stub.create_wallpaper(character_id=character)["id"]
        w2 = pets_stub.create_wallpaper(character_id=character)["id"]
        pets_stub.set_wallpaper_active(w1, True)
        pets_stub.set_wallpaper_active(w2, True)
        assert pets_stub.get_wallpaper(w1)["is_active"] is False
        assert pets_stub.get_wallpaper(w2)["is_active"] is True

    def test_activate_wallpaper_deactivates_pets(self, character):
        pet = pets_stub.create_pet(character_id=character, is_active=True)["id"]
        wallpaper = pets_stub.create_wallpaper(character_id=character)["id"]
        pets_stub.set_wallpaper_active(wallpaper, True)
        assert pets_stub.get_pet(pet)["is_active"] is False

    def test_ac4_write_ops_disabled_in_wallpaper_mode(self, character):
        """AC-4: 动态壁纸模式下，桌宠的写操作能力被完全禁用."""
        assert pets_stub.write_ops_allowed(character) is True
        wallpaper = pets_stub.create_wallpaper(character_id=character)["id"]
        pets_stub.set_wallpaper_active(wallpaper, True)
        assert pets_stub.write_ops_allowed(character) is False
        # 其他角色不受影响。
        other = create_character({"name": "Other"})["id"]
        assert pets_stub.write_ops_allowed(other) is True

    def test_deactivate_wallpaper_restores_write_ops(self, character):
        wallpaper = pets_stub.create_wallpaper(character_id=character)["id"]
        pets_stub.set_wallpaper_active(wallpaper, True)
        pets_stub.set_wallpaper_active(wallpaper, False)
        assert pets_stub.write_ops_allowed(character) is True


# ---------------------------------------------------------------------------
# Pet action log (AC-2)
# ---------------------------------------------------------------------------


class TestPetActionLog:
    def test_log_and_query(self, pet):
        entry = pets_stub.log_pet_action(
            pet, pets_stub.ACTION_MOUSE_CLICK, {"x": 10, "y": 20}
        )
        assert entry["id"].startswith("petlog_")
        assert entry["action_kind"] == pets_stub.ACTION_MOUSE_CLICK
        entries = pets_stub.list_pet_actions(pet)
        assert len(entries) == 1
        assert entries[0]["payload"]["x"] == 10

    def test_log_requires_pet(self):
        with pytest.raises(pets_stub.DesktopPetError):
            pets_stub.log_pet_action("pet_nope", pets_stub.ACTION_MOUSE_CLICK)

    def test_log_is_append_only(self, pet):
        pets_stub.log_pet_action(pet, pets_stub.ACTION_KEY_INPUT, {"text": "a"})
        pets_stub.log_pet_action(pet, pets_stub.ACTION_KEY_INPUT, {"text": "b"})
        entries = pets_stub.list_pet_actions(pet, action_kind=pets_stub.ACTION_KEY_INPUT)
        assert len(entries) == 2
        # newest first
        assert entries[0]["payload"]["text"] == "b"

    def test_log_records_character_id(self, pet, character):
        entry = pets_stub.log_pet_action(pet, pets_stub.ACTION_WINDOW_MOVE, {})
        assert entry["character_id"] == character


# ---------------------------------------------------------------------------
# Execution loop — pending queue (the A5.2-flagged gap)
# ---------------------------------------------------------------------------


class TestPendingQueue:
    def test_enqueue_via_mcp_tool_and_list(self, character):
        # The MCP desktop tool enqueues into state.mcp_pending_actions.
        result = desktop_tools._app_launch_handler(
            {"app_name": "Safari"}, {"world_id": "w1"}
        )
        action_id = result["_meta"]["action_id"]
        pending = pets_stub.list_pending()
        assert any(p["id"] == action_id for p in pending)
        record = pets_stub.get_pending(action_id)
        assert record["status"] == pets_stub.PENDING_STATUS_PENDING
        assert record["kind"] == "app_launch"

    def test_claim_transitions(self, character):
        record = desktop_tools._enqueue("mouse_click", {"x": 1, "y": 2})
        action_id = record["id"]
        claimed = pets_stub.claim_action(action_id)
        assert claimed["status"] == pets_stub.PENDING_STATUS_CLAIMED
        assert claimed["claimed_at"] is not None

    def test_report_result_executed(self, pet):
        record = desktop_tools._enqueue("browser_open", {"url": "https://a.b"})
        action_id = record["id"]
        out = pets_stub.report_result(
            action_id, pets_stub.PENDING_STATUS_EXECUTED,
            {"ok": True}, pet_id=pet,
        )
        assert out["status"] == pets_stub.PENDING_STATUS_EXECUTED
        assert out["result"]["ok"] is True
        # Executed + pet_id → audit log entry (AC-2 闭环).
        log = pets_stub.list_pet_actions(pet)
        assert any(e["payload"].get("action_id") == action_id for e in log)

    def test_report_result_failed(self):
        record = desktop_tools._enqueue("keyboard_type", {"text": "hi"})
        out = pets_stub.report_result(
            record["id"], pets_stub.PENDING_STATUS_FAILED, {"error": "denied"}
        )
        assert out["status"] == pets_stub.PENDING_STATUS_FAILED

    def test_report_result_invalid_status(self):
        record = desktop_tools._enqueue("app_launch", {"app_name": "x"})
        with pytest.raises(pets_stub.DesktopPetError):
            pets_stub.report_result(record["id"], "maybe")

    def test_ac4_blocks_write_result_in_wallpaper_mode(self, character):
        """AC-4: 壁纸模式下，写类动作即使客户端报告成功也会被服务器拒绝."""
        pet = pets_stub.create_pet(character_id=character)["id"]
        wallpaper = pets_stub.create_wallpaper(character_id=character)["id"]
        pets_stub.set_wallpaper_active(wallpaper, True)
        record = desktop_tools._enqueue("mouse_click", {"x": 5, "y": 5})
        out = pets_stub.report_result(
            record["id"], pets_stub.PENDING_STATUS_EXECUTED,
            {"character_id": character}, pet_id=pet,
        )
        assert out["status"] == pets_stub.PENDING_STATUS_FAILED
        assert out["result"]["blocked_by"] == "wallpaper_mode_read_only"

    def test_poll_with_claim_flag(self):
        desktop_tools._enqueue("app_launch", {"app_name": "Notes"})
        items = pets_stub.list_pending(status=pets_stub.PENDING_STATUS_PENDING)
        assert items
        # Claim the first one.
        action_id = items[0]["id"]
        pets_stub.claim_action(action_id)
        pending = pets_stub.list_pending(status=pets_stub.PENDING_STATUS_PENDING)
        assert all(p["id"] != action_id for p in pending)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestDesktopRoutes:
    def test_pet_crud_over_http(self, client, auth_headers, character):
        created = client.post(
            "/v1/xijian/desktop/pets",
            json={"character_id": character, "can_fly": True},
            headers=auth_headers,
        )
        assert created.status_code == 201, created.get_json()
        pet_id = created.get_json()["id"]
        assert client.get(
            f"/v1/xijian/desktop/pets/{pet_id}", headers=auth_headers
        ).status_code == 200
        patched = client.patch(
            f"/v1/xijian/desktop/pets/{pet_id}",
            json={"can_interact": True},
            headers=auth_headers,
        )
        assert patched.get_json()["can_interact"] is True
        assert client.post(
            f"/v1/xijian/desktop/pets/{pet_id}/deactivate", headers=auth_headers
        ).get_json()["is_active"] is False

    def test_wallpaper_activate_over_http(self, client, auth_headers, character):
        created = client.post(
            "/v1/xijian/desktop/wallpapers",
            json={"character_id": character, "world_id": "world_modern_tokyo"},
            headers=auth_headers,
        )
        assert created.status_code == 201
        wp_id = created.get_json()["id"]
        activated = client.post(
            f"/v1/xijian/desktop/wallpapers/{wp_id}/activate",
            headers=auth_headers,
        ).get_json()
        assert activated["is_active"] is True

    def test_pet_action_log_route(self, client, auth_headers, character):
        pet_id = client.post(
            "/v1/xijian/desktop/pets",
            json={"character_id": character},
            headers=auth_headers,
        ).get_json()["id"]
        dispatched = client.post(
            f"/v1/xijian/desktop/pets/{pet_id}/actions",
            json={"action_kind": pets_stub.ACTION_MOUSE_CLICK, "payload": {"x": 1}},
            headers=auth_headers,
        )
        assert dispatched.status_code == 201
        log = client.get(
            f"/v1/xijian/desktop/pets/{pet_id}/actions", headers=auth_headers
        ).get_json()["entries"]
        assert len(log) == 1
        assert log[0]["action_kind"] == pets_stub.ACTION_MOUSE_CLICK

    def test_mcp_pending_poll_and_result_over_http(self, client, auth_headers, character):
        # Enqueue through the MCP tool layer (as a chat pipeline would).
        desktop_tools._enqueue("browser_open", {"url": "https://example.com"})
        poll = client.get("/v1/xijian/mcp/pending", headers=auth_headers)
        assert poll.status_code == 200
        data = poll.get_json()["data"]
        assert data, "pending queue should not be empty"
        action_id = data[0]["id"]
        claimed = client.post(
            f"/v1/xijian/mcp/pending/{action_id}/claim", headers=auth_headers
        ).get_json()
        assert claimed["status"] == pets_stub.PENDING_STATUS_CLAIMED
        result = client.post(
            f"/v1/xijian/mcp/pending/{action_id}/result",
            json={"status": pets_stub.PENDING_STATUS_EXECUTED, "result": {"ok": True}},
            headers=auth_headers,
        )
        assert result.status_code == 200
        assert result.get_json()["status"] == pets_stub.PENDING_STATUS_EXECUTED
        got = client.get(
            f"/v1/xijian/mcp/pending/{action_id}", headers=auth_headers
        ).get_json()
        assert got["result"]["ok"] is True

    def test_mcp_pending_poll_with_claim(self, client, auth_headers):
        desktop_tools._enqueue("keyboard_key", {"key": "Enter"})
        poll = client.get(
            "/v1/xijian/mcp/pending?claim=1", headers=auth_headers
        ).get_json()
        assert poll["data"]
        assert all(
            p["status"] == pets_stub.PENDING_STATUS_CLAIMED for p in poll["data"]
        )

    def test_requires_auth(self, client):
        assert client.get("/v1/xijian/desktop/pets").status_code in (401, 403)
        assert client.get("/v1/xijian/mcp/pending").status_code in (401, 403)
