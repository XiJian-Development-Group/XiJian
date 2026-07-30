"""Tests for the DevKit preview/test loader and its routes.

Uses a temporary directory to mock the DevKit save directory structure.
Both the stubs layer and the HTTP layer are exercised to catch
regressions across the serialisation boundary.

Test data creates a sample character and world in ``devkit/characters/``
and ``devkit/worlds/`` subdirectories, matching the real DevKit's
save format (character_editor.save_character, world_editor.save_world).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from xijian_api.stubs import devkit as devkit_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Clear devkit stubs state and clean core state between tests."""
    devkit_stub._clear_env_override()
    # Clean up any devkit-loaded records from the previous test.
    devkit_stub.state.characters.clear()
    devkit_stub.state.worlds.clear()
    devkit_stub.state.memory_configs.clear()
    devkit_stub.state.character_state_configs.clear()
    devkit_stub.state.world_environment.clear()
    devkit_stub.state.world_compute_config.clear()
    devkit_stub.state.memory.clear()
    yield


@pytest.fixture
def mock_devkit_dir() -> Generator[str, None, None]:
    """Create a temporary directory with a mock DevKit layout.

    Produces:
        devkit/
            characters/
                char_test001/character.json, persona.md
            worlds/
                world_test001/world.json, world_doc.md, world_config.json
            memories/
                char_test001/entries.json
    """
    with tempfile.TemporaryDirectory() as tmp:
        dk = Path(tmp) / "DevKit"
        dk.mkdir()

        # --- Characters ---
        chars_dir = dk / "characters"
        chars_dir.mkdir()

        char_dir = chars_dir / "char_test001"
        char_dir.mkdir()

        character_data = {
            "id": "char_test001",
            "name": "测试角色",
            "display_name": "测试角色",
            "description": "测试用的角色",
            "persona_doc": "我是一个测试角色，用于验证DevKit预览加载功能。",
            "voice_profile": "melo_zh_female_warm_v1",
            "default_emotion": "happy",
            "language_style": "活泼可爱的语气",
            "tags": ["test", "preview"],
            "models": [{"kind": "vrm", "file_path": "/fake/path.vrm"}],
            "memory_config": {
                "max_long_term": 100,
                "long_term_importance_min": 0.5,
                "max_short_term": 30,
                "short_term_decay_rate": 0.08,
                "short_term_importance_min": 0.3,
                "max_context_tokens": 6000,
                "reserve_tokens_for_reply": 1500,
                "force_recall_on_history": True,
            },
            "character_config": {
                "speaking_speed": 1.0,
                "emotion_stability": 0.7,
                "state_config": {
                    "hunger_decay_per_hour": 2.0,
                    "thirst_decay_per_hour": 3.0,
                    "health_decay_per_hour": 0.1,
                    "mood_decay_per_hour": 1.0,
                    "low_hunger_threshold": 30.0,
                    "low_mood_threshold": 20.0,
                },
            },
            "assigned_memory_pack": "",
            "assigned_voice_pack": "",
            "assigned_model": "",
            "assigned_world": "",
            "created_at": "2026-07-30T00:00:00Z",
            "updated_at": "2026-07-30T06:00:00Z",
        }
        with open(char_dir / "character.json", "w", encoding="utf-8") as f:
            json.dump(character_data, f, ensure_ascii=False, indent=2)
        with open(char_dir / "persona.md", "w", encoding="utf-8") as f:
            f.write("# 测试角色人格\n\n我是一个活泼可爱的测试角色。")

        # --- Memories ---
        mem_dir = dk / "memories" / "char_test001"
        mem_dir.mkdir(parents=True)
        mem_entries = [
            {
                "id": "mem_001",
                "character_id": "char_test001",
                "type": "long",
                "content": "我是被创建来测试预览功能的角色。",
                "importance": 0.9,
                "source": "manual",
                "tags": ["background"],
                "created_at": 1785390000000,
            },
            {
                "id": "mem_002",
                "character_id": "char_test001",
                "type": "long",
                "content": "我喜欢在虚拟世界里到处探索。",
                "importance": 0.7,
                "source": "manual",
                "tags": ["personality"],
                "created_at": 1785390000000,
            },
        ]
        with open(mem_dir / "entries.json", "w", encoding="utf-8") as f:
            json.dump(mem_entries, f, ensure_ascii=False, indent=2)

        # --- Worlds ---
        worlds_dir = dk / "worlds"
        worlds_dir.mkdir()

        world_dir = worlds_dir / "world_test001"
        world_dir.mkdir()

        world_data = {
            "id": "world_test001",
            "name": "测试世界",
            "world_doc": "# 测试世界观\n\n一个用于预览测试的虚拟世界。",
            "config": {
                "time_flow_multiplier": 30.0,
                "day_length_minutes": 1440,
                "weather_probabilities": {
                    "morning": {"sunny": 0.6, "rain": 0.2, "snow": 0.05, "cloudy": 0.15},
                },
            },
            "created_at": "2026-07-30T00:00:00Z",
            "updated_at": "2026-07-30T06:00:00Z",
            "doc_versions": [],
        }
        with open(world_dir / "world.json", "w", encoding="utf-8") as f:
            json.dump(world_data, f, ensure_ascii=False, indent=2)
        with open(world_dir / "world_doc.md", "w", encoding="utf-8") as f:
            f.write("# 测试世界观\n\n这是一个用于测试的世界。")
        with open(world_dir / "world_config.json", "w", encoding="utf-8") as f:
            json.dump(world_data["config"], f, ensure_ascii=False, indent=2)

        # Set the env var so devkit_stub resolves to our temp dir.
        devkit_stub._set_devkit_dir_for_test(str(dk))
        yield str(dk)
        devkit_stub._clear_env_override()


