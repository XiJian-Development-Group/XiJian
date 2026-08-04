"""Tests for the resource pack engine (``stubs.packs``) and its routes.

资源包引擎（``stubs.packs``）及其路由的测试。

The pack directory is redirected to a per-test temporary directory via
``packs._set_paths_for_test`` so no test ever touches the real
CORE_ROOT.  Both the stub layer (install / scan / uninstall / preload)
and the HTTP layer (GET/POST/DELETE/rescan) are exercised.

包目录通过 ``packs._set_paths_for_test`` 重定向到每个测试独立的临时目录，
测试不会触碰真实 CORE_ROOT。存根层（安装/扫描/卸载/预置）与 HTTP 层
（GET/POST/DELETE/rescan）都会被覆盖。
"""

from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Generator

import py7zr
import pytest

from xijian_api.stubs import packs as packs_stub
from xijian_api.stubs import state as stubs_state


# ---------------------------------------------------------------------------
# Helpers — build pack directories and archives
# 辅助 — 构建包目录与归档
# ---------------------------------------------------------------------------


def _write_pack_dir(
    root: Path,
    *,
    package_id: str = "char-yuki",
    name: str = "Yuki",
    version: str = "1.0.0",
    kind: str = "character",
    char_id: str = "char-yuki",
    include_char: bool = True,
    include_world: bool = False,
    world_id: str = "world-yuki",
) -> Path:
    """Create a pack directory on disk and return it.

    在磁盘上创建包目录并返回它。
    """
    pack = root / package_id
    pack.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "xijian.pack/v1",
        "package_id": package_id,
        "name": name,
        "version": version,
        "kind": kind,
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    if include_char:
        cdir = pack / "characters" / char_id
        cdir.mkdir(parents=True, exist_ok=True)
        char_json = {
            "id": char_id,
            "name": name,
            "display_name": name,
            "description": "test character",
            "persona_doc": "initial",
            "memory_config": {"max_entries": 50, "priority": "recent"},
            "character_config": {"state_config": {"hunger_decay_per_hour": 1.5}},
        }
        (cdir / "character.json").write_text(json.dumps(char_json), encoding="utf-8")
        (cdir / "persona.md").write_text("# Persona\nTest persona.", encoding="utf-8")
        mdir = pack / "memories" / char_id
        mdir.mkdir(parents=True, exist_ok=True)
        entries = [
            {"id": f"mem_{char_id}_1", "content": "first memory", "type": "long", "importance": 0.8},
            {"id": f"mem_{char_id}_2", "content": "second memory", "type": "short", "importance": 0.4},
        ]
        (mdir / "entries.json").write_text(json.dumps(entries), encoding="utf-8")

    if include_world:
        wdir = pack / "worlds" / world_id
        wdir.mkdir(parents=True, exist_ok=True)
        world_json = {
            "id": world_id,
            "name": f"{name} World",
            "description": "test world",
        }
        (wdir / "world.json").write_text(json.dumps(world_json), encoding="utf-8")
        (wdir / "world_doc.md").write_text("# World doc\nA test world.", encoding="utf-8")
        (wdir / "world_config.json").write_text(json.dumps({"weather_probabilities": {}}), encoding="utf-8")

    return pack


def _zip_dir(src: Path, dest: Path) -> None:
    """Zip a directory with manifest.json at the archive root.

    将目录打包为 zip，manifest.json 位于归档根级。
    """
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())


def _sevenz_dir(src: Path, dest: Path) -> None:
    """7z a directory with manifest.json at the archive root.

    将目录打包为 7z，manifest.json 位于归档根级。
    """
    with py7zr.SevenZipFile(dest, "w") as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.writestr(f.read_bytes(), f.relative_to(src).as_posix())


def _packed_character_ids() -> list[str]:
    """Return core character ids tagged as coming from any pack.

    返回所有标记为来自包的核心角色 id。
    """
    return [
        r["id"] for r in stubs_state.characters.values()
        if r.get(packs_stub._SOURCE_TAG)
    ]


