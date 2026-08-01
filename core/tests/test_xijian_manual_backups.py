"""Tests for A1.1 manual backup system (``stubs.manual_backups`` +
``/v1/backups`` + ``/v1/protected-modules``).

(A1.1 手动备份系统 (``stubs.manual_backups`` +
``/v1/backups`` + ``/v1/protected-modules``) 的测试。)

Covers:
(覆盖范围：)

* **Protected modules (AC-1)** — the four default modules exist;
  module enable/disable hot-update works.
* (**受保护模块 (AC-1)** — 四个默认模块存在；模块启用/禁用热更新生效。)
* **Per-character association (US-A1.1-01)** — auto_backup toggle +
  last_backup_at is recorded after a backup.
* (**每角色关联 (US-A1.1-01)** — auto_backup 切换 + 备份后记录
  last_backup_at。)
* **Manual backup (AC-2/AC-3)** — file naming
  ``{character_id}_{ISO8601}_v{n}.bak``, version increment, zstd
  codec, retention (max 10 versions pruned oldest-first).
* (**手动备份 (AC-2/AC-3)** — 文件命名 ``{character_id}_{ISO8601}_v{n}.bak``、
  版本递增、zstd 编解码器、保留策略 (最多 10 个版本，最旧优先修剪)。)
* **Restore (US-A1.1-03)** — optional scope restore, restore to a
  different character.
* (**恢复 (US-A1.1-03)** — 可选 scope 恢复、恢复到不同角色。)
* **Auto-backup triggers** — 50-edit threshold, first-load, safe
  termination, daily scheduler.
* (**自动备份触发** — 50 次编辑阈值、首次加载、安全终止、每日调度器。)
"""

from __future__ import annotations

import os
import time

import pytest

from xijian_api.stubs import manual_backups as mb_stub
from xijian_api.stubs import memory as memory_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs.manual_backups import (
    AUTO_BACKUP_EDIT_THRESHOLD,
    DEFAULT_PROTECTED_MODULES,
    MAX_VERSIONS_PER_CHARACTER,
    SCOPE_ALL,
    SCOPE_DOC_ONLY,
    SCOPE_MEMORY_ONLY,
    SCOPE_STATE_ONLY,
    VALID_SCOPES,
)


# ---------------------------------------------------------------------------
# Protected modules
# ---------------------------------------------------------------------------


class TestProtectedModules:
    def test_default_modules_present(self):
        names = {m["module_name"] for m in mb_stub.list_protected_modules()}
        assert names == {
            "memory_entries",
            "character_documents",
            "world_documents",
            "safety_snapshots",
        }

    def test_get_protected_module(self):
        module = mb_stub.get_protected_module("memory_entries")
        assert module is not None
        assert module["enabled"] == 1

    def test_set_module_enabled(self):
        record = mb_stub.set_module_enabled("memory_entries", False)
        assert record["enabled"] == 0
        assert mb_stub.get_protected_module("memory_entries")["enabled"] == 0
        mb_stub.set_module_enabled("memory_entries", True)

    def test_unknown_module_toggle_raises(self):
        with pytest.raises(ValueError):
            mb_stub.set_auto_backup("char_yuki", "not_a_module", True)

    def test_list_with_character_association(self):
        modules = mb_stub.list_protected_modules(character_id="char_yuki")
        for module in modules:
            assert "auto_backup" in module
            assert "last_backup_at" in module


class TestCharacterProtection:
    def test_defaults_auto_backup_on(self):
        protection = mb_stub.get_character_protection("char_yuki")
        assert protection["character_id"] == "char_yuki"
        assert protection["auto_backup_enabled"] is True

    def test_toggle_off(self):
        mb_stub.set_auto_backup("char_yuki", "memory_entries", False)
        modules = mb_stub.list_protected_modules(character_id="char_yuki")
        by_name = {m["module_name"]: m for m in modules}
        assert by_name["memory_entries"]["auto_backup"] == 0
        # Other modules keep their default on-state.
        assert by_name["character_documents"]["auto_backup"] == 1

    def test_touch_backup_records_timestamp(self):
        ts = int(time.time())
        mb_stub.touch_backup("char_yuki", "memory_entries", ts)
        modules = mb_stub.list_protected_modules(character_id="char_yuki")
        by_name = {m["module_name"]: m for m in modules}
        assert by_name["memory_entries"]["last_backup_at"] == ts


# ---------------------------------------------------------------------------
# Backup CRUD + naming + retention
# ---------------------------------------------------------------------------


