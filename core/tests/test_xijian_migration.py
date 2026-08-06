"""Tests for legacy data migration (``stubs.migration`` + routes).

旧数据迁移（``stubs.migration`` + 路由）的测试。

Covers the idempotent / non-destructive / conflict-aware contract:
* files + dirs are copied from ``~/.xijian`` → CORE_ROOT;
* a mark file short-circuits re-runs;
* the legacy directory is never deleted;
* differing target files are recorded as conflicts, never overwritten;
* ``resolve`` supports ``keep="legacy"`` and ``keep="new"``.

覆盖幂等 / 非破坏 / 冲突感知的契约：
* 文件与目录从 ``~/.xijian`` 复制到 CORE_ROOT；
* 标记文件使再次运行短路；
* 旧目录永不被删除；
* 内容不同的目标文件记入冲突清单，绝不覆盖；
* ``resolve`` 支持 ``keep="legacy"`` 与 ``keep="new"``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xijian_api.stubs import migration as migration_stub

# Snapshot the real module-level paths so the autouse fixture can restore
# them after each test (the suite shares one process).
# 快照模块级真实路径，供 autouse fixture 在测试后恢复（套件共享单进程）。
_ORIGINAL_PATHS = (
    migration_stub.LEGACY_ROOT,
    migration_stub.CORE_ROOT,
    migration_stub.MIGRATION_MARK,
)


@pytest.fixture(autouse=True)
def paths(tmp_path):
    """Point migration at a temp legacy/core pair per test.

    将迁移指向每个测试独立的临时 legacy/core 对。
    """
    legacy = tmp_path / "legacy"
    core = tmp_path / "core"
    legacy.mkdir(parents=True)
    core.mkdir(parents=True)
    migration_stub._set_paths_for_test(legacy, core)
    yield {"legacy": legacy, "core": core}
    migration_stub._set_paths_for_test(*_ORIGINAL_PATHS[:2])


# ---------------------------------------------------------------------------
# Stub layer — migrate / status
# 存根层 — 迁移 / 状态
# ---------------------------------------------------------------------------


def test_migrate_copies_files_and_dirs(paths):
    """Files + dirs are copied; legacy is untouched; mark file is written.

    文件与目录被复制；旧目录不被触碰；标记文件被写入。
    """
    legacy, core = paths["legacy"], paths["core"]
    (legacy / "xijian.db").write_bytes(b"db-bytes")
    (legacy / "files").mkdir()
    (legacy / "files" / "note.txt").write_text("hello", encoding="utf-8")

    status = migration_stub.migrate_legacy_data()

    assert status["migrated"] is True
    assert status["legacy_exists"] is True
    assert (core / "xijian.db").read_bytes() == b"db-bytes"
    assert (core / "files" / "note.txt").read_text(encoding="utf-8") == "hello"
    # Non-destructive: the legacy dir still exists.
    # 非破坏性：旧目录仍然存在。
    assert legacy.exists()
    assert (core / ".migrated_from_xijian").is_file()


def test_migrate_idempotent_short_circuits(paths):
    """A second run short-circuits via the mark file (no duplicate items).

    再次运行通过标记文件短路（不产生重复条目）。
    """
    legacy, core = paths["legacy"], paths["core"]
    (legacy / "xijian.db").write_bytes(b"x")

    first = migration_stub.migrate_legacy_data()
    second = migration_stub.migrate_legacy_data()

    assert first["migrated"] is True
    assert second["migrated"] is True
    assert len(second["items"]) == len(first["items"])


def test_migrate_no_legacy_returns_status(paths):
    """Missing legacy dir returns a status dict without raising.

    旧目录缺失时返回状态字典而不抛异常。
    """
    import shutil as _shutil

    _shutil.rmtree(paths["legacy"])  # 模拟一台没有旧版目录的机器
    status = migration_stub.migrate_legacy_data()
    assert status["legacy_exists"] is False
    assert status["migrated"] is False
    assert status["items"] == []


def test_migrate_conflict_recorded_not_overwritten(paths):
    """A differing target file becomes a conflict and is never overwritten.

    内容不同的目标文件记入冲突且绝不覆盖。
    """
    legacy, core = paths["legacy"], paths["core"]
    (legacy / "files").mkdir()
    (legacy / "files" / "a.txt").write_text("legacy content", encoding="utf-8")
    (core / "files").mkdir(parents=True)
    (core / "files" / "a.txt").write_text("target content", encoding="utf-8")

    status = migration_stub.migrate_legacy_data()

    assert status["conflicts"], "expected a conflict"
    conflict = status["conflicts"][0]
    # Item is relative to the migrated dir root (files/a.txt → a.txt).
    # 条目相对被迁移目录根（files/a.txt → a.txt）。
    assert conflict["item"] == "a.txt"
    assert conflict["status"] == "pending"
    # Target untouched.
    # 目标未被覆盖。
    assert (core / "files" / "a.txt").read_text(encoding="utf-8") == "target content"
    # Legacy source still intact.
    # 旧源文件仍然完整。
    assert (legacy / "files" / "a.txt").read_text(encoding="utf-8") == "legacy content"


def test_conflict_id_unique_for_same_basename(paths):
    """Same basename in different dirs yields distinct conflict_ids.

    不同目录下同名文件产生不同的 conflict_id。
    """
    legacy, core = paths["legacy"], paths["core"]
    for sub in ("a", "b"):
        (legacy / "files" / sub).mkdir(parents=True)
        (legacy / "files" / sub / "config.json").write_text(f"legacy-{sub}", encoding="utf-8")
        (core / "files" / sub).mkdir(parents=True)
        (core / "files" / sub / "config.json").write_text(f"target-{sub}", encoding="utf-8")

    status = migration_stub.migrate_legacy_data()

    ids = [c["conflict_id"] for c in status["conflicts"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2, f"conflict_id collision: {ids}"


# ---------------------------------------------------------------------------
# Conflict resolution
# 冲突解决
# ---------------------------------------------------------------------------


def test_resolve_keep_legacy(paths):
    """keep="legacy" backs up the target and copies the legacy file over.

    keep="legacy" 备份目标文件并把旧文件复制过去。
    """
    legacy, core = paths["legacy"], paths["core"]
    (legacy / "files").mkdir()
    (legacy / "files" / "a.txt").write_text("legacy", encoding="utf-8")
    (core / "files").mkdir(parents=True)
    (core / "files" / "a.txt").write_text("target", encoding="utf-8")

    status = migration_stub.migrate_legacy_data()
    conflict_id = status["conflicts"][0]["conflict_id"]

    result = migration_stub.resolve_conflict(conflict_id, "legacy")

    assert result["ok"] is True
    assert (core / "files" / "a.txt").read_text(encoding="utf-8") == "legacy"
    # Target backed up next to itself.
    # 目标文件在旁备份。
    backups = list((core / "files").glob("a.txt.conflict-*"))
    assert len(backups) == 1


def test_resolve_keep_new(paths):
    """keep="new" keeps the target and renames the legacy source aside.

    keep="new" 保留目标文件，把旧源文件改名放在一旁。
    """
    legacy, core = paths["legacy"], paths["core"]
    (legacy / "files").mkdir()
    (legacy / "files" / "a.txt").write_text("legacy", encoding="utf-8")
    (core / "files").mkdir(parents=True)
    (core / "files" / "a.txt").write_text("target", encoding="utf-8")

    status = migration_stub.migrate_legacy_data()
    conflict_id = status["conflicts"][0]["conflict_id"]

    result = migration_stub.resolve_conflict(conflict_id, "new")

    assert result["ok"] is True
    assert (core / "files" / "a.txt").read_text(encoding="utf-8") == "target"
    # Legacy source renamed aside (still under the legacy dir).
    # 旧源文件改名放在一旁（仍在旧目录下）。
    assert list((legacy / "files").glob("a.txt.conflict-*"))


def test_resolve_unknown_conflict(paths):
    """Unknown conflict_id / invalid keep return ok=False.

    未知 conflict_id / 非法 keep 返回 ok=False。
    """
    legacy, core = paths["legacy"], paths["core"]
    # Seed a real conflict + mark file so resolution logic is reached.
    # 预置真实冲突 + 标记文件，使解析逻辑可达。
    (legacy / "files").mkdir()
    (legacy / "files" / "a.txt").write_text("legacy", encoding="utf-8")
    (core / "files").mkdir(parents=True)
    (core / "files" / "a.txt").write_text("target", encoding="utf-8")
    status = migration_stub.migrate_legacy_data()
    real_id = status["conflicts"][0]["conflict_id"]
    assert migration_stub.MIGRATION_MARK.is_file()

    result = migration_stub.resolve_conflict("conflict_nope", "legacy")
    assert result["ok"] is False
    assert result["error"] == "conflict_not_found"

    result2 = migration_stub.resolve_conflict(real_id, "sideways")
    assert result2["ok"] is False
    assert result2["error"] == "invalid_keep"


# ---------------------------------------------------------------------------
# HTTP layer
# HTTP 层
# ---------------------------------------------------------------------------


def test_api_migration_status(client, auth_headers, paths):
    """GET /v1/xijian/migration/status returns the status envelope.

    GET /v1/xijian/migration/status 返回状态信封。
    """
    resp = client.get("/v1/xijian/migration/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert "legacy_exists" in body
    assert "migrated" in body
    assert "items" in body
    assert "conflicts" in body


def test_api_migration_conflicts(client, auth_headers, paths):
    """GET /v1/xijian/migration/conflicts returns the conflict list.

    GET /v1/xijian/migration/conflicts 返回冲突清单。
    """
    resp = client.get("/v1/xijian/migration/conflicts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert "conflicts" in body


def test_api_migration_resolve_validation(client, auth_headers):
    """POST resolve rejects a missing conflict_id with 400.

    POST resolve 缺少 conflict_id 返回 400。
    """
    resp = client.post(
        "/v1/xijian/migration/resolve",
        headers=auth_headers,
        json={"keep": "legacy"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "missing_conflict_id"

    resp2 = client.post(
        "/v1/xijian/migration/resolve",
        headers=auth_headers,
        json={"conflict_id": "x", "keep": "bad"},
    )
    assert resp2.status_code == 400
    assert resp2.get_json()["error"]["code"] == "invalid_keep"