# ---------------------------------------------------------------------------
# Fixtures
# 测试夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_packs(tmp_path):
    """Point the packs directory at a fresh temp dir per test and clear
    pack-related runtime state for deterministic counts.

    将包目录指向每个测试独立的临时目录，并清空包相关运行时状态以保证计数确定性。
    """
    packs_stub._set_paths_for_test(tmp_path / "packs")
    stubs_state.packs_index.clear()
    stubs_state.characters.clear()
    stubs_state.worlds.clear()
    stubs_state.memory.clear()
    stubs_state.memory_configs.clear()
    stubs_state.character_state_configs.clear()
    stubs_state.world_environment.clear()
    stubs_state.world_compute_config.clear()
    yield
    packs_stub._set_paths_for_test(None)


# ---------------------------------------------------------------------------
# Stub layer — install (zip / 7z)
# 存根层 — 安装（zip / 7z）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("archiver", [_zip_dir, _sevenz_dir], ids=["zip", "7z"])
def test_install_archive_loads_character(tmp_path, archiver):
    """Installing a character pack loads the character + sidecars into runtime.

    安装角色包会将角色及其侧车配置加载进运行时。
    """
    pack = _write_pack_dir(tmp_path)
    archive = tmp_path / "char-yuki.7z" if archiver is _sevenz_dir else tmp_path / "char-yuki.zip"
    archiver(pack, archive)

    record = packs_stub.install_archive(archive)

    assert record["package_id"] == "char-yuki"
    assert record["kind"] == "character"
    assert record["loaded"]["characters"] == ["char-yuki"]
    assert record["loaded"]["memories"] == ["mem_char-yuki_1", "mem_char-yuki_2"]

    # Pack directory installed under <packs_root>/<package_id>/.
    assert (tmp_path / "packs" / "char-yuki" / "manifest.json").is_file()

    char = stubs_state.characters["char-yuki"]
    assert char["name"] == "Yuki"
    assert char[packs_stub._SOURCE_TAG] is True
    assert char[packs_stub._ORIGINAL_ID_TAG] == "char-yuki"
    assert char["persona_doc"].startswith("# Persona")

    # Sidecars tagged.
    assert stubs_state.memory_configs["char-yuki"].get(packs_stub._ORIGINAL_ID_TAG) == "char-yuki"
    assert stubs_state.character_state_configs["char-yuki"].get(packs_stub._ORIGINAL_ID_TAG) == "char-yuki"
    # Memories loaded with pack tag.
    mem = stubs_state.memory["mem_char-yuki_1"]
    assert mem["character_id"] == "char-yuki"
    assert mem[packs_stub._ORIGINAL_ID_TAG] == "char-yuki"
    assert mem["source"] == "pack_initial"

    # Index updated.
    entry = packs_stub.get_pack("char-yuki")
    assert entry is not None
    assert entry["version"] == "1.0.0"
    assert entry["kind"] == "character"
    assert entry["loaded"] is True


def test_install_archive_7z_world(tmp_path):
    """Installing a world pack initialises environment/compute config and NPCs.

    安装世界包会初始化环境/算力配置并自动生成 NPC。
    """
    pack = _write_pack_dir(
        tmp_path,
        package_id="world-yuki",
        name="Yuki World",
        kind="world",
        include_char=False,
        include_world=True,
    )
    archive = tmp_path / "world-yuki.7z"
    _sevenz_dir(pack, archive)

    record = packs_stub.install_archive(archive)
    assert record["kind"] == "world"
    assert record["loaded"]["worlds"] == ["world-yuki"]

    world = stubs_state.worlds["world-yuki"]
    assert world[packs_stub._SOURCE_TAG] is True
    assert world[packs_stub._ORIGINAL_ID_TAG] == "world-yuki"
    assert "A test world." in world["world_doc"]

    assert stubs_state.world_environment["world-yuki"].get(packs_stub._ORIGINAL_ID_TAG) == "world-yuki"
    assert stubs_state.world_compute_config["world-yuki"].get(packs_stub._ORIGINAL_ID_TAG) == "world-yuki"

    # NPC auto-generation (mirrors devkit behavior).
    npcs = [n for n in stubs_state.npcs.values() if n.get("world_id") == "world-yuki"]
    assert len(npcs) > 0