class TestCreateBackup:
    def test_create_returns_versioned_record(self):
        record = mb_stub.create_backup("char_yuki", scope=SCOPE_ALL)
        assert record["character_id"] == "char_yuki"
        assert record["version"] == 1
        assert record["file_name"].startswith("char_yuki_")
        assert record["file_name"].endswith(".bak")
        assert "v1" in record["file_name"]
        assert record["scope"] == SCOPE_ALL
        assert record["created_by"] == "user"
        assert record["codec"] == "zstd"
        assert record["size_bytes"] > 0

    def test_version_increments(self):
        first = mb_stub.create_backup("char_yuki")
        second = mb_stub.create_backup("char_yuki")
        assert first["version"] == 1
        assert second["version"] == 2

    def test_invalid_scope_raises(self):
        with pytest.raises(ValueError):
            mb_stub.create_backup("char_yuki", scope="everything")

    def test_unknown_character_raises(self):
        with pytest.raises(ValueError):
            mb_stub.create_backup("char_does_not_exist")

    def test_backup_payload_contains_memory(self):
        memory_stub.create(
            {
                "character_id": "char_yuki",
                "type": "long",
                "content": "被备份的事实",
                "importance": 0.8,
                "source": "manual",
            }
        )
        record = mb_stub.create_backup("char_yuki", scope=SCOPE_MEMORY_ONLY)
        # The raw payload round-trips through zstd.
        payload = mb_stub.get_backup_payload(record["id"])
        contents = [e["content"] for e in payload["memory_entries"]]
        assert "被备份的事实" in contents


class TestRetention:
    def test_prunes_beyond_max_versions(self):
        for _ in range(MAX_VERSIONS_PER_CHARACTER + 3):
            mb_stub.create_backup("char_yuki")
        backups = mb_stub.list_backups(character_id="char_yuki")
        assert len(backups) <= MAX_VERSIONS_PER_CHARACTER
        # Newest versions survive.
        versions = sorted(b["version"] for b in backups)
        assert versions == list(range(MAX_VERSIONS_PER_CHARACTER + 3 - MAX_VERSIONS_PER_CHARACTER + 1, MAX_VERSIONS_PER_CHARACTER + 4))


class TestListGetDelete:
    def test_list_filters_by_character(self):
        mb_stub.create_backup("char_yuki")
        mb_stub.create_backup("char_yuki")
        assert len(mb_stub.list_backups(character_id="char_yuki")) == 2
        assert mb_stub.list_backups(character_id="char_other") == []

    def test_get_returns_slim_record(self):
        created = mb_stub.create_backup("char_yuki")
        fetched = mb_stub.get_backup(created["id"])
        assert fetched["id"] == created["id"]
        assert "payload_bytes" not in fetched

    def test_delete(self):
        created = mb_stub.create_backup("char_yuki")
        assert mb_stub.delete_backup(created["id"]) is True
        assert mb_stub.get_backup(created["id"]) is None
        assert mb_stub.delete_backup(created["id"]) is False


# ---------------------------------------------------------------------------
# Restore (US-A1.1-03)
# ---------------------------------------------------------------------------


class TestRestore:
    def _seed_memory(self, content: str) -> str:
        record = memory_stub.create(
            {
                "character_id": "char_yuki",
                "type": "long",
                "content": content,
                "importance": 0.7,
                "source": "manual",
            }
        )
        return record["id"]

    def test_restore_memory_only_replaces_entries(self):
        self._seed_memory("备份前的记忆")
        backup = mb_stub.create_backup("char_yuki", scope=SCOPE_MEMORY_ONLY)
        # Mutate memory after the backup.
        memory_stub.create(
            {
                "character_id": "char_yuki",
                "type": "short",
                "content": "备份后的新记忆",
                "source": "manual",
            }
        )
        summary = mb_stub.restore_backup(backup["id"], scope=SCOPE_MEMORY_ONLY)
        assert summary["scope"] == SCOPE_MEMORY_ONLY
        assert summary["target_character"] == "char_yuki"
        remaining = [
            e["content"]
            for e in stubs_state.memory.values()
            if e.get("character_id") == "char_yuki"
        ]
        assert "备份后的新记忆" not in remaining
        assert "备份前的记忆" in remaining

    def test_restore_to_different_character(self):
        self._seed_memory("要转移的记忆")
        backup = mb_stub.create_backup("char_yuki", scope=SCOPE_MEMORY_ONLY)
        summary = mb_stub.restore_backup(
            backup["id"], scope=SCOPE_MEMORY_ONLY, target_character_id="char_other"
        )
        assert summary["target_character"] == "char_other"
        transferred = [
            e for e in stubs_state.memory.values()
            if e.get("character_id") == "char_other"
        ]
        assert any("要转移的记忆" in e["content"] for e in transferred)

    def test_restore_state_only(self):
        from xijian_api.stubs import character_state as cs_stub
        cs_stub.apply_field_change("char_yuki", "health", 40.0, reason="manual")
        backup = mb_stub.create_backup("char_yuki", scope=SCOPE_STATE_ONLY)
        cs_stub.apply_field_change("char_yuki", "health", 10.0, reason="manual")
        mb_stub.restore_backup(backup["id"], scope=SCOPE_STATE_ONLY)
        assert cs_stub.get_state("char_yuki")["health"] == 40.0

    def test_restore_unknown_backup_raises(self):
        with pytest.raises(KeyError):
            mb_stub.restore_backup("bak_does_not_exist")

    def test_restore_invalid_scope_raises(self):
        backup = mb_stub.create_backup("char_yuki")
        with pytest.raises(ValueError):
            mb_stub.restore_backup(backup["id"], scope="everything")