@pytest.fixture
def mock_empty_devkit_dir() -> Generator[str, None, None]:
    """Create an empty (or missing) devkit directory for edge-case tests."""
    with tempfile.TemporaryDirectory() as tmp:
        dk = Path(tmp) / "DevKit"
        dk.mkdir()  # exists but empty
        devkit_stub._set_devkit_dir_for_test(str(dk))
        yield str(dk)
        devkit_stub._clear_env_override()


# ---------------------------------------------------------------------------
# Test: is_available
# ---------------------------------------------------------------------------


def test_is_available_with_real_dir(mock_devkit_dir):
    assert devkit_stub.is_available() is True


def test_is_available_with_empty_dir(mock_empty_devkit_dir):
    assert devkit_stub.is_available() is True


def test_is_available_with_nonexistent_dir():
    devkit_stub._set_devkit_dir_for_test("/nonexistent/path/xijian_devkit_test")
    assert devkit_stub.is_available() is False
    devkit_stub._clear_env_override()


# ---------------------------------------------------------------------------
# Test: scan_characters
# ---------------------------------------------------------------------------


def test_scan_characters_finds_saved(mock_devkit_dir):
    chars = devkit_stub.scan_characters()
    assert len(chars) == 1
    assert chars[0]["id"] == "char_test001"
    assert chars[0]["name"] == "测试角色"


def test_scan_characters_empty_dir(mock_empty_devkit_dir):
    chars = devkit_stub.scan_characters()
    assert chars == []


# ---------------------------------------------------------------------------
# Test: scan_worlds
# ---------------------------------------------------------------------------


def test_scan_worlds_finds_saved(mock_devkit_dir):
    worlds = devkit_stub.scan_worlds()
    assert len(worlds) == 1
    assert worlds[0]["id"] == "world_test001"
    assert worlds[0]["name"] == "测试世界"


def test_scan_worlds_empty_dir(mock_empty_devkit_dir):
    worlds = devkit_stub.scan_worlds()
    assert worlds == []


# ---------------------------------------------------------------------------
# Test: load_character
# ---------------------------------------------------------------------------


def test_load_character_basic(mock_devkit_dir):
    record = devkit_stub.load_character("char_test001")
    assert record is not None
    assert record["name"] == "测试角色"
    assert record["display_name"] == "测试角色"
    assert record["object"] == "character"
    assert record["loaded"] is True
    assert record.get("_devkit_source") is True
    assert record.get("devkit_original_id") == "char_test001"

    # persona_doc from file should take precedence over JSON field
    assert "活泼可爱" in record.get("persona_doc", "")

    # memory_config should have been populated
    assert record["id"] in devkit_stub.state.memory_configs
    mc = devkit_stub.state.memory_configs[record["id"]]
    assert mc["max_long_term"] == 100
    assert mc["force_recall_on_history"] is True

    # character_state_config should be populated from character_config
    assert record["id"] in devkit_stub.state.character_state_configs
    sc = devkit_stub.state.character_state_configs[record["id"]]
    assert sc["hunger_decay_per_hour"] == 2.0

    # memory entries should be loaded
    mem_count = sum(
        1 for e in devkit_stub.state.memory.values()
        if e.get("character_id") == record["id"]
    )
    assert mem_count == 2


