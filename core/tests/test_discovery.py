"""Discovery 模块单元测试 — 写入/读取/删除/兼容读/校验。

Discovery 模块单元测试（不启动 Core，monkeypatch 隔离文件路径）。
"""

from __future__ import annotations

import json

import pytest

from xijian_api import discovery


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    """把 DISCOVERY_FILE / LEGACY_DISCOVERY_FILE 指向临时目录。"""
    new_file = tmp_path / "xijian_core.json"
    legacy_file = tmp_path / "legacy" / "xijian_core.json"
    monkeypatch.setattr(discovery, "DISCOVERY_FILE", new_file)
    monkeypatch.setattr(discovery, "LEGACY_DISCOVERY_FILE", legacy_file)
    return {"new": new_file, "legacy": legacy_file}


def test_write_creates_parent_and_file(_isolate_paths):
    discovery.write_discovery(port=18500, auth_token="tok-1", pid=123)
    assert discovery.DISCOVERY_FILE.is_file()
    data = json.loads(discovery.DISCOVERY_FILE.read_text(encoding="utf-8"))
    assert data["port"] == 18500
    assert data["auth_token"] == "tok-1"
    assert data["pid"] == 123
    assert data["host"] == "127.0.0.1"


def test_read_roundtrip(_isolate_paths):
    discovery.write_discovery(port=18501, auth_token="tok-2", pid=456)
    info = discovery.read_discovery()
    assert info is not None
    assert info["port"] == 18501
    assert info["auth_token"] == "tok-2"


def test_read_returns_none_when_absent(_isolate_paths):
    assert discovery.read_discovery() is None


def test_read_falls_back_to_legacy(_isolate_paths):
    """新路径缺失时回退到旧路径（兼容旧版 DevKit / Core）。"""
    _isolate_paths["legacy"].parent.mkdir(parents=True)
    _isolate_paths["legacy"].write_text(
        json.dumps({"port": 18502, "auth_token": "legacy-tok", "pid": 789}),
        encoding="utf-8",
    )
    info = discovery.read_discovery()
    assert info is not None
    assert info["port"] == 18502
    assert info["auth_token"] == "legacy-tok"


def test_read_ignores_corrupted_new_then_uses_legacy(_isolate_paths):
    """新路径损坏时跳过并尝试旧路径。"""
    discovery.DISCOVERY_FILE.parent.mkdir(parents=True, exist_ok=True)
    discovery.DISCOVERY_FILE.write_text("{not-json", encoding="utf-8")
    _isolate_paths["legacy"].parent.mkdir(parents=True)
    _isolate_paths["legacy"].write_text(
        json.dumps({"port": 18503, "auth_token": "tok-3", "pid": 1}),
        encoding="utf-8",
    )
    info = discovery.read_discovery()
    assert info is not None
    assert info["port"] == 18503


def test_remove_deletes_new_only(_isolate_paths):
    discovery.write_discovery(port=18500, auth_token="tok-4", pid=2)
    # 旧路径保留一个文件，remove 不应触碰它
    _isolate_paths["legacy"].parent.mkdir(parents=True, exist_ok=True)
    _isolate_paths["legacy"].write_text("{}", encoding="utf-8")

    discovery.remove_discovery()
    assert not discovery.DISCOVERY_FILE.exists()
    assert _isolate_paths["legacy"].exists(), "remove 只应删除新路径"


def test_remove_idempotent(_isolate_paths):
    discovery.remove_discovery()
    discovery.remove_discovery()  # 不抛错


def test_verify_discovery_health(_isolate_paths):
    """verify_discovery 对存活 Core 返回 True、对未监听端口返回 False。"""
    assert discovery.verify_discovery(None) is False
    # 未监听端口 → 校验失败
    info = {"host": "127.0.0.1", "port": 1, "auth_token": "x"}
    assert discovery.verify_discovery(info) is False
