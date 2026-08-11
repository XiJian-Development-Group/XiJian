"""Tests for the resource import pipeline (``stubs.resources`` + routes).

资源导入管线（``stubs.resources`` + 路由）的测试。

Covers the async import job flow: POST /v1/xijian/resources/import → 202 queued
→ background thread installs via packs engine → job becomes completed/failed.

覆盖异步导入任务流：POST /v1/xijian/resources/import → 202 queued
→ 后台线程通过包引擎安装 → 任务变为 completed/failed。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from xijian_api.stubs import resources as resources_stub
from xijian_api.stubs import state as stubs_state
from xijian_api.stubs import packs as packs_stub
from xijian_api.stubs.files import persist as files_persist
from xijian_api.utils.ids import gen_import_job_id


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _write_character_pack_dir(root: Path, *, package_id: str = "char-yuki", name: str = "Yuki") -> Path:
    """在磁盘上创建一个最小的角色资源包目录。"""
    pack = root / package_id
    pack.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "xijian.pack/v1",
        "package_id": package_id,
        "name": name,
        "version": "1.0.0",
        "kind": "character",
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    cdir = pack / "characters" / package_id
    cdir.mkdir(parents=True, exist_ok=True)
    char_json = {
        "id": package_id,
        "name": name,
        "display_name": name,
        "description": "test character",
        "persona_doc": "initial",
    }
    (cdir / "character.json").write_text(json.dumps(char_json), encoding="utf-8")
    (cdir / "persona.md").write_text("# Persona\nTest persona.", encoding="utf-8")
    mdir = pack / "memories" / package_id
    mdir.mkdir(parents=True, exist_ok=True)
    entries = [{"id": f"mem_{package_id}_1", "content": "memory", "type": "long", "importance": 0.8}]
    (mdir / "entries.json").write_text(json.dumps(entries), encoding="utf-8")

    return pack


def _zip_dir(src: Path, dest: Path) -> None:
    """将包含 manifest.json 的目录压缩为归档，manifest 位于归档根。"""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_imports(tmp_path, monkeypatch):
    """每个测试将资源包目录指向新的临时目录，并清除导入任务。

    同时将 ``XIJIAN_DATA_DIR`` 指向同一临时目录 (S5)：资源导入的
    服务端路径白名单要求归档位于用户数据目录内，而测试归档都建在
    ``tmp_path`` 下，因此数据根目录必须跟随。
    """
    packs_stub._set_paths_for_test(tmp_path / "packs")
    monkeypatch.setenv("XIJIAN_DATA_DIR", str(tmp_path))
    # 清除导入任务
    stubs_state.import_jobs.clear()
    yield
    packs_stub._set_paths_for_test(None)
    stubs_state.import_jobs.clear()


# ---------------------------------------------------------------------------
# Stub 层 — start_import / get
# ---------------------------------------------------------------------------


def test_start_import_queues_job_and_returns_file_id(tmp_path):
    """start_import 创建一个带有全新 file_id 的排队任务。"""
    pack = _write_character_pack_dir(tmp_path)
    archive = tmp_path / "pack.zip"
    _zip_dir(pack, archive)

    job_id = gen_import_job_id()
    payload = {"name": "Test Import", "kind": "character", "path": str(archive)}
    resources_stub.start_import(payload, job_id)

    job = resources_stub.get(job_id)
    assert job is not None
    assert job["id"] == job_id
    assert job["status"] == "queued"
    assert job["name"] == "Test Import"
    assert "file_id" in job

    # 归档字节由后台线程持久化；轮询直到完成。
    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed"
    assert job["file_id"] in stubs_state.files


def test_start_import_with_file_id(tmp_path):
    """start_import 支持已上传的 file_id 来源。"""
    pack = _write_character_pack_dir(tmp_path)
    archive = tmp_path / "pack.zip"
    _zip_dir(pack, archive)
    archive_bytes = archive.read_bytes()

    file_id = "test-file-" + Path(archive).stem
    files_persist(file_id, archive_bytes, purpose="user_data", filename="pack.zip")

    job_id = gen_import_job_id()
    payload = {"name": "From FileID", "file_id": file_id}
    resources_stub.start_import(payload, job_id)

    job = resources_stub.get(job_id)
    assert job["status"] == "queued"
    # start_import 总是分配自己的全新目标 file_id。
    assert job["file_id"] != file_id

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed"
    assert job["package_id"] == "char-yuki"


def test_start_import_requires_path_or_file_id():
    """start_import 缺少 path/file_id 时，任务在后台线程中失败。"""
    job_id = gen_import_job_id()
    payload = {"name": "No Source"}
    resources_stub.start_import(payload, job_id)

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "failed"
    assert "path" in job["error"] or "file_id" in job["error"]


def test_get_nonexistent_returns_none():
    """get 对未知的 job_id 返回 None。"""
    assert resources_stub.get("nope") is None


# ---------------------------------------------------------------------------
# 集成 — 完整异步导入流程
# ---------------------------------------------------------------------------


def test_import_job_completes_successfully(tmp_path):
    """有效的资源包归档产生已完成的任务，含 package_id + summary。"""
    pack = _write_character_pack_dir(tmp_path, package_id="char-yuki", name="Yuki")
    archive = tmp_path / "pack.zip"
    _zip_dir(pack, archive)

    job_id = gen_import_job_id()
    payload = {"name": "Import Yuki", "kind": "character", "path": str(archive)}
    resources_stub.start_import(payload, job_id)

    # 等待后台线程完成。
    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete in time")

    job = resources_stub.get(job_id)
    assert job["status"] == "completed"
    assert "package_id" in job
    assert job["package_id"] == "char-yuki"
    assert "result" in job
    assert job["result"]["kind"] == "character"
    assert job["result"]["loaded_characters"] == 1
    assert job["result"]["loaded_memories"] == 1
    assert job["completed_at"] is not None

    # 资源包已安装到运行时。
    assert "char-yuki" in stubs_state.characters
    assert stubs_state.characters["char-yuki"][packs_stub._SOURCE_TAG] is True
    assert stubs_state.characters["char-yuki"][packs_stub._ORIGINAL_ID_TAG] == "char-yuki"


def test_import_job_fails_on_invalid_archive(tmp_path):
    """损坏/错误的归档产生失败的任务，且错误信息非空。"""
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not an archive")

    job_id = gen_import_job_id()
    payload = {"name": "Bad Import", "path": str(bad)}
    resources_stub.start_import(payload, job_id)

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    job = resources_stub.get(job_id)
    assert job["status"] == "failed"
    assert "error" in job
    assert job["error"]
    assert "corrupt" in job["error"].lower() or "unsupported" in job["error"].lower()


def test_import_job_fails_on_missing_archive(tmp_path):
    """不存在的路径产生失败的任务。"""
    job_id = gen_import_job_id()
    payload = {"name": "Missing", "path": str(tmp_path / "nope.zip")}
    resources_stub.start_import(payload, job_id)

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    job = resources_stub.get(job_id)
    assert job["status"] == "failed"
    assert "error" in job
    assert job["error"]


def test_import_job_persists_archive_to_files_storage(tmp_path):
    """导入线程将归档字节以任务的 file_id 持久化到文件存储。"""
    pack = _write_character_pack_dir(tmp_path)
    archive = tmp_path / "pack.zip"
    _zip_dir(pack, archive)

    job_id = gen_import_job_id()
    payload = {"name": "Persist Test", "path": str(archive)}
    resources_stub.start_import(payload, job_id)

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    job = resources_stub.get(job_id)
    assert job["status"] == "completed"
    file_id = job["file_id"]
    assert file_id in stubs_state.files
    stored = stubs_state.files[file_id]
    assert stored["purpose"] == "user_data"
    assert stored["filename"] == "pack.zip"


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------


def _install_via_import(client, auth_headers, tmp_path, **overrides) -> dict:
    """辅助函数：以 JSON path POST /v1/xijian/resources/import，等待并返回任务。"""
    pack = _write_character_pack_dir(tmp_path, **overrides)
    archive = tmp_path / "import.zip"
    _zip_dir(pack, archive)

    resp = client.post(
        "/v1/xijian/resources/import",
        headers=auth_headers,
        json={"name": "API Import", "kind": "character", "path": str(archive)},
    )
    assert resp.status_code == 202
    job = resp.get_json()
    job_id = job["job_id"]

    # 轮询直到完成。
    import time
    for _ in range(50):
        r = client.get(f"/v1/xijian/resources/imports/{job_id}", headers=auth_headers)
        assert r.status_code == 200
        job = r.get_json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail("import job did not complete")


def test_api_import_json_path_returns_202_then_completed(client, auth_headers, tmp_path):
    """POST import 携带 JSON path 返回 202，随后任务完成。"""
    job = _install_via_import(client, auth_headers, tmp_path, package_id="api-char", name="API Char")
    assert job["status"] == "completed"
    assert job["package_id"] == "api-char"
    assert job["result"]["loaded_characters"] == 1


def test_api_import_with_file_id(client, auth_headers, tmp_path):
    """POST import 携带 file_id 可用。"""
    pack = _write_character_pack_dir(tmp_path, package_id="fileid-char", name="FileID Char")
    archive = tmp_path / "fid.zip"
    _zip_dir(pack, archive)

    # 先上传到文件端点。
    up = client.post(
        "/v1/files",
        headers=auth_headers,
        data={"file": (io.BytesIO(archive.read_bytes()), "fid.zip")},
        content_type="multipart/form-data",
    )
    assert up.status_code == 201
    file_id = up.get_json()["id"]

    # 通过 file_id 导入。
    resp = client.post(
        "/v1/xijian/resources/import",
        headers=auth_headers,
        json={"name": "Via FileID", "kind": "character", "file_id": file_id},
    )
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]

    import time
    for _ in range(50):
        r = client.get(f"/v1/xijian/resources/imports/{job_id}", headers=auth_headers)
        job = r.get_json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    assert job["status"] == "completed"
    assert job["package_id"] == "fileid-char"


def test_api_import_missing_name_returns_400(client, auth_headers):
    """POST import 缺少 name 返回 400。"""
    resp = client.post(
        "/v1/xijian/resources/import",
        headers=auth_headers,
        json={"kind": "character"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "missing_name"


def test_api_get_import_returns_job(client, auth_headers, tmp_path):
    """GET /v1/xijian/resources/imports/<job_id> 返回任务记录。"""
    job = _install_via_import(client, auth_headers, tmp_path, package_id="get-char", name="Get Char")
    job_id = job["id"]

    r = client.get(f"/v1/xijian/resources/imports/{job_id}", headers=auth_headers)
    assert r.status_code == 200
    got = r.get_json()
    assert got["id"] == job_id
    assert got["status"] == "completed"
    assert got["package_id"] == "get-char"


def test_api_get_nonexistent_import_returns_404(client, auth_headers):
    """GET 未知的导入任务返回 404。"""
    r = client.get("/v1/xijian/resources/imports/nope", headers=auth_headers)
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "import_not_found"


def test_import_devkit_submission_schema(tmp_path):
    """DevKit 提交模式（xijian.devkit.submission/v1）被接受。"""
    pack = _write_character_pack_dir(tmp_path, package_id="dk-char", name="DK Char")
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema"] = "xijian.devkit.submission/v1"
    manifest["developer_id"] = "dev-1"
    manifest["submitted_at"] = "2026-01-01T00:00:00Z"
    manifest["ai_ratio"] = 0.5
    manifest["notes"] = "ignored"
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / "dk.zip"
    _zip_dir(pack, archive)

    job_id = gen_import_job_id()
    payload = {"name": "DK Import", "path": str(archive)}
    resources_stub.start_import(payload, job_id)

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    job = resources_stub.get(job_id)
    assert job["status"] == "completed"
    assert job["package_id"] == "dk-char"


def test_import_world_pack(tmp_path):
    """导入世界资源包会加载世界 + 环境 + NPC。"""
    # 构建一个世界资源包。
    pack = tmp_path / "world-pack"
    pack.mkdir()
    manifest = {
        "schema": "xijian.pack/v1",
        "package_id": "world-test",
        "name": "Test World",
        "version": "1.0.0",
        "kind": "world",
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    wdir = pack / "worlds" / "world-test"
    wdir.mkdir(parents=True)
    world_json = {"id": "world-test", "name": "Test World", "description": "A test world"}
    (wdir / "world.json").write_text(json.dumps(world_json), encoding="utf-8")
    (wdir / "world_doc.md").write_text("# Test World\nA world.", encoding="utf-8")
    (wdir / "world_config.json").write_text(json.dumps({"weather_probabilities": {}}), encoding="utf-8")

    archive = tmp_path / "world.zip"
    _zip_dir(pack, archive)

    job_id = gen_import_job_id()
    payload = {"name": "World Import", "path": str(archive)}
    resources_stub.start_import(payload, job_id)

    import time
    for _ in range(50):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    job = resources_stub.get(job_id)
    assert job["status"] == "completed"
    assert job["package_id"] == "world-test"
    assert job["result"]["loaded_worlds"] == 1
    assert "world-test" in stubs_state.worlds
    assert stubs_state.worlds["world-test"][packs_stub._SOURCE_TAG] is True

# ---------------------------------------------------------------------------
# S5 — import path whitelist + size limit
# S5 — 导入路径白名单 + 大小上限
# ---------------------------------------------------------------------------


def _wait_job(job_id, timeout=2.5):
    import time
    for _ in range(int(timeout / 0.05)):
        job = resources_stub.get(job_id)
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail("import job did not finish")


def test_import_path_outside_data_dir_fails(tmp_path, monkeypatch):
    """A server-side path outside the user data dir is rejected (S5)."""
    # XIJIAN_DATA_DIR is set by the autouse fixture to tmp_path; the
    # archive lives in a sibling dir → outside the data root.
    outside = tmp_path / ".." / "outside-imports"
    outside.mkdir(parents=True, exist_ok=True)
    archive = outside / "evil.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    job_id = gen_import_job_id()
    resources_stub.start_import({"name": "Evil", "path": str(archive)}, job_id)
    job = _wait_job(job_id)
    assert job["status"] == "failed"
    assert "outside the user data directory" in job["error"]


def test_import_path_traversal_rejected(tmp_path, monkeypatch):
    """A ``..``-laden path that resolves outside the data dir is rejected (S5)."""
    from xijian_api.runtime import default_storage_dir

    data_root = default_storage_dir().resolve()
    target = data_root / "files" / "innocent.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hi")

    # A path that LOOKS inside the root but escapes via .. resolves outside.
    escape = data_root / "files" / ".." / ".." / ".." / ".." / ".." / ".." / ".." / ".." / "tmp" / "evil.zip"
    try:
        job_id = gen_import_job_id()
        resources_stub.start_import({"name": "Escape", "path": str(escape)}, job_id)
        job = _wait_job(job_id)
        assert job["status"] == "failed"
        assert "outside the user data directory" in job["error"]
    finally:
        target.unlink(missing_ok=True)


def test_import_path_inside_data_dir_ok(tmp_path):
    """A path inside the data dir proceeds (S5) — valid zip completes."""
    pack = _write_character_pack_dir(tmp_path, package_id="whitelist-char", name="WL")
    archive = tmp_path / "wl.zip"
    _zip_dir(pack, archive)

    job_id = gen_import_job_id()
    resources_stub.start_import({"name": "WL", "path": str(archive)}, job_id)
    job = _wait_job(job_id)
    assert job["status"] == "completed"
    assert job["package_id"] == "whitelist-char"


def test_import_path_too_large_fails(tmp_path, monkeypatch):
    """Archives over the 512 MiB cap are rejected before reading (S5)."""
    from xijian_api.stubs import resources as resources_module

    monkeypatch.setattr(resources_module, "_MAX_IMPORT_BYTES", 1024)
    big = tmp_path / "big.zip"
    big.write_bytes(b"x" * 2048)

    job_id = gen_import_job_id()
    resources_stub.start_import({"name": "Big", "path": str(big)}, job_id)
    job = _wait_job(job_id)
    assert job["status"] == "failed"
    assert "too large" in job["error"]


def test_import_file_id_too_large_fails(tmp_path, monkeypatch):
    """A stored file over the cap is rejected via its recorded size (S5)."""
    from xijian_api.stubs import resources as resources_module

    monkeypatch.setattr(resources_module, "_MAX_IMPORT_BYTES", 1024)
    file_id = "test-file-oversize"
    files_persist(file_id, b"y" * 2048, purpose="user_data", filename="big.zip")

    job_id = gen_import_job_id()
    resources_stub.start_import({"name": "Big File", "file_id": file_id}, job_id)
    job = _wait_job(job_id)
    assert job["status"] == "failed"
    assert "too large" in job["error"]
