"""Tests for ``/v1/xijian/characters/*``.
(``/v1/xijian/characters/*`` 的测试。)
"""

from __future__ import annotations


def test_characters_list_includes_yuki(client, auth_headers):
    """List characters includes the pre-seeded Yuki character.
    (列出角色包含预播种的 Yuki 角色。)
    """
    response = client.get("/v1/xijian/characters", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    ids = [c["id"] for c in body["data"]]
    assert "char_yuki" in ids


def test_character_get_yuki(client, auth_headers):
    """Get Yuki character returns its display name.
    (获取 Yuki 角色返回其显示名称。)
    """
    response = client.get("/v1/xijian/characters/char_yuki", headers=auth_headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["display_name"] == "Yuki"


def test_character_create_get_patch_delete(client, auth_headers):
    """Full CRUD lifecycle for a character.
    (角色的完整 CRUD 生命周期。)
    """
    payload = {
        "name": "测试角色",
        "display_name": "Test",
        "persona_doc": "Hello",
        "tags": ["test"],
    }
    create = client.post("/v1/xijian/characters", headers=auth_headers, json=payload)
    assert create.status_code == 201
    character_id = create.get_json()["id"]
    assert character_id.startswith("char_")

    get = client.get(f"/v1/xijian/characters/{character_id}", headers=auth_headers)
    assert get.status_code == 200

    patch = client.patch(
        f"/v1/xijian/characters/{character_id}",
        headers=auth_headers,
        json={"display_name": "Renamed"},
    )
    assert patch.status_code == 200
    assert patch.get_json()["display_name"] == "Renamed"

    delete = client.delete(f"/v1/xijian/characters/{character_id}", headers=auth_headers)
    assert delete.status_code == 204


def test_character_load_unload_toggles_loaded(client, auth_headers):
    """Load/unload a character toggles its loaded state.
    (加载/卸载角色切换其加载状态。)
    """
    load = client.post("/v1/xijian/characters/char_yuki/load", headers=auth_headers)
    assert load.status_code == 200
    assert load.get_json()["loaded"] is True
    unload = client.post("/v1/xijian/characters/char_yuki/unload", headers=auth_headers)
    assert unload.status_code == 200
    assert unload.get_json()["loaded"] is False


def test_character_state_round_trip(client, auth_headers):
    """Get and update character state.
    (获取和更新角色状态。)
    """
    state = client.get("/v1/xijian/characters/char_yuki/state", headers=auth_headers)
    assert state.status_code == 200
    assert "affection" in state.get_json()

    update = client.post(
        "/v1/xijian/characters/char_yuki/state",
        headers=auth_headers,
        json={"affection": 80, "mood": "happy"},
    )
    assert update.status_code == 200
    assert update.get_json()["affection"] == 80


def test_character_interact_known(client, auth_headers):
    """Interacting with a known interaction_id is accepted.
    (使用已知的 interaction_id 进行交互被接受。)
    """
    response = client.post(
        "/v1/xijian/characters/char_yuki/interact",
        headers=auth_headers,
        json={"interaction_id": "int_hug", "context": {"location": "home"}},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["accepted"] is True
    assert body["interaction_id"] == "int_hug"


def test_character_interact_nsfw_blocked_by_default(client, auth_headers):
    """NSFW interactions are blocked by default.
    (NSFW 交互默认被阻止。)
    """
    response = client.post(
        "/v1/xijian/characters/char_yuki/interact",
        headers=auth_headers,
        json={"interaction_id": "int_kiss"},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["accepted"] is False
    assert body["reason"] == "nsfw_blocked"


def test_character_state_update_blocked_when_protection_off(client, auth_headers):
    """Character state updates are blocked when protection is disabled.
    (保护禁用时角色状态更新被阻止。)
    """
    # Disable protection (two-step) via the unified /safety/gate/* API.
    # (通过统一的 /safety/gate/* API 分两步禁用保护。)
    start = client.post(
        "/v1/xijian/safety/gate/disable",
        headers=auth_headers,
        json={"confirmation": "I understand the risks"},
    )
    challenge_id = start.get_json()["challenge_id"]
    challenge_phrase = start.get_json()["challenge_phrase"]
    client.post(
        "/v1/xijian/safety/gate/disable",
        headers=auth_headers,
        json={"challenge_id": challenge_id, "phrase": challenge_phrase},
    )

    blocked = client.post(
        "/v1/xijian/characters/char_yuki/state",
        headers=auth_headers,
        json={"affection": 99},
    )
    assert blocked.status_code == 403
    assert blocked.get_json()["error"]["type"] == "protection_error"

    # Restore.
    # (恢复保护。)
    client.post("/v1/xijian/safety/gate/enable", headers=auth_headers)