def test_load_character_nonexistent(mock_devkit_dir):
    record = devkit_stub.load_character("nonexistent_char")
    assert record is None


def test_load_character_twice_replaces(mock_devkit_dir):
    """Loading the same character twice should replace, not duplicate."""
    r1 = devkit_stub.load_character("char_test001")
    assert r1 is not None
    first_id = r1["id"]

    r2 = devkit_stub.load_character("char_test001")
    assert r2 is not None
    second_id = r2["id"]

    # Should be the same id but might have been updated
    assert first_id == second_id
    # Only one entry in state.characters with this devkit_original_id
    matches = [
        c for c in devkit_stub.state.characters.values()
        if c.get("devkit_original_id") == "char_test001"
    ]
    assert len(matches) == 1


def test_load_character_updates_memory_config(mock_devkit_dir):
    """Memory config should be updated on reload."""
    devkit_stub.load_character("char_test001")

    # Mutate memory config in the devkit save
    dk = devkit_stub.get_devkit_dir()
    char_path = os.path.join(dk, "characters", "char_test001", "character.json")
    with open(char_path, encoding="utf-8") as f:
        data = json.load(f)
    data["memory_config"]["max_long_term"] = 500
    with open(char_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    devkit_stub.load_character("char_test001")
    mc = devkit_stub.state.memory_configs.get("char_test001")
    assert mc is not None
    assert mc["max_long_term"] == 500


# ---------------------------------------------------------------------------
# Test: load_world
# ---------------------------------------------------------------------------


def test_load_world_basic(mock_devkit_dir):
    record = devkit_stub.load_world("world_test001")
    assert record is not None
    assert record["name"] == "测试世界"
    assert record.get("_devkit_source") is True
    assert record.get("devkit_original_id") == "world_test001"

    # world_environment should be init'd
    assert record["id"] in devkit_stub.state.world_environment
    env = devkit_stub.state.world_environment[record["id"]]
    assert "weather" in env
    assert "time_of_day" in env

    # world_compute_config should be init'd
    assert record["id"] in devkit_stub.state.world_compute_config
    wcc = devkit_stub.state.world_compute_config[record["id"]]
    assert wcc["total_token_budget"] == 50_000
    assert wcc["max_npcs"] == 50


def test_load_world_nonexistent(mock_devkit_dir):
    record = devkit_stub.load_world("nonexistent_world")
    assert record is None


# ---------------------------------------------------------------------------
# Test: unload
# ---------------------------------------------------------------------------


def test_unload_character(mock_devkit_dir):
    devkit_stub.load_character("char_test001")
    ok = devkit_stub.unload("character", "char_test001")
    assert ok is True

    # Should no longer be in state
    matches = [
        c for c in devkit_stub.state.characters.values()
        if c.get("devkit_original_id") == "char_test001"
    ]
    assert len(matches) == 0

    # Memory config should be cleaned up
    assert "char_test001" not in devkit_stub.state.memory_configs


def test_unload_nonexistent(mock_devkit_dir):
    ok = devkit_stub.unload("character", "nonexistent")
    assert ok is False


def test_unload_world(mock_devkit_dir):
    devkit_stub.load_world("world_test001")
    ok = devkit_stub.unload("world", "world_test001")
    assert ok is True

    matches = [
        w for w in devkit_stub.state.worlds.values()
        if w.get("devkit_original_id") == "world_test001"
    ]
    assert len(matches) == 0
    assert "world_test001" not in devkit_stub.state.world_environment


# ---------------------------------------------------------------------------
# Test: preview
# ---------------------------------------------------------------------------


def test_get_character_preview(mock_devkit_dir):
    preview = devkit_stub.get_character_preview("char_test001")
    assert preview is not None
    assert preview["id"] == "char_test001"
    assert preview["name"] == "测试角色"
    assert "_preview" in preview
    assert preview["_preview"]["persona_exists"] is True
    assert preview["_preview"]["memories_count"] == 2
    assert preview["_preview"]["is_loaded"] is False  # not loaded yet

    # After loading
    devkit_stub.load_character("char_test001")
    preview2 = devkit_stub.get_character_preview("char_test001")
    assert preview2["_preview"]["is_loaded"] is True


def test_get_character_preview_nonexistent(mock_devkit_dir):
    preview = devkit_stub.get_character_preview("nonexistent")
    assert preview is None


def test_get_world_preview(mock_devkit_dir):
    preview = devkit_stub.get_world_preview("world_test001")
    assert preview is not None
    assert preview["id"] == "world_test001"
    assert "_preview" in preview
    assert preview["_preview"]["doc_exists"] is True
    assert preview["_preview"]["config_exists"] is True


# ---------------------------------------------------------------------------
# Test: list_loaded
# ---------------------------------------------------------------------------


def test_list_loaded(mock_devkit_dir):
    loaded = devkit_stub.list_loaded()
    assert loaded["characters"] == []
    assert loaded["worlds"] == []

    devkit_stub.load_character("char_test001")
    loaded = devkit_stub.list_loaded()
    assert len(loaded["characters"]) == 1
    assert loaded["characters"][0]["name"] == "测试角色"

    devkit_stub.load_world("world_test001")
    loaded = devkit_stub.list_loaded()
    assert len(loaded["worlds"]) == 1
    assert loaded["worlds"][0]["name"] == "测试世界"


# ---------------------------------------------------------------------------
# Test: reload
# ---------------------------------------------------------------------------


def test_reload_characters(mock_devkit_dir):
    devkit_stub.load_character("char_test001")
    # Mutate devkit save
    dk = devkit_stub.get_devkit_dir()
    char_path = os.path.join(dk, "characters", "char_test001", "character.json")
    with open(char_path, encoding="utf-8") as f:
        data = json.load(f)
    data["name"] = "修改后的角色名"
    with open(char_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    result = devkit_stub.reload_characters()
    assert len(result) == 1
    assert result[0]["name"] == "修改后的角色名"

    # Verify runtime is updated
    record = devkit_stub.state.characters["char_test001"]
    assert record["name"] == "修改后的角色名"


def test_reload_worlds(mock_devkit_dir):
    devkit_stub.load_world("world_test001")
    # Mutate
    dk = devkit_stub.get_devkit_dir()
    wpath = os.path.join(dk, "worlds", "world_test001", "world.json")
    with open(wpath, encoding="utf-8") as f:
        data = json.load(f)
    data["name"] = "修改后的世界"
    with open(wpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    result = devkit_stub.reload_worlds()
    assert len(result) == 1
    assert result[0]["name"] == "修改后的世界"
    assert devkit_stub.state.worlds["world_test001"]["name"] == "修改后的世界"


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------


def test_corrupted_json_skipped():
    """A corrupted .json file should be silently skipped during scan."""
    with tempfile.TemporaryDirectory() as tmp:
        dk = Path(tmp) / "DevKit"
        chars_dir = dk / "characters"
        chars_dir.mkdir(parents=True)
        bad_char_dir = chars_dir / "bad_char"
        bad_char_dir.mkdir()
        with open(bad_char_dir / "character.json", "w") as f:
            f.write("{this is not valid json")

        devkit_stub._set_devkit_dir_for_test(str(dk))
        chars = devkit_stub.scan_characters()
        assert chars == []
        devkit_stub._clear_env_override()


def test_load_without_persona_file():
    """Character without persona.md should fall back to persona_doc in JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        dk = Path(tmp) / "DevKit"
        chars_dir = dk / "characters"
        chars_dir.mkdir(parents=True)
        char_dir = chars_dir / "no_persona_char"
        char_dir.mkdir()
        data = {
            "id": "no_persona_char",
            "name": "无Persona角色",
            "persona_doc": "内嵌的角色描述",
        }
        with open(char_dir / "character.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        devkit_stub._set_devkit_dir_for_test(str(dk))
        record = devkit_stub.load_character("no_persona_char")
        assert record is not None
        assert record["persona_doc"] == "内嵌的角色描述"
        devkit_stub._clear_env_override()


# ---------------------------------------------------------------------------
# Test: HTTP routes (via Flask test client)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(mock_devkit_dir):
    """A Flask test client with the devkit routes and error handlers registered."""
    from flask import Flask
    from xijian_api.routes.xijian_devkit import bp
    from xijian_api.errors import register_error_handlers

    app = Flask(__name__)
    app.register_blueprint(bp)
    register_error_handlers(app)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestRoutes:

    def test_status_nonexistent(self):
        """Status should report not available when devkit dir is missing."""
        from flask import Flask
        from xijian_api.routes.xijian_devkit import bp
        devkit_stub._set_devkit_dir_for_test("/nonexistent/devkit_test_path")
        app = Flask(__name__)
        app.register_blueprint(bp)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/v1/xijian/devkit/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["available"] is False
            assert "error" in data
        devkit_stub._clear_env_override()

    def test_status(self, client):
        resp = client.get("/v1/xijian/devkit/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["available"] is True
        assert data["character_count"] == 1
        assert data["world_count"] == 1

    def test_list_characters(self, client):
        resp = client.get("/v1/xijian/devkit/characters")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data.get("data", data)) >= 1

    def test_character_detail(self, client):
        resp = client.get("/v1/xijian/devkit/characters/char_test001")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "测试角色"
        assert "_preview" in data

    def test_character_detail_not_found(self, client):
        resp = client.get("/v1/xijian/devkit/characters/nonexistent")
        assert resp.status_code == 404

    def test_character_load(self, client):
        resp = client.post("/v1/xijian/devkit/characters/char_test001/load")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["name"] == "测试角色"

    def test_character_load_twice(self, client):
        """Loading twice should still succeed (replace)."""
        resp1 = client.post("/v1/xijian/devkit/characters/char_test001/load")
        assert resp1.status_code == 200
        resp2 = client.post("/v1/xijian/devkit/characters/char_test001/load")
        assert resp2.status_code == 200

    def test_character_unload(self, client):
        client.post("/v1/xijian/devkit/characters/char_test001/load")
        resp = client.delete("/v1/xijian/devkit/characters/char_test001")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_character_unload_post(self, client):
        """POST /unload should work the same as DELETE."""
        client.post("/v1/xijian/devkit/characters/char_test001/load")
        resp = client.post("/v1/xijian/devkit/characters/char_test001/unload")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_character_unload_not_loaded(self, client):
        resp = client.delete("/v1/xijian/devkit/characters/nonexistent")
        assert resp.status_code == 404

    def test_list_worlds(self, client):
        resp = client.get("/v1/xijian/devkit/worlds")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data.get("data", data)) >= 1

    def test_world_detail(self, client):
        resp = client.get("/v1/xijian/devkit/worlds/world_test001")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "测试世界"

    def test_world_load(self, client):
        resp = client.post("/v1/xijian/devkit/worlds/world_test001/load")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["name"] == "测试世界"

    def test_world_unload(self, client):
        client.post("/v1/xijian/devkit/worlds/world_test001/load")
        resp = client.delete("/v1/xijian/devkit/worlds/world_test001")
        assert resp.status_code == 200

    def test_loaded_list(self, client):
        resp = client.get("/v1/xijian/devkit/loaded")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "characters" in data
        assert "worlds" in data

        client.post("/v1/xijian/devkit/characters/char_test001/load")
        resp = client.get("/v1/xijian/devkit/loaded")
        data = resp.get_json()
        assert len(data["characters"]) == 1

    def test_reload(self, client):
        client.post("/v1/xijian/devkit/characters/char_test001/load")
        client.post("/v1/xijian/devkit/worlds/world_test001/load")
        resp = client.post("/v1/xijian/devkit/reload")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["reloaded"]["characters"] == 1
        assert data["reloaded"]["worlds"] == 1

    def test_reload_with_kind_filter(self, client):
        resp = client.post("/v1/xijian/devkit/reload?kind=character")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "characters" in data["reloaded"]
        assert "worlds" not in data["reloaded"]

    def test_reload_invalid_kind(self, client):
        resp = client.post("/v1/xijian/devkit/reload?kind=invalid")
        assert resp.status_code == 400

    def test_generic_list(self, client):
        resp = client.get("/v1/xijian/devkit/characters")
        assert resp.status_code == 200
        resp = client.get("/v1/xijian/devkit/worlds")
        assert resp.status_code == 200
        resp = client.get("/v1/xijian/devkit/invalid")
        assert resp.status_code == 400