def test_install_pack_dir(tmp_path):
    """install_pack_dir copies an extracted directory into the packs root.

    install_pack_dir 将已解压目录复制进包根目录。
    """
    src = _write_pack_dir(tmp_path, package_id="dir-pack", name="Dir Pack")
    record = packs_stub.install_pack_dir(src)
    assert record["package_id"] == "dir-pack"
    assert (tmp_path / "packs" / "dir-pack" / "manifest.json").is_file()
    # Source directory untouched.
    assert (src / "manifest.json").is_file()


def test_install_archive_wrapper_dir_layout_zip(tmp_path):
    """A single top-level wrapper dir (DevKit submission shape) installs flat.

    单层顶层包装目录（DevKit 提交形态）会被平铺安装。
    """
    src = tmp_path / "payload"
    pack = _write_pack_dir(src, package_id="wrap-pack", name="Wrap Pack", char_id="wrap-char")
    # Re-wrap: move the pack dir into a single top-level wrapper dir.
    wrapper = tmp_path / "wrapped"
    wrapper.mkdir()
    shutil.move(str(pack), str(wrapper / "wrap-pack"))
    archive = tmp_path / "wrap.zip"
    _zip_dir(wrapper, archive)  # archive root = wrapper dir

    record = packs_stub.install_archive(archive)
    assert record["package_id"] == "wrap-pack"
    assert record["loaded"]["characters"] == ["wrap-char"]
    assert "wrap-char" in stubs_state.characters
    final = tmp_path / "packs" / "wrap-pack"
    assert (final / "manifest.json").is_file()
    assert not (final / "wrapped").exists()  # no nested wrapper layer


def test_install_archive_wrapper_dir_layout_7z(tmp_path):
    """Same wrapper shape via 7z installs flat too.

    相同包装形态的 7z 同样被平铺安装。
    """
    src = tmp_path / "payload"
    pack = _write_pack_dir(src, package_id="wrap7-pack", name="Wrap7", char_id="wrap7-char")
    wrapper = tmp_path / "wrapped"
    wrapper.mkdir()
    shutil.move(str(pack), str(wrapper / "wrap7-pack"))
    archive = tmp_path / "wrap.7z"
    _sevenz_dir(wrapper, archive)

    record = packs_stub.install_archive(archive)
    assert record["package_id"] == "wrap7-pack"
    assert record["loaded"]["characters"] == ["wrap7-char"]
    assert "wrap7-char" in stubs_state.characters
    final = tmp_path / "packs" / "wrap7-pack"
    assert (final / "manifest.json").is_file()
    assert not (final / "wrapped").exists()


def test_zip_symlink_entry_rejected(tmp_path):
    """A zip containing a symlink entry is rejected and cleaned up.

    含符号链接条目的 zip 被拒绝并清理。
    """
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zi = zipfile.ZipInfo("evil_link")
        zi.create_system = 3
        zi.external_attr = (0o120777 << 16)  # S_IFLNK | 0777
        zf.writestr(zi, "/tmp/outside")

    dest = tmp_path / "out"
    with pytest.raises(packs_stub.PackValidationError, match="symlink"):
        packs_stub.extract_archive(evil, dest)
    # Nothing left behind.
    assert not dest.exists() or not any(dest.iterdir())


def test_7z_symlink_entry_rejected(tmp_path):
    """A 7z containing a symlink entry is rejected and cleaned up.

    含符号链接条目的 7z 被拒绝并清理。
    """
    import os as _os

    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "target.txt").write_text("x", encoding="utf-8")
    _os.symlink("target.txt", payload / "evil_link")

    evil = tmp_path / "evil.7z"
    with py7zr.SevenZipFile(evil, "w") as zf:
        zf.writeall(payload, "payload")

    dest = tmp_path / "out7"
    with pytest.raises(packs_stub.PackValidationError, match="symlink"):
        packs_stub.extract_archive(evil, dest)
    assert not dest.exists() or not any(dest.iterdir())


