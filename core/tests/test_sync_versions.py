"""Tests for ``core/scripts/sync-versions.py``.

版本同步脚本的测试。

The script is imported as a module so its pure functions and per-target
sync helpers can be exercised against temporary files — the repo's real
files are never touched by the suite.

脚本以模块方式导入，这样它的纯函数和按目标同步的辅助函数可以针对
临时文件测试 —— 测试套件绝不触碰仓库真实文件。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# The script file is ``sync-versions.py`` (hyphen) — not a valid Python
# module name — so it is loaded by path via importlib.
# 脚本文件是 ``sync-versions.py``（连字符）—— 不是合法的 Python 模块名 ——
# 因此用 importlib 按路径加载。
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync-versions.py"
_spec = importlib.util.spec_from_file_location("sync_versions", SCRIPT_PATH)
sv = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sv)


# ---------------------------------------------------------------------------
# 版本转换
# ---------------------------------------------------------------------------


class TestNormalizePep440:
    def test_v_prefix_stripped(self):
        assert sv.normalize_pep440("v1.2.3") == "1.2.3"

    def test_alpha_suffix(self):
        assert sv.normalize_pep440("v1.2.0-Alpha") == "1.2.0a0"

    def test_beta_suffix(self):
        assert sv.normalize_pep440("1.0.0-Beta") == "1.0.0b0"

    def test_underscore_separator(self):
        assert sv.normalize_pep440("v1.0.0_Alpha") == "1.0.0a0"

    def test_plain_numeric(self):
        assert sv.normalize_pep440("1.6.2") == "1.6.2"


class TestNumericPart:
    def test_plain(self):
        assert sv.numeric_part("1.6.2") == "1.6.2"

    def test_v_prefix(self):
        assert sv.numeric_part("v1.6.2") == "1.6.2"

    def test_suffixed(self):
        assert sv.numeric_part("v1.6.2-Alpha") == "1.6.2"


# ---------------------------------------------------------------------------
# 按目标同步辅助函数（基于临时文件）
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestSyncPyproject:
    def test_updates_version(self, tmp_path):
        p = _write(tmp_path / "pyproject.toml", '[project]\nname = "x" \nversion = "0.1.0"\n')
        changed, content = sv.sync_pyproject(p, "1.2.0a0")
        assert changed
        assert 'version = "1.2.0a0"' in content

    def test_idempotent(self, tmp_path):
        p = _write(tmp_path / "pyproject.toml", '[project]\nversion = "1.2.0a0"\n')
        changed, _ = sv.sync_pyproject(p, "1.2.0a0")
        assert not changed

    def test_missing_field_raises(self, tmp_path):
        p = _write(tmp_path / "pyproject.toml", "[project]\nname = \"x\"\n")
        with pytest.raises(ValueError):
            sv.sync_pyproject(p, "1.2.0a0")


class TestSyncCoreInit:
    def test_updates_version(self, tmp_path):
        p = _write(tmp_path / "__init__.py", '__version__ = "0.1.0"\n')
        changed, content = sv.sync_core_init(p, "1.2.0a0")
        assert changed
        assert '__version__ = "1.2.0a0"' in content


class TestSyncVersionModule:
    def test_generates_module(self, tmp_path):
        p = tmp_path / "_version.py"
        changed, content = sv.sync_core_version_module(p, "v1.2.0-Alpha", "1.2.0a0")
        assert changed
        assert "CORE_VERSION = 'v1.2.0-Alpha'" in content
        assert "CORE_VERSION_NORMALIZED = '1.2.0a0'" in content

    def test_idempotent(self, tmp_path):
        p = tmp_path / "_version.py"
        _, content = sv.sync_core_version_module(p, "v1.2.0-Alpha", "1.2.0a0")
        p.write_text(content, encoding="utf-8")
        changed, _ = sv.sync_core_version_module(p, "v1.2.0-Alpha", "1.2.0a0")
        assert not changed


class TestSyncDevkitVersionPy:
    def test_updates_fallback(self, tmp_path):
        p = _write(tmp_path / "version.py", 'FALLBACK_VERSION = "v1.6.2"\n')
        changed, content = sv.sync_devkit_version_py(p, "v1.7.0")
        assert changed
        assert 'FALLBACK_VERSION = "v1.7.0"' in content


class TestSyncDevkitSpec:
    def test_updates_both_fields(self, tmp_path):
        p = _write(
            tmp_path / "spec",
            '"CFBundleShortVersionString": "1.5.0",\n"CFBundleVersion": "1.5.0",\n',
        )
        changed, content = sv.sync_devkit_spec(p, "1.6.2")
        assert changed
        assert '"CFBundleShortVersionString": "1.6.2"' in content
        assert '"CFBundleVersion": "1.6.2"' in content


# ---------------------------------------------------------------------------
# 使用临时配置 + 目标映射的端到端 run_sync
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> dict[str, Path]:
    """创建一个处处版本不同步的迷你仓库布局。"""
    config = tmp_path / "Config" / "Config.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({
            "Version": {"CoreApi": "v2.0.0-Beta", "DevKit": "v3.1.4"},
        }),
        encoding="utf-8",
    )
    targets = {
        "core_pyproject": _write(tmp_path / "core" / "pyproject.toml", '[project]\nversion = "0.1.0"\n'),
        "core_init": _write(tmp_path / "core" / "__init__.py", '__version__ = "0.1.0"\n'),
        "core_version_module": tmp_path / "core" / "_version.py",
        "devkit_version_py": _write(tmp_path / "devkit" / "version.py", 'FALLBACK_VERSION = "v1.6.2"\n'),
        "devkit_spec": _write(
            tmp_path / "devkit" / "spec",
            '"CFBundleShortVersionString": "1.5.0",\n"CFBundleVersion": "1.5.0",\n',
        ),
    }
    return {"config": config, "targets": targets}


def test_run_sync_writes_every_target(tmp_path):
    repo = _make_repo(tmp_path)
    report = sv.run_sync(repo["config"], dry_run=False, targets=repo["targets"])
    assert all(e["status"] == "changed" for e in report), report
    assert 'version = "2.0.0b0"' in repo["targets"]["core_pyproject"].read_text()
    assert '__version__ = "2.0.0b0"' in repo["targets"]["core_init"].read_text()
    assert "CORE_VERSION = 'v2.0.0-Beta'" in repo["targets"]["core_version_module"].read_text()
    assert 'FALLBACK_VERSION = "v3.1.4"' in repo["targets"]["devkit_version_py"].read_text()
    assert '"CFBundleShortVersionString": "3.1.4"' in repo["targets"]["devkit_spec"].read_text()


def test_run_sync_dry_run_writes_nothing(tmp_path):
    repo = _make_repo(tmp_path)
    before = repo["targets"]["core_pyproject"].read_text()
    report = sv.run_sync(repo["config"], dry_run=True, targets=repo["targets"])
    assert all(e["status"] == "changed" for e in report)
    assert repo["targets"]["core_pyproject"].read_text() == before


def test_run_sync_second_pass_is_unchanged(tmp_path):
    repo = _make_repo(tmp_path)
    sv.run_sync(repo["config"], dry_run=False, targets=repo["targets"])
    report = sv.run_sync(repo["config"], dry_run=False, targets=repo["targets"])
    assert all(e["status"] == "unchanged" for e in report), report


def test_load_versions_missing_coreapi_raises(tmp_path):
    config = tmp_path / "Config.json"
    config.write_text(json.dumps({"Version": {"DevKit": "v1.0.0"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        sv.load_versions(config)


def test_cli_check_exit_code(tmp_path, monkeypatch):
    """``--check`` 在版本不同步时退出码为 1，同步时为 0。"""
    repo = _make_repo(tmp_path)
    monkeypatch.setattr(sv, "DEFAULT_TARGETS", repo["targets"])

    # 不同步 → 退出码 1。
    rc = sv.main(["--check", "--config", str(repo["config"])])
    assert rc == 1

    # 先同步，再检查 → 退出码 0。
    sv.main(["--config", str(repo["config"])])
    rc = sv.main(["--check", "--config", str(repo["config"])])
    assert rc == 0


def test_cli_missing_config_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "DEFAULT_TARGETS", {})
    rc = sv.main(["--check", "--config", str(tmp_path / "nope.json")])
    assert rc == 2