# ---------------------------------------------------------------------------
# Auto-backup triggers
# ---------------------------------------------------------------------------


class TestAutoBackupTriggers:
    def test_edit_threshold_auto_backup(self):
        assert mb_stub.notify_memory_modified("char_yuki", AUTO_BACKUP_EDIT_THRESHOLD - 1) is None
        record = mb_stub.notify_memory_modified("char_yuki", 1)
        assert record is not None
        assert record["created_by"] == "system"
        # Counter resets — a second trigger needs 50 more edits.
        assert mb_stub.notify_memory_modified("char_yuki", 1) is None

    def test_first_load_triggers_once(self):
        first = mb_stub.notify_first_load("char_yuki")
        assert first is not None
        assert first["created_by"] == "system"
        assert mb_stub.notify_first_load("char_yuki") is None

    def test_safe_termination_backs_up_characters(self):
        # Give the character an explicit auto_backup association.
        mb_stub.set_auto_backup("char_yuki", "memory_entries", True)
        result = mb_stub.notify_safe_termination()
        assert result["count"] >= 1
        assert len(result["created"]) == result["count"]

    def test_daily_backup_due(self, monkeypatch):
        monkeypatch.setattr(mb_stub.time, "localtime", lambda t=None: __import__("time").struct_time(
            (2026, 8, 1, mb_stub.DAILY_BACKUP_HOUR + 1, 0, 0, 5, 213, 0)
        ))
        assert mb_stub._is_daily_backup_due() is True
        # Same day → not due again.
        assert mb_stub._is_daily_backup_due() is False

    def test_run_daily_backups_creates_system_backups(self):
        mb_stub.set_auto_backup("char_yuki", "memory_entries", True)
        result = mb_stub._run_daily_backups()
        assert result["count"] >= 1
        for bid in result["created"]:
            record = mb_stub.get_backup(bid)
            assert record["created_by"] == "system"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestBackupRoutes:
    def test_create_backup_route(self, client, auth_headers):
        response = client.post(
            "/v1/backups",
            headers=auth_headers,
            json={"character_id": "char_yuki"},
        )
        assert response.status_code == 201
        body = response.get_json()
        assert body["object"] == "manual_backup"
        assert body["file_name"].startswith("char_yuki_")

    def test_create_backup_missing_character(self, client, auth_headers):
        response = client.post("/v1/backups", headers=auth_headers, json={})
        assert response.status_code == 400

    def test_list_backups_route(self, client, auth_headers):
        client.post("/v1/backups", headers=auth_headers, json={"character_id": "char_yuki"})
        response = client.get("/v1/backups?character_id=char_yuki", headers=auth_headers)
        assert response.status_code == 200
        assert response.get_json()["object"] == "list"

    def test_restore_route(self, client, auth_headers):
        create = client.post(
            "/v1/backups", headers=auth_headers,
            json={"character_id": "char_yuki", "scope": SCOPE_MEMORY_ONLY},
        )
        bid = create.get_json()["id"]
        response = client.post(
            f"/v1/backups/{bid}/restore",
            headers=auth_headers,
            json={"scope": SCOPE_MEMORY_ONLY},
        )
        assert response.status_code == 200
        assert response.get_json()["backup_id"] == bid

    def test_restore_route_404(self, client, auth_headers):
        response = client.post(
            "/v1/backups/bak_missing/restore", headers=auth_headers, json={}
        )
        assert response.status_code == 404

    def test_protected_modules_route(self, client, auth_headers):
        response = client.get("/v1/protected-modules", headers=auth_headers)
        assert response.status_code == 200
        names = [m["module_name"] for m in response.get_json()["data"]]
        assert "memory_entries" in names

    def test_character_protection_patch(self, client, auth_headers):
        response = client.patch(
            "/v1/characters/char_yuki/protected-modules",
            headers=auth_headers,
            json={"module_name": "memory_entries", "enabled": False},
        )
        assert response.status_code == 200
        assert response.get_json()["auto_backup"] == 0

    def test_delete_backup_route(self, client, auth_headers):
        create = client.post(
            "/v1/backups", headers=auth_headers, json={"character_id": "char_yuki"}
        )
        bid = create.get_json()["id"]
        response = client.delete(f"/v1/backups/{bid}", headers=auth_headers)
        assert response.status_code == 200