def test_install_archive_replaces_existing_same_package(tmp_path):
    """Re-installing the same package_id replaces the old runtime records.

    重新安装相同 package_id 会替换旧的运行时记录。
    """
    pack = _write_pack_dir(tmp_path)
    archive = tmp_path / "a.zip"
    _zip_dir(pack, archive)

    first = packs_stub.install_archive(archive)
    assert len(_packed_character_ids()) == 1

    # Overwrite with a new version of the same package.
    pack2 = _write_pack_dir(tmp_path / "v2", package_id="char-yuki", name="Yuki", version="2.0.0")
    archive2 = tmp_path / "a2.zip"
    _zip_dir(pack2, archive2)

    second = packs_stub.install_archive(archive2)
    assert second["version"] == "2.0.0"
    assert len(_packed_character_ids()) == 1  # replaced, not duplicated
    assert stubs_state.characters["char-yuki"]["name"] == "Yuki"
    assert packs_stub.get_pack("char-yuki")["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# Manifest validation failures
# 清单校验失败
# ---------------------------------------------------------------------------


def test_manifest_missing_name_rejected(tmp_path):
    """A manifest without a name is rejected."""
    pack = _write_pack_dir(tmp_path, package_id="bad-pack", name="")
    (pack / "manifest.json").write_text(
        json.dumps({"schema": "xijian.pack/v1", "version": "1.0.0", "kind": "character"}),
        encoding="utf-8",
    )
    archive = tmp_path / "bad.zip"
    _zip_dir(pack, archive)
    with pytest.raises(packs_stub.PackValidationError, match="name"):
        packs_stub.install_archive(archive)


def test_manifest_bad_schema_rejected(tmp_path):
    """A manifest with an unsupported schema is rejected."""
    pack = _write_pack_dir(tmp_path, package_id="bad-pack")
    (pack / "manifest.json").write_text(
        json.dumps({"schema": "xijian.v99", "name": "X", "version": "1.0.0", "kind": "character"}),
        encoding="utf-8",
    )
    archive = tmp_path / "bad.zip"
    _zip_dir(pack, archive)
    with pytest.raises(packs_stub.PackValidationError, match="schema"):
        packs_stub.install_archive(archive)


def test_manifest_bad_kind_rejected(tmp_path):
    """A manifest with an invalid kind is rejected."""
    pack = _write_pack_dir(tmp_path, package_id="bad-pack")
    (pack / "manifest.json").write_text(
        json.dumps({"schema": "xijian.pack/v1", "name": "X", "version": "1.0.0", "kind": "robot"}),
        encoding="utf-8",
    )
    archive = tmp_path / "bad.zip"
    _zip_dir(pack, archive)
    with pytest.raises(packs_stub.PackValidationError, match="kind"):
        packs_stub.install_archive(archive)


def test_manifest_devkit_submission_schema_accepted(tmp_path):
    """DevKit submission schema is accepted and extra fields stripped.

    DevKit 提交 schema 被接受，额外字段被剥离。
    """
    pack = _write_pack_dir(tmp_path, package_id="devkit-pack", name="Devkit Pack")
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema"] = "xijian.devkit.submission/v1"
    manifest["developer_id"] = "dev-1"
    manifest["submitted_at"] = "2026-01-01T00:00:00Z"
    manifest["ai_ratio"] = 0.5
    manifest["notes"] = "ignored"
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / "dk.zip"
    _zip_dir(pack, archive)

    record = packs_stub.install_archive(archive)
    assert record["package_id"] == "devkit-pack"
    stored = packs_stub.get_pack("devkit-pack")["manifest"]
    assert stored.get("developer_id") is None
    assert stored.get("notes") is None


def test_manifest_kind_derived_from_layout(tmp_path):
    """Missing kind is derived from the directory layout.

    缺失的 kind 按目录布局推导。
    """
    pack = _write_pack_dir(tmp_path, package_id="derived", name="Derived", kind="")
    (pack / "manifest.json").write_text(
        json.dumps({"schema": "xijian.pack/v1", "package_id": "derived", "name": "Derived", "version": "1.0.0"}),
        encoding="utf-8",
    )
    archive = tmp_path / "d.zip"
    _zip_dir(pack, archive)
    record = packs_stub.install_archive(archive)
    assert record["kind"] == "character"  # only characters/ present
    assert record["loaded"]["characters"] == ["char-yuki"]


def test_package_id_slugified_when_invalid(tmp_path):
    """An invalid package_id is replaced by a name-derived slug.

    非法的 package_id 被替换为从名称派生的 slug。
    """
    pack = _write_pack_dir(tmp_path, package_id="Bad Pack!!", name="My Great Character")
    archive = tmp_path / "s.zip"
    _zip_dir(pack, archive)
    record = packs_stub.install_archive(archive)
    assert record["package_id"] == "my-great-character"
    assert packs_stub.get_pack("my-great-character") is not None


def test_archive_without_manifest_rejected(tmp_path):
    """An archive with no manifest.json is rejected."""
    pack = tmp_path / "empty-pack"
    pack.mkdir()
    (pack / "characters").mkdir()
    archive = tmp_path / "e.zip"
    _zip_dir(pack, archive)
    with pytest.raises(packs_stub.PackValidationError, match="manifest"):
        packs_stub.install_archive(archive)


def test_unsupported_archive_format_rejected(tmp_path):
    """A .tar.gz archive is rejected with PackValidationError."""
    bad = tmp_path / "x.tar.gz"
    bad.write_bytes(b"not an archive")
    with pytest.raises(packs_stub.PackValidationError, match="unsupported"):
        packs_stub.extract_archive(bad, tmp_path / "out")


def test_path_traversal_zip_rejected_and_cleaned(tmp_path):
    """A zip with a traversal entry is rejected and partially-extracted files cleaned.

    含路径穿越条目的 zip 被拒绝，已解出的部分被清理。
    """
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
        zf.writestr("manifest.json", "{}")

    dest = tmp_path / "out"
    with pytest.raises(packs_stub.PackValidationError, match=r"\.\."):
        packs_stub.extract_archive(evil, dest)
    # Nothing left behind.
    assert not dest.exists() or not any(dest.iterdir())


def test_path_traversal_absolute_and_backslash_rejected(tmp_path):
    """Absolute and backslash entries are rejected too."""
    evil = tmp_path / "evil2.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("/etc/passwd", "x")
    with pytest.raises(packs_stub.PackValidationError, match="absolute"):
        packs_stub.extract_archive(evil, tmp_path / "out")

    evil3 = tmp_path / "evil3.zip"
    with zipfile.ZipFile(evil3, "w") as zf:
        zf.writestr("..\\..\\win.ini", "x")
    with pytest.raises(packs_stub.PackValidationError):
        packs_stub.extract_archive(evil3, tmp_path / "out3")


# ---------------------------------------------------------------------------
# Scan / unload / uninstall
# 扫描 / 卸载
# ---------------------------------------------------------------------------


def test_scan_packs_rebuilds_index_and_runtime(tmp_path):
    """scan_packs rebuilds packs_index and loads every pack into runtime.

    scan_packs 重建 packs_index 并将每个包加载进运行时。
    """
    pack = _write_pack_dir(tmp_path)
    archive = tmp_path / "s.zip"
    _zip_dir(pack, archive)
    packs_stub.install_archive(archive)

    # Wipe index + runtime to simulate a cold start.
    stubs_state.packs_index.clear()
    stubs_state.characters.clear()

    result = packs_stub.scan_packs()
    assert len(result["installed"]) == 1
    assert result["errors"] == []
    assert stubs_state.packs_index["char-yuki"]["version"] == "1.0.0"
    assert "char-yuki" in stubs_state.characters

    # Idempotent: a second scan replaces records without duplicating.
    result2 = packs_stub.scan_packs()
    assert len(result2["installed"]) == 1
    assert len(_packed_character_ids()) == 1


def test_unload_pack_clears_runtime_only(tmp_path):
    """unload_pack removes runtime records but keeps the directory.

    unload_pack 仅清除运行时记录，保留目录。
    """
    pack = _write_pack_dir(tmp_path)
    archive = tmp_path / "u.zip"
    _zip_dir(pack, archive)
    packs_stub.install_archive(archive)

    packs_stub.unload_pack("char-yuki")

    assert "char-yuki" not in stubs_state.characters
    assert "char-yuki" not in stubs_state.memory_configs
    assert "char-yuki" not in stubs_state.character_state_configs
    assert not [m for m in stubs_state.memory.values() if m.get(packs_stub._ORIGINAL_ID_TAG) == "char-yuki"]
    assert packs_stub.get_pack("char-yuki") is not None  # index untouched
    assert (tmp_path / "packs" / "char-yuki").is_dir()  # dir untouched


def test_uninstall_pack_clears_runtime_and_directory(tmp_path):
    """uninstall_pack removes runtime records, the directory and the index.

    uninstall_pack 清除运行时记录、删除目录并移除索引。
    """
    pack = _write_pack_dir(tmp_path)
    archive = tmp_path / "un.zip"
    _zip_dir(pack, archive)
    packs_stub.install_archive(archive)

    removed = packs_stub.uninstall_pack("char-yuki")
    assert removed["package_id"] == "char-yuki"
    assert removed["version"] == "1.0.0"

    assert packs_stub.get_pack("char-yuki") is None
    assert not (tmp_path / "packs" / "char-yuki").exists()
    assert "char-yuki" not in stubs_state.characters

    with pytest.raises(packs_stub.PackValidationError, match="not installed"):
        packs_stub.uninstall_pack("char-yuki")


def test_list_packs(tmp_path):
    """list_packs returns every installed pack with package_id merged in.

    list_packs 返回每个已安装包（已合并 package_id）。
    """
    pack = _write_pack_dir(tmp_path)
    archive = tmp_path / "l.zip"
    _zip_dir(pack, archive)
    packs_stub.install_archive(archive)

    items = packs_stub.list_packs()
    assert len(items) == 1
    assert items[0]["package_id"] == "char-yuki"
    assert items[0]["name"] == "Yuki"


# ---------------------------------------------------------------------------
# ensure_preload_packs
# 预置包
# ---------------------------------------------------------------------------


def test_ensure_preload_packs_installs_and_is_idempotent(tmp_path, monkeypatch):
    """Preload installs archives + dirs; same version is skipped on re-run.

    预置安装归档与目录；同版本再次运行时跳过。
    """
    preload = tmp_path / "preload"
    preload.mkdir()

    # Archive entry.
    pack = _write_pack_dir(tmp_path / "src", package_id="pre-pack", name="Pre Pack", char_id="pre-pack")
    archive = preload / "pre-pack.zip"
    _zip_dir(pack, archive)
    # Directory entry (already extracted).
    _write_pack_dir(preload, package_id="dir-pack", name="Dir Pack", char_id="dir-pack")

    monkeypatch.setenv("XIJIAN_PRELOAD_PACKS_DIR", str(preload))

    first = packs_stub.ensure_preload_packs()
    assert len(first["installed"]) == 2
    assert first["skipped"] == []
    assert first["errors"] == []
    assert "pre-pack" in stubs_state.characters
    assert "dir-pack" in stubs_state.characters

    second = packs_stub.ensure_preload_packs()
    assert second["installed"] == []
    assert len(second["skipped"]) == 2
    for item in second["skipped"]:
        assert item["reason"] == "already installed"


def test_ensure_preload_packs_no_dir_returns_empty(tmp_path, monkeypatch):
    """Missing preload dir yields empty results without raising.

    预置目录缺失时返回空结果且不抛异常。
    """
    monkeypatch.setenv("XIJIAN_PRELOAD_PACKS_DIR", str(tmp_path / "nope"))
    result = packs_stub.ensure_preload_packs()
    assert result == {"installed": [], "skipped": [], "errors": []}


# ---------------------------------------------------------------------------
# HTTP layer
# HTTP 层
# ---------------------------------------------------------------------------


def _install_zip_via_api(client, auth_headers, tmp_path, **overrides) -> dict:
    """Install a zip pack through the API and return the response JSON.

    通过 API 安装 zip 包并返回响应 JSON。
    """
    pack = _write_pack_dir(tmp_path, **overrides)
    archive = tmp_path / "upload.zip"
    _zip_dir(pack, archive)
    data = {"file": (io.BytesIO(archive.read_bytes()), "upload.zip")}
    resp = client.post(
        "/v1/xijian/packs/install",
        headers=auth_headers,
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def test_api_list_and_detail(client, auth_headers, tmp_path):
    """GET list + detail return installed pack records.

    GET 列表与详情返回已安装包记录。
    """
    _install_zip_via_api(client, auth_headers, tmp_path, package_id="char-yuki")

    listing = client.get("/v1/xijian/packs", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.get_json()
    assert any(p["package_id"] == "char-yuki" for p in body)

    detail = client.get("/v1/xijian/packs/char-yuki", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.get_json()["name"] == "Yuki"

    missing = client.get("/v1/xijian/packs/nope", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "pack_not_found"


def test_api_install_multipart_and_path(client, auth_headers, tmp_path):
    """POST install accepts both multipart file and JSON path.

    POST install 同时接受 multipart 文件与 JSON path。
    """
    # multipart (done by the helper above).
    _install_zip_via_api(client, auth_headers, tmp_path, package_id="mp-pack")

    # JSON path.
    pack = _write_pack_dir(tmp_path / "path-src", package_id="path-pack", name="Path Pack")
    archive = tmp_path / "path.zip"
    _zip_dir(pack, archive)
    resp = client.post(
        "/v1/xijian/packs/install",
        headers=auth_headers,
        json={"path": str(archive)},
    )
    assert resp.status_code == 201
    assert resp.get_json()["package_id"] == "path-pack"

    items = client.get("/v1/xijian/packs", headers=auth_headers).get_json()
    assert {p["package_id"] for p in items} == {"mp-pack", "path-pack"}


def test_api_install_invalid_extension(client, auth_headers, tmp_path):
    """POST install with a non-archive file is rejected with 400.

    POST install 上传非归档文件返回 400。
    """
    resp = client.post(
        "/v1/xijian/packs/install",
        headers=auth_headers,
        data={"file": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_extension"


def test_api_install_missing_path(client, auth_headers):
    """POST install without file or path is rejected with 400.

    POST install 缺少 file 或 path 返回 400。
    """
    resp = client.post(
        "/v1/xijian/packs/install",
        headers=auth_headers,
        json={"name": "x"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "missing_file_or_path"


def test_api_install_bad_archive_returns_400(client, auth_headers, tmp_path):
    """POST install with an invalid pack archive returns 400.

    POST install 使用无效包归档返回 400。
    """
    pack = tmp_path / "bad"
    pack.mkdir()
    (pack / "manifest.json").write_text("not json", encoding="utf-8")
    archive = tmp_path / "bad.zip"
    _zip_dir(pack, archive)
    resp = client.post(
        "/v1/xijian/packs/install",
        headers=auth_headers,
        json={"path": str(archive)},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "pack_validation_error"


def test_api_delete(client, auth_headers, tmp_path):
    """DELETE uninstalls the pack and returns the removed record.

    DELETE 卸载包并返回被移除的记录。
    """
    _install_zip_via_api(client, auth_headers, tmp_path, package_id="del-pack")

    resp = client.delete("/v1/xijian/packs/del-pack", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.get_json()["package_id"] == "del-pack"

    assert client.get("/v1/xijian/packs/del-pack", headers=auth_headers).status_code == 404

    missing = client.delete("/v1/xijian/packs/del-pack", headers=auth_headers)
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "pack_not_found"


def test_api_rescan(client, auth_headers, tmp_path):
    """POST rescan rebuilds the index and reports counts.

    POST rescan 重建索引并返回计数。
    """
    _install_zip_via_api(client, auth_headers, tmp_path, package_id="scan-pack")

    # Wipe the index to simulate drift.
    stubs_state.packs_index.clear()

    resp = client.post("/v1/xijian/packs/rescan", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["installed"] == 1
    assert body["errors"] == []

    assert packs_stub.get_pack("scan-pack") is not None
    # The pack's character id is char-yuki (default in the helper).
    assert "char-yuki" in stubs_state.characters